# Changelog

## 0.4.0 (unreleased)

### Expert round 5 (specialists: prompt engineer, LLM-eval engineer, QA lead, CI integrator; on top of round 4, below)
Tests 495 -> 521. Review notes with the command behind each finding: `docs/ROUND5.md`. No version bump: 0.4.0 is
still unreleased (PyPI has 0.2.0).
- Author line: "Calas Automations Inc." in `pyproject.toml`, `LICENSE`, the README
  licence line, `prompt_assert.__author__` and `action.yml`; no email is
  declared (none existed). A test pins it and the built wheel's `METADATA`
  was checked (see ROUND5.md).
- Fix: the cases-file reader removed a no-break space (and other Unicode
  spaces) from the edges of an unquoted value or a `[a, b]` list item, so
  `input: ok<no-break space>` silently sent `ok`; a line holding only such a
  space was skipped as blank. (Quoted values were already kept.) YAML's blanks
  are the space and the tab, and now only those are trimmed; a line of only a
  no-break space is a line-numbered error. Found by comparing the reader with
  PyYAML on 4,000 random documents.
- New assertion `is_xml: true|false`: one well-formed XML document, nothing
  else; a `<!DOCTYPE` or `<!ENTITY` declaration fails (entity expansion).
- New `action.yml`: a composite GitHub Action (`uses: calasdan-sketch/prompt-assert@<tag>`)
  that installs a pinned release, runs your command, and adds annotations and
  the job summary. Inputs reach the shell as environment variables; the
  version must look like a version. Not run on GitHub from here.
- Tests: golden files (`tests/golden/`) for the human, JSON, JUnit, Markdown,
  annotation, `list`, `plan --all`, `diff` and corpus outputs; seeded property
  tests over every assertion; a PyYAML cross-check (runs only if PyYAML is
  installed).
- Changed behaviour: unquoted values with a leading or trailing no-break
  space (or other non-ASCII space) in a cases file now keep it; before, it was
  dropped.
- Not done: see the idea table in `docs/ROUND5.md`.

### Expert round 4 (what a stranger gets from `pip install`; on top of rounds 3 and 2, below)
Tests 471 -> 495. Review notes with the command behind each finding: `docs/ROUND4.md`. No
version bump: 0.4.0 is still unreleased (PyPI has 0.2.0), so this is one
release.
- `prompt_assert.__version__` exists and is the single place the version is
  written (`--version` reads it); a test keeps it equal to `pyproject.toml`
  and to this file's heading.
- Package metadata names the author (Calas Automations Inc., the licence
  holder). The source distribution now carries `benchmarks/` so its own
  test run is complete (before, the benchmark test skipped there).
- A model address must be `http://` or `https://` with a host. `file://`
  and `ftp://` used to be handed to urllib, which opened them; they are now
  exit 2 with a plain message. An address with `user:pass@` is refused (the
  password would have been printed in "model unreachable" lines), as are
  addresses with spaces, control or invisible characters. Checked before the
  first request, so `--replay` is unaffected.
