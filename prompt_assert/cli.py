"""prompt-assert: pytest for prompts, local-first.

Prompts are versioned files; golden cases live in YAML. Run them in CI,
diff versions A/B, and catch prompt drift before your users do.

  python3 -m prompt_assert init
  python3 -m prompt_assert --demo run --prompt sentiment --version v1
  python3 -m prompt_assert run --all            # every prompt x every version
  python3 -m prompt_assert diff v1 v2 --prompt sentiment
  python3 -m prompt_assert list
  python3 -m prompt_assert run --all --record   # once; commit prompts/*.cassette.json
  python3 -m prompt_assert run --all --replay   # CI: no model, no cost
  python3 -m prompt_assert plan --all           # calls a run would make
  python3 -m prompt_assert corpus injection     # a starter attack/leak suite

(--demo trials the suite against a built-in fake model; after
`pip install .` the `prompt-assert` command is on your PATH.)

Exit status: 0 every case passed (or the command is done); 1 a case failed;
2 usage or configuration error, nothing was judged: a prompt or cases file
that is missing or unusable (no cases, an assertion that does not exist ...),
a file that cannot be read or written (`init` in a read-only folder), an API
key that cannot be sent, a model that cannot be used. `diff` only reports: it
exits 0 whenever the comparison was made, whether or not verdicts flipped (a
flip can be a fix as well as a regression; gate builds with `run`), unless
--fail-on-regression is given: then a pass -> FAIL flip is exit 1. A response
cache that cannot be written is no error: it is skipped, with one warning.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import json
import os
import sys
import tempfile

from . import cassette as cassette_mod
from . import changed as changed_mod
from . import corpus as corpus_mod
from . import demo as demo_mod
from . import junit as junit_mod
from . import model as model_mod
from . import plan as plan_mod
from . import report as report_mod
from . import runner as runner_mod
from . import scan as scan_mod
from . import suite as suite_mod
from .textsafe import clean

from . import __version__ as VERSION  # one place: prompt_assert/__init__.py


def _harden_streams() -> None:
    """Never traceback on a console that cannot encode the report
    (Windows cp1252 pipes, LANG=C): the ✗ and ← marks would otherwise
    raise UnicodeEncodeError mid-report on a failing case."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        # Both handlers raise UnicodeEncodeError on a character the code
        # page lacks: "strict" (the default) and "surrogateescape" (what a
        # piped stdout reports on Windows 11 / Python 3.11, with cp1252).
        if reconfigure is None or getattr(stream, "errors", None) not in (
                "strict", "surrogateescape"):
            continue
        try:
            reconfigure(errors="replace")
        except (OSError, ValueError):
            pass


# Windows does not report a pipe nobody reads as BrokenPipeError: print() of
# a small output works there (it only fills the buffer) and the flush then
# fails with a plain OSError(EINVAL) - at interpreter exit, as "Exception
# ignored", with exit status 120 - or, under `python -u`, out of print().
# A module-level flag, so tests can switch the rule on (and off) anywhere.
_IS_WINDOWS = os.name == "nt"


def _is_closed_pipe(e: BaseException) -> bool:
    """True when `e` means nobody is reading this pipe any more: a
    BrokenPipeError or any OSError with errno EPIPE, and, on Windows only,
    plain EINVAL. Anywhere else EINVAL is a real error and is not
    swallowed."""
    if not isinstance(e, OSError):
        return False
    return (isinstance(e, BrokenPipeError) or e.errno == errno.EPIPE
            or (_IS_WINDOWS and e.errno == errno.EINVAL))


def _discard_output(stream) -> None:
    """Point `stream`'s file descriptor at the null device, so what is
    still buffered (and the interpreter's own flush at exit) goes nowhere
    instead of failing again. A stream with no descriptor needs nothing."""
    try:
        with open(os.devnull, "wb") as null:
            os.dup2(null.fileno(), stream.fileno())
    except (OSError, ValueError, AttributeError):
        pass


class _PipeSafe:
    """Stand-in for sys.stdout / sys.stderr while main() runs.

    The reader of a pipe can quit at any moment (`prompt-assert run --all
    | head -1`). Left alone, Python raises BrokenPipeError out of print()
    or, when the output was still buffered, prints "Exception ignored" at
    exit and replaces the exit code with 120 (see _IS_WINDOWS for how
    Windows words that failure). Here the first failed write or flush
    points the stream at the null device and the command carries on into
    it: a closed pipe acts like `> /dev/null`, the output stops and the
    exit code is whatever the command computed.

    That is the choice against the usual `except BrokenPipeError:
    sys.exit(1)` recipe: for `run` the exit code IS the verdict (0 pass,
    1 a case failed, 2 an error), and a shortened pipe must never turn a
    regression into a pass, or into some other number.
    """

    def __init__(self, stream):
        self._stream = stream
        self._dead = False

    def _call(self, name, *args):
        if self._dead:
            return
        try:
            getattr(self._stream, name)(*args)
        except OSError as e:
            if not _is_closed_pipe(e):
                raise
            self._dead = True
            _discard_output(self._stream)

    def write(self, text):
        # Every character that would act on the terminal instead of
        # printing (ANSI escapes, \r, bidi overrides, zero-width ...) is
        # shown as an escape: a model's reply or a server's error text must
        # not be able to rewrite the report (textsafe).
        self._call("write", clean(text) if isinstance(text, str) else text)
        return len(text)

    def flush(self):
        self._call("flush")

    def __getattr__(self, name):  # encoding, isatty(), reconfigure() ...
        return getattr(self._stream, name)


