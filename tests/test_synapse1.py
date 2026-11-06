"""Synapse round 1 (0.3.0): output that cannot act on the terminal, the
new text assertions, --repeat with a pass-rate gate, the JUnit report and
`diff --fail-on-regression`.

Every test uses fake servers on 127.0.0.1 and temp directories, nothing else.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unicodedata
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar
from unittest import mock

from prompt_assert import asserts, junit, runner, suite, textsafe
from prompt_assert import cli as cli_mod

ESC = "\x1b"


@contextlib.contextmanager
def clean_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PROMPT_ASSERT_", "PROMPTREGRESS_"))}
    env.update(extra)
    with mock.patch.dict(os.environ, env, clear=True):
        yield


def run_cli(argv, **env):
    out, err = io.StringIO(), io.StringIO()
    with clean_env(**env), redirect_stdout(out), redirect_stderr(err):
        code = cli_mod.main(argv)
    return code, out.getvalue(), err.getvalue()


class _Script(BaseHTTPRequestHandler):
    """A model whose answers are taken in turn from `answers` (the last one
    repeats), or an HTTP error with `error_body` when `status` is set."""
    answers: ClassVar[list] = ["ok"]
    calls: ClassVar[list] = []
    status = 200
    error_body = ""

    def do_POST(self):
        cls = type(self)
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        cls.calls.append(1)
        if cls.status != 200:
            payload = cls.error_body.encode("utf-8")
            self.send_response(cls.status)
        else:
            i = min(len(cls.calls) - 1, len(cls.answers) - 1)
            payload = json.dumps({"choices": [{"message": {
                "role": "assistant", "content": cls.answers[i]}}]}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class Project(unittest.TestCase):
    def project(self, cases_yaml, versions=("v1",), prompt="Q: {{input}}\n"):
        root = tempfile.mkdtemp(prefix="pa-s1-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        d = os.path.join(root, "prompts")
        os.makedirs(d)
        for v in versions:
            with open(os.path.join(d, f"s.{v}.txt"), "w",
                      encoding="utf-8") as f:
                f.write(prompt if isinstance(prompt, str) else prompt[v])
        with open(os.path.join(d, "s.cases.yaml"), "w",
                  encoding="utf-8") as f:
            f.write(cases_yaml)
        return root

    def model(self, answers, status=200, error_body=""):
        handler = type("H", (_Script,), {"answers": list(answers),
                                         "calls": [], "status": status,
                                         "error_body": error_body})
        server = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}/v1", handler


ONE_CASE = """cases:
  - name: c
    input: hi
    assert:
      - contains: good
