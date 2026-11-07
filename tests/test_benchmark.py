"""The committed benchmark (benchmarks/bench.py) still gives the numbers in
benchmarks/results.json. Offline and deterministic apart from wall-clock
timings, which are not compared."""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "benchmarks")


class TestBenchmark(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, BENCH)
        import bench
        cls.bench = bench
        cls.res = bench.collect()

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(BENCH)

    def test_matches_the_committed_results(self):
        with open(os.path.join(BENCH, "results.json")) as f:
            committed = json.load(f)
        self.assertEqual(self.bench.deterministic(self.res),
                         self.bench.deterministic(committed))

    def test_the_claims_the_readme_makes(self):
        d, f, c = (self.res[k] for k in ("detection", "flakiness", "cost"))
        self.assertEqual(d["security"]["caught"], d["security"]["of"])
        self.assertGreater(d["security"]["caught"], d["vocabulary"]["caught"])
        for kind in self.bench.SUITES:
            self.assertEqual(d[kind]["false_alarms"], 0, kind)
        rows = {r["gate"]: r for r in f["rows"]}
        self.assertLess(rows["5 runs, 60%, --early-stop"]["calls_per_run"],
                        rows["5 runs, 60% must pass"]["calls_per_run"])
        self.assertEqual(
            rows["5 runs, 60%, --early-stop"]["false_alarm_rate"],
            rows["5 runs, 60% must pass"]["false_alarm_rate"],
            "early stop must not change the verdicts")
        self.assertEqual(c["calls_in_ci_with_committed_cassette"], 0)
        self.assertEqual(
            c["calls_after_editing_one_assertion"]["0.4.0 key (what is sent)"],
            0)
        self.assertTrue(c["replay_reproduces_recorded_run_exactly"])

    def test_the_reply_shape_claims(self):
        r = self.res["reply_shapes"]
        ln, js, fj = r["links"], r["json"], r["fenced_json"]
        self.assertEqual(ln["now_caught"], ln["hostile"])
        self.assertLess(ln["before_caught"], ln["now_caught"])
        self.assertEqual(ln["now_false_alarms"], 0)
        self.assertEqual(js["now_wrongly_accepted"], 0)
        self.assertEqual(js["now_wrongly_rejected"], 0)
        self.assertEqual(js["before_wrongly_accepted"], js["unsafe"])
        self.assertEqual(fj["contains_json_wrongly_rejected"], 0)
        self.assertEqual(fj["contains_json_wrongly_accepted"], 0)
        self.assertGreater(fj["schema_wrongly_rejected"], 0)


if __name__ == "__main__":
    unittest.main()
