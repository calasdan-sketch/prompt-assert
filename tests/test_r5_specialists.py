"""Round-5 specialist regressions: author line, whitespace in the YAML reader,
the `is_xml` assertion, the GitHub Action, property tests and golden files for
every output format.

Fake model only (`--demo`), temp directories only, no network.
Regenerate the golden files after a deliberate output change with
`PROMPT_ASSERT_UPDATE_GOLDEN=1 python -m pytest tests/test_r5_specialists.py`
and read the diff before committing it.
"""

import contextlib
import io
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import prompt_assert
from prompt_assert import asserts, miniyaml
from prompt_assert import cli as cli_mod

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None
try:
    import yaml as pyyaml     # only used to cross-check, never required
except ImportError:
    pyyaml = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(ROOT, "tests", "golden")
AUTHOR = "Calas Automations Inc."


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


@contextlib.contextmanager
def clean_env():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PROMPT_ASSERT_", "PROMPTREGRESS_"))}
    with mock.patch.dict(os.environ, env, clear=True):
        yield


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with clean_env(), redirect_stdout(out), redirect_stderr(err):
        code = cli_mod.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class TestAuthorLine(unittest.TestCase):
    """The owner's name, in every place that carries one."""

    @unittest.skipIf(tomllib is None, "tomllib needs Python 3.11+")
    def test_pyproject_names_only_the_company(self):
        project = tomllib.loads(read("pyproject.toml"))["project"]
        self.assertEqual(project["authors"], [{"name": AUTHOR}])
        self.assertNotIn("maintainers", project)   # none declared: no email invented

    def test_the_other_places(self):
        self.assertEqual(prompt_assert.__author__, AUTHOR)
        self.assertTrue(read("LICENSE").startswith(
            "MIT License\n\nCopyright (c) 2026 " + AUTHOR + "\n"))
        self.assertIn("copyright 2026 " + AUTHOR, read("README.md"))
        self.assertRegex(read("action.yml"), r"(?m)^author: " + re.escape(AUTHOR))

    def test_no_stale_owner_label_in_the_shipped_text(self):
        for name in ("pyproject.toml", "README.md", "LICENSE", "action.yml",
                     "CHANGELOG.md", "PUBLISH-CHECKLIST.md"):
            if not os.path.exists(os.path.join(ROOT, name)):
                continue                      # not in the source distribution
            text = read(name)
            for stale in ("DC Enterprises", "Dan Calas"):
                self.assertNotIn(stale, text, f"{stale!r} in {name}")


class TestYamlWhitespace(unittest.TestCase):
    """YAML's blanks are the space and the tab. A no-break space (or any
    other Unicode space) at the edge of a value is part of the value."""

    def test_no_break_space_survives_in_plain_and_quoted_values(self):
        doc = ("cases:\n  - name: \u00a0padded\u00a0\n    input: x\u00a0\n"
               "    assert:\n      - equals: \"\u3000y\u3000\"\n")
        case = miniyaml.safe_load(doc)["cases"][0]
        self.assertEqual(case["name"], "\u00a0padded\u00a0")
        self.assertEqual(case["input"], "x\u00a0")
        self.assertEqual(case["assert"][0]["equals"], "\u3000y\u3000")

    def test_a_line_of_only_no_break_spaces_is_not_blank(self):
        with self.assertRaises(ValueError):       # fails loudly, never skipped
            miniyaml.safe_load("a: 1\n\u00a0\nb: 2\n")

    def test_blanks_around_flow_lists_and_keys_still_trimmed(self):
        self.assertEqual(miniyaml.safe_load("k:\t[ a ,\tb ]  \n"),
                         {"k": ["a", "b"]})
        self.assertEqual(miniyaml.safe_load("key :   v  \n"), {"key": "v"})

    def test_double_quoted_json_strings_round_trip(self):
        """Property: any text written as a JSON string is a YAML
        double-quoted scalar and reads back unchanged. Seeded, no PyYAML."""
        rnd = random.Random(5)
        pool = (list("abc XYZ019_-.,:;!?#'\"\\/[]{}()&*%@`|>~=+<$^\t")
                + ["\u00e9", "\u65e5", "\U0001f600", "\u00a0", "\u202e",
                   "\u2028", "\u0085", "\n", "\r"])
        for _ in range(1500):
            s = "".join(rnd.choice(pool) for _ in range(rnd.randint(0, 25)))
            doc = "k: " + json.dumps(s) + "\n"
            try:
                got = miniyaml.safe_load(doc)
            except ValueError:
                continue                           # refusing is allowed ...
            self.assertEqual(got, {"k": s}, doc)   # ... changing a value is not

    @unittest.skipIf(pyyaml is None, "PyYAML is not installed (optional)")
    def test_agrees_with_pyyaml_whenever_both_accept(self):
        rnd = random.Random(55)
        pool = (list("abcXYZ 019_-.,:;!?#'\"\\/[]{}()&*%@`|>~=+<$^")
                + ["\u00e9", "\u65e5", "\U0001f600", "\t", "\u202e",
                   "\u00a0", "\u3000"])

        def rs(n=24):
            return "".join(rnd.choice(pool) for _ in range(rnd.randint(0, n)))
        checked = 0
        for _ in range(1200):
            doc = {"cases": [{"name": rs(), "input": rs(),
                              "assert": [{"contains": rs()}]}
                             for _ in range(rnd.randint(1, 2))]}
            style = rnd.choice([{}, {"width": 10 ** 6}, {"width": 20},
                                {"allow_unicode": True},
                                {"default_style": '"'}, {"default_style": "'"}])
            text = pyyaml.safe_dump(doc, **style)
            try:
                got = miniyaml.safe_load(text)
            except ValueError:
                continue
            checked += 1
            self.assertEqual(got, pyyaml.safe_load(text), text)
        self.assertGreater(checked, 100)


