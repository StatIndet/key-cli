from __future__ import annotations

import os
import shutil
import subprocess
from typing import Sequence

from ..utils.output import DEPENDENCY_FAILURE, GENERAL_FAILURE, Result, fail
from ..utils.executable import current_key_executable


def qs_command() -> str | None:
    return shutil.which("qs")


def run_qs(arguments: Sequence[str], command: str) -> Result:
    program = qs_command()
    if not program:
        return fail(command, DEPENDENCY_FAILURE, "qs_unavailable", "qs is not installed or not in PATH")
    environment = os.environ.copy()
    # Quickshell must use the same key-cli executable that launched it.  Do
    # not inherit a stale CLAVIS_KEY from a different installation when this
    # process was invoked through an explicit executable path.
    environment["CLAVIS_KEY"] = current_key_executable(prefer_environment=False)
    try:
        completed = subprocess.run([program, *arguments], check=False, env=environment)
    except OSError as exc:
        return fail(command, GENERAL_FAILURE, "qs_start_failed", str(exc))
    return Result(completed.returncode, command, {"exitCode": completed.returncode}, "", completed.returncode != 0)


def run(args) -> Result:
    if args.kill:
        return run_qs(["-c", "clavis", "kill"], "shell.kill")
    if args.log:
        command = ["-c", "clavis", "log"]
        if args.log_rules:
            command += ["-r", args.log_rules]
        return run_qs(command, "shell.log")
    if args.show:
        return run_qs(["-c", "clavis", "ipc", "show"], "shell.ipc")

    command = ["-c", "clavis", "-n"]
    if args.daemon:
        command.append("-d")
    if args.log_rules:
        command += ["--log-rules", args.log_rules]
    # --foreground/--no-duplicate are key-level compatibility flags for the
    # user service. Quickshell itself remains the lifecycle owner.
    return run_qs(command, "shell.start")
