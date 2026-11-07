# Publish checklist for prompt-assert (for Dan; nothing here was run)

## Version to release

**0.4.0.** PyPI has 0.2.0 only; 0.3.0 was never published, so 0.4.0 contains
everything from 0.3.0 and 0.4.0. It is a "minor" bump with a few small
behaviour changes (listed under "Changed behaviour" in `CHANGELOG.md`). The
number is already 0.4.0 everywhere; do not change it.

## Check first (about 10 minutes)

1. On GitHub, open the repository, Actions tab: the `ci` workflow must be
   green on Windows, macOS and Linux, and the new `package` job must be green.
   It has never run on Windows or with the `package` job. If something is red,
   do not publish; send me the log.
2. The author in the package is "Calas Automations Inc." (same as the
   LICENSE). If you want a different name, change `authors` in
   `pyproject.toml` and the first line of `LICENSE` now: it becomes public.
3. Skim the top of `CHANGELOG.md` and `README.md` once.

## Commands (on your own machine, in the repository folder)

```bash
git status                                  # should say: nothing to commit
python -m pip install --upgrade build twine
python -m build                             # makes dist/prompt_assert-0.4.0*
python -m twine check dist/*                # both lines should say PASSED
```

Then upload. A version number can never be used again after this:

```bash
python -m twine upload dist/*               # asks for your PyPI token
```

## Right after

1. In a new empty folder: make a new virtual environment, `pip install
   prompt-assert`, then `prompt-assert --version` (must say 0.4.0), then
   `prompt-assert init` and `prompt-assert --demo run --prompt sentiment
   --version v1` (must end with `3/3 passed`).
2. In `README.md`, shorten the install section (the part that explains what
   to do if `--version` says 0.2.0), in `CHANGELOG.md` change
   "0.4.0 (unreleased)" to "0.4.0" with the date, commit, and tag `v0.4.0`.
   `examples/prompt-assert-example.yml` pins 0.4.0 and only works after the
   upload, so do not point anyone at it before then.

## Cost

PyPI is free. Nothing in this package calls a paid service unless you point
it at one (the demo and the cassette replay never do).