class TestIsXml(unittest.TestCase):
    def ev(self, want, text):
        return asserts.evaluate({"is_xml": want}, text)

    def test_well_formed_and_not(self):
        self.assertTrue(self.ev(True, "<a><b>x</b></a>")[0])
        self.assertTrue(self.ev(True, '  <?xml version="1.0" encoding="utf-8"?><r/>\n')[0])
        for bad in ("", "plain text", "<a>", "<a/><b/>", "<a b=1/>",
                    "<a>\x00</a>", "```xml\n<a/>\n```"):
            ok, detail = self.ev(True, bad)
            self.assertFalse(ok, bad)
            self.assertIn("not well-formed XML", detail)

    def test_false_and_not(self):
        self.assertTrue(self.ev(False, "just words")[0])
        self.assertFalse(self.ev(False, "<a/>")[0])
        self.assertTrue(asserts.evaluate({"not": {"is_xml": True}}, "words")[0])

    def test_entity_declarations_are_refused_not_expanded(self):
        bomb = ('<!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">'
                ']><l>&b;&b;&b;</l>')
        ok, detail = self.ev(True, bomb)
        self.assertFalse(ok)
        self.assertIn("DOCTYPE", detail)
        self.assertFalse(self.ev(True, "<!doctype x><x/>")[0])

    def test_deep_nesting_is_an_answer_not_a_crash(self):
        deep = "<a>" * 50000 + "</a>" * 50000
        self.assertIsInstance(self.ev(True, deep)[0], bool)

    def test_operand_must_be_a_flag(self):
        for bad in ("yes", 1, None, ["x"]):
            with self.assertRaises(ValueError):
                asserts.check({"is_xml": bad})

    def test_listed_everywhere(self):
        self.assertIn("is_xml", asserts.KINDS)
        self.assertIn("`is_xml`", read("README.md"))