@contextlib.contextmanager
def _closed_pipe_tolerant():
    """Run the body with stdout and stderr wrapped in _PipeSafe."""
    real = (sys.stdout, sys.stderr)  # either is None under pythonw
    safe = tuple(_PipeSafe(s) if s is not None else None for s in real)
    sys.stdout, sys.stderr = safe
    try:
        yield
    finally:
        try:
            # Flush now, with the command done, while a closed pipe is
            # still handled. A small output only ever sits in the buffer,
            # so nothing has failed yet; left to the interpreter's flush
            # at exit, the error would be "Exception ignored" and the exit
            # code 120 instead of the command's own.
            for s in safe:
                if s is not None:
                    s.flush()
        finally:
            sys.stdout, sys.stderr = real


def _model_kw(args) -> dict:
    kw: dict = {}
    for name in ("model", "base_url", "api_key", "timeout",
                 "max_reply_bytes"):
        val = getattr(args, name, None)
        if val is not None:
            kw[name] = val
    return kw


INIT_PROMPT_V1 = """Classify the sentiment of the following review.
Reply with exactly one word: positive, negative, or neutral.

Review: {{input}}
"""

INIT_PROMPT_V2 = """You are a sentiment classifier. Read the review and reply with
exactly one word: positive, negative, or neutral. No other text.

Review: {{input}}
"""

INIT_CASES = """cases:
  - name: angry review
    input: "This broke after two days. Terrible quality."
    assert:
      - contains: negative
  - name: happy review
    input: "Absolutely love it, works perfectly!"
    assert:
      - contains: positive
  - name: neutral review
    input: "It's fine, nothing special."
    assert:
      - regex: (?i)\\bpositive\\b|\\bnegative\\b|\\bneutral\\b
      - max_tokens: 10
"""


def scaffold_example(root: str) -> list[str]:
    """Write the example prompts/ scaffold. Returns files written."""
    d = os.path.join(root, "prompts")
    os.makedirs(d, exist_ok=True)
    written = []
    for fn, content in [("sentiment.v1.txt", INIT_PROMPT_V1),
                        ("sentiment.v2.txt", INIT_PROMPT_V2),
                        ("sentiment.cases.yaml", INIT_CASES)]:
        p = os.path.join(d, fn)
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            written.append(fn)
    return written


def cmd_init(args) -> int:
    try:
        written = scaffold_example(args.root)
        if written:
            print(f"scaffolded prompts/: {', '.join(written)}")
        else:
            print("prompts/ already scaffolded")
        if runner_mod.ensure_cache_ignored(args.root):
            print(f"wrote {runner_mod.CACHE_ROOT}/.gitignore "
                  "(the response cache is never committed)")
    except OSError as e:
        # asked to write files and unable to: a real failure, not a traceback
        print(f"error: init could not write its files: "
              f"{runner_mod.error_text(e)}", file=sys.stderr)
        return 2
    print("Point PROMPT_ASSERT_BASE_URL at your model "
          "(default: Ollama at http://localhost:11434/v1).")
    return 0


def _demo_root(args) -> str:
    """Root for a --demo run. Uses the real prompts/ when the requested
    prompt+version exists there (for `run --all`: when any prompt does);
    otherwise scaffolds the example into a temp dir (removed when the
    command ends) so the trial works with zero setup."""
    prompts = suite_mod.list_prompts(args.root)
    if args.cmd in ("run", "plan") and getattr(args, "all", False) \
            and prompts:
        return args.root
    have = prompts.get(getattr(args, "prompt", None) or "", [])
    if args.cmd in ("run", "plan") and not getattr(args, "all", False) \
            and args.version in have:
        return args.root
    if args.cmd == "diff" and args.v1 in have and args.v2 in have:
        return args.root
    stack = getattr(args, "cleanup", None) or contextlib.ExitStack()
    tmp = stack.enter_context(tempfile.TemporaryDirectory(
        prefix="prompt-assert-demo-", ignore_cleanup_errors=True))
    scaffold_example(tmp)
    print(f"(demo: scaffolded example prompts in {tmp}; removed afterwards)",
          file=sys.stderr)
    return tmp


def cmd_list(args) -> int:
    as_json = getattr(args, "format", "human") == "json"
    prompts = suite_mod.list_prompts(args.root)
    if not prompts:
        if as_json:
            print(json.dumps({"prompts": []}))
        else:
            print("no prompts found — run `prompt-assert init`")
        return 0
    problems = []
    listed = []
    for name, versions in prompts.items():
        count = 0
        try:
            count = len(suite_mod.load_cases(args.root, name))
            shown = f"{count} cases"
        except FileNotFoundError:
            shown = "0 cases"
        except ValueError as e:
            shown = "invalid cases file"
            problems.append(str(e))
        except OSError as e:
            shown = "unreadable cases file"
            problems.append(runner_mod.error_text(e))
        listed.append({"name": name, "versions": versions, "cases": count,
                       "status": "ok" if shown.endswith("cases")
                       else shown})
        if not as_json:
            print(f"{name}: versions {', '.join(versions)} ({shown})")
    if as_json:
        print(json.dumps({"prompts": listed}, indent=2))
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    return 2 if problems else 0


