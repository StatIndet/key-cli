from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _resolved_executable(value: str) -> str | None:
    candidate = value.strip()
    if not candidate:
        return None
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        if not path.is_file() or not os.access(path, os.X_OK):
            return None
        return os.path.realpath(path)
    except OSError:
        return None


def current_key_executable(*, prefer_environment: bool = True) -> str:
    """Return an absolute key executable when the current process can resolve it."""
    invocation = str(sys.argv[0] or "")
    if os.path.sep in invocation or (os.path.altsep and os.path.altsep in invocation):
        resolved = _resolved_executable(invocation)
        if resolved:
            return resolved

    configured = os.environ.get("CLAVIS_KEY", "").strip()
    if prefer_environment:
        resolved = _resolved_executable(configured)
        if resolved:
            return resolved

    found = shutil.which("key")
    resolved = _resolved_executable(found or "")
    if resolved:
        return resolved

    if not prefer_environment:
        resolved = _resolved_executable(configured)
        if resolved:
            return resolved
    return "key"
