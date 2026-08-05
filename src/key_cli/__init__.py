"""The stable, small command line boundary for Clavis."""

from __future__ import annotations

import sys
from typing import Sequence

__version__ = "0.2.0"

from .parser import build_parser
from .utils.output import emit_result


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if getattr(args, "version_flag", False):
        args.command = "version"
        args.json = getattr(args, "json", False)
        from .commands.version import run

        return emit_result(run(args), args.json)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 0
    try:
        result = args.handler(args)
        json_requested = getattr(args, "json", False) or getattr(args, "format", None) == "json"
        return emit_result(result, json_requested)
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        print("key: interrupted", file=sys.stderr)
        return 130


__all__ = ["main", "__version__"]