def _suite_dict(name: str, version: str,
                results: list[runner_mod.CaseResult]) -> dict:
    return {
        "prompt": name, "version": version,
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "cases": [r.as_dict() for r in results],
    }


def _brief(assertion, limit: int = 100) -> str:
    """An assertion for one line of the report: a long schema is cut."""
    text = str(assertion)
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _print_run(name: str, version: str,
               results: list[runner_mod.CaseResult]) -> int:
    """Human report for one prompt version: pytest-style dots, then the
    failing cases. Returns the exit code (1 when a case failed)."""
    line = "".join("." if r.passed else "F" for r in results)
    print(f"{name}.{version}: {line}")
    failed = [r for r in results if not r.passed]
    for r in failed:
        runs = (f" (passed {r.passes}/{r.runs} runs"
                + (", stopped early" if r.stopped_early else "") + ")"
                if (r.runs > 1 or r.stopped_early) and not r.error else "")
        print(f"\n  FAIL {r.name}{runs}")
        if r.error:
            print(f"    error: {r.error}")
            continue
        for a in r.assertions:
            if not a.passed:
                print(f"    ✗ {_brief(a.assertion)} -> {a.detail}")
        print(f"    output: {r.output[:300]!r}")
    flaky = [r for r in results if r.passed and r.flaky]
    if flaky:
        print()
    for r in flaky:
        print(f"  flaky: {r.name} passed {r.passes}/{r.runs} runs")
    n_pass = len(results) - len(failed)
    cached = sum(1 for r in results if r.cached)
    runs = max((r.runs for r in results), default=1)
    print(f"\n{n_pass}/{len(results)} passed"
          + (f" ({runs} runs each)" if runs > 1 else "")
          + (f" ({cached} cached)" if cached else ""))
    return 0 if not failed else 1


def _use_cache(args) -> bool:
    # --demo never reads or writes the cache: fake-model answers must not
    # be able to make a later real run pass.
    return not (args.no_cache or args.demo)


def _first_error(*result_lists) -> str:
    """The first model error of a run (unreachable, HTTP 401/404, timeout
    ...), or "" when every case got an answer. The runner stops at that
    case, so the message also says how many cases were left unrun."""
    for results in result_lists:
        for i, r in enumerate(results):
            if r.error and not r.cached:
                return (f"{r.error} (stopped at case {r.name!r}; "
                        f"no further case was run)")
    return ""


def _load_suites(root: str, names) -> dict | None:
    """name -> cases for each prompt in `names`, or None after printing every
    problem found. All of it is read before the first model call: a suite
    that cannot be used is reported at once, not after the suites before it
    were run (and paid for)."""
    loaded: dict[str, list] = {}
    usable = True
    for name in dict.fromkeys(names):
        try:
            loaded[name] = suite_mod.load_cases(root, name)
        except (OSError, ValueError) as e:
            print(f"error: {runner_mod.error_text(e)}", file=sys.stderr)
            usable = False
    return loaded if usable else None


def _run_or_error(root, name, version, args, cases=None, tapes=None):
    """run_suite with the user-facing error contract: returns
    (results, None) or (None, exit code) after printing the error."""
    try:
        results, _ = runner_mod.run_suite(
            root, name, version, cases=cases, model_kw=_model_kw(args),
            use_cache=_use_cache(args), repeat=args.repeat,
            min_pass_rate=args.min_pass_rate, jobs=args.jobs,
            early_stop=args.early_stop, tags=args.tag or None,
            tape=(tapes or {}).get(name))
    except (OSError, ValueError) as e:
        print(f"error: {runner_mod.error_text(e)}", file=sys.stderr)
        return None, 2
    err = _first_error(results)
    if err:
        # model down / rejected the call - report the real reason, don't
        # pretend with an all-FAIL run
        print(f"error: {err}", file=sys.stderr)
        return None, 2
    return results, None


def _check_run_options(args) -> bool:
    """True after printing the error when the cassette, repeat and budget
    flags contradict each other."""
    problems = []
    if args.record and args.replay:
        problems.append("--record and --replay cannot be used together")
    if (args.record or args.replay) and args.demo:
        problems.append("--record / --replay need a real prompts folder and "
                        "are not available with --demo (the fake model has "
                        "nothing worth recording)")
    if args.record and args.early_stop:
        problems.append("--record with --early-stop would record only some "
                        "of the runs; record without it")
    if args.early_stop and args.repeat == 1:
        problems.append("--early-stop needs --repeat N with N above 1")
    if args.max_cost is not None and (
            args.price_in is None or args.price_out is None
            or args.assume_output_tokens is None):
        problems.append("--max-cost needs --price-in, --price-out and "
                        "--assume-output-tokens (per million tokens; none "
                        "is built in)")
    if (getattr(args, "github_annotations", False)
            and getattr(args, "format", "human") == "json"):
        problems.append("--github-annotations prints workflow commands, "
                        "which would break --format json; use one or the "
                        "other")
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    return bool(problems)


