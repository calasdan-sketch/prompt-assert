# Round 5: specialists who use it (author line, YAML fidelity, XML, Action)

Question for this round: what do the people who would actually put
prompt-assert into their daily work trip over? Rounds 1 to 4 covered the
assertions, hostile input and the first hour after `pip install`.

## How the review was done (honest version)

One agent played every role below in turn and ran real commands for each. No
independent sub-agents or humans were available, so this is one reviewer in
different hats, not independent confirmation. Every finding names its command.

## Baseline (before any change)

| check | result |
|---|---|
| `pytest -q` | 495 passed, 2 skipped, 406 subtests, 269 s |
| `python benchmarks/bench.py` | tables identical to committed `RESULTS.md` |
| `pip index versions prompt-assert` (read-only, 2026-11-08) | registry has 0.2.0 only |

## Author line (done first)

Already "Calas Automations Inc." in `pyproject.toml` `authors` (name only,
no email: none existed) and `LICENSE`. Added: `prompt_assert.__author__`, the
README licence line, `author:` in `action.yml`. No "maintainers" field, no
`package.json` (Python only), no SARIF output exists, the JUnit file carries
no author (the format has no place for one, only `name="prompt-assert"`). A
grep of the tree (tests, docs, bench, CI) for `DC Enterprises`, `Dan Calas`,
and the old handle used as a label found none; the `calasdan-sketch` hits are
real GitHub URLs and were left alone. Pinned by `TestAuthorLine`.

Proof from the freshly built wheel and sdist:
`python -m build -o <scratch>/dist2 .` then
`unzip -p dist2/*.whl '*/METADATA'` gives `Author: Calas Automations Inc.`;
`python -m twine check` PASSED for both files; in a clean non-editable venv
`python -c "import prompt_assert; print(prompt_assert.__author__)"` prints
`Calas Automations Inc.`.

URLs: whether the GitHub repository is public could not be checked from this
session (access to it is not enabled here). The package metadata and README
still carry `https://github.com/calasdan-sketch/prompt-assert` as in earlier
rounds. If the repository is private, delete the three `[project.urls]` lines
and the `pip install git+https://...` README line before publishing.

## Specialists and what each found

1. **LLM-eval engineer (promptfoo / deepeval user), YAML fidelity.**
   Wrote a PyYAML cross-check: 4,000 random case files dumped by PyYAML in six
   styles, read by `miniyaml`, compared (`python explore.py` in scratch; now
   `test_agrees_with_pyyaml_whenever_both_accept`). 1,174 accepted, 2,826
   refused (by design), 0 crashes, **5 mismatches**: `str.strip()` removed a
   no-break space from unquoted values (`input: x<nbsp>` sent `x`) and from
   `[a, b]` items, and a line of only a no-break space was skipped. Fixed:
   only space and tab are trimmed; the lone-space line is a line-numbered
   error. After the fix: 0 mismatches.
2. **Upgrade tester (0.2.0 -> this tree).** `pip download
   prompt-assert==0.2.0 --no-deps`, installed in its own venv, ran `init`,
   `--demo run`, then `run --all` against a local fake OpenAI-style server
   (6 calls, 6/6 pass). Then the new wheel over the same project folder:
   same verdicts 6/6, 6 new calls (the cache key changed in 0.4.0, so old
   cache files are ignored, not misread), both sets of cache files coexist
   (12 files), no crash, no migration needed. The 0.2.0 cases file (`init`'s
   example) loads unchanged. Differential test of the assertions both
   versions have (`contains`, `not_contains`, `regex`, `not_regex`,
   `max_tokens`, `min_tokens`, `json_valid`) on 20,000 random replies:
   0 verdict differences (only where 0.4.0 is deliberately stricter, see
   round 3, would they differ; none turned up with this generator).
3. **QA lead, mutation testing.** Copy of the tree in scratch; 43 mutants
   (flipped `==`, `!=`, `<=`, `and`, `or`, `in` on lines in `asserts.py` and
   `miniyaml.py`) run against the fast test files then the rest: **43 of 43
   killed** (13 before the new tests existed, 30 after). The hand-made
   script is not shipped (`mutate.py` in scratch). Limit: only two modules,
   only comparison flips.
