# Round 4: the stranger's first hour

Question for this round: what does a person who has never seen this
repository get when they type `pip install prompt-assert` and follow the
README? Rounds 1 to 3 made the assertions, the YAML reader and the hostile
input handling solid; round 4 looked at packaging, the first run, Windows and
whether the README matches what the program prints.

## How the review was done (honest version)

One agent played every specialist role below, one after the other, and ran
real commands for each. No independent sub-agents or human reviewers were
available in this run, so the "panel" is one reviewer wearing different hats:
one careful pass, not independent confirmation. The machine was shared and
heavily loaded, so times are upper bounds.

## Baseline (before any change, `git archive HEAD` copy)

| check | result |
|---|---|
| `pytest -q` (Python 3.13) | 471 passed, 2 skipped, 358 subtests, 223 s |
| `python benchmarks/bench.py` | tables identical to the committed `RESULTS.md` (only the wall-clock line varies by run) |
| `ruff check --isolated --select E,F,W --ignore E501` | 8 findings, all in tests/benchmarks (unused imports, E402 by design, one lambda) |
| `mypy prompt_assert` (not configured in the repo) | 29 errors in 7 files |
| `python -m build`, `twine check` | both files PASSED |
| `pip index versions prompt-assert` (read-only) | only 0.2.0 is on PyPI; the repo is 0.4.0 |

## Specialists and what each found

1. **First-time user (README only, wheel in a clean venv, folder with a
   space in its name).** Built the wheel, installed it (not editable) into
   fresh 3.11 and 3.13 venvs, ran `--version`, `--help`, `init`,
   `--demo run`, `--demo diff v1 v2`, `--demo run --version v2` (exit 1),
   `list`, `plan --all`, `corpus injection`, and the old `promptregress`
   alias: all matched the README. Found: typing just `prompt-assert` printed
   argparse's "the following arguments are required: cmd" (fixed: three
   lines on what to try); `list` had no `--format json` (added); the README
   install section told people to install from a checkout without saying how
   to tell whether the registry version is old (rewritten around
   `prompt-assert --version`).
2. **Release / packaging engineer.** `unzip -l` on the wheel and `tar tzf`
   on the sdist: no junk, no personal paths, no secrets (grepped for
   `/home/`, `/Users/`, key shapes: the only hits are the secret-scanner's own
   regexes and a test that builds a fake key at run time). Metadata had no
   `Author` field (added: Calas Automations Inc., the licence holder in
   `LICENSE`). The package had no `__version__` (added; `--version` reads
   it; a test ties it to `pyproject.toml` and the CHANGELOG heading). The
   sdist lacked `benchmarks/`, so its own benchmark test skipped (now
   shipped). Declared dependencies: none, and `pip list` in the clean venv
   shows only pip and prompt-assert, so the list is complete and minimal.
   `py.typed` is NOT shipped on purpose: mypy reports 29 errors, and the
   marker would claim typing that is not there.
3. **Supply-chain reviewer.** Zero runtime dependencies; build backend is
   `setuptools>=77`. Found by running it: `--base-url file:///etc/passwd`
   was handed to urllib, which opened the local file as the "model" (error:
   `Not a directory: '/etc/passwd/chat/completions'`). Now refused. Found: a
   `user:password@host` address was printed, password included, in "model
   unreachable" lines that end up in CI logs. Now refused before any request
   and never printed. Not fixed: the workflows pin actions by tag
   (`actions/checkout@v4`), not commit hash (hashes cannot be looked up
   offline); no Trusted Publishing workflow (publishing was off limits).
4. **Docs / DX writer.** The example CI workflow installed the repository's
   newest commit (`pip install git+https://...`), so a later push could change
   a user's CI. It now pins `prompt-assert==0.4.0` (works once 0.4.0 is on
   PyPI; see PUBLISH-CHECKLIST). Limits section now states the Windows
   status, the Python 3.10 status and that `--format json` prints nothing on
   stdout for exit 2.