def _open_tapes(args, root, names):
    """({name: Tape} or None when neither --record nor --replay, exit code)."""
    if not (args.record or args.replay):
        return None, 0
    mode = "record" if args.record else "replay"
    tapes = {}
    for name in dict.fromkeys(names):
        path = cassette_mod.cassette_path(root, name)
        try:
            tapes[name] = cassette_mod.Tape(path, mode)
        except (cassette_mod.CassetteError, OSError) as e:
            print(f"error: {runner_mod.error_text(e)}", file=sys.stderr)
            return None, 2
    return tapes, 0


def _within_budget(args, root, targets, cases, tapes) -> bool:
    """False after printing why when the run's plan is over a budget flag."""
    if (args.max_calls is None and args.max_input_tokens is None
            and args.max_cost is None):
        return True
    try:
        plan = plan_mod.with_cost(
            plan_mod.compute(root, targets, cases, _model_kw(args),
                             _use_cache(args), args.repeat,
                             tags=args.tag or None, tapes=tapes),
            args.price_in, args.price_out, args.assume_output_tokens)
    except (OSError, ValueError) as e:
        print(f"error: {runner_mod.error_text(e)}", file=sys.stderr)
        return False
    over = plan_mod.violations(plan, args.max_calls, args.max_input_tokens,
                               args.max_cost)
    for problem in over:
        print(f"error: over budget: {problem}; nothing was run",
              file=sys.stderr)
    return not over


def _finish_tapes(args, tapes, full_run: bool) -> int:
    """After a successful run: write the cassettes (record) or note
    recordings nothing used (replay, full runs only). Exit code."""
    if not tapes:
        return 0
    for name, tape in tapes.items():
        if tape.mode == "record":
            leaks = sorted({k for key, e in tape.responses.items()
                            if key in tape.used
                            for k in scan_mod.find_secrets(e["output"])})
            if leaks:
                print(f"warning: {tape.path}: a recorded reply looks like it "
                      f"contains {', '.join(leaks)}; read the file before "
                      "you commit it", file=sys.stderr)
            personal = sorted({k for key, e in tape.responses.items()
                               if key in tape.used
                               for k in scan_mod.find_pii(
                                   e["output"], scan_mod.PII_DEFAULT)})
            if personal:
                print(f"warning: {tape.path}: a recorded reply looks like it "
                      f"contains personal data ({', '.join(personal)}); "
                      "a cassette is committed - read the file before you "
                      "commit it", file=sys.stderr)
            try:
                tape.save(prune=full_run)
            except OSError as e:
                print(f"error: cannot write the cassette: "
                      f"{runner_mod.error_text(e)}", file=sys.stderr)
                return 2
            made = sum(1 for k in tape.responses if k in tape.used)
            drift = ""
            if tape.new + tape.changed + tape.same:
                drift = (f" ({tape.new} new, {tape.changed} changed, "
                         f"{tape.same} unchanged since the last recording)")
            print(f"recorded {made} answer(s) to {tape.path}{drift}",
                  file=sys.stderr)
            if tape.changed:
                print(f"note: {tape.changed} answer(s) differ from the "
                      "last recording - the model said something new; "
                      "`git diff` shows which", file=sys.stderr)
        elif full_run and tape.stale():
            print(f"note: {tape.path} holds {tape.stale()} recording(s) "
                  "nothing used; `run --all --record` drops them",
                  file=sys.stderr)
    return 0


def _select_targets(args, root, targets, cases):
    """(targets, rc): drop suites with no case under --tag; when a --tag
    leaves nothing at all, that is a usage error (a run of nothing must not
    be green)."""
    if not args.tag:
        return targets, 0
    kept = [(n, v) for n, v in targets
            if runner_mod.select_cases(cases[n], args.tag)]
    if not kept:
        print(f"error: no case has the tag {', '.join(args.tag)} - nothing "
              "to run", file=sys.stderr)
        return None, 2
    return kept, 0


def _cases_path(root: str, name: str) -> str | None:
    """The cases file as GitHub wants it in an annotation: relative to the
    working directory, forward slashes; None when it lies outside it."""
    try:
        path = os.path.relpath(os.path.join(root, "prompts",
                                            f"{name}.cases.yaml"))
    except ValueError:          # another drive on Windows
        return None
    return None if path.startswith("..") else path.replace(os.sep, "/")


def _write_reports(args, root, suites) -> int:
    """The JUnit, Markdown and annotation outputs asked for. 0, or 2 when a
    file cannot be written: a CI job must not go green without the file it
    was told to expect."""
    for what, path, write in (
            ("JUnit", args.junit, lambda: junit_mod.write(args.junit,
                                                          suites)),
            ("Markdown", args.markdown, lambda: report_mod.write_atomic(
                args.markdown, report_mod.markdown(suites)))):
        if not path:
            continue
        try:
            write()
        except OSError as e:
            print(f"error: cannot write the {what} report: "
                  f"{runner_mod.error_text(e)}", file=sys.stderr)
            return 2
    if args.github_annotations:
        for line in report_mod.annotations(
                suites, lambda n: _cases_path(root, n)):
            print(line)
    return 0


