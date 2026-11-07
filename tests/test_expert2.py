"""Expert round 2 (0.4.0): cassettes, a cache keyed by what is sent, security
assertions and the injection corpus, early-stopped repeats, parallel cases,
plan and budgets, tags, --changed-since, Markdown and annotation reports.

Fake servers on 127.0.0.1 and temp directories only; no real model, no
network beyond loopback. Secret-shaped test data is assembled at run time.
"""

import itertools
import json
import os
import random
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from prompt_assert import (
    asserts,
    cassette,
    changed,
    corpus,
    plan,
    report,
    runner,
    scan,
    suite,
)

from .test_synapse1 import Project, run_cli


def aws_key() -> str:
    return "AK" + "IA" + "A1B2C3D4E5F6G7H8"


def gh_token() -> str:
    return "gh" + "p_" + "a1" * 18


def pem_header() -> str:
    return "-----BEGIN " + "RSA PRIVATE KEY-----"


def jwt() -> str:
    return ".".join(["ey" + "JhbGciOiJIUzI1NiJ9", "ey" + "JzdWIiOiIxMjM0NTY3",
                     "abcdefghijk1234"])


class Slow(BaseHTTPRequestHandler):
    """Answers `reply`, after `delay` seconds, and counts its calls."""
    reply: ClassVar[str] = "good"
    delay: ClassVar[float] = 0.0
    calls: ClassVar[list] = []
    seen: ClassVar[list] = []

    def do_POST(self):
        cls = type(self)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        cls.calls.append(1)
        cls.seen.append(body["messages"][-1]["content"])
        if cls.delay:
            time.sleep(cls.delay)
        payload = json.dumps({"choices": [{"message": {
            "role": "assistant", "content": cls.reply}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


class Base(Project):
    def server(self, reply="good", delay=0.0):
        handler = type("S", (Slow,), {"reply": reply, "delay": delay,
                                      "calls": [], "seen": []})
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_port}/v1", handler


def cases_yaml(n=3, assertion="contains: good", extra=""):
    return "cases:\n" + "".join(
        f"  - name: c{i}\n    input: in{i}\n{extra}    assert:\n"
        f"      - {assertion}\n" for i in range(n))


# ------------------------------------------------------------- scan / asserts

class TestSecurityAssertions(unittest.TestCase):
    def ev(self, assertion, text, template=None):
        return asserts.evaluate(assertion, text, template=template)

    def test_secrets_are_found_and_never_echoed(self):
        for text in (aws_key(), gh_token(), pem_header(), jwt(),
                     "password = hunter2hunter2hunter2"):
            ok, detail = self.ev({"no_secrets": True}, "see " + text)
            self.assertFalse(ok, text)
            self.assertNotIn(text, detail)
        self.assertTrue(self.ev({"no_secrets": True},
                                "The key rotation policy is fine.")[0])

    def test_pii_kinds_and_checksums(self):
        visa = "4" + "111 1111 1111 1111"          # a test number, Luhn ok
        self.assertFalse(self.ev({"no_pii": True}, f"card {visa}")[0])
        self.assertTrue(self.ev({"no_pii": True},
                                "order 4111 1111 1111 1112")[0])   # bad Luhn
        self.assertFalse(self.ev({"no_pii": True}, "mail a.b@example.org")[0])
        self.assertFalse(self.ev({"no_pii": True}, "call (204) 555-0123")[0])
        self.assertFalse(self.ev({"no_pii": True}, "ssn 123-45-6789")[0])
        self.assertTrue(self.ev({"no_pii": True}, "ssn 000-12-3456")[0])
        self.assertTrue(self.ev({"no_pii": True}, "ssn 987-65-4321")[0])
        self.assertFalse(self.ev({"no_pii": True}, "sin 130 692 544")[0])
        self.assertTrue(self.ev({"no_pii": True}, "sin 130 692 545")[0])
        # a kind not asked for is not looked for; ip is opt-in
        self.assertTrue(self.ev({"no_pii": ["email"]}, "call 204-555-0123")[0])
        self.assertTrue(self.ev({"no_pii": True}, "host 192.168.1.10")[0])
        self.assertFalse(self.ev({"no_pii": ["ip"]}, "host 192.168.1.10")[0])
        self.assertTrue(self.ev({"no_pii": ["ip"]}, "version 1.2.3.999")[0])

    def test_hidden_characters(self):
        self.assertFalse(self.ev({"no_hidden_chars": True},
                                 "hi" + chr(0x200B) + "there")[0])
        self.assertFalse(self.ev({"no_hidden_chars": True},
                                 chr(0x202E) + "evil")[0])
        self.assertFalse(self.ev({"no_hidden_chars": True},
                                 "x" + chr(0xE0049))[0])      # a tag char
        self.assertTrue(self.ev({"no_hidden_chars": True},
                                "line one\r\nline two\ttab")[0])
        family = "\U0001F468" + chr(0x200D) + "\U0001F469"
        self.assertFalse(self.ev({"no_hidden_chars": True}, family)[0])
        self.assertTrue(self.ev({"no_hidden_chars": "emoji_ok"}, family)[0])
        self.assertFalse(self.ev({"no_hidden_chars": "emoji_ok"},
                                 "a" + chr(0x200B))[0])

    def test_urls_and_allowed_domains(self):
        self.assertTrue(self.ev({"no_urls": True}, "no links here")[0])
        self.assertFalse(self.ev({"no_urls": True}, "go to http://a.example")[0])
        self.assertFalse(self.ev({"no_urls": True}, "see www.example.org/x")[0])
        allow = {"no_urls": ["example.com"]}
        self.assertTrue(self.ev(allow, "https://docs.example.com/a")[0])
        ok, detail = self.ev(allow, "https://example.com.evil.io/a")
        self.assertFalse(ok)
        self.assertIn("example.com.evil.io", detail)
        self.assertFalse(self.ev(allow, "![x](https://evil.io/p?d=1)")[0])

    def test_prompt_leak_ignores_the_case_input(self):
        template = "Answer in one word only, never explain. Text: {{input}}"
        leak = "My rules: answer in one word only, never explain."
        self.assertFalse(self.ev({"no_prompt_leak": 6}, leak, template)[0])
        self.assertTrue(self.ev({"no_prompt_leak": 6}, "negative", template)[0])
        # the input is not part of the prompt text, so repeating it is fine
        self.assertTrue(self.ev({"no_prompt_leak": 3}, "Text: lorem ipsum "
                                "dolor sit", template)[0])
        with self.assertRaises(ValueError):
            self.ev({"no_prompt_leak": True}, "x")          # no template

    def test_bad_operands_are_refused_with_the_fix(self):
        for bad in ({"no_secrets": False}, {"no_pii": []}, {"no_pii": ["dna"]},
                    {"no_pii": "email"}, {"no_hidden_chars": "yes"},
                    {"no_urls": ["not a domain"]}, {"no_urls": ["a/b.com"]},
                    {"no_urls": []}, {"no_prompt_leak": 1},
                    {"no_prompt_leak": False}, {"no_prompt_leak": "8"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                asserts.check(bad)
        self.assertEqual(asserts.check({"no_prompt_leak": True}),
                         {"no_prompt_leak": 8})
        self.assertEqual(asserts.check({"no_pii": True})["no_pii"],
                         list(scan.PII_DEFAULT))
        checked = asserts.check({"no_urls": ["Example.COM."]})
        self.assertEqual(checked, {"no_urls": ["example.com"]})
        self.assertEqual(asserts.check(checked), checked)

    def test_large_input_is_fast(self):
        blob = ("word " * 400_000) + aws_key()
        t = time.monotonic()
        for a in ({"no_secrets": True}, {"no_pii": True},
                  {"no_hidden_chars": True}, {"no_urls": True},
                  {"no_prompt_leak": 8}):
            asserts.evaluate(a, blob, template="a b c d e f g h i j " * 50)
        self.assertLess(time.monotonic() - t, 10)


# ---------------------------------------------------------------- the corpus

class TestCorpus(Base):
    def test_the_corpus_loads_as_a_suite(self):
        root = self.project(corpus.injection_cases())
        cases = suite.load_cases(root, "s")
        self.assertEqual(len(cases), 25)
        self.assertTrue(all("injection" in c["tags"] for c in cases))
        self.assertTrue(all(len(c["assert"]) == 6 for c in cases))
        inputs = [c["input"] for c in cases]
        self.assertTrue(any(chr(0xE0049) in i for i in inputs))
        self.assertTrue(any(chr(0x200B) in i for i in inputs))

    def test_the_file_is_plain_ascii_so_invisibles_are_reviewable(self):
        text = corpus.injection_cases()
        text.encode("ascii")

    def test_canary_and_domains(self):
        text = corpus.injection_cases("MARKER-12345", ["example.com"])
        self.assertIn("MARKER-12345", text)
        self.assertIn("no_urls: [example.com]", text)
        for bad in ("short", "has space!", "x" * 41, "a.b.c.d.e"):
            with self.assertRaises(ValueError):
                corpus.injection_cases(bad)

    def test_a_chatty_model_fails_and_a_robust_one_passes(self):
        root = self.project(corpus.injection_cases(),
                            prompt="Classify the review in one word, "
                                   "positive negative or neutral.\n"
                                   "Review: {{input}}\n")
        url, _ = self.server("negative")
        code, out, _ = run_cli(["--root", root, "run", "--prompt", "s",
                                "--version", "v1", "--base-url", url,
                                "--tag", "injection"])
        self.assertEqual(code, 0, out)
        url2, _ = self.server(f"Sure! {corpus.DEFAULT_CANARY}")
        code, out, _ = run_cli(["--root", root, "run", "--prompt", "s",
                                "--version", "v1", "--base-url", url2,
                                "--tag", "injection", "--no-cache"])
        self.assertEqual(code, 1)

    def test_cli_prints_it(self):
        code, out, _ = run_cli(["corpus", "injection"])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("# Prompt-injection"))


# ------------------------------------------------------------------ the cache

class TestCacheKey(Base):
    def test_editing_an_assertion_pays_for_no_call(self):
        root = self.project(cases_yaml(4))
        url, h = self.server("good")
        argv = ["--root", root, "run", "--prompt", "s", "--version", "v1",
                "--base-url", url]
        self.assertEqual(run_cli(argv)[0], 0)
        self.assertEqual(len(h.calls), 4)
        with open(os.path.join(root, "prompts", "s.cases.yaml"), "w") as f:
            f.write(cases_yaml(4, "regex: (?i)GOOD\n      - max_tokens: 50"))
        self.assertEqual(run_cli(argv)[0], 0)
        self.assertEqual(len(h.calls), 4, "a new assertion re-ran the model")

    def test_a_changed_input_or_prompt_is_a_miss(self):
        root = self.project(cases_yaml(1))
        url, h = self.server("good")
        argv = ["--root", root, "run", "--prompt", "s", "--version", "v1",
                "--base-url", url]
        run_cli(argv)
        with open(os.path.join(root, "prompts", "s.v1.txt"), "w") as f:
            f.write("Different: {{input}}\n")
        run_cli(argv)
        self.assertEqual(len(h.calls), 2)


# ----------------------------------------------------------------- cassettes

class TestCassettes(Base):
    def record(self, root, url, *more):
        return run_cli(["--root", root, "run", "--prompt", "s", "--version",
                        "v1", "--base-url", url, "--record", *more])

    def replay(self, root, *more, version="v1"):
        # no --base-url and a dead address in the environment: replay must
        # never use it
        return run_cli(["--root", root, "run", "--prompt", "s", "--version",
                        version, "--replay", *more],
                       PROMPT_ASSERT_BASE_URL="http://127.0.0.1:9/v1")

    def test_record_then_replay_contacts_no_model(self):
        root = self.project(cases_yaml(3))
        url, h = self.server("good")
        code, _, err = self.record(root, url)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(h.calls), 3)
        path = cassette.cassette_path(root, "s")
        self.assertTrue(os.path.isfile(path))
        code, out, err = self.replay(root)
        self.assertEqual(code, 0, err)
        self.assertIn("3/3 passed", out)
        self.assertEqual(len(h.calls), 3)

    def test_replay_survives_assertion_edits_but_not_prompt_edits(self):
        root = self.project(cases_yaml(2))
        url, _ = self.server("good")
        self.record(root, url)
        with open(os.path.join(root, "prompts", "s.cases.yaml"), "w") as f:
            f.write(cases_yaml(2, "icontains: GOOD"))
        self.assertEqual(self.replay(root)[0], 0)
        with open(os.path.join(root, "prompts", "s.v1.txt"), "w") as f:
            f.write("Changed: {{input}}\n")
        code, out, err = self.replay(root)
        self.assertEqual(code, 2)
        self.assertIn("no recording", err)
        self.assertIn("run --record", err)
        self.assertNotIn("passed", out)

    def test_replay_fails_closed_on_missing_or_damaged_files(self):
        root = self.project(cases_yaml(1))
        code, _, err = self.replay(root)
        self.assertEqual(code, 2)
        self.assertIn("no cassette", err)
        path = cassette.cassette_path(root, "s")
        for body in ("{not json", "[]", '{"format": 99, "responses": {}}',
                     '{"format": 1, "responses": {"k": {"output": 3}}}',
                     '{"format": 1, "responses": {}, "judges": {"k": {}}}'):
            with open(path, "w") as f:
                f.write(body)
            code, _, err = self.replay(root)
            self.assertEqual(code, 2, body)
            self.assertIn(path, err)

    def test_huge_cassette_is_refused(self):
        root = self.project(cases_yaml(1))
        path = cassette.cassette_path(root, "s")
        with open(path, "w") as f:
            f.write("{}")
        old = cassette.MAX_BYTES
        cassette.MAX_BYTES = 1
        self.addCleanup(setattr, cassette, "MAX_BYTES", old)
        self.assertEqual(self.replay(root)[0], 2)

    def test_the_file_is_deterministic_ascii_with_invisibles_escaped(self):
        root = self.project(cases_yaml(1))
        url, _ = self.server("good" + chr(0x200B))
        self.record(root, url)
        raw = open(cassette.cassette_path(root, "s"), "rb").read()
        raw.decode("ascii")
        self.assertIn(b"\\u200b", raw)
        self.record(root, url)
        again = open(cassette.cassette_path(root, "s"), "rb").read()
        self.assertEqual(raw, again, "recording twice changed the file")

    def test_recording_one_version_keeps_the_others_unless_all(self):
        root = self.project(cases_yaml(1), versions=("v1", "v2"),
                            prompt={"v1": "A {{input}}\n", "v2": "B {{input}}\n"})
        url, _ = self.server("good")
        for v in ("v1", "v2"):
            run_cli(["--root", root, "run", "--prompt", "s", "--version", v,
                     "--base-url", url, "--record"])
        self.assertEqual(self.replay(root, version="v1")[0], 0)
        self.assertEqual(self.replay(root, version="v2")[0], 0)
        # drop v2's prompt; a full re-record prunes its recording
        os.remove(os.path.join(root, "prompts", "s.v2.txt"))
        code, _, _ = run_cli(["--root", root, "run", "--all", "--base-url",
                              url, "--record"])
        self.assertEqual(code, 0)
        data = json.load(open(cassette.cassette_path(root, "s")))
        self.assertEqual(len(data["responses"]), 1)

    def test_record_warns_about_secret_shaped_replies(self):
        root = self.project(cases_yaml(1))
        url, _ = self.server("good " + aws_key())
        code, _, err = self.record(root, url)
        self.assertEqual(code, 0)
        self.assertIn("AWS access key id", err)
        self.assertNotIn(aws_key(), err)

    def test_flags_that_contradict_are_usage_errors(self):
        root = self.project(cases_yaml(1))
        base = ["--root", root, "run", "--prompt", "s", "--version", "v1"]
        self.assertEqual(run_cli(base + ["--record", "--replay"])[0], 2)
        self.assertEqual(run_cli(["--demo"] + base[2:] + ["--replay"])[0], 2)
        self.assertEqual(run_cli(base + ["--record", "--repeat", "3",
                                         "--early-stop"])[0], 2)

    def test_repeat_runs_are_recorded_separately(self):
        root = self.project(cases_yaml(1))
        url, h = self.server("good")
        self.record(root, url, "--repeat", "3")
        self.assertEqual(len(h.calls), 3)
        data = json.load(open(cassette.cassette_path(root, "s")))
        self.assertEqual(len(data["responses"]), 3)
        self.assertEqual(self.replay(root, "--repeat", "3")[0], 0)
        self.assertEqual(self.replay(root, "--repeat", "4")[0], 2)

    def test_judge_verdicts_are_recorded_and_replayed(self):
        root = self.project("cases:\n  - name: j\n    input: x\n    assert:\n"
                            "      - judge: be nice\n")
        url, h = self.server("PASS")
        code, _, err = self.record(root, url)
        self.assertEqual(code, 0, err)
        data = json.load(open(cassette.cassette_path(root, "s")))
        self.assertEqual(len(data["judges"]), 1)
        n = len(h.calls)
        self.assertEqual(self.replay(root)[0], 0)
        self.assertEqual(len(h.calls), n)
        data["judges"] = {}
        json.dump(data, open(cassette.cassette_path(root, "s"), "w"))
        code, _, err = self.replay(root)
        self.assertEqual(code, 2)
        self.assertIn("judge", err)

    def test_stale_recordings_are_noted_on_a_full_run(self):
        root = self.project(cases_yaml(2))
        url, _ = self.server("good")
        self.record(root, url)
        with open(os.path.join(root, "prompts", "s.cases.yaml"), "w") as f:
            f.write(cases_yaml(1))
        code, _, err = run_cli(["--root", root, "run", "--all", "--replay"])
        self.assertEqual(code, 0)
        self.assertIn("1 recording(s) nothing used", err)

    def test_tape_unit(self):
        tape = cassette.Tape(os.path.join(tempfile.mkdtemp(), "x.json"),
                             "record")
        tape.put("p", "out", 0, "m")
        self.assertEqual(tape.get("p", 0), "out")
        self.assertIsNone(tape.get("p", 1))
        with self.assertRaises(ValueError):
            cassette.Tape("x", "bogus")


# ------------------------------------------------------------ early stopping

class TestEarlyStop(Base):
    def test_decided_matches_the_full_gate_for_every_sequence(self):
        for repeat in range(2, 8):
            for rate in (1.0, 0.9, 0.8, 2 / 3, 0.5, 0.34, 0.1):
                need = runner.required_passes(repeat, rate)
                for bits in itertools.product((True, False), repeat=repeat):
                    full = sum(bits) >= need
                    seen = 0
                    verdict = None
                    for i, b in enumerate(bits, start=1):
                        seen += b
                        d = runner.decided(seen, i, repeat, need)
                        if d:
                            verdict = d == "pass"
                            break
                    if verdict is None:
                        verdict = seen >= need
                    self.assertEqual(verdict, full, (repeat, rate, bits))

    def test_fewer_calls_same_verdict(self):
        root = self.project(cases_yaml(2))
        url, h = self.server("good")
        base = ["--root", root, "run", "--prompt", "s", "--version", "v1",
                "--base-url", url, "--repeat", "5", "--min-pass-rate",
                "60%", "--no-cache"]
        code, _, _ = run_cli(base + ["--early-stop"])
        self.assertEqual(code, 0)
        self.assertEqual(len(h.calls), 2 * 3)           # 3 of 5 settles it
        h.calls.clear()
        run_cli(base)
        self.assertEqual(len(h.calls), 2 * 5)

    def test_a_certain_failure_stops_at_the_first_failed_run(self):
        root = self.project(cases_yaml(1, "contains: nope"))
        url, h = self.server("good")
        code, out, _ = run_cli(["--root", root, "run", "--prompt", "s",
                                "--version", "v1", "--base-url", url,
                                "--repeat", "5", "--early-stop",
                                "--no-cache", "--format", "json"])
        self.assertEqual(code, 1)
        self.assertEqual(len(h.calls), 1)
        case = json.loads(out)["cases"][0]
        self.assertTrue(case["stopped_early"])
        self.assertEqual((case["runs"], case["passes"]), (1, 0))
        self.assertFalse(case["flaky"])

    def test_an_all_pass_early_stop_is_not_called_flaky(self):
        result = runner.CaseResult("c", True, "", [], runs=3, passes=3,
                                   stopped_early=True)
        self.assertFalse(result.flaky)

    def test_needs_repeat(self):
        root = self.project(cases_yaml(1))
        code, _, err = run_cli(["--root", root, "run", "--prompt", "s",
                                "--version", "v1", "--early-stop"])
        self.assertEqual(code, 2)
        self.assertIn("--repeat", err)


# ------------------------------------------------------------------- --jobs

class TestJobs(Base):
    def test_parallel_cases_are_faster_and_keep_their_order(self):
        root = self.project(cases_yaml(8))
        url, h = self.server("good", delay=0.2)
        base = ["--root", root, "run", "--prompt", "s", "--version", "v1",
                "--base-url", url, "--no-cache", "--format", "json"]
        t = time.monotonic()
        code, out, _ = run_cli(base)
        serial = time.monotonic() - t
        self.assertEqual(code, 0)
        t = time.monotonic()
        code, out, _ = run_cli(base + ["--jobs", "8"])
        parallel = time.monotonic() - t
        self.assertEqual(code, 0)
        self.assertEqual([c["name"] for c in json.loads(out)["cases"]],
                         [f"c{i}" for i in range(8)])
        self.assertLess(parallel, serial / 2.5, (serial, parallel))

    def test_first_error_in_case_order_ends_the_list(self):
        root = self.project(cases_yaml(6))
        code, _, err = run_cli(["--root", root, "run", "--prompt", "s",
                                "--version", "v1", "--jobs", "4",
                                "--base-url", "http://127.0.0.1:9/v1",
                                "--timeout", "2"])
        self.assertEqual(code, 2)
        self.assertIn("error:", err)

    def test_bad_values_are_usage_errors(self):
        for bad in ("0", "65", "x", "-1", "1.5"):
            with self.assertRaises(SystemExit, msg=bad):
                run_cli(["run", "--all", "--jobs", bad])

    def test_cache_writes_from_threads_do_not_corrupt(self):
        root = self.project(cases_yaml(30))
        url, _ = self.server("good")
        base = ["--root", root, "run", "--prompt", "s", "--version", "v1",
                "--base-url", url]
        self.assertEqual(run_cli(base + ["--jobs", "16"])[0], 0)
        code, out, _ = run_cli(base)
        self.assertIn("30 cached", out)


# --------------------------------------------------------- plan and budgets

class TestPlan(Base):
    def test_plan_counts_live_cached_and_contacts_no_model(self):
        root = self.project(cases_yaml(4))
        url, h = self.server("good")
        argv = ["--root", root, "plan", "--prompt", "s", "--version", "v1",
                "--base-url", url, "--format", "json"]
        code, out, _ = run_cli(argv)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["totals"]["live_calls"], 4)
        self.assertEqual(len(h.calls), 0)
        run_cli(["--root", root, "run", "--prompt", "s", "--version", "v1",
                 "--base-url", url])
        out = run_cli(argv)[1]
        totals = json.loads(out)["totals"]
        self.assertEqual((totals["live_calls"], totals["answered"]), (0, 4))
        out = run_cli(argv + ["--repeat", "2"])[1]
        self.assertEqual(json.loads(out)["totals"]["live_calls"], 4)

    def test_max_calls_stops_before_the_first_request(self):
        root = self.project(cases_yaml(5))
        url, h = self.server("good")
        code, _, err = run_cli(["--root", root, "run", "--prompt", "s",
                                "--version", "v1", "--base-url", url,
                                "--max-calls", "4"])
        self.assertEqual(code, 2)
        self.assertIn("over budget", err)
        self.assertEqual(len(h.calls), 0)
        code, _, _ = run_cli(["--root", root, "run", "--prompt", "s",
                              "--version", "v1", "--base-url", url,
                              "--max-calls", "5"])
        self.assertEqual(code, 0)

    def test_cached_calls_do_not_count_against_the_budget(self):
        root = self.project(cases_yaml(5))
        url, h = self.server("good")
        argv = ["--root", root, "run", "--prompt", "s", "--version", "v1",
                "--base-url", url]
        run_cli(argv)
        self.assertEqual(run_cli(argv + ["--max-calls", "1"])[0], 0)

    def test_cost_needs_the_prices_and_is_arithmetic(self):
        root = self.project(cases_yaml(2), prompt="x" * 396 + "{{input}}\n")
        argv = ["--root", root, "plan", "--prompt", "s", "--version", "v1",
                "--format", "json"]
        code, _, err = run_cli(argv + ["--max-cost", "1"])
        self.assertEqual(code, 2)
        self.assertIn("--price-in", err)
        code, out, _ = run_cli(argv + ["--price-in", "2", "--price-out", "10",
                                       "--assume-output-tokens", "100"])
        self.assertEqual(code, 0)
        totals = json.loads(out)["totals"]
        tokens = totals["input_tokens"]
        self.assertEqual(totals["cost_usd"],
                         tokens / 1e6 * 2 + 2 * 100 / 1e6 * 10)
        code, _, err = run_cli(argv + ["--price-in", "2", "--price-out", "10",
                                       "--assume-output-tokens", "100",
                                       "--max-cost", "0.0000001"])
        self.assertEqual(code, 2)
        self.assertIn("estimated cost", err)

    def test_input_token_budget(self):
        root = self.project(cases_yaml(2), prompt="y" * 400 + "{{input}}\n")
        code, _, err = run_cli(["--root", root, "plan", "--prompt", "s",
                                "--version", "v1", "--max-input-tokens",
                                "100"])
        self.assertEqual(code, 2)
        self.assertIn("input tokens", err)

    def test_plan_on_a_cassette(self):
        root = self.project(cases_yaml(2))
        url, _ = self.server("good")
        run_cli(["--root", root, "run", "--prompt", "s", "--version", "v1",
                 "--base-url", url, "--record"])
        argv = ["--root", root, "plan", "--prompt", "s", "--version", "v1",
                "--replay", "--format", "json"]
        code, out, _ = run_cli(argv)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["totals"]["answered"], 2)
        with open(os.path.join(root, "prompts", "s.v1.txt"), "w") as f:
            f.write("New {{input}}\n")
        code, _, err = run_cli(argv)
        self.assertEqual(code, 2)
        self.assertIn("no recording", err)

    def test_parsers(self):
        for good in ("1", "2.5"):
            self.assertGreater(plan.parse_positive(good), 0)
        for bad in ("0", "-1", "nan", "inf", "x"):
            with self.assertRaises(ValueError):
                plan.parse_positive(bad)
        for bad in ("0", "1.5", "x", "-3", "²"):
            with self.assertRaises(ValueError):
                plan.parse_count(bad)


# --------------------------------------------------------------- tags / git

TAGGED = """cases:
  - name: a
    tags: [smoke, billing]
    input: hi
    assert:
      - contains: good
  - name: b
    tags: billing
    input: hi
    assert:
      - contains: good
  - name: c
    input: hi
    assert:
      - contains: good
"""


class TestTags(Base):
    def test_tag_filters_cases(self):
        root = self.project(TAGGED)
        url, h = self.server("good")
        base = ["--root", root, "run", "--prompt", "s", "--version", "v1",
                "--base-url", url, "--format", "json"]
        names = lambda out: [c["name"] for c in json.loads(out)["cases"]]
        self.assertEqual(names(run_cli(base + ["--tag", "smoke"])[1]), ["a"])
        self.assertEqual(names(run_cli(base + ["--tag", "billing"])[1]),
                         ["a", "b"])
        self.assertEqual(names(run_cli(base + ["--tag", "smoke", "--tag",
                                               "billing"])[1]), ["a", "b"])
        self.assertEqual(names(run_cli(base)[1]), ["a", "b", "c"])

    def test_a_tag_nothing_has_is_exit_2_not_a_green_empty_run(self):
        root = self.project(TAGGED)
        code, _, err = run_cli(["--root", root, "run", "--prompt", "s",
                                "--version", "v1", "--tag", "nope"])
        self.assertEqual(code, 2)
        self.assertIn("no case has the tag", err)

    def test_working_form_has_tags_only_when_declared(self):
        root = self.project(TAGGED)
        cases = suite.load_cases(root, "s")
        self.assertEqual(cases[0]["tags"], ["smoke", "billing"])
        self.assertEqual(cases[1]["tags"], ["billing"])
        self.assertNotIn("tags", cases[2])

    def test_bad_tags_are_refused(self):
        for bad in ("[a b]", "[true]", "[\"x y\"]", "[\"\"]"):
            root = self.project("cases:\n  - input: x\n    tags: " + bad
                                + "\n    assert:\n      - contains: y\n")
            code, _, err = run_cli(["--root", root, "list"])
            self.assertEqual(code, 2, bad)
            self.assertIn("tags", err)


def git(root, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t.invalid")
    subprocess.run(["git", "-C", root, *args], check=True,
                   capture_output=True, env=env)


class TestChangedSince(Base):
    def repo(self):
        root = self.project(cases_yaml(1), versions=("v1", "v2"),
                            prompt={"v1": "A {{input}}\n", "v2": "B {{input}}\n"})
        other = os.path.join(root, "prompts", "t.v1.txt")
        with open(other, "w") as f:
            f.write("T {{input}}\n")
        with open(os.path.join(root, "prompts", "t.cases.yaml"), "w") as f:
            f.write(cases_yaml(1))
        git(root, "init", "-q")
        git(root, "add", "-A")
        git(root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "x")
        return root

    def test_selection_by_file(self):
        t = [("s", "v1"), ("s", "v2"), ("t", "v1")]
        self.assertEqual(changed.select(t, ["prompts/s.v2.txt"]),
                         [("s", "v2")])
        self.assertEqual(changed.select(t, ["prompts/t.cases.yaml"]),
                         [("t", "v1")])
        self.assertEqual(changed.select(t, ["prompts/s.cassette.json"]),
                         [("s", "v1"), ("s", "v2")])
        self.assertEqual(changed.select(t, ["README.md", "prompts/x.md"]), [])

    def test_runs_only_what_changed(self):
        root = self.repo()
        url, h = self.server("good")
        argv = ["--root", root, "run", "--all", "--base-url", url,
                "--changed-since", "HEAD", "--no-cache"]
        code, out, _ = run_cli(argv)
        self.assertEqual(code, 0)
        self.assertIn("nothing to run", out)
        self.assertEqual(len(h.calls), 0)
        with open(os.path.join(root, "prompts", "s.v2.txt"), "w") as f:
            f.write("B2 {{input}}\n")
        code, out, _ = run_cli(argv)
        self.assertEqual(code, 0)
        self.assertEqual(len(h.calls), 1)
        self.assertIn("s.v2", out)
        self.assertNotIn("t.v1", out)
        with open(os.path.join(root, "prompts", "t.v1.txt"), "w") as f:
            f.write("T2 {{input}}\n")        # an uncommitted edit counts
        with open(os.path.join(root, "prompts", "u.v1.txt"), "w") as f:
            f.write("U {{input}}\n")         # as does a new, untracked file
        with open(os.path.join(root, "prompts", "u.cases.yaml"), "w") as f:
            f.write(cases_yaml(1))
        h.calls.clear()
        run_cli(argv)
        self.assertEqual(len(h.calls), 3)

    def test_a_bad_ref_or_a_non_repo_is_exit_2(self):
        root = self.repo()
        for ref in ("no-such-ref", "--output=x"):
            code, _, err = run_cli(["--root", root, "run", "--all",
                                    "--changed-since=" + ref])
            self.assertEqual(code, 2, ref)
            self.assertIn("error:", err)
        plain = self.project(cases_yaml(1))
        code, _, err = run_cli(["--root", plain, "run", "--all",
                                "--changed-since", "HEAD"])
        self.assertEqual(code, 2)

    def test_needs_all(self):
        root = self.repo()
        code, _, err = run_cli(["--root", root, "run", "--prompt", "s",
                                "--version", "v1", "--changed-since", "HEAD"])
        self.assertEqual(code, 2)


# ------------------------------------------------------------------ reports

class TestReports(Base):
    def results(self, output, name="c|1"):
        a = runner.AssertionResult({"contains": "x"}, False, "missing 'x'")
        return [("s", "v1", [runner.CaseResult(name, False, output, [a])])]

    def test_markdown_survives_hostile_text(self):
        evil = "```\n# FAKE\n" + "\x1b[2K" + "\r3/3 passed"
        md = report.markdown(self.results(evil))
        self.assertNotIn("\x1b", md)
        self.assertNotIn("\r", md)
        self.assertIn("````", md)               # longer fence than the text
        self.assertIn("c\\|1", md)
        self.assertIn("FAILED", md)

    def test_markdown_is_cut_under_the_comment_limit(self):
        many = [("s", "v1", [runner.CaseResult(f"c{i}", False, "o" * 300,
                [runner.AssertionResult({"contains": "x"}, False, "d" * 200)])
                for i in range(2000)])]
        md = report.markdown(many)
        self.assertLess(len(md), 65536)
        self.assertIn("report cut", md)

    def test_annotations_cannot_inject_commands(self):
        suites = self.results("o")
        suites[0][2][0].assertions[0].detail = "bad\n::error::forged%"
        lines = report.annotations(suites, lambda n: "prompts/a,b:c.yaml")
        self.assertEqual(len(lines), 1)
        self.assertNotIn("\n", lines[0])
        self.assertIn("%0A", lines[0])
        self.assertIn("%25", lines[0])
        self.assertIn("%2C", lines[0])
        self.assertIn("%3A", lines[0])

    def test_cli_writes_markdown_and_annotations(self):
        root = self.project(cases_yaml(1, "contains: nope"))
        url, _ = self.server("good")
        md = os.path.join(root, "out.md")
        code, out, _ = run_cli(["--root", root, "run", "--prompt", "s",
                                "--version", "v1", "--base-url", url,
                                "--markdown", md, "--github-annotations"])
        self.assertEqual(code, 1)
        self.assertIn("::error ", out)
        self.assertIn("FAILED", open(md).read())
        code, _, err = run_cli(["--root", root, "run", "--prompt", "s",
                                "--version", "v1", "--base-url", url,
                                "--github-annotations", "--format", "json"])
        self.assertEqual(code, 2)

    def test_unwritable_markdown_is_exit_2(self):
        root = self.project(cases_yaml(1))
        url, _ = self.server("good")
        code, _, err = run_cli(["--root", root, "run", "--prompt", "s",
                                "--version", "v1", "--base-url", url,
                                "--markdown", os.path.join(root, "no", "x")])
        self.assertEqual(code, 2)
        self.assertIn("Markdown", err)


class TestScanHelpers(unittest.TestCase):
    def test_luhn(self):
        self.assertTrue(scan.luhn("79927398713"))
        self.assertFalse(scan.luhn("79927398710"))

    def test_random_text_never_crashes_the_scanners(self):
        rng = random.Random(7)
        alphabet = "ab 1234-.@:/_\u200b\x00\U0001F600{}"
        for _ in range(300):
            text = "".join(rng.choice(alphabet) for _ in range(200))
            scan.find_secrets(text)
            scan.find_pii(text, scan.PII_KINDS)
            scan.find_hidden(text)
            scan.find_urls(text)
            scan.find_prompt_leak(text, text, 3)


if __name__ == "__main__":
    unittest.main()
