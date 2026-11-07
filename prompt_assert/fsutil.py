"""File-system helpers shared by the writers (cassette, JUnit, Markdown)."""

import os
import time

# Windows refuses to replace a file another program has open (a virus
# scanner, an editor, a CI log tailer) with PermissionError, usually for a
# few milliseconds. POSIX never does this, so there the first try is the only
# try. A flag, so tests can switch the rule on anywhere.
IS_WINDOWS = os.name == "nt"
_TRIES = 5
_PAUSE = 0.05


def replace(tmp: str, path: str) -> None:
    """os.replace(tmp, path); on Windows, retried a few times when the
    target is briefly locked. The last PermissionError is raised as is."""
    tries = _TRIES if IS_WINDOWS else 1
    for attempt in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(_PAUSE * (attempt + 1))
