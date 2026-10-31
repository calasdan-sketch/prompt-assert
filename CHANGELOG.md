# Changelog

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