"""


# --- textsafe --------------------------------------------------------------

class TestTextsafe(unittest.TestCase):
    def test_every_control_format_and_separator_character_is_escaped(self):
        bad = [cp for cp in range(sys.maxunicode + 1)
               if unicodedata.category(chr(cp)) in ("Cc", "Cf", "Cs", "Zl",
                                                    "Zp")
               and cp not in (9, 10)]
        for cp in bad:
            shown = textsafe.clean(chr(cp))
            self.assertNotEqual(shown, chr(cp), hex(cp))
            self.assertTrue(shown.isascii() and shown.isprintable(),
                            (hex(cp), shown))

    def test_ordinary_text_is_untouched(self):
        for text in ("plain", "café ünïcödé", "日本語", "emoji 😀",
                     "✗ ← marks", "tab\there\nnew line", "a  b"):
            self.assertEqual(textsafe.clean(text), text)

    def test_escapes_are_readable(self):
        self.assertEqual(textsafe.clean("a\x1b[31mb\rc\u202ed\x00"),
                         "a\\x1b[31mb\\rc\\u202ed\\0")
        self.assertEqual(textsafe.clean("\U000e0041"), "\\U000e0041")

    def test_xml_cleaning_also_drops_xml_illegal_noncharacters(self):
        self.assertEqual(textsafe.clean_xml("￾￿"),
                         "\\ufffe\\uffff")

    def test_this_repo_has_no_hidden_characters_in_its_sources(self):
        # Trojan Source: a bidi override or zero-width character in code
        # shows one thing and runs another. Write them as escapes.
        top = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        files = [os.path.join(top, "README.md"),
                 os.path.join(top, "CHANGELOG.md")]
        for sub in ("prompt_assert", "tests"):
            files += [os.path.join(top, sub, f)
                      for f in os.listdir(os.path.join(top, sub))
                      if f.endswith(".py")]
        for path in files:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertEqual(textsafe.clean(text), text, path)

    def test_huge_input_is_fast_enough(self):
        text = ("x" * 1000 + ESC) * 2000      # ~2 MB
        self.assertNotIn(ESC, textsafe.clean(text))


class TestTerminalInjection(Project):
    """A reply, a judge's reasoning, a server's error, a case name: none of
    it may reach the terminal as a live control sequence."""

    def test_model_output_cannot_rewrite_the_report(self):
        evil = f"bad{ESC}[2K\r3/3 passed\u202e{ESC}]0;title\x07"
        url, _ = self.model([evil])
        root = self.project(ONE_CASE)
        code, out, err = run_cli(["--root", root, "run", "--prompt", "s",
                                  "--version", "v1", "--base-url", url])
        self.assertEqual(code, 1)
        for raw in (ESC, "\r", "\u202e", "\x07"):
            self.assertNotIn(raw, out + err)

    def test_server_error_text_is_escaped(self):
        body = json.dumps({"error": {"message": f"nope {ESC}[31mRED\u200b"}})
        url, _ = self.model([], status=400, error_body=body)
        root = self.project(ONE_CASE)
        code, out, err = run_cli(["--root", root, "run", "--prompt", "s",
                                  "--version", "v1", "--base-url", url])
        self.assertEqual(code, 2)
        self.assertNotIn(ESC, err)
        self.assertNotIn("\u200b", err)
        self.assertIn("\\x1b[31mRED\\u200b", err)

    def test_judge_reasoning_is_escaped(self):
        url, _ = self.model(["answer", f"FAIL {ESC}[1Ahidden"])
        root = self.project("""cases:
  - name: c
    input: hi
    assert:
      - judge: must be polite
