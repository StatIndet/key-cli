from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from key_cli.commands import record
from key_cli.utils.process import Identity


def _gif_start_args(output: Path) -> SimpleNamespace:
    return SimpleNamespace(
        type="gif",
        target="region",
        geometry="640x480+0+0",
        fps=30,
        audio="none",
        output=str(output),
    )


def test_gif_start_records_supported_intermediate_container_without_notifying(
    tmp_path: Path, monkeypatch
) -> None:
    spawned: dict[str, object] = {}
    saved: list[dict] = []
    notifications: list[tuple[str, str]] = []

    class FakeProcess:
        pid = 1234

    def fake_spawn(program, arguments, log_path):
        spawned.update(program=program, arguments=arguments, log_path=log_path)
        return FakeProcess(), None

    monkeypatch.setattr(record, "active_state", lambda *args: ({}, False))
    monkeypatch.setattr(
        record.shutil,
        "which",
        lambda name: f"/usr/bin/{name}",
    )
    monkeypatch.setattr(record, "spawn", fake_spawn)
    monkeypatch.setattr(
        record,
        "wait_for_identity",
        lambda pid, expected, argument: Identity(pid, 42, expected),
    )
    monkeypatch.setattr(record, "matches", lambda *args: True)
    monkeypatch.setattr(record, "save", lambda kind, state: saved.append(dict(state)))
    monkeypatch.setattr(
        record,
        "_notify",
        lambda title, body: notifications.append((title, body)),
    )

    result = record.start(_gif_start_args(tmp_path))

    assert result.exit_code == 0
    arguments = spawned["arguments"]
    assert isinstance(arguments, list)
    temporary = Path(arguments[arguments.index("-o") + 1])
    assert temporary.name.endswith(".partial.mp4")
    assert saved[-1]["type"] == "gif"
    assert Path(saved[-1]["outputPath"]).suffix == ".gif"
    assert notifications == []


def test_gif_conversion_uses_ffmpeg_and_waits_for_completion(tmp_path: Path, monkeypatch) -> None:
    temporary = tmp_path / ".recording.partial.mp4"
    output = tmp_path / "recording.gif"
    temporary.write_bytes(b"video")
    captured: dict[str, object] = {}

    monkeypatch.setattr(record.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        output.write_bytes(b"GIF89a")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(record.subprocess, "run", fake_run)

    converted, message = record._convert_gif(temporary, output, 30)

    assert converted is True
    assert message == ""
    command = captured["command"]
    assert isinstance(command, list)
    assert command[0] == "/usr/bin/ffmpeg"
    assert str(temporary) in command
    assert "-filter_complex" in command
    assert command[-1] == str(output)
    assert output.read_bytes() == b"GIF89a"


def test_gif_completion_notifies_only_after_processing(tmp_path: Path, monkeypatch) -> None:
    temporary = tmp_path / ".recording.partial.mp4"
    output = tmp_path / "recording.gif"
    temporary.write_bytes(b"video")
    events: list[object] = []
    state = {
        "schemaVersion": 1,
        "state": "finalizing",
        "type": "gif",
        "fps": 30,
        "pid": 0,
        "processStartTicks": None,
        "temporaryPath": str(temporary),
        "outputPath": str(output),
        "error": None,
    }

    def fake_convert(source, destination, fps):
        events.append("processing")
        destination.write_bytes(b"GIF89a")
        return True, ""

    monkeypatch.setattr(record, "_convert_gif", fake_convert)
    monkeypatch.setattr(
        record,
        "save",
        lambda kind, value: events.append(("save", value["state"])),
    )
    monkeypatch.setattr(
        record,
        "_notify",
        lambda title, body: events.append(("notify", title, body)),
    )

    result = record._finalize(state)

    assert result.exit_code == 0
    assert events.index("processing") < events.index(
        next(event for event in events if isinstance(event, tuple) and event[0] == "notify")
    )
    assert output.is_file()
    assert not temporary.exists()
