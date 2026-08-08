from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from ..utils.process import Identity, capture
from ..utils.output import GENERAL_FAILURE, Result, SESSION_CONFLICT, fail, ok
from .state import base_state, load, locked, save, StateError


def now_ms() -> int:
    return int(time.time() * 1000)


def output_directory(value: str | None, kind: str) -> Path:
    if value:
        return Path(value).expanduser()
    if kind == "audio":
        music = os.environ.get("XDG_MUSIC_DIR", "")
        return Path(music).expanduser() / "Clavis" / "Audio" if music else Path.home() / "Music" / "Clavis" / "Audio"
    videos = os.environ.get("XDG_VIDEOS_DIR", "")
    return Path(videos).expanduser() if videos else Path.home() / "Videos"


def process_fields(state: dict[str, Any]) -> Identity:
    return Identity(int(state.get("pid") or 0), int(state.get("processStartTicks") or 0), "")


def response(state: dict[str, Any], command: str, *, ok_value: bool, error_value=None, **extra: Any) -> Result:
    payload = dict(state)
    payload.update(extra)
    payload["command"] = command
    payload["ok"] = ok_value
    payload["error"] = error_value
    if ok_value:
        text = {
            "record.start": f"Screen recording started (PID {state.get('pid')})",
            "record.stop": f"Screen recording saved to {state.get('outputPath')}",
            "record.status": f"Screen recording state: {state.get('state', 'idle')}",
        }.get(command, "ok")
    else:
        text = (error_value or {}).get("message", "screen recording failed")
    return Result(0 if ok_value else int(extra.get("exitCode", GENERAL_FAILURE)), command, payload, text, not ok_value)


def active_state(kind: str, executable: str, argument: str = "") -> tuple[dict[str, Any], bool]:
    state = load(kind)
    identity = process_fields(state)
    if state.get("state") == "error" and _matches(identity, executable, argument):
        # A previous key version could report a startup race after the
        # recorder had already exec'd.  Recover only when the saved PID,
        # start time, executable, and optional output argument still match.
        state["state"] = "recording"
        state["error"] = None
        state["updatedAtMs"] = now_ms()
        save(kind, state)
        return state, True
    is_active = state.get("state") in {"starting", "recording", "paused", "stopping", "finalizing"}
    if is_active and not _matches(identity, executable, argument):
        state["state"] = "error"
        state["error"] = {"code": "recorder_exited", "message": f"{executable} is no longer running"}
        state["pid"] = 0
        state["processStartTicks"] = None
        save(kind, state)
        return state, False
    return state, is_active


def _matches(identity: Identity, executable: str, argument: str) -> bool:
    from ..utils.process import matches

    return matches(identity, executable, argument)


def new_state(kind: str) -> dict[str, Any]:
    state = base_state()
    state["sessionId"] = uuid.uuid4().hex
    state["kind"] = kind
    state["startedAtMs"] = now_ms()
    return state


def spawn(program: str, arguments: list[str], log_path: Path | None = None) -> tuple[subprocess.Popen | None, str | None]:
    handle = None
    try:
        stdout = subprocess.DEVNULL
        stderr = subprocess.DEVNULL
        if log_path:
            log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            handle = log_path.open("ab")
            stdout = handle
            stderr = handle
        try:
            process = subprocess.Popen(
                [program, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
        finally:
            if handle is not None:
                handle.close()
        return process, None
    except OSError as exc:
        if handle is not None and not handle.closed:
            handle.close()
        return None, str(exc)