class TestAssertionProperties(unittest.TestCase):
    """Seeded property tests over random replies (hypothesis is not a
    dependency of this repository)."""

    def setUp(self):
        self.rnd = random.Random(2026)
        self.pool = (list("abc DEF\n\t.-_{}[]\":,0123") + ["\u00e9", "\U0001f600",
                     "\u00a0", "\u202e", "\x00", "\u200b"])

    def text(self, n=40):
        return "".join(self.rnd.choice(self.pool)
                       for _ in range(self.rnd.randint(0, n)))

    def test_contains_agrees_with_in(self):
        for _ in range(500):
            a, b, c = self.text(10), self.text(6) or "q", self.text(10)
            self.assertTrue(asserts.evaluate({"contains": b}, a + b + c)[0])
            self.assertEqual(asserts.evaluate({"contains": b}, a)[0], b in a)
            self.assertEqual(
                asserts.evaluate({"not_contains": b}, a)[0], b not in a)

    def test_not_turns_every_decidable_assertion_round(self):
        kinds = [{"contains": "a"}, {"icontains": "D"}, {"regex": "[a-c]+"},
                 {"starts_with": "a"}, {"max_chars": 5}, {"min_words": 2},
                 {"json_valid": True}, {"is_xml": True}, {"no_urls": True},
                 {"contains_json": True}]
        for _ in range(300):
            out = self.text()
            for a in kinds:
                plain = asserts.evaluate(a, out)[0]
                self.assertIsInstance(plain, bool)
                self.assertEqual(asserts.evaluate({"not": a}, out)[0], not plain)

    def test_max_and_min_are_complementary_at_the_edge(self):
        for _ in range(300):
            out = self.text()
            n = len(out.strip())
            self.assertTrue(asserts.evaluate({"max_chars": n}, out)[0])
            self.assertTrue(asserts.evaluate({"min_chars": n}, out)[0])
            if n:
                self.assertFalse(asserts.evaluate({"max_chars": n - 1}, out)[0])
            self.assertFalse(asserts.evaluate({"min_chars": n + 1}, out)[0])

    def test_json_values_round_trip_through_the_json_assertions(self):
        def value(depth=0):
            r = self.rnd.random()
            if depth > 3 or r < .4:
                return self.rnd.choice([None, True, False, 0, -7, 2.5,
                                        self.text(6)])
            if r < .7:
                return [value(depth + 1) for _ in range(self.rnd.randint(0, 3))]
            return {self.text(4): value(depth + 1)
                    for _ in range(self.rnd.randint(0, 3))}
        for _ in range(300):
            v = value()
            reply = json.dumps(v, ensure_ascii=self.rnd.random() < .5)
            self.assertTrue(asserts.evaluate({"json_valid": True}, reply)[0], reply)
            if isinstance(v, (dict, list)):
                fenced = "Here you go:\n```json\n" + reply + "\n```\nDone."
                self.assertTrue(
                    asserts.evaluate({"contains_json": True}, fenced)[0], fenced)

    def test_nothing_but_valueerror_escapes(self):
        """Hostile operands and replies: an answer, or ValueError, never a
        traceback."""
        operands = [None, True, 0, -1, 10 ** 30, 1.5, "", "(", "[", "x" * 5000,
                    [], [1], {}, {"a": 1}, "\ud800", float("nan")]
        for kind in asserts.KINDS:
            if kind == "judge":
                continue
            for want in operands:
                try:
                    asserts.evaluate({kind: want}, self.text())
                except ValueError:
                    pass


class TestGitHubAction(unittest.TestCase):
    def setUp(self):
        self.doc = miniyaml.safe_load(read("action.yml"))

    def test_shape(self):
        self.assertEqual(self.doc["name"], "prompt-assert")
        self.assertEqual(self.doc["runs"]["using"], "composite")
        self.assertEqual(self.doc["inputs"]["version"]["required"], True)
        self.assertNotIn("default", self.doc["inputs"]["version"])  # pin it
        for name, spec in self.doc["inputs"].items():
            self.assertIn("description", spec, name)

    def test_inputs_never_appear_inside_the_script_text(self):
        step = self.doc["runs"]["steps"][1]
        self.assertNotIn("${{", step["run"])
        for key in ("PA_VERSION", "PA_COMMAND", "PA_DIR", "PA_SUMMARY"):
            self.assertIn("${{ inputs.", step["env"][key])

    @unittest.skipIf(os.name == "nt" or shutil.which("bash") is None,
                     "needs bash")
    def test_script_with_a_stand_in_python(self):
        script = self.doc["runs"]["steps"][1]["run"]
        with tempfile.TemporaryDirectory() as tmp:
            shim = os.path.join(tmp, "shim")
            os.mkdir(shim)
            with open(os.path.join(shim, "python"), "w") as f:
                f.write('#!/bin/sh\necho "PY:$*"\n')
            os.chmod(os.path.join(shim, "python"), 0o755)
            os.mkdir(os.path.join(tmp, "my project"))
            path = os.path.join(tmp, "act.sh")
            with open(path, "w") as f:
                f.write(script)

            def go(version="0.4.0", command="run --all --replay",
                   summary="true", step_summary="/tmp/summary.md"):
                env = {"PATH": shim + os.pathsep + os.environ["PATH"],
                       "PA_VERSION": version, "PA_COMMAND": command,
                       "PA_DIR": "my project", "PA_SUMMARY": summary}
                if step_summary:
                    env["GITHUB_STEP_SUMMARY"] = step_summary
                p = subprocess.run(["bash", path], cwd=tmp, env=env,
                                   capture_output=True, text=True)
                return p.returncode, p.stdout, p.stderr
            code, out, err = go()
            self.assertEqual(code, 0, err)
            self.assertIn("-m pip install --disable-pip-version-check --quiet "
                          "prompt-assert==0.4.0", out)
            self.assertIn("-m prompt_assert run --all --replay "
                          "--github-annotations --markdown /tmp/summary.md", out)
            # the command chose its own output: nothing is added
            _, out, _ = go(command="run --all --format json")
            self.assertIn("PY:-m prompt_assert run --all --format json\n", out)
            _, out, _ = go(summary="false")
            self.assertTrue(out.endswith("--replay\n"), out)
            # a glob is passed as text, a non-run command gets no extras
            _, out, _ = go(command="plan *")
            self.assertIn("PY:-m prompt_assert plan *\n", out)
            # fail closed: bad versions and empty commands are exit 2
            for bad in ("", "0.4.0; id", "$(id)", "1..2", ".1", "1.", "latest"):
                code, out, err = go(version=bad)
                self.assertEqual(code, 2, bad)
                self.assertNotIn("PY:", out)
            code, _, err = go(command="   ")
            self.assertEqual(code, 2)
            self.assertIn("command is empty", err)