5. **CI / CD integrator.** CI never built or installed the wheel. Added a
   `package` job (build, `twine check`, install the wheel into a clean venv in
   a folder with a space, run the quick start, run the sdist's own tests).
   Not run on GitHub from here. Running the command CI and the README give for
   the tests (`python -m unittest discover -s tests`) on the checkout: 396 tests and 2
   import errors (`attempted relative import with no known parent package`
   in `test_expert2` and `test_expert3`), so CI's test step would have failed
   on every cell; fixed with `-t .` and a test that guards the three places. `list --format json` gives scripts something
   to read.
6. **Hostile-input fuzzer.** 6,000 random mutations of a cases file (NUL,
   bidi, lone surrogates, tabs, anchors, 400-digit numbers, `(a+)+`, escape
   codes) through the YAML reader, the loader and every assertion: 0 crashes
   (only the documented `ValueError`), 0 runs over 2 s. By hand: a 50 MB
   `input:` ran (18 s, under load) with a clean verdict; random bytes as a
   prompt file gave a message without a file name (fixed); an address with
   an invisible character or 5,000 characters is refused with a short,
   cleaned message.
7. **Performance engineer.** `import prompt_assert.cli` about 140 ms under
   load (stdlib `inspect` and `dataclasses` dominate); no change made, the
   gain would be small. Benchmark panels unchanged (no assertion code
   changed): see "Benchmark" below.
8. **Competitor's maintainer.** Read-only registry check, 2026-11-07:
   promptfoo 0.125.1 (`npm view`), deepeval 4.3.3, inspect-ai 0.3.288 are
   still the versions the README's comparison table was measured against, so
   its install-size rows still describe the current releases. Feature rows
   were not re-read this round (unverified beyond their 2026-11-06/07 dates).
9. **Windows reviewer (read the code, ran nothing on Windows).** `grep` for
   `shell=True`, `signal`, `fcntl`, `os.sep`, `encoding`, `os.replace`,
   `newline` across `prompt_assert/`: no `shell=True`, no signals; every
   `open()` names UTF-8; the console is already hardened against cp1252
   (`_harden_streams`, `PYTHONIOENCODING=ascii` run here: no traceback).
   Gaps fixed: `os.replace` on a briefly locked file raises PermissionError
   on Windows (now retried 5 times there); prompt names `nul`, `con`, `a:b`,
   trailing dot or space would open a device, a hidden stream or another
   file (refused on Windows only); `init` wrote CRLF files on Windows (now
   LF). None of this was run on Windows; the tests fake the operating system.

## What was built or fixed

All in `CHANGELOG.md` under "Expert round 4". Tests: 471 passed, 2 skipped before; 495 passed, 2 skipped after (24 new,
in `tests/test_round4.py`; subtests 358 -> 406). Benchmark: tables
unchanged.

## Scored ideas

Scores 1 (low) to 5 (high). Value = user value, Diff. = differentiation from
other tools. Effort and risk: 1 is cheap/safe.

| # | idea | value | diff. | effort | risk | status |
|---|---|---|---|---|---|---|
| 1 | Refuse `file://` / `ftp://` model addresses | 4 | 2 | 1 | 1 | DONE |
| 2 | Refuse and never print `user:pass@` in addresses | 4 | 2 | 1 | 1 | DONE |
| 3 | Non-UTF-8 prompt file names the file | 3 | 1 | 1 | 1 | DONE |
| 4 | `__version__`, one source of truth, tested | 3 | 1 | 1 | 1 | DONE |
| 5 | Author in package metadata | 2 | 1 | 1 | 1 | DONE |
| 6 | Friendly message for a bare `prompt-assert` | 3 | 1 | 1 | 1 | DONE |
| 7 | `list --format json` | 3 | 2 | 1 | 1 | DONE |
| 8 | Windows device/invalid names refused | 3 | 1 | 1 | 2 | DONE (not run on Windows) |
| 9 | Retry `os.replace` on Windows lock | 3 | 1 | 1 | 2 | DONE (not run on Windows) |
| 10 | `init` writes LF on every OS | 2 | 1 | 1 | 1 | DONE |
| 11 | CI `package` job: build, check, install wheel, sdist tests | 4 | 2 | 2 | 1 | DONE (not run on GitHub) |
| 12 | Example workflow pins the version | 3 | 1 | 1 | 1 | DONE |
| 13 | sdist ships `benchmarks/` | 2 | 1 | 1 | 1 | DONE |
| 14 | README install section driven by `--version` | 4 | 1 | 1 | 1 | DONE |
| 15 | Test command in CI and README imports all test modules | 4 | 1 | 1 | 1 | DONE |
| 16 | README states Windows / 3.10 / JSON-error limits | 3 | 1 | 1 | 1 | DONE |
| 17 | `py.typed` + mypy in CI | 3 | 1 | 4 | 2 | NOT-DONE (29 mypy errors to fix first) |
| 18 | JSON error object on stdout when exit 2 with `--format json` | 3 | 3 | 2 | 3 | NOT-DONE (changes the stdout contract; decide first) |
| 19 | SARIF output for code scanning | 2 | 3 | 3 | 2 | NOT-DONE |
| 20 | GitHub Action wrapper | 3 | 3 | 3 | 3 | NOT-DONE |
| 21 | Pin CI actions by commit hash | 3 | 1 | 2 | 2 | NOT-DONE (hashes need the network; use Dependabot) |
| 22 | Trusted Publishing workflow (no token on a laptop) | 4 | 1 | 3 | 4 | NOT-DONE (publishing was off limits) |
| 23 | `doctor` / endpoint ping | 3 | 2 | 2 | 2 | NOT-DONE (it spends a model call; the first-case error already says the same) |
| 24 | Refusal assertion, XML/HTML/SQL checks (promptfoo has them) | 4 | 2 | 4 | 3 | NOT-DONE |
| 25 | Schema read from a file | 2 | 2 | 2 | 1 | NOT-DONE |
| 26 | Faster test suite (223 s; timing tests dominate) | 3 | 1 | 3 | 2 | NOT-DONE |
| 27 | Lazy imports for start-up | 1 | 1 | 2 | 2 | NOT-DONE (gain too small to measure under load) |
| 28 | Atomic cache writes | 1 | 1 | 2 | 2 | NOT-DONE (a half-written entry already reads as a miss) |
| 29 | Run the whole suite on Windows and 3.10 | 5 | 1 | 1 | 1 | NOT-DONE (no such machine here; CI has the jobs) |

## Could not test

- Anything on Windows (real console, locking, `windows-latest` CI).
- Python 3.10 (not installed; only 3.11, 3.12, 3.13 exist here).
- The new `package` job and the pinned example workflow on GitHub's runners.
- Read-only file systems and permission failures: this sandbox runs as root,
  so those tests skip and my manual attempt proved nothing.
- Competitors' feature pages (only their latest version numbers were read).
- A real model: everything used the built-in fake model or fake servers.
- Whether PyPI will accept the upload (only the read-only listing was read).
