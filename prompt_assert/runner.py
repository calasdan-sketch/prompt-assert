"""Runner: execute cases against a prompt version, with caching.

Cache (.prompt-assert/cache/) is keyed by prompt text + case + model +
provider base URL, so
`diff v1 v2` never pays for the same call twice and CI re-runs are cheap.

LLM-judge verdicts are cached too, keyed by (prompt version, case name,
output hash, judge model + base URL) — a second identical run never re-pays the judge. Transient
judge outages are never cached: only real verdicts are stored.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import asserts as asserts_mod
from . import cassette as cassette_mod
from . import model as model_mod
from . import suite as suite_mod

CACHE_ROOT = ".prompt-assert"
CACHE_DIR = CACHE_ROOT + "/cache"
# Dropped into .prompt-assert/ so model outputs never land in a commit;
# the file ignores everything beside itself.
CACHE_GITIGNORE = ("# prompt-assert response cache: safe to delete, "
                   "never commit\n*\n!.gitignore\n")


def error_text(e: BaseException) -> str:
    """One line for the user. An error of ours (a ValueError, a
    FileNotFoundError("no cases file: ...")) reads as it was written; a
    read or write that failed in the system names the path and the reason
    ("prompts/a.v1.txt: Permission denied"), not Python's "[Errno 13] ..."."""
    if isinstance(e, OSError) and e.errno is not None:
        why = e.strerror or str(e)
        return f"{e.filename}: {why}" if e.filename else why
    return str(e)