def cmd_run(args) -> int:
    if not args.all and not (args.prompt and args.version):
        print("error: run needs --prompt NAME --version vN, or --all",
              file=sys.stderr)
        return 2
    root = _demo_root(args) if args.demo else args.root
    if args.all:
        targets = [(name, v) for name, versions in
                   suite_mod.list_prompts(root).items() for v in versions]
        if not targets:
            print(f"error: no prompts found under {root} — run "
                  f"`prompt-assert init`", file=sys.stderr)
            return 2
    else:
        targets = [(args.prompt, args.version)]
    if _bad_repeat_options(args) or _check_run_options(args):
        return 2
    if args.changed_since:
        if not args.all:
            print("error: --changed-since goes with --all", file=sys.stderr)
            return 2
        try:
            files = changed_mod.changed_files(root, args.changed_since)
        except changed_mod.ChangedError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        targets = changed_mod.select(targets, files)
        if not targets:
            print(f"nothing to run: no prompt, cases file or cassette "
                  f"changed since {args.changed_since}")
            return _write_reports(args, root, [])
    cases = _load_suites(root, [name for name, _ in targets])
    if cases is None:
        return 2
    targets, rc = _select_targets(args, root, targets, cases)
    if targets is None:
        return rc
    tapes, rc = _open_tapes(args, root, [n for n, _ in targets])
    if rc:
        return rc
    if not _within_budget(args, root, targets, cases, tapes):
        return 2
    suites = []
    for name, version in targets:
        results, rc = _run_or_error(root, name, version, args, cases[name],
                                    tapes)
        if results is None:
            return rc
        suites.append((name, version, results))
    full = args.all and not args.tag and not args.changed_since
    rc = _finish_tapes(args, tapes, full)
    if rc:
        return rc
    rc = _write_reports(args, root, suites)
    if rc:
        return rc
    if args.format == "json":
        if not args.all:
            name, version, results = suites[0]
            print(json.dumps(_suite_dict(name, version, results), indent=2))
            return 0 if all(r.passed for r in results) else 1
        print(json.dumps({
            "suites": [_suite_dict(n, v, r) for n, v, r in suites],
            "passed": sum(1 for _, _, r in suites for x in r if x.passed),
            "failed": sum(1 for _, _, r in suites for x in r if not x.passed),
        }, indent=2))
        return 0 if all(x.passed for _, _, r in suites for x in r) else 1
    if not args.all:
        return _print_run(*suites[0])
    rc = 0
    for name, version, results in suites:
        rc = max(rc, _print_run(name, version, results))
        print()
    # summary table: one row per prompt version, failures marked
    w = max(len(f"{n}.{v}") for n, v, _ in suites)
    print(f"{'suite':<{w}}  passed")
    for name, version, results in suites:
        n_pass = sum(1 for r in results if r.passed)
        mark = "" if n_pass == len(results) else "  FAIL"
        print(f"{name}.{version:<{w - len(name) - 1}}  "
              f"{n_pass}/{len(results)}{mark}")
    total = sum(len(r) for _, _, r in suites)
    passed = sum(1 for _, _, r in suites for x in r if x.passed)
    bad = sum(1 for _, _, r in suites if any(not x.passed for x in r))
    print(f"\n{len(suites)} suite(s), {bad} with failures; "
          f"{passed}/{total} cases passed")
    return rc


def _bad_repeat_options(args) -> bool:
    """True after printing the error when the repeat flags contradict
    each other (see runner.check_repeat_options)."""
    try:
        runner_mod.check_repeat_options(args.repeat, args.min_pass_rate)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return True
    return False


def cmd_diff(args) -> int:
    if _bad_repeat_options(args) or _check_run_options(args):
        return 2
    root = _demo_root(args) if args.demo else args.root
    targets = [(args.prompt, args.v1), (args.prompt, args.v2)]
    cases = _load_suites(root, [args.prompt])
    if cases is None:
        return 2
    kept, rc = _select_targets(args, root, targets[:1], cases)
    if kept is None:
        return rc
    tapes, rc = _open_tapes(args, root, [args.prompt])
    if rc:
        return rc
    if not _within_budget(args, root, targets, cases, tapes):
        return 2
    r1, rc = _run_or_error(root, args.prompt, args.v1, args,
                           cases[args.prompt], tapes)
    if r1 is None:
        return rc
    r2, rc = _run_or_error(root, args.prompt, args.v2, args,
                           cases[args.prompt], tapes)
    if r2 is None:
        return rc
    rc = _finish_tapes(args, tapes, False)
    if rc:
        return rc
    rows = []
    for a, b in zip(r1, r2):
        rows.append({
            "name": a.name,
            "v1": "pass" if a.passed else "FAIL",
            "v2": "pass" if b.passed else "FAIL",
            "changed": a.passed != b.passed,
            # before/after output text on flips (truncated like run's)
            "v1_output": a.output[:300],
            "v2_output": b.output[:300],
        })
    if args.format == "json":
        print(json.dumps({"prompt": args.prompt, "v1": args.v1,
                          "v2": args.v2, "cases": rows}, indent=2))
        return _diff_status(args, rows)
    w = max([len(r["name"]) for r in rows] + [4])
    print(f"{'case':<{w}}  {args.v1:>6}  {args.v2:>6}")
    for r in rows:
        mark = "← CHANGED" if r["changed"] else " "
        print(f"{r['name']:<{w}}  {r['v1']:>6}  {r['v2']:>6}  {mark}")
    flipped = [r for r in rows if r["changed"]]
    for r in flipped:
        print(f"\nCHANGED {r['name']}:")
        print(f"  {args.v1} output: {r['v1_output']!r}")
        print(f"  {args.v2} output: {r['v2_output']!r}")
    print(f"\n{len(flipped)}/{len(rows)} case(s) changed verdict "
          f"{args.v1} -> {args.v2}")
    status = _diff_status(args, rows)
    if status:
        broke = sum(1 for r in rows if r["v1"] == "pass" and r["v2"] == "FAIL")
        print(f"{broke} case(s) regressed (pass -> FAIL): exit 1 "
              f"(--fail-on-regression)", file=sys.stderr)
    return status


