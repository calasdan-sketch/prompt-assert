"""JUnit XML report for `run --junit PATH`.

CI systems read this format natively: GitLab shows it in the merge request,
Jenkins and Azure Pipelines in the build, and GitHub Actions through a test
report action. One <testsuite> per prompt version, one <testcase> per case;
a case that failed has a <failure> with the failing assertions and the
output. Stdlib only (xml.etree).

Everything that came from a model, a server or a cases file goes through
textsafe.clean_xml first: XML 1.0 cannot hold most control characters at
all (a raw \\x1b makes the whole file unreadable to the CI parser), and
ElementTree escapes &, < and > itself.
"""

from __future__ import annotations

import os
import tempfile
import xml.etree.ElementTree as ET

from .textsafe import clean_xml

#: How much of a case's output goes into its <failure> text.
MAX_OUTPUT_CHARS = 2000


def _failure_text(r) -> tuple[str, str]:
    """(message, body) of a failed case's <failure>."""
    if r.error:
        return "model error", r.error
    lines = []
    if r.runs > 1:
        lines.append(f"passed {r.passes}/{r.runs} runs")
    bad = [a for a in r.assertions if not a.passed]
    for a in bad:
        lines.append(f"{a.assertion} -> {a.detail}")
    output = r.output
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + f"... ({len(r.output)} chars)"
    lines.append(f"output: {output}")
    message = bad[0].detail if bad else (lines[0] if lines else "failed")
    return message, "\n".join(lines)


def build(suites) -> ET.Element:
    """<testsuites> for [(prompt name, version, [CaseResult ...]) ...]."""
    total = failures = 0
    seconds = 0.0
    root = ET.Element("testsuites", name="prompt-assert")
    for name, version, results in suites:
        suite_name = clean_xml(f"{name}.{version}")
        n_fail = sum(1 for r in results if not r.passed)
        t = sum(r.seconds for r in results)
        ts = ET.SubElement(root, "testsuite", name=suite_name,
                           tests=str(len(results)), failures=str(n_fail),
                           errors="0", skipped="0", time=f"{t:.3f}")
        for r in results:
            tc = ET.SubElement(ts, "testcase", classname=suite_name,
                               name=clean_xml(r.name),
                               time=f"{r.seconds:.3f}")
            if not r.passed:
                message, body = _failure_text(r)
                failure = ET.SubElement(tc, "failure",
                                        message=clean_xml(message)[:500],
                                        type="AssertionError")
                failure.text = clean_xml(body)
            elif r.flaky:
                out = ET.SubElement(tc, "system-out")
                out.text = clean_xml(f"flaky: passed {r.passes}/{r.runs} "
                                     f"runs")
        total += len(results)
        failures += n_fail
        seconds += t
    root.set("tests", str(total))
    root.set("failures", str(failures))
    root.set("errors", "0")
    root.set("time", f"{seconds:.3f}")
    return root


def _umask() -> int:
    mask = os.umask(0o022)
    os.umask(mask)
    return mask


def write(path: str, suites) -> None:
    """Write the report to `path`, all or nothing: it is written beside the
    target and moved into place, so a reader never sees half a file and a
    failed write leaves the old report as it was. OSError when it cannot be
    written."""
    tree = ET.ElementTree(build(suites))
    ET.indent(tree)
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".junit-", suffix=".xml.tmp",
                               dir=folder)
    try:
        with os.fdopen(fd, "wb") as f:
            tree.write(f, encoding="utf-8", xml_declaration=True)
        # mkstemp makes the file private (0600); a report is no secret and
        # CI steps running as another user must read it: the usual mode
        os.chmod(tmp, 0o666 & ~_umask())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
