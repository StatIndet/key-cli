from __future__ import annotations

import platform
import shutil
import subprocess
from typing import Any

from ..utils.output import DEPENDENCY_FAILURE, Result


COMMANDS = {
    "qs": {"features": ["shell", "ipc"]},
    "gpu-screen-recorder": {"features": ["record"]},
    "slurp": {"features": ["record-region"]},
    "ffmpeg": {"features": ["audio", "record-gif"]},
    "ffprobe": {"features": ["audio-validation"]},
    "pactl": {"features": ["audio-source-resolution"]},
    "cliphist": {"features": ["clipboard-list", "clipboard-store"]},
    "wl-copy": {"features": ["clipboard-restore"]},
    "wl-paste": {"features": ["clipboard-store", "clipboard-watch"]},
}


def safe_version(program: str) -> str | None:
    path = shutil.which(program)
    if not path:
        return None
    for option in ("--version", "-V", "version"):
        try:
            process = subprocess.run(
                [path, option], capture_output=True, text=True, timeout=2, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if process.returncode != 0:
            continue
        text = (process.stdout or process.stderr).strip().splitlines()
        if text:
            return text[0][:240]
    return None


def run(args) -> Result:
    commands: dict[str, Any] = {}
    missing: list[str] = []
    for name, metadata in COMMANDS.items():
        path = shutil.which(name)
        value = {
            "available": path is not None,
            "path": path,
            "version": safe_version(name),
            "features": metadata["features"],
        }
        commands[name] = value
        if path is None:
            missing.append(name)
    features = {
        "shell": commands["qs"]["available"],
        "ipc": commands["qs"]["available"],
        "record": commands["gpu-screen-recorder"]["available"],
        "record-gif": commands["gpu-screen-recorder"]["available"]
        and commands["ffmpeg"]["available"],
        "record-region": commands["gpu-screen-recorder"]["available"]
        and commands["slurp"]["available"],
        "audio": all(commands[name]["available"] for name in ("ffmpeg", "ffprobe", "pactl")),
        "clipboard-list": commands["cliphist"]["available"],
        "clipboard-restore": all(commands[name]["available"] for name in ("cliphist", "wl-copy")),
        "clipboard-watch": all(commands[name]["available"] for name in ("cliphist", "wl-paste")),
    }
    payload = {
        "schemaVersion": 1,
        "platform": platform.platform(),
        "commands": commands,
        "features": features,
        "missing": missing,
    }
    text = "key dependencies: " + ("ready" if not missing else "missing " + ", ".join(missing))
    return Result(0 if not missing else DEPENDENCY_FAILURE, "doctor", payload, text, bool(missing))
