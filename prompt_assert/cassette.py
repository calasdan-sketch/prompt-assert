"""Cassettes: recorded model answers that live in git, so CI replays them for
free and gets the same verdict every time.

The response cache (.prompt-assert/, git-ignored) saves money on one machine.
A cassette is the committed version: `run --record` calls the model and
writes prompts/<name>.cassette.json; `run --replay` answers every call from
that file and never contacts a model (it needs no URL, no key, no network).

An answer is looked up by the exact text sent to the model (the rendered
prompt) and the run number of --repeat. Nothing else is part of the key, so
changing an assertion, a tag or a case name keeps every recording valid, and
changing a prompt or an input makes exactly the affected cases miss. A miss
in replay is a configuration error (exit 2) that says what to record, never
a silent pass and never a live call. The model name is stored for the
reader; it is not part of the key (replay is about the recorded answers, not
the model that gave them).

The file is plain JSON, sorted and with every non-ASCII character escaped,
so a reviewer sees an invisible character in a pull request as \\u200b.
Recorded replies can contain anything the model said (secrets and personal
data included): read the diff before committing, and `--record` warns about
recordings that look like secrets.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading

FORMAT = 1
SUFFIX = ".cassette.json"
#: The largest cassette read (and so, with a margin, written): bigger is a
#: mistake or an attack, not a test fixture.
MAX_BYTES = 64 * 1024 * 1024
MODES = ("record", "replay")


class CassetteError(ValueError):
    """The cassette cannot be used: missing, damaged, or without the
    recording a replay asks for."""


def cassette_path(root: str, name: str, prompts_dir: str = "prompts") -> str:
    return os.path.join(root, prompts_dir, name + SUFFIX)


def response_key(rendered: str, sample: int = 0) -> str:
    h = hashlib.sha256()
    h.update(f"response\x00{sample}\x00".encode())
    h.update(rendered.encode("utf-8", "surrogatepass"))
    return h.hexdigest()[:40]


def judge_key(rubric: str, output: str) -> str:
    h = hashlib.sha256()
    h.update(b"judge\x00")
    h.update(rubric.encode("utf-8", "surrogatepass"))
    h.update(b"\x00")
    h.update(output.encode("utf-8", "surrogatepass"))
    return h.hexdigest()[:40]


class Tape:
    """One prompt's cassette, open for recording or replaying. Safe to use
    from several threads."""

    def __init__(self, path: str, mode: str):
        if mode not in MODES:
            raise ValueError(f"cassette mode must be one of {MODES}")
        self.path, self.mode = path, mode
        self.responses: dict[str, dict] = {}
        self.judges: dict[str, dict] = {}
        self.used: set[str] = set()
        self.warnings: list[str] = []
        self._lock = threading.Lock()
        self._load()

    # ------------------------------------------------------------ reading
    def _load(self) -> None:
        try:
            size = os.path.getsize(self.path)
        except FileNotFoundError:
            if self.mode == "record":
                return          # the first recording starts the file
            raise CassetteError(
                f"{self.path}: no cassette to replay - record one with "
                f"`run --record` (it calls the model once and writes it)")
        if size > MAX_BYTES:
            raise CassetteError(f"{self.path}: is {size} bytes; a cassette "
                                f"over {MAX_BYTES} bytes is refused")
        try:
            with open(self.path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except UnicodeDecodeError:
            raise CassetteError(f"{self.path}: not UTF-8 text")
        except (ValueError, RecursionError) as e:
            raise CassetteError(f"{self.path}: not valid JSON ({e}) - "
                                f"re-record it with `run --record`")
        if (not isinstance(data, dict) or data.get("format") != FORMAT
                or not isinstance(data.get("responses"), dict)
                or not isinstance(data.get("judges", {}), dict)):
            raise CassetteError(
                f"{self.path}: not a prompt-assert cassette of format "
                f"{FORMAT} - re-record it with `run --record`")
        for key, entry in data["responses"].items():
            if not isinstance(entry, dict) or not isinstance(
                    entry.get("output"), str):
                raise CassetteError(f"{self.path}: recording {key!r} has no "
                                    f"text output")
            self.responses[key] = entry
        for key, entry in data.get("judges", {}).items():
            if (not isinstance(entry, dict)
                    or not isinstance(entry.get("passed"), bool)
                    or not isinstance(entry.get("detail"), str)):
                raise CassetteError(f"{self.path}: judge recording {key!r} "
                                    f"has no verdict")
            self.judges[key] = entry

    def get(self, rendered: str, sample: int = 0) -> str | None:
        key = response_key(rendered, sample)
        with self._lock:
            entry = self.responses.get(key)
            if entry is not None:
                self.used.add(key)
        return None if entry is None else entry["output"]

    def get_judge(self, rubric: str, output: str):
        key = judge_key(rubric, output)
        with self._lock:
            entry = self.judges.get(key)
            if entry is not None:
                self.used.add("judge:" + key)
        return None if entry is None else (entry["passed"], entry["detail"])

    # ---------------------------------------------------------- recording
    def put(self, rendered: str, output: str, sample: int = 0,
            model: str = "") -> None:
        key = response_key(rendered, sample)
        with self._lock:
            self.responses[key] = {"output": output, "model": model}
            self.used.add(key)

    def put_judge(self, rubric: str, output: str, passed: bool, detail: str,
                  model: str = "") -> None:
        key = judge_key(rubric, output)
        with self._lock:
            self.judges[key] = {"passed": passed, "detail": detail,
                                "model": model}
            self.used.add("judge:" + key)

    def stale(self) -> int:
        """Recordings nothing asked for in this run (replay)."""
        total = len(self.responses) + len(self.judges)
        return total - len(self.used)

    def save(self, prune: bool = False) -> None:
        """Write the cassette, all or nothing. Recordings already in the
        file are kept (recording one version must not erase another's)
        unless `prune`: then only what this run asked for stays, so a
        recording of a prompt that no longer exists does not linger."""
        with self._lock:
            keep_r = {k: v for k, v in self.responses.items()
                      if not prune or k in self.used}
            keep_j = {k: v for k, v in self.judges.items()
                      if not prune or "judge:" + k in self.used}
        payload = json.dumps({"format": FORMAT, "responses": keep_r,
                              "judges": keep_j},
                             sort_keys=True, indent=1, ensure_ascii=True)
        data = (payload + "\n").encode("ascii")
        if len(data) > MAX_BYTES:
            raise OSError(f"{self.path}: the cassette would be "
                          f"{len(data)} bytes, over the {MAX_BYTES} limit")
        folder = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(prefix=".cassette-", suffix=".tmp",
                                   dir=folder)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            mask = os.umask(0o022)
            os.umask(mask)
            os.chmod(tmp, 0o666 & ~mask)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