def cmd_plan(args) -> int:
    """What a run would cost, without running it or contacting a model."""
    if not args.all and not (args.prompt and args.version):
        print("error: plan needs --prompt NAME --version vN, or --all",
              file=sys.stderr)
        return 2
    if _bad_repeat_options(args) or _check_run_options(args):
        return 2
    root = _demo_root(args) if args.demo else args.root
    if args.all:
        targets = [(name, v) for name, versions in
                   suite_mod.list_prompts(root).items() for v in versions]
        if not targets:
            print(f"error: no prompts found under {root}", file=sys.stderr)
            return 2
        if args.changed_since:
            try:
                targets = changed_mod.select(
                    targets, changed_mod.changed_files(root,
                                                       args.changed_since))
            except changed_mod.ChangedError as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
    else:
        targets = [(args.prompt, args.version)]
    cases = _load_suites(root, [name for name, _ in targets])
    if cases is None:
        return 2
    targets, rc = _select_targets(args, root, targets, cases)
    if targets is None:
        return rc
    tapes, rc = _open_tapes(args, root, [n for n, _ in targets])
    if rc:
        return rc
    try:
        plan = plan_mod.with_cost(
            plan_mod.compute(root, targets, cases, _model_kw(args),
                             _use_cache(args), args.repeat,
                             tags=args.tag or None, tapes=tapes),
            args.price_in, args.price_out, args.assume_output_tokens)
    except (OSError, ValueError) as e:
        print(f"error: {runner_mod.error_text(e)}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(plan, indent=2))
    else:
        print(clean(plan_mod.render(plan)))
    over = plan_mod.violations(plan, args.max_calls, args.max_input_tokens,
                               args.max_cost)
    for problem in over:
        print(f"over budget: {problem}", file=sys.stderr)
    # a replay that would miss a recording is as unusable as one over budget
    missing = plan["totals"]["missing"]
    if missing:
        print(f"error: {missing} call(s) have no recording; "
              "`run --record` would add them", file=sys.stderr)
    return 2 if over or missing else 0


def cmd_corpus(args) -> int:
    try:
        text = corpus_mod.injection_cases(args.canary, args.allow_domain)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


def _diff_status(args, rows) -> int:
    """0, or 1 under --fail-on-regression when a case went pass -> FAIL (a
    FAIL -> pass flip is a fix and never fails it)."""
    if getattr(args, "fail_on_regression", False) and any(
            r["v1"] == "pass" and r["v2"] == "FAIL" for r in rows):
        return 1
    return 0


def _flag(parse):
    """An argparse `type` from one of model's parse_* functions: a value it
    refuses is a usage error that says why."""
    def convert(text):
        try:
            return parse(text)
        except ValueError as e:
            raise argparse.ArgumentTypeError(str(e))
    return convert


def _add_model_options(p: argparse.ArgumentParser, *, on_subcommand: bool) -> None:
    """--model/--base-url/--api-key/--timeout/--max-reply-bytes/--demo,
    accepted both before and after the subcommand. On the subcommand the
    defaults are SUPPRESS, so an unset option there keeps the value parsed
    from the top level instead of clobbering it with None/False."""
    d = argparse.SUPPRESS if on_subcommand else None
    p.add_argument("--model", default=d, help="Model name (env PROMPT_ASSERT_MODEL).")
    p.add_argument("--base-url", default=d,
                   help="OpenAI-compatible endpoint (env PROMPT_ASSERT_BASE_URL).")
    p.add_argument("--api-key", default=d,
                   help="API key (env PROMPT_ASSERT_API_KEY is safer: a key "
                        "on the command line is visible to other users "
                        "via `ps` and shell history).")
    p.add_argument("--timeout", type=_flag(model_mod.parse_seconds),
                   default=d, metavar="SECONDS",
                   help=f"Seconds for one model request, from connecting to "
                        f"the last byte of the reply: a deadline, not a "
                        f"limit per read (default "
                        f"{model_mod.DEFAULT_TIMEOUT:g}; env "
                        f"PROMPT_ASSERT_TIMEOUT). The first case the model "
                        f"cannot answer stops the run.")
    p.add_argument("--max-reply-bytes", type=_flag(model_mod.parse_byte_limit),
                   default=d, metavar="N",
                   help=f"Largest reply body accepted, in bytes (default "
                        f"{model_mod.DEFAULT_MAX_REPLY_BYTES}, 8 MiB; env "
                        f"PROMPT_ASSERT_MAX_REPLY_BYTES). A bigger reply is "
                        f"an error; it is never read into memory.")
    p.add_argument("--demo", action="store_true",
                   default=argparse.SUPPRESS if on_subcommand else False,
                   help="Trial mode: run against the built-in in-process "
                        "fake model instead of a real endpoint. "
                        "Scaffolds the example prompts if the requested "
                        "prompt is missing.")


def _add_repeat_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repeat", type=_flag(runner_mod.parse_repeat),
                   default=1, metavar="N",
                   help="Run each case N times (default 1). Runs after the "
                        "first are cached separately; --no-cache draws "
                        "fresh ones.")
    p.add_argument("--min-pass-rate", type=_flag(runner_mod.parse_pass_rate),
                   default=1.0, metavar="RATE",
                   help="With --repeat: the share of runs a case must pass, "
                        "e.g. 0.8 or 80%% (default 1: every run). Rounded "
                        "up: 0.8 of 5 runs is 4.")