def ensure_cache_ignored(root: str) -> bool:
    """Create .prompt-assert/.gitignore if missing. True when written.
    Raises OSError when it cannot be written."""
    d = os.path.join(root, CACHE_ROOT)
    p = os.path.join(d, ".gitignore")
    if os.path.exists(p):
        return False
    os.makedirs(d, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(CACHE_GITIGNORE)
    return True


@dataclasses.dataclass
class AssertionResult:
    assertion: dict
    passed: bool
    detail: str


@dataclasses.dataclass
class CaseResult:
    name: str
    passed: bool
    output: str
    assertions: list[AssertionResult]
    cached: bool = False
    error: str = ""
    #: With --repeat: how many times the case was run and how many of those
    #: runs passed. output and assertions are those of a run that agrees
    #: with the verdict: the first failing run when the case failed, the
    #: first passing run when it passed (so `passed` and the assertions
    #: shown never contradict each other).
    runs: int = 1
    passes: int | None = None
    #: Wall-clock seconds spent on the case, model and judge calls included.
    seconds: float = 0.0
    #: With --early-stop: True when the case was decided before all of its
    #: --repeat runs were made (`runs` is then the number actually made).
    stopped_early: bool = False

    @property
    def flaky(self) -> bool:
        """Passed the --min-pass-rate gate although some of its runs failed.
        A case that failed the gate is a failure, not flaky."""
        return (self.passed and self.runs > 1
                and 0 < (self.passes or 0) < self.runs)

    def as_dict(self) -> dict:
        d = {
            "name": self.name, "passed": self.passed,
            "output": self.output, "cached": self.cached,
            "error": self.error,
            "assertions": [
                {"assertion": a.assertion, "passed": a.passed,
                 "detail": a.detail} for a in self.assertions],
        }
        if self.runs > 1 or self.stopped_early:
            d.update(runs=self.runs, passes=self.passes, flaky=self.flaky)
        if self.stopped_early:
            d["stopped_early"] = True
        return d


def _cache_path(root: str, key: str) -> str:
    return os.path.join(root, CACHE_DIR, key + ".json")


def _cache_read(root: str, key: str) -> dict | None:
    """The cached JSON object for key, or None when absent/unreadable.
    Shared by response and judge-verdict entries."""
    p = _cache_path(root, key)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


#: Roots whose cache could not be written, with the reason. The first
#: failure of a command prints one warning and ends that root's cache
#: writes; cli.main() empties this at the start of every command.
_cache_off: dict[str, str] = {}
_cache_off_lock = threading.Lock()


def reset_cache_warnings() -> None:
    """Forget which roots had their cache switched off (a command warns
    once; the next command starts afresh)."""
    _cache_off.clear()


def _cache_write(root: str, key: str, payload: dict) -> bool:
    """Store `payload` under `key`. True when it was stored.

    The cache only saves money, so a write that fails - a file where the
    folder belongs, a read-only or full disk, a checkout owned by another
    user - never fails or crashes a run: the first failure prints one
    warning on stderr, the rest are skipped, and the command goes on
    without a cache. The .gitignore is written first and nothing is cached
    without it: model output must not end up in a commit."""
    if root in _cache_off:
        return False
    p = _cache_path(root, key)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        ensure_cache_ignored(root)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except OSError as e:
        with _cache_off_lock:             # --jobs: warn once, not per thread
            first = root not in _cache_off
            why = _cache_off.setdefault(root, error_text(e))
        if first:
            print(f"warning: cache disabled: cannot write {why} "
                  "(the run goes on without it)", file=sys.stderr)
        return False
    return True


def _provider(model_kw: dict) -> tuple[str, str]:
    """(model, base_url) actually used for model_kw, env defaults applied."""
    return model_mod.endpoint(model_kw.get("base_url"),
                              model_kw.get("model"))


def _judge_key(prompt_name: str, version: str, case_name: str,
               output: str, rubric: str, provider: str = "") -> str:
    """Cache key for a judge verdict. Includes the rubric so two cases
    that share a name but judge different things never collide, and the
    judge's model + base URL so another judge's verdict is never reused."""
    h = hashlib.sha256()
    for part in (prompt_name, version, case_name, rubric, provider):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    h.update(hashlib.sha256(output.encode("utf-8")).digest())
    return "judge-" + h.hexdigest()[:32]


def sample_key(key: str, sample: int) -> str:
    """The cache key of run number `sample` (1, 2 ...) of --repeat; run 0
    keeps the plain key."""
    return hashlib.sha256(f"{key}\x00sample\x00{sample}".encode()
                          ).hexdigest()[:32]


def _model_label(model_kw: dict) -> str:
    return _provider(model_kw)[0]


def run_case(root: str, prompt_name: str, version: str, prompt_text: str,
             case: dict, model_kw: dict, use_cache: bool = True,
             judge_model_kw: dict | None = None,
             sample: int = 0,
             tape: cassette_mod.Tape | None = None) -> CaseResult:
    """One run of one case. `sample` numbers the runs of --repeat: run 0
    shares the cache with a plain run, run 1, 2 ... each have their own
    entry, so a repeat never re-reads the first answer as the second.

    The response cache is keyed by what is sent to the model (the rendered
    prompt) and the model and endpoint - not by the case's assertions or
    name, so editing an assertion re-runs nothing. With a `tape` in replay
    mode the answer comes from the cassette and no model is contacted; in
    record mode the model is always called and the answer written to it."""
    model_name, base_url = _provider(model_kw)
    rendered = suite_mod.render(prompt_text, case["input"], case.get("vars"))
    key = suite_mod.response_key(rendered, model_name, base_url)
    if sample:
        key = sample_key(key, sample)
    replay = tape is not None and tape.mode == "replay"
    record = tape is not None and tape.mode == "record"
    cached = False
    if replay:
        output = tape.get(rendered, sample)
        if output is None:
            return CaseResult(
                name=case["name"], passed=False, output="", assertions=[],
                error=("no recording for this case"
                       + (f" (run {sample + 1})" if sample else "")
                       + f" in {tape.path}: the prompt or the input changed "
                       "since it was recorded - run `run --record` and "
                       "commit the result"))
        cached = True
    else:
        if use_cache and not record:
            hit = _cache_read(root, key)
            if hit is not None and isinstance(hit.get("output"), str):
                output, cached = hit["output"], True
        if not cached:
            try:
                output = model_mod.complete(rendered, **model_kw)
            except model_mod.ModelError as e:
                return CaseResult(name=case["name"], passed=False, output="",
                                  assertions=[], error=str(e))
            if record:
                tape.put(rendered, output, sample, model_name)
            elif use_cache:  # same contract as judge verdicts: off = no I/O
                _cache_write(root, key, {"output": output})

    results: list[AssertionResult] = []
    leak_text = suite_mod.template_text(prompt_text)   # no role markers
    for assertion in case["assert"]:
        try:
            assertion = asserts_mod.check(assertion)
        except ValueError as e:
            # load_cases refuses such a case; one built by hand (the API) is
            # reported here as a failed assertion instead of crashing the run
            results.append(AssertionResult(assertion, False,
                                           f"bad assertion: {e}"))
            continue
        if "judge" in assertion:
            spec = assertion["judge"]
            rubric = spec["rubric"] if isinstance(spec, dict) else spec
            jmodel_kw = judge_model_kw or model_kw
            jkey = _judge_key(prompt_name, version, case["name"],
                              output, rubric, "|".join(_provider(jmodel_kw)))
            if replay:
                verdict = tape.get_judge(rubric, output)
                if verdict is None:
                    return CaseResult(
                        name=case["name"], passed=False, output=output,
                        assertions=[],
                        error=f"no recorded judge verdict in {tape.path}: "
                              "run `run --record` and commit the result")
                passed, detail = verdict
            else:
                hit = (_cache_read(root, jkey)
                       if use_cache and not record else None)
                if hit is not None and "passed" in hit and "detail" in hit:
                    passed, detail = bool(hit["passed"]), str(hit["detail"])
                else:
                    try:
                        passed, detail = model_mod.judge(output, rubric,
                                                         **jmodel_kw)
                    except model_mod.ModelError as e:
                        # transient outage - report, never cache
                        passed, detail = False, f"judge unavailable: {e}"
                    else:
                        if record:
                            tape.put_judge(rubric, output, passed, detail,
                                           _model_label(jmodel_kw))
                        elif use_cache:
                            _cache_write(root, jkey, {"passed": passed,
                                                      "detail": detail})
            results.append(AssertionResult(assertion, passed, detail))
            continue
        try:
            passed, detail = asserts_mod.evaluate(assertion, output,
                                                  template=leak_text)
        except ValueError as e:
            passed, detail = False, f"bad assertion: {e}"
        results.append(AssertionResult(assertion, passed, detail))
    return CaseResult(name=case["name"],
                      passed=all(r.passed for r in results),
                      output=output, assertions=results, cached=cached)


def _warn_about_template(root: str, prompt_name: str, version: str,
                         prompt_text: str, cases: list[dict]) -> None:
    """One stderr line per problem, each naming the prompt file. The run
    goes on and its verdict is what it was: a prompt may not want an input."""
    path = suite_mod.prompt_path(root, prompt_name, version)
    for problem in suite_mod.template_problems(prompt_text, cases):
        print(f"warning: {path}: {problem}", file=sys.stderr)


MAX_REPEAT = 1000


def parse_repeat(value) -> int:
    """--repeat: a whole number of runs per case, 1 to MAX_REPEAT."""
    text = str(value).strip()
    # ASCII digits only: str.isdigit() also says yes to "²" (which int()
    # then refuses with a raw Python error) and to other scripts' digits
    if not re.fullmatch(r"[0-9]+", text) or not 1 <= int(text) <= MAX_REPEAT:
        raise ValueError(f"must be a whole number from 1 to {MAX_REPEAT}, "
                         f"got {value!r}")
    return int(text)


def parse_pass_rate(value) -> float:
    """--min-pass-rate: the share of runs a case must pass, as a fraction
    (0.8) or a percentage (80%). It must be above 0 - a gate that passes
    a case that failed every run is no gate - and at most 1 (100%)."""
    text = str(value).strip()
    percent = text.endswith("%")
    try:
        rate = float(text[:-1] if percent else text)
    except ValueError:
        rate = float("nan")
    if percent:
        rate /= 100
    if not 0 < rate <= 1:          # also false for nan
        raise ValueError(f"must be above 0 and at most 1 (or 100%), "
                         f"got {value!r}")
    return rate


def check_repeat_options(repeat: int, min_pass_rate: float) -> None:
    """ValueError for a pass rate below 1 with a single run: one run must
    pass whatever the rate says, so the rate would be ignored without a
    word."""
    if repeat == 1 and min_pass_rate < 1:
        raise ValueError(
            "--min-pass-rate below 1 needs --repeat N with N above 1: a "
            "single run must pass whatever the rate, so it would be ignored")


def required_passes(runs: int, rate: float) -> int:
    """The fewest passing runs out of `runs` that meet `rate`, never 0.
    Rounded up, with a little slack for float error: 0.8 of 5 is 4 runs,
    2/3 of 3 is 2."""
    return max(1, math.ceil(runs * rate - 1e-9))


def decided(passes: int, ran: int, repeat: int, need: int) -> str:
    """"pass" or "fail" once the gate cannot change whatever the remaining
    runs say, else "". Exact, not statistical: passing `need` of `repeat`
    runs is settled at the need-th pass, and it is lost at the first moment
    more than `repeat - need` runs have failed."""
    if passes >= need:
        return "pass"
    if ran - passes > repeat - need:
        return "fail"
    return ""


def _repeat_case(root, prompt_name, version, prompt_text, case, model_kw,
                 use_cache, repeat, min_pass_rate, tape=None,
                 early_stop=False, judge_model_kw=None) -> CaseResult:
    """Run `case` `repeat` times; it passes when enough runs passed. The
    first run the model could not answer ends it with that error. With
    early_stop the runs end as soon as the verdict is settled (see
    decided()): the same verdict for fewer calls, but `runs` then counts the
    runs made and a late failure of a case already settled goes unseen."""
    started = time.monotonic()
    need = required_passes(repeat, min_pass_rate)
    runs: list[CaseResult] = []
    for sample in range(repeat):
        r = run_case(root, prompt_name, version, prompt_text, case,
                     model_kw, use_cache, judge_model_kw=judge_model_kw,
                     sample=sample, tape=tape)
        if r.error:
            r.seconds = time.monotonic() - started
            return r
        runs.append(r)
        if early_stop and repeat > 1 and decided(
                sum(1 for x in runs if x.passed), len(runs), repeat, need):
            break
    if repeat == 1:
        only = runs[0]
        only.seconds = time.monotonic() - started
        return only
    passes = sum(1 for r in runs if r.passed)
    passed = passes >= need
    shown = next((r for r in runs if r.passed == passed), runs[0])
    return dataclasses.replace(
        shown, passed=passed,
        cached=all(r.cached for r in runs), runs=len(runs), passes=passes,
        seconds=time.monotonic() - started,
        stopped_early=len(runs) < repeat)


MAX_JOBS = 64


def parse_jobs(value) -> int:
    """--jobs: how many cases run at once, 1 to MAX_JOBS."""
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]+", text) or not 1 <= int(text) <= MAX_JOBS:
        raise ValueError(f"must be a whole number from 1 to {MAX_JOBS}, "
                         f"got {value!r}")
    return int(text)