4. **QA lead, golden files.** No golden test existed for any output format.
   Added `tests/golden/` with 11 files: human pass and fail, JSON,
   JUnit, Markdown, `--github-annotations`, `list` (text and JSON), `plan
   --all`, `diff`, `corpus injection`. Times are normalised; regenerate with
   `PROMPT_ASSERT_UPDATE_GOLDEN=1`. They ship in the sdist.
5. **Prompt engineer.** Prompts that ask a model for `<answer>...</answer>`
   tags had no format check short of a regex. Added `is_xml: true|false`
   (well-formed, one document; `<!DOCTYPE`/`<!ENTITY` refused so entity
   bombs cannot expand). Checked: 20 MB single element 0.35 s, 3 million
   child elements 3.9 s under load, 50,000-deep nesting returns an answer,
   not a crash. Noticed, not changed: failures print the assertion as a
   Python dict (`{'max_tokens': 10}`); a friendlier `max_tokens: 10` would be
   nicer but eight tests, the README and any script that parses the line
   depend on it (idea 14).
6. **CI integrator.** No GitHub Action existed (promptfoo documents one at
   `/docs/integrations/github-action/`; page exists, content not read). Added
   `action.yml`, a composite action: pinned `version` input is required, the
   version must match digits and dots, inputs reach the script only as
   environment variables (a test fails if `${{` appears in the script text),
   `plan *` is not globbed, annotations and the job summary are added to a
   `run` command unless the command chose its own output flags. The script
   was run with a stand-in `python` for good and hostile values
   (`0.4.0; id`, `$(id)`, `1..2`, empty). Not run on GitHub.
