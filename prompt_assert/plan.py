"""Plan a run without running it: how many model calls it would make, how
many are already answered (cassette or cache), a rough input-token count and
- only if you supply the prices - a cost. Nothing here contacts a model.

The same plan guards `run` and `diff`: --max-calls, --max-input-tokens and
--max-cost stop a run with exit 2 BEFORE the first request when the plan is
over budget, so a prompt edit that suddenly needs 4,000 fresh calls cannot
quietly spend them in CI.

Honest limits: tokens are estimated at one per 4 characters (the same rough
rule as max_tokens), so treat them as +-30%; output length is unknown until
the model answers, so a cost needs --assume-output-tokens from you; prices
are yours to give (they change, and none is built in); judge assertions
add calls this plan cannot count exactly (each runs only when its case's
reply is not already judged), so they are reported as an upper bound and
left out of tokens and cost. With --early-stop a run can make fewer calls
than planned, never more.
"""

from __future__ import annotations

import math

from . import asserts as asserts_mod
from . import model as model_mod
from . import runner as runner_mod
from . import suite as suite_mod


def parse_positive(value) -> float:
    """A finite number above 0 (a limit or a price)."""
    try:
        number = float(str(value).strip())
    except ValueError:
        number = float("nan")
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"must be a number above 0, got {value!r}")
    return number


def parse_count(value) -> int:
    """A whole number of 1 or more."""
    text = str(value).strip()
    if not text.isascii() or not text.isdigit() or int(text) < 1:
        raise ValueError(f"must be a whole number from 1, got {value!r}")
    return int(text)


def compute(root, targets, cases_by_name, model_kw, use_cache, repeat,
            tags=None, tapes=None) -> dict:
    """The plan for running `targets` ([(name, version)]). `tapes` maps a
    prompt name to its Tape when recording or replaying."""
    model_name, base_url = model_mod.endpoint(model_kw.get("base_url"),
                                              model_kw.get("model"))
    total = {"suites": 0, "cases": 0, "runs": 0, "live_calls": 0,
             "answered": 0, "missing": 0, "input_tokens": 0,
             "judge_calls_max": 0}
    suites = []
    for name, version in targets:
        template = suite_mod.load_prompt(root, name, version)
        cases = runner_mod.select_cases(cases_by_name[name], tags)
        tape = (tapes or {}).get(name)
        row = {"prompt": name, "version": version, "cases": len(cases),
               "runs": 0, "live_calls": 0, "answered": 0, "missing": 0,
               "input_tokens": 0, "judge_calls_max": 0}
        for case in cases:
            rendered = suite_mod.render(template, case["input"],
                                        case.get("vars"))
            judges = sum(1 for a in case["assert"]
                         if isinstance(a, dict) and "judge" in a)
            for sample in range(repeat):
                row["runs"] += 1
                if tape is not None and tape.mode == "replay":
                    if tape.get(rendered, sample) is None:
                        row["missing"] += 1
                    else:
                        row["answered"] += 1
                    continue
                live = True
                if tape is None and use_cache:
                    key = suite_mod.response_key(rendered, model_name,
                                                 base_url)
                    if sample:
                        key = runner_mod.sample_key(key, sample)
                    hit = runner_mod._cache_read(root, key)
                    live = not (hit is not None
                                and isinstance(hit.get("output"), str))
                if live:
                    row["live_calls"] += 1
                    row["input_tokens"] += asserts_mod.estimate_tokens(
                        rendered)
                    row["judge_calls_max"] += judges
                else:
                    row["answered"] += 1
        for k in ("runs", "live_calls", "answered", "missing",
                  "input_tokens", "judge_calls_max"):
            total[k] += row[k]
        total["cases"] += row["cases"]
        total["suites"] += 1
        suites.append(row)
    return {"model": model_name, "totals": total, "suites": suites}


def with_cost(plan: dict, price_in, price_out, assume_output_tokens):
    """Add `cost` (USD) to the plan's totals when all three inputs are
    given; otherwise leave it out. Prices are per million tokens."""
    if price_in is None or price_out is None or assume_output_tokens is None:
        return plan
    t = plan["totals"]
    t["cost_usd"] = (t["input_tokens"] / 1e6 * price_in
                     + t["live_calls"] * assume_output_tokens / 1e6
                     * price_out)
    return plan


def violations(plan: dict, max_calls=None, max_input_tokens=None,
               max_cost=None) -> list[str]:
    """What is over budget, as sentences (empty: within budget)."""
    t = plan["totals"]
    out = []
    if max_calls is not None and t["live_calls"] > max_calls:
        out.append(f"the run needs {t['live_calls']} live model call(s); "
                   f"--max-calls allows {max_calls}")
    if (max_input_tokens is not None
            and t["input_tokens"] > max_input_tokens):
        out.append(f"the run sends about {t['input_tokens']} input tokens; "
                   f"--max-input-tokens allows {max_input_tokens}")
    if max_cost is not None:
        if "cost_usd" not in t:
            out.append("--max-cost needs --price-in, --price-out and "
                       "--assume-output-tokens, so the cost can be "
                       "estimated; none is built in")
        elif t["cost_usd"] > max_cost:
            out.append(f"the estimated cost is {t['cost_usd']:.4f} (the "
                       f"currency of your prices); --max-cost allows "
                       f"{max_cost:g}")
    return out


def render(plan: dict) -> str:
    """The plan as a table for humans."""
    rows = plan["suites"]
    w = max([len(f"{r['prompt']}.{r['version']}") for r in rows] + [5])
    header = (f"{'suite':<{w}}  cases  runs  live  answered  missing  "
              "~input tokens")
    lines = [header]
    for r in rows:
        lines.append(f"{r['prompt'] + '.' + r['version']:<{w}}  "
                     f"{r['cases']:>5}  {r['runs']:>4}  "
                     f"{r['live_calls']:>4}  {r['answered']:>8}  "
                     f"{r['missing']:>7}  {r['input_tokens']:>13}")
    t = plan["totals"]
    lines.append("")
    lines.append(f"{t['live_calls']} live model call(s), {t['answered']} "
                 f"answered from a cassette or the cache"
                 + (f", {t['missing']} with no recording" if t["missing"]
                    else "")
                 + f"; about {t['input_tokens']} input tokens (estimate, "
                 "one per 4 characters)")
    if t["judge_calls_max"]:
        lines.append(f"plus up to {t['judge_calls_max']} judge call(s) "
                     "(not in the tokens or cost above)")
    if "cost_usd" in t:
        lines.append(f"estimated cost {t['cost_usd']:.4f} in the currency "
                     "of the prices you gave (input tokens + assumed "
                     "output, judge calls excluded)")
    return "\n".join(lines)