def select_cases(cases: list[dict], tags=None) -> list[dict]:
    """The cases carrying at least one of `tags` (all of them when none are
    given). Tags are matched exactly, case-sensitively."""
    if not tags:
        return list(cases)
    wanted = set(tags)
    return [c for c in cases if wanted.intersection(c.get("tags", ()))]


def run_suite(root: str, prompt_name: str, version: str,
              cases: list[dict] | None = None,
              model_kw: dict | None = None,
              use_cache: bool = True,
              fail_fast: bool = True,
              repeat: int = 1,
              min_pass_rate: float = 1.0,
              jobs: int = 1,
              early_stop: bool = False,
              tape: cassette_mod.Tape | None = None,
              tags=None) -> tuple[list[CaseResult], str]:
    """Run every case of prompt_name against `version`.

    fail_fast (default): the first case the model could not answer
    (unreachable, timeout, HTTP 401/404 ...) ends the run - the remaining
    cases would only repeat the same error, each paying the timeout
    again. The returned list is then shorter than the case list; its
    last entry carries the error.

    repeat > 1 runs each case that many times (a model at temperature 0
    still varies on many providers); a case passes when at least
    min_pass_rate of its runs passed (default 1.0: every run). See
    CaseResult.runs / passes / flaky. early_stop ends a case's runs once
    its verdict is settled.

    jobs > 1 runs that many cases at once (results stay in case order; the
    first error in case order still ends the list, and cases not yet started
    are not started). tape replays or records answers (see cassette.py);
    tags keeps only the cases with one of those tags.
    """
    repeat = parse_repeat(repeat)
    min_pass_rate = parse_pass_rate(min_pass_rate)
    check_repeat_options(repeat, min_pass_rate)
    jobs = parse_jobs(jobs)
    prompt_text = suite_mod.load_prompt(root, prompt_name, version)
    cases = cases if cases is not None else suite_mod.load_cases(root,
                                                                 prompt_name)
    cases = select_cases(cases, tags)
    _warn_about_template(root, prompt_name, version, prompt_text, cases)

    def one(c):
        return _repeat_case(root, prompt_name, version, prompt_text, c,
                            model_kw or {}, use_cache, repeat,
                            min_pass_rate, tape=tape, early_stop=early_stop)

    results: list[CaseResult] = []
    if jobs == 1 or len(cases) < 2:
        for c in cases:
            r = one(c)
            results.append(r)
            if fail_fast and r.error:
                break
        return results, prompt_text

    stop = threading.Event()

    def guarded(c):
        if stop.is_set():
            return None          # an earlier case already failed to run
        r = one(c)
        if r.error and fail_fast:
            stop.set()
        return r

    with ThreadPoolExecutor(max_workers=min(jobs, len(cases)),
                            thread_name_prefix="prompt-assert-case") as pool:
        for r in pool.map(guarded, cases):
            if r is None:
                break
            results.append(r)
            if fail_fast and r.error:
                break
    return results, prompt_text