def normalise(text):
    """Strip what changes from run to run: seconds and the temp folder."""
    text = re.sub(r'time="[0-9.]+"', 'time="T"', text)
    text = re.sub(r'"seconds": [0-9.eE+-]+', '"seconds": 0', text)
    text = re.sub(r"\d+\.\d+s\b", "Ns", text)
    return text


class TestGoldenOutputs(unittest.TestCase):
    """Every output format against a committed file: a change to any of them
    is a decision, not an accident."""

    def check(self, name, text):
        text = normalise(text)
        path = os.path.join(GOLDEN, name)
        if os.environ.get("PROMPT_ASSERT_UPDATE_GOLDEN"):
            os.makedirs(GOLDEN, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        with open(path, encoding="utf-8", newline="") as f:
            self.assertEqual(text, f.read(), f"{name} changed")

    def demo(self, *argv):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_cli("--root", tmp, "--demo", *argv)
            files = {}
            for n in ("j.xml", "m.md"):
                p = os.path.join(tmp, n)
                if os.path.exists(p):
                    with open(p, encoding="utf-8", newline="") as f:
                        files[n] = f.read()
        return code, out, err, files

    def test_human_pass_and_fail(self):
        code, out, err, _ = self.demo("run", "--prompt", "sentiment",
                                      "--version", "v1")
        self.assertEqual(code, 0)
        self.check("run_v1.txt", out)
        code, out, err, _ = self.demo("run", "--prompt", "sentiment",
                                      "--version", "v2")
        self.assertEqual(code, 1)
        self.check("run_v2.txt", out)

    def test_json(self):
        code, out, err, _ = self.demo("run", "--prompt", "sentiment",
                                      "--version", "v2", "--format", "json")
        self.assertEqual(code, 1)
        json.loads(out)
        self.check("run_v2.json", out)

    def test_junit_and_markdown_and_annotations(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_cli(
                "--root", tmp, "--demo", "run", "--prompt", "sentiment",
                "--version", "v2", "--junit", os.path.join(tmp, "j.xml"),
                "--markdown", os.path.join(tmp, "m.md"),
                "--github-annotations")
            self.assertEqual(code, 1)
            with open(os.path.join(tmp, "j.xml"), encoding="utf-8") as f:
                junit = f.read()
            with open(os.path.join(tmp, "m.md"), encoding="utf-8") as f:
                md = f.read()
        self.check("junit_v2.xml", junit)
        self.check("report_v2.md", md)
        self.check("run_v2_annotated.txt", out)

    def test_diff_list_plan_all_and_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run_cli("--root", tmp, "init")[0], 0)
            code, out, _ = run_cli("--root", tmp, "list", "--format", "json")
            self.assertEqual(code, 0)
            self.check("list.json", out)
            code, out, _ = run_cli("--root", tmp, "list")
            self.check("list.txt", out)
            code, out, _ = run_cli("--root", tmp, "plan", "--all")
            self.assertEqual(code, 0)
            self.check("plan_all.txt", out)
        code, out, _, _ = self.demo("diff", "v1", "v2", "--prompt", "sentiment")
        self.assertEqual(code, 0)
        self.check("diff_v1_v2.txt", out)
        code, out, _ = run_cli("corpus", "injection")
        self.assertEqual(code, 0)
        self.check("corpus_injection.yaml", out)


if __name__ == "__main__":
    unittest.main()
