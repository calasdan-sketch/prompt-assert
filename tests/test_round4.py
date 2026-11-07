"""Round-4 regressions: what a stranger who runs `pip install` actually gets.

Fake model addresses only, temp directories only, no network.
"""

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import prompt_assert
from prompt_assert import cli as cli_mod
from prompt_assert import fsutil
from prompt_assert import model as model_mod
from prompt_assert import suite as suite_mod

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


class TestOneVersion(unittest.TestCase):
    def test_every_place_that_says_the_version_agrees(self):
        v = prompt_assert.__version__
        self.assertEqual(cli_mod.VERSION, v)
        self.assertRegex(read("pyproject.toml"),
                         r'(?m)^version\s*=\s*"%s"' % re.escape(v))
        self.assertRegex(read("CHANGELOG.md"),
                         r"(?m)^## %s\b" % re.escape(v))
        with self.assertRaises(SystemExit) as cm:
            with redirect_stdout(io.StringIO()) as buf:
                cli_mod.main(["--version"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn(v, buf.getvalue())

    @unittest.skipIf(tomllib is None, "tomllib needs Python 3.11+")
    def test_the_author_is_named(self):
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
            project = tomllib.load(f)["project"]
        names = [a.get("name") for a in project["authors"]]
        self.assertEqual(names, ["Calas Automations Inc."])
        # the licence holder is the same name
        self.assertIn("Calas Automations Inc.", read("LICENSE"))

    def test_the_source_distribution_ships_the_benchmark(self):
        self.assertIn("recursive-include benchmarks", read("MANIFEST.in"))


class TestTheCommandThatRunsTheTests(unittest.TestCase):
    def test_ci_and_readme_use_a_form_that_imports_every_test_module(self):
        # `unittest discover -s tests` alone imports tests/ as top level and
        # test_expert2 / test_expert3 (relative imports) fail to load: the
        # run "passes" 396 tests and two import errors.
        found = 0
        for name in (".github/workflows/ci.yml", "README.md"):
            for line in read(*name.split("/")).splitlines():
                if "unittest discover" in line and "-s tests" in line:
                    found += 1
                    self.assertIn("-t .", line, name)
        self.assertGreaterEqual(found, 3)


class TestBaseUrl(unittest.TestCase):
    def test_only_http_and_https_with_a_host(self):
        for ok in ("http://localhost:11434/v1", "https://api.example.com/v1",
                   "HTTP://127.0.0.1:8080"):
            self.assertEqual(model_mod.check_base_url(ok), ok)
        for bad in ("file:///etc/passwd", "ftp://example.com/v1", "localhost:11434",
                    "http://", "//example.com/v1", "example.com", "data:text/plain,x",
                    "javascript:alert(1)", ""):
            with self.subTest(url=bad), self.assertRaises(model_mod.ModelError) as cm:
                model_mod.check_base_url(bad)
            self.assertIn("http://host:port/v1", str(cm.exception))

    def test_a_password_in_the_address_is_refused_and_never_printed(self):
        with self.assertRaises(model_mod.ModelError) as cm:
            model_mod.check_base_url("http://user:hunter2@host/v1")
        self.assertNotIn("hunter2", str(cm.exception))
        self.assertIn("PROMPT_ASSERT_API_KEY", str(cm.exception))

    def test_odd_characters_and_huge_addresses_are_refused_safely(self):
        for bad in ("http://[::1/v1", "http://ho st/v1", "http://host/\x1b[31m",
                    "http://\u202ehost/v1", "http://host/\u200bv1",
                    "http://host/" + "p" * 5000 + "\x1b"):
            with self.subTest(url=bad[:30]), \
                    self.assertRaises(model_mod.ModelError) as cm:
                model_mod.check_base_url(bad)
            self.assertLess(len(str(cm.exception)), 400)
            self.assertNotIn("\x1b", str(cm.exception))
            self.assertNotIn("\u202e", str(cm.exception))

    def test_surrounding_blanks_are_still_fine(self):
        # an address read from a file with `$(cat url.txt)` ends in a newline
        self.assertEqual(model_mod.check_base_url(" http://h/v1\n"),
                         " http://h/v1\n")

    def test_a_file_address_is_exit_2_in_plain_words(self):
        with tempfile.TemporaryDirectory() as d:
            run_cli("--root", d, "init")
            code, out, err = run_cli("--root", d, "--base-url",
                                     "file:///etc/passwd", "run", "--all")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("must look like http://host:port/v1", err)
        self.assertNotIn("Errno", err)
        self.assertNotIn("Traceback", err)

    def test_the_environment_variable_is_checked_too(self):
        with tempfile.TemporaryDirectory() as d:
            run_cli("--root", d, "init")
            out, err = io.StringIO(), io.StringIO()
            with clean_env(), mock.patch.dict(
                    os.environ, {"PROMPT_ASSERT_BASE_URL": "ftp://x/y"}), \
                    redirect_stdout(out), redirect_stderr(err):
                code = cli_mod.main(["--root", d, "run", "--all"])
        self.assertEqual(code, 2)
        self.assertIn("must look like", err.getvalue())

    def test_replay_needs_no_address_at_all(self):
        # --replay never contacts a model: a junk address must not matter
        with tempfile.TemporaryDirectory() as d:
            run_cli("--root", d, "init")
            code, _, err = run_cli("--root", d, "--base-url", "nonsense",
                                   "run", "--all", "--replay")
        self.assertEqual(code, 2)                      # no cassette yet
        self.assertNotIn("must look like", err)


class TestOddFiles(unittest.TestCase):
    def test_a_prompt_that_is_not_utf8_names_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            run_cli("--root", d, "init")
            p = os.path.join(d, "prompts", "sentiment.v1.txt")
            with open(p, "wb") as f:
                f.write(b"Classify: \xff\xfe{{input}}\n")
            code, out, err = run_cli("--root", d, "--demo", "run",
                                     "--prompt", "sentiment", "--version", "v1")
        self.assertEqual(code, 2)
        self.assertIn("sentiment.v1.txt", err)
        self.assertIn("not UTF-8 text", err)
        self.assertNotIn("codec", err)
        self.assertNotIn("Traceback", err)

    def test_init_writes_unix_line_endings_on_every_system(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, _ = run_cli("--root", d, "init")
            self.assertEqual(code, 0)
            for fn in os.listdir(os.path.join(d, "prompts")):
                with open(os.path.join(d, "prompts", fn), "rb") as f:
                    self.assertNotIn(b"\r", f.read(), fn)


class TestWindowsNames(unittest.TestCase):
    """Not run on Windows: the rule is switched on by a flag."""

    def test_names_windows_would_misread_are_refused(self):
        bad = ["nul", "NUL", "con", "aux.v1", "com1", "LPT9", "a:b", "x*",
               'q"', "a|b", "a?", "<x>", "name.", "name ", "tab\tname"]
        with mock.patch.object(suite_mod, "IS_WINDOWS", True):
            for name in bad:
                with self.subTest(name=name), self.assertRaises(ValueError) as cm:
                    suite_mod.check_name(name, "prompt")
                self.assertIn("Windows", str(cm.exception))
            for name in ("sentiment", "my-bot_2", "console", "nulls", "com10",
                         "café", "a.b"):
                with self.subTest(name=name):
                    self.assertEqual(suite_mod.check_name(name), name)

    def test_elsewhere_the_same_names_are_left_alone(self):
        with mock.patch.object(suite_mod, "IS_WINDOWS", False):
            for name in ("nul", "a:b", "x*"):
                self.assertEqual(suite_mod.check_name(name), name)

    def test_paths_are_refused_everywhere(self):
        for flag in (True, False):
            with mock.patch.object(suite_mod, "IS_WINDOWS", flag):
                for name in ("../x", "a/b", "a\\b", "..", ""):
                    with self.subTest(name=name, windows=flag), \
                            self.assertRaises(ValueError):
                        suite_mod.check_name(name)


class TestReplaceRetry(unittest.TestCase):
    def test_windows_retries_a_locked_target_then_succeeds(self):
        calls = []

        def flaky(a, b):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError(13, "in use")

        with mock.patch.object(fsutil, "IS_WINDOWS", True), \
                mock.patch.object(fsutil, "_PAUSE", 0), \
                mock.patch.object(fsutil.os, "replace", flaky):
            fsutil.replace("a", "b")
        self.assertEqual(len(calls), 3)

    def test_windows_gives_up_with_the_last_error(self):
        with mock.patch.object(fsutil, "IS_WINDOWS", True), \
                mock.patch.object(fsutil, "_PAUSE", 0), \
                mock.patch.object(fsutil.os, "replace",
                                  side_effect=PermissionError(13, "in use")) as m:
            with self.assertRaises(PermissionError):
                fsutil.replace("a", "b")
        self.assertEqual(m.call_count, fsutil._TRIES)

    def test_elsewhere_a_permission_error_is_not_retried(self):
        with mock.patch.object(fsutil, "IS_WINDOWS", False), \
                mock.patch.object(fsutil.os, "replace",
                                  side_effect=PermissionError(13, "no")) as m:
            with self.assertRaises(PermissionError):
                fsutil.replace("a", "b")
        self.assertEqual(m.call_count, 1)

    def test_the_writers_use_it(self):
        for mod in ("cassette", "junit", "report"):
            self.assertIn("fsutil.replace(", read("prompt_assert", mod + ".py"))
            self.assertNotIn("os.replace(", read("prompt_assert", mod + ".py"))


class TestFirstCommands(unittest.TestCase):
    def test_no_command_says_what_to_type(self):
        code, out, err = run_cli()
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("prompt-assert init", err)
        self.assertIn("--demo", err)
        self.assertNotIn("required", err)

    def test_list_as_json(self):
        with tempfile.TemporaryDirectory() as d:
            run_cli("--root", d, "init")
            code, out, err = run_cli("--root", d, "list", "--format", "json")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["prompts"], [{
            "name": "sentiment", "versions": ["v1", "v2"], "cases": 3,
            "status": "ok"}])

    def test_list_as_json_when_there_is_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, _ = run_cli("--root", d, "list", "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"prompts": []})

    def test_list_as_json_with_a_broken_cases_file(self):
        with tempfile.TemporaryDirectory() as d:
            run_cli("--root", d, "init")
            with open(os.path.join(d, "prompts", "sentiment.cases.yaml"), "w") as f:
                f.write("cases:\n  - name: x\n")
            code, out, err = run_cli("--root", d, "list", "--format", "json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["prompts"][0]["status"],
                         "invalid cases file")
        self.assertIn("error:", err)


if __name__ == "__main__":
    unittest.main()
