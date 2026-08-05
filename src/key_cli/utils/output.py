from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any


SUCCESS = 0
GENERAL_FAILURE = 1
USAGE_ERROR = 2
DEPENDENCY_FAILURE = 3
SESSION_CONFLICT = 4
STATE_FAILURE = 5
RECORDER_START_FAILURE = 6
RECORDER_STOP_FAILURE = 7
POSTPROCESS_FAILURE = 8


@dataclass
class Result:
    exit_code: int
    command: str
    payload: dict[str, Any]
    text: str = ""
    text_is_error: bool = False

    def json(self) -> dict[str, Any]:
        value = dict(self.payload)
        value.setdefault("schemaVersion", 1)
        value.setdefault("command", self.command)
        value.setdefault("ok", self.exit_code == SUCCESS)
        value.setdefault("error", None)
        return value


def error(code: str, message: str, **details: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"code": code, "message": message}
    if details:
        value["details"] = details
    return value


def emit_result(result: Result, json_requested: bool) -> int:
    if json_requested:
        print(json.dumps(result.json(), ensure_ascii=False, separators=(",", ":")))
    elif result.text:
        stream = sys.stderr if result.text_is_error else sys.stdout
        print(result.text, file=stream)
    return result.exit_code


def ok(command: str, text: str = "", **payload: Any) -> Result:
    return Result(SUCCESS, command, payload, text)


def fail(
    command: str,
    exit_code: int,
    code: str,
    message: str,
    text: str | None = None,
    **payload: Any,
) -> Result:
    payload = dict(payload)
    payload["error"] = error(code, message)
    return Result(exit_code, command, payload, text or f"Error [{code}]: {message}", True)