""")
        code, out, err = run_cli(["--root", root, "run", "--prompt", "s",
                                  "--version", "v1", "--base-url", url,
                                  "--no-cache"])
        self.assertEqual(code, 1)
        self.assertNotIn(ESC, out)

    def test_case_name_is_escaped_in_run_and_diff(self):
        root = self.project('cases:\n  - name: "n\\e[31m\\u202e"\n'
                            '    input: hi\n    assert:\n'
                            '      - contains: good\n', versions=("v1", "v2"))
        url, _ = self.model(["no"])
        for argv in (["run", "--prompt", "s", "--version", "v1"],
                     ["diff", "v1", "v2", "--prompt", "s"]):
            code, out, err = run_cli(["--root", root, *argv,
                                      "--base-url", url])
            self.assertNotIn(ESC, out + err)
            self.assertNotIn("\u202e", out + err)

    def test_json_output_is_still_valid_json_with_the_text_intact(self):
        url, _ = self.model([f"x{ESC}y"])
        root = self.project(ONE_CASE)
        code, out, _ = run_cli(["--root", root, "run", "--prompt", "s",
                                "--version", "v1", "--base-url", url,
                                "--format", "json"])
        self.assertEqual(json.loads(out)["cases"][0]["output"], f"x{ESC}y")


# --- new assertions ----------------------------------------------------------

class TestNewAssertions(unittest.TestCase):
    def ev(self, assertion, output):
        return asserts.evaluate(assertion, output)[0]

    def test_icontains(self):
        self.assertTrue(self.ev({"icontains": "NEGATIVE"}, "it is negative"))
        self.assertTrue(self.ev({"icontains": "strasse"}, "STRASSE"))
        self.assertFalse(self.ev({"icontains": "pos"}, "negative"))

    def test_equals_ignores_outer_whitespace_only(self):
        self.assertTrue(self.ev({"equals": "neutral"}, "  neutral\n"))
        self.assertFalse(self.ev({"equals": "neutral"}, "Neutral"))
        self.assertFalse(self.ev({"equals": "neutral"}, "neutral."))
        self.assertTrue(self.ev({"equals": 42}, "42"))

    def test_contains_any_and_all(self):
        self.assertTrue(self.ev({"contains_any": ["a1", "b2"]}, "xx b2"))
        self.assertFalse(self.ev({"contains_any": ["a1", "b2"]}, "none"))
        self.assertTrue(self.ev({"contains_all": ["a1", "b2"]}, "a1 b2"))
        ok, detail = asserts.evaluate({"contains_all": ["a1", "b2"]}, "a1")
        self.assertFalse(ok)
        self.assertIn("'b2'", detail)

    def test_bad_operands_are_refused(self):
        for bad in ({"contains_any": []}, {"contains_all": "text"},
                    {"contains_any": ["ok", ""]}, {"contains_all": [True]},
                    {"contains_any": [None]}, {"contains_any": None},
                    {"contains_any": ["x"] * 101}, {"equals": ""},
                    {"icontains": ""}, {"equals": False}):
            with self.assertRaises(ValueError, msg=bad):
                asserts.check(bad)

    def test_loader_reads_and_checks_lists(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        os.makedirs(os.path.join(root, "prompts"))
        p = os.path.join(root, "prompts", "s.cases.yaml")
        with open(p, "w", encoding="utf-8") as f:
            f.write("cases:\n  - input: x\n    assert:\n"
                    "      - contains_any: [refund, 3.10]\n"
                    "      - contains_all:\n          - a\n          - b\n")
        cases = suite.load_cases(root, "s")
        self.assertEqual(cases[0]["assert"][0],
                         {"contains_any": ["refund", "3.10"]})
        self.assertEqual(cases[0]["assert"][1], {"contains_all": ["a", "b"]})
        with open(p, "w", encoding="utf-8") as f:
            f.write("cases:\n  - input: x\n    assert:\n"
                    "      - contains_any: [yes, ok]\n")
        with self.assertRaises(ValueError) as cm:
            suite.load_cases(root, "s")
        self.assertIn("contains_any[0]", str(cm.exception))

    def test_typo_suggests_the_new_names(self):
        with self.assertRaises(ValueError) as cm:
            asserts.check({"contains_al": ["a"]})
        self.assertIn("contains_all", str(cm.exception))


# --- --repeat / --min-pass-rate -------------------------------------------

class TestRepeat(Project):
    def run_s(self, root, url, *extra):
        return run_cli(["--root", root, "run", "--prompt", "s", "--version",
                        "v1", "--base-url", url, *extra])

    def test_parsers(self):
        self.assertEqual(runner.parse_repeat("5"), 5)
        for bad in ("0", "-1", "1001", "abc", "2.5", ""):
            with self.assertRaises(ValueError, msg=bad):
                runner.parse_repeat(bad)
        self.assertEqual(runner.parse_pass_rate("80%"), 0.8)
        self.assertEqual(runner.parse_pass_rate("1"), 1.0)
        for bad in ("0", "0%", "-0.5", "1.01", "101%", "nan", "inf", "x",
                    "%", ""):
            with self.assertRaises(ValueError, msg=bad):
                runner.parse_pass_rate(bad)

    def test_required_passes_rounds_up_and_is_never_zero(self):
        self.assertEqual(runner.required_passes(5, 0.8), 4)
        self.assertEqual(runner.required_passes(3, 2 / 3), 2)
        self.assertEqual(runner.required_passes(10, 0.7), 7)
        self.assertEqual(runner.required_passes(100, 0.001), 1)
        self.assertEqual(runner.required_passes(1, 1.0), 1)

    def test_every_run_must_pass_by_default(self):
        url, h = self.model(["good", "bad", "good"])
        root = self.project(ONE_CASE)
        code, out, _ = self.run_s(root, url, "--repeat", "3")
        self.assertEqual(code, 1)
        self.assertEqual(len(h.calls), 3)
        self.assertIn("FAIL c (passed 2/3 runs)", out)
        self.assertIn("'bad'", out)               # the failing run is shown

    def test_pass_rate_gate_and_flaky_report(self):
        url, _ = self.model(["good", "bad", "good"])
        root = self.project(ONE_CASE)
        code, out, _ = self.run_s(root, url, "--repeat", "3",
                                  "--min-pass-rate", "60%",
                                  "--format", "json")
        self.assertEqual(code, 0)
        case = json.loads(out)["cases"][0]
        self.assertEqual((case["runs"], case["passes"], case["flaky"]),
                         (3, 2, True))
        url, _ = self.model(["good", "bad", "good"])
        code, out, _ = self.run_s(root, url, "--repeat", "3",
                                  "--min-pass-rate", "0.6", "--no-cache")
        self.assertEqual(code, 0)
        self.assertIn("flaky: c passed 2/3 runs", out)
        self.assertIn("1/1 passed (3 runs each)", out)

    def test_each_run_has_its_own_cache_entry(self):
        url, h = self.model(["good", "bad", "good"])
        root = self.project(ONE_CASE)
        self.run_s(root, url, "--repeat", "3")
        self.assertEqual(len(h.calls), 3)
        code, out, _ = self.run_s(root, url, "--repeat", "3")
        self.assertEqual(len(h.calls), 3)          # all three were cached
        self.assertIn("(passed 2/3 runs)", out)    # same three answers
        self.assertIn("cached", out)
        # a plain run shares the first entry
        code, _, _ = self.run_s(root, url)
        self.assertEqual((code, len(h.calls)), (0, 3))

    def test_model_error_mid_repeat_is_exit_2(self):
        root = self.project(ONE_CASE)
        code, _, err = self.run_s(root, "http://127.0.0.1:9/v1",
                                  "--repeat", "3", "--timeout", "2")
        self.assertEqual(code, 2)
        self.assertIn("unreachable", err)

    def test_bad_flag_values_are_usage_errors(self):
        root = self.project(ONE_CASE)
        for flags in (["--repeat", "0"], ["--min-pass-rate", "0"],
                      ["--min-pass-rate", "150%"]):
            with self.assertRaises(SystemExit) as cm:
                run_cli(["--root", root, "run", "--prompt", "s",
                         "--version", "v1", *flags])
            self.assertEqual(cm.exception.code, 2)

    def test_api_refuses_bad_values_too(self):
        root = self.project(ONE_CASE)
        with self.assertRaises(ValueError):
            runner.run_suite(root, "s", "v1", repeat=0)
        with self.assertRaises(ValueError):
            runner.run_suite(root, "s", "v1", min_pass_rate=0)


# --- JUnit -----------------------------------------------------------------

class TestJUnit(Project):
    def test_report_counts_and_failure_detail(self):
        url, _ = self.model([f"bad{ESC}[31m\x00￾ & <tag>"])
        root = self.project(ONE_CASE + """  - name: "two & <three>"
    input: x
    assert:
      - not_contains: zzz
