from __future__ import annotations

import subprocess
import sys

from key_cli.parser import build_parser


def test_public_command_groups_are_small() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "key_cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stderr == ""
    for command in ("shell", "ipc", "record", "audio", "clipboard", "doctor", "version"):
        assert command in result.stdout


def test_record_lifecycle_arguments() -> None:
    args = build_parser().parse_args(["record", "start", "--geometry", "640x480+0+0", "--json"])
    assert args.action == "start"
    assert args.geometry == "640x480+0+0"
    assert args.json is True


def test_gif_recording_argument_is_preserved() -> None:
    args = build_parser().parse_args(
        ["record", "start", "--type", "gif", "--geometry", "640x480+0+0", "--json"]
    )
    assert args.type == "gif"


def test_record_lifecycle_matches_qml_commands() -> None:
    for action in ("status", "stop", "pause", "resume"):
        args = build_parser().parse_args(["record", action, "--json"])
        assert args.action == action
        assert args.json is True


def test_audio_lifecycle_matches_qml_commands() -> None:
    start = build_parser().parse_args(["audio", "start", "--source", "system", "--json"])
    assert start.action == "start"
    assert start.source == "system"
    assert start.json is True
    for action in ("status", "stop"):
        args = build_parser().parse_args(["audio", action, "--json"])
        assert args.action == action
        assert args.json is True


def test_clipboard_json_forms_match_qml_commands() -> None:
    listing = build_parser().parse_args(["clipboard", "list", "--format", "json", "--limit", "5"])
    assert listing.action == "list"
    assert listing.format == "json"
    assert listing.limit == 5
    restore = build_parser().parse_args(["clipboard", "restore", "123", "--format", "json"])
    assert restore.action == "restore"
    assert restore.id == "123"
    assert restore.format == "json"


def test_ipc_preserves_argument_array() -> None:
    args = build_parser().parse_args(["ipc", "call", "keystone", "dashboard", "with spaces"])
    assert args.target == "keystone"
    assert args.method == "dashboard"
    assert args.arguments == ["with spaces"]
