# Benchmark results

Simulated model, real prompt-assert code. Read the header of `bench.py` before quoting any number.

## 1. Regression detection (25 harmful and 19 harmless prompt edits)

| assertion suite | assertions per case | harmful edits caught | harmless edits flagged |
|---|---:|---:|---:|
| `contains: <label>` only (a floor, not a recommendation) | 1.0 | 0/25 (0%) | 0/19 |
| `equals` + `max_tokens` + canary check | 2.7 | 21/25 (84%) | 0/19 |
| 0.3.0 vocabulary, hand-written regexes for each risk | 8.7 | 23/25 (92%) | 0/19 |
| 0.4.0 security assertions | 7.7 | 25/25 (100%) | 0/19 |

Missed by the hand-written vocabulary suite: paraphrase intro, drop reveal [leak]; rewrite intro, soften reveal [leak].

## 2. Flakiness (model fails 4% of calls at random; 300 simulated days)

Regression: drops the 'never reveal' rule (the leak case then fails every call).

| gate | false alarms on a good prompt | regression detected | calls per run |
|---|---:|---:|---:|
| 1 run | 27.7% | 100.0% | 12.0 |
| 5 runs, all must pass | 82.7% | 100.0% | 60.0 |
| 5 runs, 60% must pass | 0.7% | 100.0% | 60.0 |
| 5 runs, 60%, --early-stop | 0.7% | 100.0% | 37.2 |
| 5 runs, 80% | 12.3% | 100.0% | 60.0 |
| 5 runs, 80%, --early-stop | 12.3% | 100.0% | 49.4 |

## 3. Cost (200-case suite)

| | calls |
|---|---:|
| after editing one assertion, 0.3.0 cache key | 200 |
| after editing one assertion, 0.4.0 cache key | 0 |
| CI run with a committed cassette | 0 |

Replay reproduces the recorded run exactly: True.

Wall clock with a simulated 10 ms per call (this machine; varies): `--jobs 1` 2.06 s, `--jobs 8` 0.26 s (7.9x).
