from __future__ import annotations

import argparse

from .commands.audio import run as audio
from .commands.clipboard import run as clipboard
from .commands.doctor import run as doctor
from .commands.ipc import run as ipc
from .commands.record import run as record
from .commands.shell import run as shell
from .commands.version import run as version


def _json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="write one stable JSON response")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="key", description="Clavis shell lifecycle and discrete task CLI"
    )
    parser.add_argument(
        "-v",
        "--version",
        dest="version_flag",
        action="store_true",
        help="print the key-cli version",
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    shell_parser = commands.add_parser(
        "shell", help="start, stop, or inspect the Clavis Quickshell"
    )
    shell_parser.set_defaults(handler=shell)
    shell_parser.add_argument("--daemon", "-d", action="store_true", help="pass -d to qs")
    shell_parser.add_argument(
        "--kill", "-k", action="store_true", help="stop the clavis qs configuration"
    )
    shell_parser.add_argument("--log", "-l", action="store_true", help="print the clavis qs log")
    shell_parser.add_argument(
        "--show", "-s", action="store_true", help="show the clavis IPC methods"
    )
    shell_parser.add_argument("--log-rules", metavar="RULES")
    shell_parser.add_argument("--foreground", action="store_true", help=argparse.SUPPRESS)
    shell_parser.add_argument("--no-duplicate", action="store_true", help=argparse.SUPPRESS)

    ipc_parser = commands.add_parser("ipc", help="route IPC through the Clavis qs configuration")
    ipc_parser.set_defaults(handler=ipc)
    ipc_parser.add_argument("action", nargs="?", choices=["show", "list", "call"], default="show")
    ipc_parser.add_argument("target", nargs="?")
    ipc_parser.add_argument("method", nargs="?")
    ipc_parser.add_argument("arguments", nargs=argparse.REMAINDER)

    record_parser = commands.add_parser("record", help="record the screen with gpu-screen-recorder")
    record_commands = record_parser.add_subparsers(dest="action", required=True)
    start = record_commands.add_parser("start", help="start one screen recording")
    start.add_argument("--type", choices=["video", "gif"], default="video")
    start.add_argument("--target", choices=["region", "screen"], default="region")
    start.add_argument("--geometry", help="compositor-logical WIDTHxHEIGHT+X+Y")
    start.add_argument("--audio", choices=["none", "system"], default="none")
    start.add_argument("--fps", type=int, default=60)
    start.add_argument("--output", help="output directory")
    start.add_argument("--clipboard", action="store_true", help="copy the completed file URI")
    _json(start)
    for action in ("status", "stop", "pause", "resume"):
        sub = record_commands.add_parser(action, help=f"{action} the saved screen recording")
        if action == "stop":
            sub.add_argument("--clipboard", action="store_true", help="copy the completed file URI")
        _json(sub)
    record_parser.set_defaults(handler=record)

    audio_parser = commands.add_parser("audio", help="record a microphone or system audio file")
    audio_commands = audio_parser.add_subparsers(dest="action", required=True)
    start = audio_commands.add_parser("start", help="start an audio recording")
    start.add_argument("--source", choices=["mic", "system"], required=True)
    start.add_argument("--output", help="output directory")
    _json(start)
    for action in ("status", "stop"):
        sub = audio_commands.add_parser(action, help=f"{action} the audio recording")
        _json(sub)
    audio_parser.set_defaults(handler=audio)

    clipboard_parser = commands.add_parser(
        "clipboard", help="operate on the cliphist clipboard backend"
    )
    clipboard_commands = clipboard_parser.add_subparsers(dest="action", required=True)
    list_parser = clipboard_commands.add_parser("list", help="list clipboard entries")
    list_parser.add_argument("--limit", type=int, default=100)
    list_parser.add_argument("--format", choices=["json"], default=None)
    list_parser.add_argument("--json", action="store_true")
    for action in ("inspect", "restore", "delete"):
        sub = clipboard_commands.add_parser(action, help=f"{action} one clipboard entry")
        sub.add_argument("id")
        sub.add_argument("--format", choices=["json"], default=None)
        sub.add_argument("--json", action="store_true")
    for action in ("clear", "status", "watch", "store"):
        help_text = (
            f"internal clipboard {action}"
            if action in {"watch", "store"}
            else f"clipboard {action}"
        )
        sub = clipboard_commands.add_parser(action, help=help_text)
        if action == "store":
            sub.add_argument("--stdin", action="store_true", help=argparse.SUPPRESS)
        sub.add_argument("--format", choices=["json"], default=None)
        sub.add_argument("--json", action="store_true")
    clipboard_parser.set_defaults(handler=clipboard)

    doctor_parser = commands.add_parser("doctor", help="check key runtime dependencies")
    doctor_parser.set_defaults(handler=doctor)
    _json(doctor_parser)

    version_parser = commands.add_parser("version", help="print key-cli version metadata")
    version_parser.set_defaults(handler=version)
    _json(version_parser)
    return parser
