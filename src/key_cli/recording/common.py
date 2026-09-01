from __future__ import annotations

import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from ..utils.process import Identity
from ..utils.output import GENERAL_FAILURE, Result
from .state import base_state, load, save


def now_ms() -> int:
    return int(time.time() * 1000)


def output_directory(value: str | None, kind: str) -> Path:
    if value:
        return Path(value).expanduser()
    if kind == "audio":
        return _xdg_user_directory("XDG_MUSIC_DIR", Path.home() / "Music") / "Recordings"
    return _xdg_user_directory("XDG_VIDEOS_DIR", Path.home() / "Videos")


def _xdg_user_directory(name: str, fallback: Path) -> Path:
    configured = os.environ.get(name, "").strip()
    if configured:
        path = Path(configured).expanduser()
        if path.is_absolute():
            return path

    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser()
    try:
        contents = (config_home / "user-dirs.dirs").read_text(encoding="utf-8")
    except OSError:
        return fallback

    match = re.search(rf'^{re.escape(name)}="([^"]*)"\s*$', contents, re.MULTILINE)
    if not match:
        return fallback
    configured = (
        match.group(1).replace("${HOME}", str(Path.home())).replace("$HOME", str(Path.home()))
    )
    path = Path(configured).expanduser()
    return path if path.is_absolute() else fallback


def process_fields(state: dict[str, Any]) -> Identity:
    return Identity(int(state.get("pid") or 0), int(state.get("processStartTicks") or 0), "")


def response(
    state: dict[str, Any], command: str, *, ok_value: bool, error_value=None, **extra: Any
) -> Result:
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
    return Result(
        0 if ok_value else int(extra.get("exitCode", GENERAL_FAILURE)),
        command,
        payload,
        text,
        not ok_value,
    )


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
        state["error"] = {
            "code": "recorder_exited",
            "message": f"{executable} is no longer running",
        }
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


def spawn(program: str, arguments: list[str]) -> tuple[subprocess.Popen | None, str | None]:
    try:
        process = subprocess.Popen(
            [program, *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return process, None
    except OSError as exc:
        return None, str(exc)