def _add_run_options(p: argparse.ArgumentParser) -> None:
    """Options of every command that runs (or plans) cases."""
    p.add_argument("--jobs", type=_flag(runner_mod.parse_jobs), default=1,
                   metavar="N",
                   help=f"Run N cases at once (default 1, at most "
                        f"{runner_mod.MAX_JOBS}). Results keep case order; "
                        "mind your provider's rate limit.")
    p.add_argument("--early-stop", action="store_true",
                   help="With --repeat: stop a case's runs as soon as its "
                        "verdict is settled (same verdict, fewer calls; a "
                        "late failure of a case already settled goes "
                        "unseen).")
    p.add_argument("--tag", action="append", default=None, metavar="TAG",
                   help="Run only cases with this tag (repeat the flag for "
                        "several: any match runs).")
    p.add_argument("--record", action="store_true",
                   help="Call the model and write the answers to "
                        "prompts/<name>.cassette.json (commit it).")
    p.add_argument("--replay", action="store_true",
                   help="Answer every call from prompts/<name>.cassette.json "
                        "and contact no model; a call with no recording is "
                        "exit 2.")
    p.add_argument("--max-calls", type=_flag(plan_mod.parse_count),
                   default=None, metavar="N",
                   help="Exit 2 before the first request if the run needs "
                        "more than N live model calls.")
    p.add_argument("--max-input-tokens", type=_flag(plan_mod.parse_count),
                   default=None, metavar="N",
                   help="Exit 2 before the first request if the live calls "
                        "send more than about N tokens (1 per 4 characters).")
    p.add_argument("--max-cost", type=_flag(plan_mod.parse_positive),
                   default=None, metavar="AMOUNT",
                   help="Exit 2 before the first request if the estimated "
                        "cost is over AMOUNT; needs the three flags below.")
    p.add_argument("--price-in", type=_flag(plan_mod.parse_positive),
                   default=None, metavar="PER_MTOK",
                   help="Your price per million input tokens (none is "
                        "built in).")
    p.add_argument("--price-out", type=_flag(plan_mod.parse_positive),
                   default=None, metavar="PER_MTOK",
                   help="Your price per million output tokens.")
    p.add_argument("--assume-output-tokens", type=_flag(plan_mod.parse_count),
                   default=None, metavar="N",
                   help="Output tokens to assume per call, for the cost.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="prompt-assert", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    p.add_argument("--root", default=os.getcwd(),
                   help="The project folder: prompts/ and .prompt-assert/ "
                        "are looked for in it (default: the current "
                        "directory). Give it before the subcommand.")
    _add_model_options(p, on_subcommand=False)
    sub = p.add_subparsers(dest="cmd", metavar="{init,list,run,plan,corpus,diff}")

    sub.add_parser("init", help="Scaffold prompts/ with an example.")
    ls = sub.add_parser("list", help="List prompts, versions, case counts.")
    ls.add_argument("--format", choices=["human", "json"], default="human")

    r = sub.add_parser("run", help="Run golden cases against a version "
                                   "(or --all: every prompt x every version).")
    r.add_argument("--prompt")
    r.add_argument("--version")
    r.add_argument("--all", action="store_true",
                   help="Run every prompt against each of its versions and "
                        "print a summary table; exit 1 if any case fails.")
    r.add_argument("--no-cache", action="store_true")
    r.add_argument("--format", choices=["human", "json"], default="human")
    r.add_argument("--junit", metavar="PATH",
                   help="Also write a JUnit XML report to PATH (for CI test "
                        "views). A report that cannot be written is exit 2.")
    r.add_argument("--markdown", metavar="PATH",
                   help="Also write a Markdown summary to PATH (a PR "
                        "comment, or $GITHUB_STEP_SUMMARY).")
    r.add_argument("--github-annotations", action="store_true",
                   help="Print a GitHub Actions ::error line per failed "
                        "case (not with --format json).")
    r.add_argument("--changed-since", metavar="REF",
                   help="With --all: run only the suites whose prompt "
                        "version, cases file or cassette differs from the "
                        "git revision REF.")
    _add_repeat_options(r)
    _add_run_options(r)
    _add_model_options(r, on_subcommand=True)

    pl = sub.add_parser(
        "plan", help="Count the model calls (and estimate tokens and cost) "
                     "a run would need, without running it.",
        description="Reads the prompts, cases, cache and cassettes and "
                    "reports how many live model calls `run` would make. "
                    "Contacts no model. Exit 2 when over a budget flag or "
                    "when a replay would miss a recording.")
    pl.add_argument("--prompt")
    pl.add_argument("--version")
    pl.add_argument("--all", action="store_true")
    pl.add_argument("--no-cache", action="store_true")
    pl.add_argument("--format", choices=["human", "json"], default="human")
    pl.add_argument("--changed-since", metavar="REF")
    _add_repeat_options(pl)
    _add_run_options(pl)
    _add_model_options(pl, on_subcommand=True)

    c = sub.add_parser(
        "corpus", help="Print a starter prompt-injection case file.",
        description=corpus_mod.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    c.add_argument("name", choices=["injection"])
    c.add_argument("--canary", default=corpus_mod.DEFAULT_CANARY,
                   help="The harmless marker the attacks ask the model to "
                        "print (default %(default)s).")
    c.add_argument("--allow-domain", action="append", default=[],
                   metavar="DOMAIN",
                   help="A domain the reply may link to (default: no links).")

    d = sub.add_parser(
        "diff", help="A/B two versions on the same cases (exits 0 even when "
                     "verdicts flip, unless --fail-on-regression).",
        description="Run both versions on the same cases and show which cases "
                    "change verdict. Exit status: 0 whenever the comparison "
                    "was made, flipped verdicts included - a flip can be a "
                    "fix as well as a regression, so diff reports and does "
                    "not judge; gate a build with `run` or "
                    "--fail-on-regression (exit 1 on a pass -> FAIL flip). "
                    "2 when the "
                    "comparison could not be made (a missing or unusable "
                    "file, a model that cannot answer).")
    d.add_argument("v1")
    d.add_argument("v2")
    d.add_argument("--prompt", required=True)
    d.add_argument("--no-cache", action="store_true")
    d.add_argument("--format", choices=["human", "json"], default="human")
    d.add_argument("--fail-on-regression", action="store_true",
                   help="Exit 1 when a case passes on v1 and fails on v2 "
                        "(a FAIL -> pass flip never fails it).")
    _add_repeat_options(d)
    _add_run_options(d)
    _add_model_options(d, on_subcommand=True)
    return p


def main(argv=None) -> int:
    _harden_streams()
    with contextlib.ExitStack() as stack:
        # `... | head -1` must neither traceback nor change the exit code.
        stack.enter_context(_closed_pipe_tolerant())
        parser = build_parser()
        args = parser.parse_args(argv)
        if args.cmd is None:
            print("prompt-assert: no command given. New here? Start with:\n"
                  "  prompt-assert init                  "
                  "# scaffold prompts/ with an example\n"
                  "  prompt-assert --demo run --all      "
                  "# try it with a built-in fake model\n"
                  "  prompt-assert --help                # everything else",
                  file=sys.stderr)
            return 2
        # Resources a subcommand opens (the demo's scaffold dir) are
        # released here, however the command ends.
        args.cleanup = stack
        if args.demo and args.cmd in ("run", "diff"):
            # Zero-dependency trial: ALWAYS the in-process fake model.
            # A PROMPT_ASSERT_BASE_URL left in the environment (or a
            # --base-url) used to win silently and hit a real server.
            demo_server, url = demo_mod.start_demo_model()
            stack.callback(demo_server.server_close)   # runs after shutdown
            stack.callback(demo_server.shutdown)
            ignored = [f"--base-url {args.base_url}"] if args.base_url else []
            if model_mod.env_get("PROMPT_ASSERT_BASE_URL"):
                ignored.append("PROMPT_ASSERT_BASE_URL="
                               f"{model_mod.env_get('PROMPT_ASSERT_BASE_URL')}")
            if ignored:
                print("(demo: using the built-in fake model; ignoring "
                      f"{', '.join(ignored)})", file=sys.stderr)
            args.base_url = url
            # the fake model has no use for a key, and one left in the real
            # environment (even a broken one) is none of its business; nor
            # are the limits the environment sets
            args.api_key = "demo"
            if args.timeout is None:
                args.timeout = model_mod.DEFAULT_TIMEOUT
            if args.max_reply_bytes is None:
                args.max_reply_bytes = model_mod.DEFAULT_MAX_REPLY_BYTES
        elif args.cmd in ("run", "diff") and not args.replay:
            # a key that cannot be sent, a limit that is not a number: a
            # configuration error. Say so before the first request, whether
            # or not the cache could have answered
            try:
                model_mod.check_base_url(
                    model_mod.endpoint(args.base_url, args.model)[1])
                model_mod.check_key(model_mod.resolve_api_key(args.api_key))
                model_mod.resolve_timeout(args.timeout)
                model_mod.resolve_max_reply_bytes(args.max_reply_bytes)
            except model_mod.ModelError as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
        runner_mod.reset_cache_warnings()
        try:
            return {"init": cmd_init, "list": cmd_list, "run": cmd_run,
                    "diff": cmd_diff, "plan": cmd_plan,
                    "corpus": cmd_corpus}[args.cmd](args)
        except OSError as e:
            # whatever file trouble nothing above handled (an unreadable
            # prompts folder ...): one line and exit 2, never a traceback
            # and never the 1 that means "a case failed"
            print(f"error: {runner_mod.error_text(e)}", file=sys.stderr)
            return 2


if __name__ == "__main__":
    sys.exit(main())