7. **Competitor's maintainer (read-only).** 2026-11-08: `npm view promptfoo
   version` 0.125.1, `pip index versions deepeval` 4.3.3 and `inspect-ai`
   0.3.288, same as the README table's measurements. promptfoo's
   expected-outputs page still lists `contains`, `contains-all/any`,
   `contains-json`, `equals`, `icontains`, `is-json`, `levenshtein`,
   `regex`, `starts-with`, `similar`, `factuality`, `llm-rubric` ...
   (16 names found by a crude page scrape; the page has more behind tabs, so
   treat as a floor). README count updated 27 -> 28 assertion kinds.
8. **Release / packaging check.** Fresh wheel + sdist: `twine check` PASSED;
   the sdist contains `action.yml` and `tests/golden/`; wheel has 24 files;
   metadata above. Clean non-editable venv in a folder with a space: `--version`
   0.4.0, `init`, `--demo run` (3/3, v2 exit 1), `diff`, a cases file with
   `is_xml` against the local fake server, `plan --all`: all as the README
   says.
9. **Debug sweep.** `ruff check --select E,F,W` clean on the new code;
   `LC_ALL=C PYTHONIOENCODING=ascii` demo run: no traceback; hostile and
   huge inputs: see item 5 and the 6,000-mutation fuzz of round 4 (not
   repeated). Windows: static only, never run.

## Pass B: attacking the result

Re-read the diff adversarially. Checked: `_WS` trimming left the tab-in-indent
check intact (`test_miniyaml`, round 4 and the 4,000-document cross-check
still pass); `is_xml` inside `not` and in `check()` (operand must be a bool);
`xml_problem` catches `RecursionError`; the action's `case` pattern rejects
`.1`, `1.`, `1..2`, empty, any letter. Found and fixed during Pass B: the
CHANGELOG first claimed quoted values lost their no-break space (they did
not, only unquoted ones and list items); the sdist test for the author line
read `PUBLISH-CHECKLIST.md`, which the sdist does not contain (now skipped
when absent). Final numbers: 521 passed, 2 skipped, 408 subtests (495 ->
521, 26 new tests); benchmark tables identical to the committed
`RESULTS.md`.

## Scored ideas

Scores 1 (low) to 5 (high). Value, Diff. (differentiation), effort, risk: 1 is
cheap or safe.

| # | idea | value | diff. | effort | risk | status |
|---|---|---|---|---|---|---|
| 1 | Author line everywhere + pinning test | 3 | 1 | 1 | 1 | DONE |
| 2 | Unquoted NBSP/Unicode-space fidelity in the cases reader | 4 | 2 | 1 | 1 | DONE |
| 3 | PyYAML cross-check test (optional dependency) | 4 | 3 | 2 | 1 | DONE |
| 4 | Seeded round-trip property test of JSON-quoted scalars | 3 | 2 | 1 | 1 | DONE |
| 5 | `is_xml` assertion (entity-safe) | 4 | 2 | 2 | 2 | DONE |
| 6 | Composite GitHub Action `action.yml` | 4 | 3 | 3 | 3 | DONE (not run on GitHub) |
| 7 | Golden files for every output format | 4 | 2 | 2 | 1 | DONE |
| 8 | Seeded property tests across assertions | 3 | 2 | 2 | 1 | DONE |
| 9 | Mutation sanity run (43 mutants, 2 modules) | 3 | 3 | 2 | 1 | DONE (script not shipped) |
| 10 | Upgrade test from the registry version | 4 | 1 | 1 | 1 | DONE |
| 11 | Old-vs-new assertion differential (20,000 replies) | 3 | 2 | 1 | 1 | DONE |
| 12 | sdist ships the action and golden files | 2 | 1 | 1 | 1 | DONE |
| 13 | JSON error object on stdout for exit 2 with `--format json` | 3 | 3 | 2 | 3 | NOT-DONE (changes the stdout contract; Dan decides) |
| 14 | Print assertions as `max_tokens: 10`, not a Python dict | 3 | 2 | 2 | 3 | NOT-DONE (eight tests and any parsing script depend on it) |
| 15 | `is_refusal` assertion | 3 | 3 | 3 | 4 | NOT-DONE (phrase lists give false confidence; judge it) |
| 16 | `is_html` / `is_sql` | 2 | 1 | 3 | 3 | NOT-DONE |
| 17 | `max_seconds` latency assertion | 3 | 2 | 3 | 3 | NOT-DONE (timing makes flaky tests) |
| 18 | SARIF output | 2 | 3 | 3 | 2 | NOT-DONE (prompts have no source lines to point at) |
| 19 | `py.typed` + mypy in CI | 3 | 1 | 4 | 2 | NOT-DONE (29 mypy errors, round 4) |
| 20 | Trusted Publishing workflow | 4 | 1 | 3 | 4 | NOT-DONE (publishing off limits; Dan decides) |
| 21 | Pin CI actions by commit hash | 3 | 1 | 2 | 2 | NOT-DONE (needs network lookups; use Dependabot) |
| 22 | Schema read from a file (`schema_file:`) | 2 | 2 | 2 | 1 | NOT-DONE |
| 23 | `--fail-fast` (stop at first failing case) | 2 | 1 | 2 | 2 | NOT-DONE |
| 24 | Faster suite (240 s; timing tests dominate) | 3 | 1 | 3 | 2 | NOT-DONE |
| 25 | JSON output schema/version field | 2 | 2 | 1 | 2 | NOT-DONE (a golden test now makes this a visible change; decide with 13) |
| 26 | CI job that runs `uses: ./` against the action | 3 | 1 | 2 | 2 | NOT-DONE (needs 0.4.0 on PyPI first) |
| 27 | Run the suite on Windows and Python 3.10 | 5 | 1 | 1 | 1 | NOT-DONE (no such machine here; CI has the jobs) |

## Could not test

- Anything on Windows (real console, file locking, `windows-latest` CI).
- `action.yml` on GitHub: only its shell script, with a stand-in `python`, and
  its structure (read with our own YAML reader). `actions/setup-python@v5` is
  pinned by tag, not by hash.
- Whether the GitHub repository is public.
- PyPI upload and `pip install prompt-assert==0.4.0` from the registry
  (0.4.0 is not there yet).
- Mutation testing beyond `asserts.py` and `miniyaml.py`.
- The competitor feature rows beyond the version numbers and one page scrape.
