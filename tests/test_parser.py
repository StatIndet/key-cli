from __future__ import annotations

from key_cli.parser import build_parser


def test_public_command_groups_are_small() -> None:
    parser = build_parser()
    actions = parser.parse_args([])
    assert not hasattr(actions, "handler")
    assert {action for action in parser._subparsers._group_actions[0].choices} == {
        "shell",
        "ipc",
        "record",
        "audio",
        "clipboard",
        "doctor",
        "version",
    }


def test_record_lifecycle_arguments() -> None:
    args = build_parser().parse_args(["record", "start", "--geometry", "640x480+0+0", "--json"])
    assert args.action == "start"
    assert args.geometry == "640x480+0+0"
    assert args.json is True


def test_ipc_preserves_argument_array() -> None:
    args = build_parser().parse_args(["ipc", "call", "keystone", "dashboard", "with spaces"])
    assert args.target == "keystone"
    assert args.method == "dashboard"
    assert args.arguments == ["with spaces"]
