# Changelog

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
