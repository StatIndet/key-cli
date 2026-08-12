from __future__ import annotations

from ..utils.output import Result, fail
from .shell import run_qs


def run(args) -> Result:
    if args.action in (None, "show", "list"):
        return run_qs(["-c", "clavis", "ipc", "show"], "ipc.show")
    if args.action == "call":
        if not args.target or not args.method:
            return fail(
                "ipc.call", 2, "usage_error", "usage: key ipc call TARGET METHOD [ARGUMENTS...]"
            )
        return run_qs(
            ["-c", "clavis", "ipc", "call", args.target, args.method, *args.arguments],
            "ipc.call",
        )
    return fail("ipc", 2, "usage_error", f"unknown IPC action: {args.action}")
