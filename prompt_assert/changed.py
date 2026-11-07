"""Run only what a change touched: `run --all --changed-since REF`.

Asks git which files under prompts/ differ from REF (committed, staged,
edited or new and not ignored), and maps them to suites:

  prompts/<name>.vN.txt          -> that version of <name>
  prompts/<name>.cases.yaml      -> every version of <name>
  prompts/<name>.cassette.json   -> every version of <name>

Anything else is ignored. If git is missing, the folder is not a repository
or REF does not exist, that is an error (exit 2): a selection that silently
fell back to "nothing changed" would turn CI green without running a case.
"""

from __future__ import annotations

import re
import subprocess

_PATTERNS = (
    (re.compile(r"^prompts/(?P<name>.+)\.(?P<v>v\d+)\.txt$"), "version"),
    (re.compile(r"^prompts/(?P<name>.+)\.cases\.yaml$"), "all"),
    (re.compile(r"^prompts/(?P<name>.+)\.cassette\.json$"), "all"),
)


class ChangedError(RuntimeError):
    pass


def _git(root: str, *args: str) -> str:
    try:
        done = subprocess.run(["git", "-C", root, *args], capture_output=True,
                              text=True, timeout=60, check=False)
    except FileNotFoundError:
        raise ChangedError("--changed-since needs git, which was not found")
    except subprocess.TimeoutExpired:
        raise ChangedError("git took more than 60 seconds")
    if done.returncode != 0:
        detail = " ".join(done.stderr.split())[:300]
        raise ChangedError(f"git {args[0]} failed: {detail}")
    return done.stdout


def changed_files(root: str, ref: str) -> list[str]:
    """Paths (relative to `root`, forward slashes) that differ from `ref`."""
    if not ref or ref.startswith("-"):
        raise ChangedError(f"{ref!r} is not a git revision")
    _git(root, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
    tracked = _git(root, "diff", "--name-only", "--relative", "-z", ref,
                   "--", "prompts")
    fresh = _git(root, "ls-files", "--others", "--exclude-standard",
                 "-z", "--", "prompts")
    names = [p for p in (tracked + fresh).split("\0") if p]
    return sorted(set(names))


def select(targets, files):
    """The (name, version) targets touched by `files`."""
    versions: set[tuple[str, str]] = set()
    names: set[str] = set()
    for path in files:
        for rx, how in _PATTERNS:
            m = rx.match(path)
            if m:
                if how == "version":
                    versions.add((m.group("name"), m.group("v")))
                else:
                    names.add(m.group("name"))
                break
    return [t for t in targets if t in versions or t[0] in names]
