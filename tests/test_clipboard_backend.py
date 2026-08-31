from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from key_cli.clipboard import backend
from key_cli.clipboard.backend import (
    file_metadata,
    image_info,
    inspect_payload,
    lightweight,
    parse_uri_list,
    run_wl_copy,
    select_mime,
)


def test_png_metadata_and_safe_preview_shape() -> None:
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (16).to_bytes(4, "big") + (8).to_bytes(4, "big")
    info = image_info(data)
    assert info == ("image/png", 16, 8)
    payload, error = inspect_payload("1", data, False)
    assert error is None
    assert payload["payloadKind"] == "image"
    assert payload["width"] == 16


def test_cliphist_binary_marker_is_image() -> None:
    entry = lightweight("7", "[[ binary data 12 KB png 32x20 ]]")
    assert entry["id"] == "7"
    assert entry["payloadKind"] == "image"


def test_html_source_is_preserved_as_plain_text() -> None:
    html = b'<html><body><img src="data:image/png;base64,AAAA"></body></html>'
    payload, error = inspect_payload("8", html, False)
    assert error is None
    assert payload["payloadKind"] == "text"
    assert payload["textSubtype"] == "plain"
    assert payload["mimeType"] == "text/plain;charset=utf-8"
    assert payload["preview"] == html.decode()


def test_html_markup_and_angle_bracket_text_keep_their_original_source() -> None:
    html_payload, html_error = inspect_payload("html", b"<p>Hello</p>", False)
    plain_payload, plain_error = inspect_payload("plain", b"<not-a-tag>", False)
    assert html_error is None
    assert plain_error is None
    assert html_payload["textSubtype"] == "plain"
    assert html_payload["preview"] == "<p>Hello</p>"
    assert plain_payload["textSubtype"] == "plain"


def test_restore_keeps_html_source_bytes_and_publishes_plain_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = b"<p>literal &amp; source</p>"
    copied: dict[str, object] = {}

    monkeypatch.setattr(backend, "executable", lambda name: name)
    monkeypatch.setattr(
        backend,
        "run",
        lambda program, arguments, *args, **kwargs: subprocess.CompletedProcess(
            [program, *arguments],
            0,
            stdout=source if program == "cliphist" else b"",
            stderr=b"",
        ),
    )

    def fake_wl_copy(program: str, arguments: list[str], input_data: bytes):
        copied.update(program=program, arguments=arguments, input_data=input_data)
        return subprocess.CompletedProcess([program, *arguments], 0)

    monkeypatch.setattr(backend, "run_wl_copy", fake_wl_copy)

    result = backend.run_command(SimpleNamespace(action="restore", id="12"))

    assert result.exit_code == 0
    assert result.json()["mimeType"] == "text/plain;charset=utf-8"
    assert copied == {
        "program": "wl-copy",
        "arguments": ["--type", "text/plain;charset=utf-8"],
        "input_data": source,
    }


def test_store_preserves_file_manager_mime_before_text(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[str], bytes | None]] = []

    monkeypatch.setattr(backend, "executable", lambda name: name)

    def fake_run(program: str, arguments: list[str], input_data: bytes | None = None, **_: object):
        calls.append((program, arguments, input_data))
        if program == "wl-paste" and arguments == ["--list-types"]:
            return subprocess.CompletedProcess(
                [program, *arguments],
                0,
                stdout=(b"text/plain\ntext/uri-list\nx-special/gnome-copied-files\n"),
                stderr=b"",
            )
        if program == "wl-paste":
            assert arguments == ["--type", "x-special/gnome-copied-files"]
            return subprocess.CompletedProcess(
                [program, *arguments],
                0,
                stdout=b"copy\nfile:///tmp/report.pdf\n",
                stderr=b"",
            )
        return subprocess.CompletedProcess([program, *arguments], 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(backend, "run", fake_run)
    result = backend.store("clipboard.store", "cliphist", {})

    assert result.exit_code == 0
    assert result.json()["selectedMime"] == "x-special/gnome-copied-files"
    assert calls[-1][2] == b"copy\nfile:///tmp/report.pdf\n"


def test_mime_priority_prefers_plain_text_over_html() -> None:
    assert select_mime(["text/plain", "text/html", "image/png"]) == "image/png"
    assert select_mime(["text/plain", "text/html"]) == "text/plain"
    assert select_mime(["text/html;charset=utf-8"]) == "text/html;charset=utf-8"
    assert select_mime(["text/plain;charset=utf-8", "text/plain"]) == "text/plain;charset=utf-8"


def test_watcher_store_uses_supplied_payload_without_rereading_clipboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str], bytes | None]] = []

    monkeypatch.setattr(backend, "executable", lambda name: name)

    def fake_run(program: str, arguments: list[str], input_data: bytes | None = None, **_: object):
        calls.append((program, arguments, input_data))
        return subprocess.CompletedProcess([program, *arguments], 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(backend, "run", fake_run)
    result = backend.store(
        "clipboard.store",
        "cliphist",
        {},
        b"<p>literal</p>",
        "text/plain;charset=utf-8",
    )

    assert result.exit_code == 0
    assert result.json()["selectedMime"] == "text/plain;charset=utf-8"
    assert calls == [("cliphist", ["store"], b"<p>literal</p>")]


def test_watcher_store_skips_an_empty_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend, "executable", lambda name: name)
    monkeypatch.setattr(
        backend,
        "run",
        lambda *args, **kwargs: pytest.fail("empty selection must not reach cliphist"),
    )

    result = backend.store("clipboard.store", "cliphist", {}, b"")

    assert result.exit_code == 0
    assert result.json()["stored"] is False


