from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..utils.output import STATE_FAILURE, error


def runtime_dir() -> Path:
    value = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if not value:
        raise RuntimeError("XDG_RUNTIME_DIR is not set")
    path = Path(value) / "key"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


@contextmanager
def locked(kind: str) -> Iterator[None]:
    directory = runtime_dir()
    lock_path = directory / f"{kind}.lock"
    with lock_path.open("a+") as lock:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StateError("another key recording operation is in progress") from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


class StateError(RuntimeError):
    pass


def state_path(kind: str) -> Path:
    return runtime_dir() / f"{kind}.json"


def load(kind: str) -> dict[str, Any]:
    path = state_path(kind)
    if not path.exists():
        return {"schemaVersion": 1, "state": "idle"}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schemaVersion") != 1:
            raise ValueError("unsupported schema")
        return value
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise StateError(f"invalid {kind} state file: {exc}") from exc


def save(kind: str, value: dict[str, Any]) -> None:
    path = state_path(kind)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    value = dict(value)
    value["schemaVersion"] = 1
    fd, temporary = tempfile.mkstemp(prefix=f".{kind}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def base_state() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "state": "idle",
        "sessionId": "",
        "pid": 0,
        "processStartTicks": None,
        "processStartedAtMs": 0,
        "startedAtMs": 0,
        "completedAtMs": 0,
        "updatedAtMs": 0,
        "temporaryPath": "",
        "outputPath": "",
        "error": None,
    }


def state_error(exc: Exception) -> tuple[int, dict[str, Any]]:
    return STATE_FAILURE, error("state_failure", str(exc))
