from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Identity:
    pid: int
    start_ticks: int
    executable: str = ""

    def json(self) -> dict[str, int | str]:
        return {
            "pid": self.pid,
            "startTicks": str(self.start_ticks),
            "executable": self.executable,
        }


def start_ticks(pid: int) -> int:
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        end = text.rfind(")")
        fields = text[end + 2 :].split()
        # fields starts at field 3 (state), so field 22 is offset 19.
        return int(fields[19])
    except (OSError, ValueError, IndexError):
        return 0


def executable(pid: int) -> str:
    try:
        return os.path.basename(os.readlink(f"/proc/{pid}/exe"))
    except OSError:
        try:
            return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
        except OSError:
            return ""


def command_line(pid: int) -> list[str]:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[:-1]
    except OSError:
        return []


def capture(pid: int, expected_executable: str = "") -> Identity:
    return Identity(pid, start_ticks(pid), expected_executable or executable(pid))


def wait_for_identity(
    pid: int,
    expected_executable: str = "",
    argument: str = "",
    timeout: float = 1.0,
) -> Identity:
    deadline = time.monotonic() + timeout
    identity = capture(pid, expected_executable)
    while time.monotonic() < deadline:
        if identity.start_ticks > 0 and (
            not expected_executable
            or executable(pid) in {expected_executable, os.path.basename(expected_executable)}
        ) and (
            not argument
            or any(os.fsencode(argument) in value for value in command_line(pid))
        ):
            return identity
        time.sleep(0.05)
        identity = capture(pid, expected_executable)
    return identity


def alive(identity: Identity) -> bool:
    return identity.pid > 0 and start_ticks(identity.pid) == identity.start_ticks and identity.start_ticks > 0


def matches(identity: Identity, executable_name: str, argument: str = "") -> bool:
    if not alive(identity):
        return False
    actual = executable(identity.pid)
    if executable_name and actual not in {executable_name, os.path.basename(executable_name)}:
        return False
    if argument:
        needle = os.fsencode(argument)
        if not any(needle in value for value in command_line(identity.pid)):
            return False
    return True


def wait_gone(identity: Identity, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not alive(identity):
            return True
        time.sleep(0.05)
    return not alive(identity)


def stop_verified(identity: Identity, executable_name: str, argument: str = "") -> tuple[bool, bool, str]:
    """Stop one verified process with SIGINT, SIGTERM, then SIGKILL."""
    if not matches(identity, executable_name, argument):
        return False, False, "process identity no longer matches the saved session"
    try:
        os.kill(identity.pid, signal.SIGINT)
    except ProcessLookupError:
        return True, False, ""
    except OSError as exc:
        return False, False, str(exc)
    if wait_gone(identity, 2.0):
        return True, False, ""
    if matches(identity, executable_name, argument):
        try:
            os.kill(identity.pid, signal.SIGTERM)
        except ProcessLookupError:
            return True, True, ""
        except OSError as exc:
            return False, True, str(exc)
    if wait_gone(identity, 2.0):
        return True, True, ""
    if matches(identity, executable_name, argument):
        try:
            os.kill(identity.pid, signal.SIGKILL)
        except ProcessLookupError:
            return True, True, ""
        except OSError as exc:
            return False, True, str(exc)
    if wait_gone(identity, 2.0):
        return True, True, ""
    return False, True, "timed out waiting for the verified process to stop"