""")
        path = os.path.join(root, "report.xml")
        code, out, err = run_cli(["--root", root, "run", "--prompt", "s",
                                  "--version", "v1", "--base-url", url,
                                  "--junit", path])
        self.assertEqual(code, 1)
        tree = ET.parse(path)                # parses: no illegal characters
        top = tree.getroot()
        self.assertEqual((top.get("tests"), top.get("failures")), ("2", "1"))
        cases = top.findall("./testsuite/testcase")
        self.assertEqual([c.get("name") for c in cases],
                         ["c", "two & <three>"])
        self.assertEqual(cases[0].get("classname"), "s.v1")
        failure = cases[0].find("failure")
        self.assertIn("missing 'good'", failure.get("message"))
        self.assertIn("\\x1b[31m", failure.text)
        self.assertIsNone(cases[1].find("failure"))

    def test_run_all_writes_one_suite_per_version(self):
        url, _ = self.model(["good"])
        root = self.project(ONE_CASE, versions=("v1", "v2"))
        path = os.path.join(root, "r.xml")
        code, _, _ = run_cli(["--root", root, "run", "--all",
                              "--base-url", url, "--junit", path,
                              "--format", "json"])
        self.assertEqual(code, 0)
        names = [s.get("name") for s in ET.parse(path).getroot()]
        self.assertEqual(names, ["s.v1", "s.v2"])

    def test_unwritable_report_is_exit_2_and_leaves_no_temp_file(self):
        url, _ = self.model(["good"])
        root = self.project(ONE_CASE)
        for path in (root,                                  # a folder
                     os.path.join(root, "no", "such", "r.xml")):
            code, _, err = run_cli(["--root", root, "run", "--prompt", "s",
                                    "--version", "v1", "--base-url", url,
                                    "--junit", path])
            self.assertEqual(code, 2, path)
            self.assertIn("cannot write the JUnit report", err)
        self.assertFalse([f for f in os.listdir(root)
                          if f.startswith(".junit-")])

    def test_huge_output_is_capped_in_the_report(self):
        r = runner.CaseResult(name="c", passed=False, output="x" * 100000,
                              assertions=[runner.AssertionResult(
                                  {"contains": "y"}, False, "missing 'y'")])
        text = ET.tostring(junit.build([("s", "v1", [r])]), encoding="unicode")
        self.assertLess(len(text), junit.MAX_OUTPUT_CHARS + 2000)


# --- diff --fail-on-regression ---------------------------------------------

class TestDiffGate(Project):
    def diff(self, answers_by_version, *extra):
        prompts = {"v1": "one {{input}}", "v2": "two {{input}}"}
        root = self.project(ONE_CASE, versions=("v1", "v2"), prompt=prompts)
        url, _ = self.model(answers_by_version)
        return run_cli(["--root", root, "diff", "v1", "v2", "--prompt", "s",
                        "--base-url", url, *extra])

    def test_regression_fails_only_with_the_flag(self):
        code, _, _ = self.diff(["good", "bad"])
        self.assertEqual(code, 0)
        code, _, err = self.diff(["good", "bad"], "--fail-on-regression")
        self.assertEqual(code, 1)
        self.assertIn("regressed", err)
        code, out, _ = self.diff(["good", "bad"], "--fail-on-regression",
                                 "--format", "json")
        self.assertEqual(code, 1)
        self.assertTrue(json.loads(out)["cases"][0]["changed"])

    def test_a_fix_never_fails_it(self):
        code, _, _ = self.diff(["bad", "good"], "--fail-on-regression")
        self.assertEqual(code, 0)


# --- panel review fixes (2026-11-06) ---------------------------------------

class TestPanelFixes(Project):
    def run_json(self, answers, *flags):
        url, h = self.model(answers)
        root = self.project(ONE_CASE)
        code, out, err = run_cli(["--root", root, "run", "--prompt", "s",
                                  "--version", "v1", "--base-url", url,
                                  "--format", "json", *flags])
        return code, (json.loads(out)["cases"][0] if out else None), err, h

    def test_gate_passed_case_shows_a_passing_run(self):
        # 1: passed: true must not sit next to a failed assertion
        code, case, _, _ = self.run_json(["bad", "good", "good"],
                                         "--repeat", "3",
                                         "--min-pass-rate", "60%")
        self.assertEqual(code, 0)
        self.assertTrue(case["passed"])
        self.assertTrue(all(a["passed"] for a in case["assertions"]))
        self.assertEqual(case["output"], "good")
        self.assertTrue(case["flaky"])

    def test_gate_failed_case_shows_a_failing_run(self):
        code, case, _, _ = self.run_json(["good", "bad", "good"],
                                         "--repeat", "3")
        self.assertEqual(code, 1)
        self.assertFalse(case["passed"])
        self.assertFalse(case["assertions"][0]["passed"])
        self.assertEqual(case["output"], "bad")

    def test_flaky_only_for_a_case_that_passed_the_gate(self):
        # 2: one meaning in JSON, the terminal and the docs
        code, case, _, _ = self.run_json(["good", "bad", "bad"],
                                         "--repeat", "3",
                                         "--min-pass-rate", "60%")
        self.assertEqual(code, 1)
        self.assertEqual((case["passes"], case["flaky"]), (1, False))
        r = runner.CaseResult(name="c", passed=False, output="", assertions=[],
                              runs=3, passes=1)
        self.assertFalse(r.flaky)

    def test_pass_rate_without_repeat_is_a_usage_error(self):
        # 3: never silently ignored
        url, h = self.model(["good"])
        root = self.project(ONE_CASE, versions=("v1", "v2"))
        for argv in (["run", "--prompt", "s", "--version", "v1"],
                     ["run", "--all"], ["diff", "v1", "v2", "--prompt", "s"]):
            code, _, err = run_cli(["--root", root, *argv, "--base-url", url,
                                    "--min-pass-rate", "50%"])
            self.assertEqual(code, 2, argv)
            self.assertIn("needs --repeat", err)
        self.assertEqual(h.calls, [])                 # nothing was sent
        code, _, _ = run_cli(["--root", root, "run", "--prompt", "s",
                              "--version", "v1", "--base-url", url,
                              "--min-pass-rate", "100%"])
        self.assertEqual(code, 0)                     # 1 is the default
        with self.assertRaises(ValueError):
            runner.run_suite(root, "s", "v1", min_pass_rate=0.5)

    def test_variation_selectors_and_hangul_fillers_are_escaped(self):
        # 4: invisible although not Cc/Cf
        for cp in (0xfe00, 0xfe0f, 0xe0100, 0xe01ef, 0x180b, 0x180d, 0x180f,
                   0x115f, 0x1160, 0x3164, 0xffa0):
            shown = textsafe.clean("a" + chr(cp) + "b")
            self.assertNotIn(chr(cp), shown, hex(cp))
            self.assertTrue(shown.isascii(), hex(cp))
        self.assertEqual(textsafe.clean("\u2764\ufe0f"), "\u2764\\ufe0f")
        url, _ = self.model(["hid\U000e0101den\u3164"])
        root = self.project(ONE_CASE)
        _, out, _ = run_cli(["--root", root, "run", "--prompt", "s",
                             "--version", "v1", "--base-url", url])
        self.assertNotIn("\U000e0101", out)
        self.assertNotIn("\u3164", out)

    def test_repeat_takes_ascii_digits_only(self):
        # 5: no raw int() error, no other scripts' digits
        for bad in ("\u00b2", "\u0663", "\uff15", "5\u00b2"):
            with self.assertRaises(ValueError) as cm:
                runner.parse_repeat(bad)
            self.assertIn("whole number from 1", str(cm.exception))
        root = self.project(ONE_CASE)
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm, redirect_stderr(err):
            cli_mod.main(["--root", root, "run", "--prompt", "s",
                          "--version", "v1", "--repeat", "\u00b2"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("whole number from 1", err.getvalue())
        self.assertNotIn("invalid literal", err.getvalue())

    def test_junit_model_error_is_an_error_element(self):
        # 6: the API can hand build() an errored case; it is an <error>
        r = runner.CaseResult(name="c", passed=False, output="",
                              assertions=[], error=f"down {ESC}[31m")
        ok = runner.CaseResult(name="d", passed=True, output="x",
                               assertions=[])
        top = junit.build([("s", "v1", [r, ok])])
        self.assertEqual((top.get("tests"), top.get("failures"),
                          top.get("errors")), ("2", "0", "1"))
        suite_el = top.find("testsuite")
        self.assertEqual(suite_el.get("errors"), "1")
        case = suite_el.find("testcase")
        self.assertIsNone(case.find("failure"))
        self.assertIn("\\x1b", case.find("error").get("message"))

    def test_cli_model_error_writes_no_junit_report(self):
        root = self.project(ONE_CASE)
        path = os.path.join(root, "r.xml")
        code, _, _ = run_cli(["--root", root, "run", "--prompt", "s",
                              "--version", "v1", "--base-url",
                              "http://127.0.0.1:9/v1", "--timeout", "2",
                              "--junit", path])
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
