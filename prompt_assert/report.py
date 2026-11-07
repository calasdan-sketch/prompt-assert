"""Reports for pull requests: a Markdown summary (a PR comment, or
$GITHUB_STEP_SUMMARY) and GitHub Actions annotations. Everything that came
from a model is cleaned (textsafe) first, so a reply cannot forge a line of
the report or an annotation command."""

from __future__ import annotations

import os
import re
import tempfile

from . import fsutil
from .textsafe import clean

#: GitHub refuses a comment over 65536 characters; stay well under.
MAX_MARKDOWN = 60000
SNIPPET = 300


def _fence(text: str) -> str:
    """A code fence longer than any run of backticks in `text`."""
    longest = run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def _code(text: str) -> str:
    text = clean(text)
    fence = _fence(text)
    return f"{fence}\n{text}\n{fence}"


def _html(text: str) -> str:
    """Text for inside an HTML element (the <summary> line): GitHub renders
    <img> and <a> a model or a case name wrote, so nothing is left as markup."""
    out = (clean(text).replace("&", "&amp;").replace("<", "&lt;")
           .replace(">", "&gt;").replace("\n", " "))
    return out.replace("://", ":&#47;&#47;")


def _cell(text: str) -> str:
    """Text for a Markdown table cell or list item: no markup, no link (a
    reply could carry `[click](https://evil.example)` or an image that loads
    a tracking URL), no autolink, no row break."""
    out = _html(text)
    for ch in "\\`*_[]()|!~":
        out = out.replace(ch, "\\" + ch)
    out = re.sub(r"(?i)www\.", "www&#46;", out)
    return out.replace("@", "&#64;")


def markdown(suites, title: str = "prompt-assert") -> str:
    """`suites`: [(name, version, [CaseResult])]."""
    total = sum(len(r) for _, _, r in suites)
    passed = sum(1 for _, _, r in suites for x in r if x.passed)
    failed = total - passed
    flaky = sum(1 for _, _, r in suites for x in r if x.passed and x.flaky)
    lines = [f"### {title}: {'passed' if not failed else 'FAILED'}", "",
             f"{passed}/{total} cases passed"
             + (f", {flaky} flaky" if flaky else ""), "",
             "| suite | passed | failed |", "|---|---:|---:|"]
    for name, version, results in suites:
        ok = sum(1 for x in results if x.passed)
        lines.append(f"| {_cell(name + '.' + version)} | {ok} | "
                     f"{len(results) - ok} |")
    for name, version, results in suites:
        for r in results:
            label = _cell(f"{name}.{version} / {r.name}")
            if not r.passed:
                lines += ["", "<details><summary>FAIL " + _html(
                    f"{name}.{version} / {r.name}") + "</summary>", ""]
                if r.error:
                    lines.append(_code("error: " + r.error))
                for a in r.assertions:
                    if not a.passed:
                        lines.append("- " + _cell(f"{a.assertion} -> "
                                                   f"{a.detail}"))
                if r.output:
                    lines += ["", _code(r.output[:SNIPPET])]
                lines += ["", "</details>"]
            elif r.flaky:
                lines += ["", (f"flaky: {label} passed {r.passes}/{r.runs} "
                              "runs")]
    text = "\n".join(lines) + "\n"
    if len(text) > MAX_MARKDOWN:
        text = text[:MAX_MARKDOWN].rsplit("\n", 1)[0] + (
            "\n\n(report cut: too long for a comment)\n")
    return text


def _data(text: str) -> str:
    return (clean(text).replace("%", "%25").replace("\r", "%0D")
            .replace("\n", "%0A"))


def _prop(text: str) -> str:
    return _data(text).replace(":", "%3A").replace(",", "%2C")


def annotations(suites, path_of=None) -> list[str]:
    """One `::error` workflow command per failed case (and `::warning` per
    flaky one). `path_of(name)` gives the cases file to attach it to."""
    out = []
    for name, version, results in suites:
        for r in results:
            where = f"{name}.{version} / {r.name}"
            file = path_of(name) if path_of else None
            props = (f"file={_prop(file)}," if file else "")
            if not r.passed:
                bad = "; ".join(f"{a.assertion} -> {a.detail}"
                                for a in r.assertions if not a.passed)
                msg = f"{where}: " + (r.error or bad or "failed")
                out.append(f"::error {props}title=prompt-assert::"
                           f"{_data(msg[:500])}")
            elif r.flaky:
                out.append(f"::warning {props}title=prompt-assert flaky::"
                           f"{_data(f'{where} passed {r.passes}/{r.runs} runs')}")
    return out


def write_atomic(path: str, text: str) -> None:
    """Write `text` to `path` whole or not at all (OSError on failure)."""
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".report-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        mask = os.umask(0o022)
        os.umask(mask)
        os.chmod(tmp, 0o666 & ~mask)
        fsutil.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