- A prompt file that is not UTF-8 is exit 2 naming the file ("not UTF-8
  text ... save the file as UTF-8"); it used to print Python's codec text
  without a file name.
- Typing `prompt-assert` with no command prints three lines on what to try
  first (exit 2) instead of argparse's "arguments are required: cmd".
- `list --format json` (scripts could not read `list`).
- Windows (static review plus tests with the operating system faked; not
  run on Windows): prompt and version names that Windows would misread
  (`nul`, `con`, `a:b`, a trailing dot or space) are refused there with a
  clear message; the cassette, JUnit and Markdown writers retry a few times
  when Windows briefly refuses to replace a file another program has open;
  `init` writes LF line endings on every system.
- CI: a `package` job builds the wheel and sdist, runs `twine check`,
  installs the wheel into a clean environment from a folder with a space in
  its name, runs the quick start, and runs the sdist's own tests. The example
  workflow pins `prompt-assert==0.4.0` instead of installing the repository's
  newest commit (it works once 0.4.0 is on PyPI).
- Fix: the command CI and the README gave for the tests, `python -m unittest
  discover -s tests`, could not import `test_expert2` and `test_expert3`
  (relative imports), so it reported 396 tests and two import errors and
  CI's test step could not have passed. It is now `... -s tests -t .`
  (`pytest` was always fine); a test keeps the three places right.
- README: install section no longer promises what the registry lacks.
- Changed behaviour: (1) `file://`, `ftp://`, `user:pass@host` and
  whitespace-inside addresses are now refused; (2) on Windows only, the
  names above are refused; (3) no command is exit 2 as before but the
  message differs.
- Not done: `py.typed` (mypy reports 29 errors; shipping the marker would
  claim typing that is not there), a JSON error object on stdout for exit 2,
  SARIF, a GitHub Action wrapper, pinning CI actions by commit hash.

### Expert round 3 (on top of round 2, below)
Tests 425 -> 471; two earlier tests edited and one timing bound loosened (see
"Changed behaviour").
- JSON: `json_valid` and `schema` refuse `NaN`, `Infinity`, numbers that
  overflow, and objects with a repeated name (Python's parser accepted all).
- `schema` now enforces `additionalProperties`, `minimum`, `maximum`,
  `exclusiveMinimum`, `exclusiveMaximum`, `minLength`, `maxLength`, `pattern`,
  `minItems`, `maxItems`, `uniqueItems`, `const`, `anyOf`, `oneOf`, `allOf`;
  other keywords are still refused. A schema may be pasted as JSON text.
  Checking stops after 20 problems, so a huge wrong reply is fast.
- New assertions: `contains_json` (JSON in a fence or in prose, optional
  schema), `not` (turns any deterministic assertion around), `not_equals`,
  `max_chars`, `min_chars`, `max_words`, `min_words`, `max_lines`.
- System prompts and conversations: a prompt file starting with `[[system]]`,
  `[[user]]` or `[[assistant]]` is sent as chat messages. The turns are cut
  before input is filled in, so an input cannot forge a turn. Cassettes, the
  cache, `plan` and `no_prompt_leak` follow the conversation.
- `no_urls` also finds protocol-relative links (`//host/x.png`) and
  `javascript:`, `vbscript:` and `data:` URIs (no allow-list can pass them).
- A `regex`, `not_regex` or schema `pattern` that repeats a repeat without a
  limit (`(a+)+$`, `(\w+\s?)*`) is refused when the file is read: it can hang
  a run for hours and Python cannot interrupt it. Polynomial patterns are
  not detected (documented).
- Markdown report: case names, assertion details and replies are escaped so a
  reply or a pull request's case file cannot put links, images or HTML into
  the summary.
- `--record` prints how many answers are new, changed or unchanged against
  the file it replaces, and warns about personal data in recorded replies.
- Prompt and version names must be plain names (`--prompt ../x` is exit 2).
- Fixes: a byte-order mark in a prompt file was sent to the model (and made
  the cassette key differ); a long assertion is cut in the failure report;
  `--changed-since` reads git's output as UTF-8; the YAML flow-map error now
  says how to write a schema.
- Benchmark panel 4: labelled replies judged before and after (links, JSON,
  fenced JSON). `results.json` and `RESULTS.md` regenerated; panels 1-3 are
  unchanged.
- Changed behaviour: (1) `{{name}}` is filled in one pass, so a case input
  that contains `{{tone}}` is no longer rewritten by the case's `vars`;
  (2) values `json_valid` accepted before (NaN, duplicate keys) now fail;
  (3) a nested-repeat regex that used to load is now exit 2; (4) a failing
  summary line in the Markdown report shows a case name without table
  escaping (it is HTML-escaped instead). Edited tests:
  `test_round5_fixes.py` (keywords that are now enforced moved off the
  "refused" list), `test_expert2.py` (the Markdown pipe check; the `--jobs`
  speed bound 2.5x -> 2x, it failed on a busy machine).
- Not done: a refusal assertion, XML/HTML/SQL checks, a model-driven
  multi-turn run, tool calls, SARIF, `$ref`/`format` in schemas, a GitHub
  Action wrapper, a schema read from a file.

### Expert round 2
All 366 earlier tests pass; 2 were edited (below).
- Cassettes: `run --record` writes `prompts/<name>.cassette.json`,
  `run --replay` answers every call from it and contacts no model (needs no
  URL or key); a missing recording, a missing or damaged cassette is exit 2.
  Sorted, ASCII-escaped JSON, atomic write, 64 MiB cap; judge verdicts are
  recorded; `--record` warns about secret-shaped replies.
- Response cache key is now the text sent + model + endpoint, not the whole
  case: editing an assertion, tag or case name no longer re-runs the model.
  (Behaviour change; the cache is disposable.)
- Security assertions: `no_pii` (email, phone, card and Canadian SIN with
  Luhn, US SSN, optional IP), `no_secrets`, `no_hidden_chars`
  (`emoji_ok`), `no_urls` (all, or an allow-list of domains),
  `no_prompt_leak` (N-word runs of the prompt text, placeholders cut out).
  Details never echo the matched value. `asserts.evaluate()` takes an
  optional `template=`.
- `prompt-assert corpus injection`: 25 original prompt-injection and leakage
  cases (`--canary`, `--allow-domain`), tagged, plain ASCII.
- `plan` (calls, cached/recorded, estimated input tokens; optional cost from
  user-supplied prices) and budgets `--max-calls`, `--max-input-tokens`,
  `--max-cost` that stop `run`/`diff` with exit 2 before the first request.
- `--jobs N` (1-64) runs cases in parallel, results in case order.
- `--early-stop` with `--repeat`: stop a case once its pass/fail is settled
  (exact, not statistical). JSON adds `stopped_early`; `runs` is the number
  made.
- `tags:` on cases, `--tag` (repeatable); a tag nothing has is exit 2.
- `run --all --changed-since REF` and `plan --changed-since REF`: only
  suites whose prompt version, cases file or cassette changed in git.
- `--markdown PATH` and `--github-annotations` reports; text from a model is
  cleaned, annotations escape `%`, CR, LF, `:` and `,`.
- `benchmarks/bench.py`: offline benchmark against a simulated model with a
  committed `results.json` / `RESULTS.md`, checked by a test.
- Tests: 366 -> 425 passed (`tests/test_expert2.py`, `tests/test_benchmark.py`).
  Edited: `tests/test_round5_fixes.py` used `tags` as its example of an
  unknown case key (it is now a key; uses `notes`);
  `tests/test_packaging.py` version.
- Not done: multi-turn and tool-call assertions, SARIF, a GitHub Action
  wrapper, prompt-diff impact analysis (see README Limitations).

## 0.3.0 (unreleased)
- `--repeat N` (run and diff): run each case N times; `--min-pass-rate RATE`
  (`0.8` or `80%`, default every run) says how many must pass. Cases that
  pass the gate but failed some runs are listed as `flaky:`; `--format json`
  adds `runs`, `passes` and `flaky` to repeated cases. Each run has its own
  cache entry; run 1 shares the entry of a plain run.
- `run --junit PATH` writes a JUnit XML report (one testsuite per prompt
  version, failures with assertion details and output). Written atomically;
  a report that cannot be written is exit 2.
- `diff --fail-on-regression` exits 1 when a case goes pass -> FAIL (never
  for a FAIL -> pass fix). Without the flag `diff` still always exits 0.
- New assertions: `equals` (whole reply, outer whitespace ignored),
  `icontains` (case-insensitive), `contains_any` and `contains_all` (a list
  of 1-100 non-empty texts).
- Security: everything printed to the terminal or CI log is filtered, so a
  model reply, a judge's reasoning, a server error or a case name can no
  longer inject ANSI escapes, carriage returns, bidi overrides or zero-width
  characters (shown as visible escapes instead). Previously the judge's
  reasoning and server error messages were printed raw. `--format json` is
  unchanged.
- Each case result now records its wall-clock time (used by the JUnit
  report).
- Project URLs now point at github.com/calasdan-sketch/prompt-assert.
- Panel review fixes:
  - A repeated case that passed the gate now shows a passing run (it showed
    the first failing run, so JSON had `passed: true` next to a failed
    assertion); a case that failed shows its first failing run.
  - `flaky` now means only "passed the gate although some runs failed", in
    JSON as in the terminal (JSON also set it for cases that failed).
  - `--min-pass-rate` below 1 without `--repeat` is a usage error (exit 2);
    it used to be ignored without a word.
  - The output filter also escapes variation selectors (U+FE00-FE0F,
    U+E0100-E01EF, Mongolian U+180B-180D, U+180F) and Hangul fillers
    (U+115F, U+1160, U+3164, U+FFA0). An emoji with a presentation
    selector now shows `\ufe0f` after it.
  - `--repeat` takes ASCII digits only (`²` gave a raw Python error).
  - JUnit: a CaseResult carrying a model error (Python API) is written as
    an `<error>` and counted in `errors`; the CLI writes no report when a
    model error stops the run (documented).
  - README Windows-path example uses a neutral user name.

## 0.2.0
- Renamed: the project is now **prompt-assert** on PyPI (formerly promptregress; PyPI rejected that name as too similar to an existing project). Import package is `prompt_assert`; the primary command is `prompt-assert`.
- The old `promptregress` command is kept as a second console script alias.
- Environment variables are now `PROMPT_ASSERT_*` (BASE_URL, API_KEY, MODEL, TIMEOUT, MAX_REPLY_BYTES). The old `PROMPTREGRESS_*` names still work as a fallback when the new one is unset.
- The response cache folder is now `.prompt-assert/` (was `.promptregress/`). The cache is disposable and is rebuilt on the next run; the old folder can be deleted.
- Example workflow renamed to `examples/prompt-assert-example.yml`.
- The Python import `promptregress` and `python -m promptregress` no longer exist: use `prompt_assert` / `python -m prompt_assert`.
- GitHub URLs are unchanged (the repo is still named promptregress).

## 0.1.4
- Lint cleanups only (import order, ClassVar on the demo handler, parenthesised string pair). No behaviour change.
- .gitignore now covers virtualenvs, .ruff_cache and the local response cache.
- Added this changelog.