def test_gnome_uri_payload_has_operation_and_never_uses_operation_as_filename() -> None:
    operation, uris = parse_uri_list("copy\nfile:///tmp/one\nfile:///tmp/two\n")
    assert operation == "copy"
    assert uris == ["file:///tmp/one", "file:///tmp/two"]

    payload, error = inspect_payload("9", b"cut\nfile:///tmp/one\nfile:///tmp/two\n", False)
    assert error is None
    assert payload["payloadKind"] == "file-list"
    assert payload["mimeType"] == "x-special/gnome-copied-files"
    assert payload["fileOperation"] == "cut"
    assert payload["icon"] == "file_copy"
    assert [item["name"] for item in payload["files"]] == ["one", "two"]


def test_file_uri_metadata_is_local_but_recent_uri_is_not_a_path(tmp_path) -> None:
    image_path = tmp_path / "photo.png"
    image_path.write_bytes(b"not-an-image")
    metadata = file_metadata(image_path.as_uri())
    assert metadata["local"] is True
    assert metadata["exists"] is True
    assert metadata["category"] == "image"
    assert metadata["name"] == "photo.png"

    recent = file_metadata("recent:///abcdef")
    assert recent["local"] is False
    assert recent["exists"] is False
    assert recent["name"] == "abcdef"


@pytest.mark.parametrize(
    ("name", "category", "icon"),
    [
        ("directory", "folder", "folder"),
        ("photo.png", "image", "image"),
        ("movie.mp4", "video", "video_file"),
        ("sound.flac", "audio", "audio_file"),
        ("document.pdf", "pdf", "picture_as_pdf"),
        ("notes.txt", "document", "description"),
    ],
)
def test_file_metadata_keeps_file_type_icons(tmp_path, name: str, category: str, icon: str) -> None:
    path = tmp_path / name
    if category == "folder":
        path.mkdir()
    else:
        path.write_bytes(b"file payload")
    metadata = file_metadata(path.as_uri())
    assert metadata["category"] == category
    assert metadata["icon"] == icon


@pytest.mark.parametrize(
    "name",
    ["else_if.cpp", "generate_cookie.py", "test_wavy.js", "widget.qml", "run.sh"],
)
def test_file_metadata_classifies_source_and_script_files_as_code(tmp_path, name: str) -> None:
    path = tmp_path / name
    path.write_text("source", encoding="utf-8")

    metadata = file_metadata(path.as_uri())

    assert metadata["category"] == "code"
    assert metadata["icon"] == "code"


def test_file_metadata_keeps_plain_documents_out_of_code_category(tmp_path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("notes", encoding="utf-8")

    metadata = file_metadata(path.as_uri())

    assert metadata["category"] == "document"
    assert metadata["icon"] == "description"


def test_plain_path_text_is_not_promoted_to_file(tmp_path) -> None:
    existing_path = tmp_path / "existing-folder"
    existing_path.mkdir()
    payload, error = inspect_payload("10", (str(existing_path) + "\n").encode(), False)
    assert error is None
    assert payload["payloadKind"] == "text"
    assert payload["files"] == []


def test_wl_copy_does_not_capture_forked_selection_owner_pipes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeStdin:
        def write(self, value: bytes) -> None:
            captured["written"] = value

        def close(self) -> None:
            captured["stdin_closed"] = True

    class FakeProcess:
        stdin = FakeStdin()
        returncode = None

        def poll(self):
            return self.returncode

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(backend.subprocess, "Popen", fake_popen)
    result = run_wl_copy("wl-copy", ["--type", "text/plain"], b"hello")

    assert result is not None
    assert captured["argv"] == ["wl-copy", "--foreground", "--type", "text/plain"]
    assert captured["written"] == b"hello"
    assert captured["stdin_closed"] is True
    assert captured["stdin"] is subprocess.PIPE
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.DEVNULL
    assert captured["start_new_session"] is True


def test_wl_copy_reports_owner_start_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingStdin:
        def write(self, value: bytes) -> None:
            return None

        def close(self) -> None:
            return None

    class FailingProcess:
        stdin = FailingStdin()
        returncode = 7

        def poll(self):
            return self.returncode

    monkeypatch.setattr(
        backend.subprocess,
        "Popen",
        lambda *args, **kwargs: FailingProcess(),
    )
    result = run_wl_copy("wl-copy", [], b"payload")
    assert result is not None
    assert result.returncode == 7
