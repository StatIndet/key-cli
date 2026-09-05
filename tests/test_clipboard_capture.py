from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl69AAAAABJRU5ErkJggg=="
)


@pytest.fixture
def clipboard_cli(tmp_path):
    tools = tmp_path / "bin"
    tools.mkdir()
    double = Path(__file__).parent / "fixtures" / "clipboard_tools.py"
    for name in ("wl-paste", "wl-copy", "cliphist"):
        target = tools / name
        target.write_text(f"#!{sys.executable}\n" + double.read_text())
        target.chmod(0o755)
    key = tools / "key"
    key.write_text(f"#!{sys.executable}\nfrom key_cli import main\nraise SystemExit(main())\n")
    key.chmod(0o755)
    env = os.environ.copy()
    env.update(
        PATH=str(tools) + os.pathsep + env.get("PATH", ""),
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
        XDG_RUNTIME_DIR=str(tmp_path),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
        CLIPBOARD_TEST_DIR=str(tmp_path),
    )
    env.pop("CLIPBOARD_TYPE", None)
    env.pop("CLIPBOARD_STATE", None)

    def invoke(action, offer=None, **config):
        if offer is not None:
            config["offer"] = {
                mime: base64.b64encode(payload).decode() for mime, payload in offer.items()
            }
            (tmp_path / "offer.json").write_text(json.dumps(config))
        return subprocess.run(
            [str(key), "clipboard", *action, "--format", "json"],
            env=env,
            capture_output=True,
            timeout=10,
        )

    return tmp_path, invoke


@pytest.mark.parametrize(
    ("offer", "expected", "kind", "mime"),
    [
        (
            {"text/plain": b" \t<literal>&amp;\r\n"},
            b" \t<literal>&amp;\r\n",
            "text",
            "text/plain;charset=utf-8",
        ),
        (
            {"text/plain": b"plain", "text/html": b"<b>plain</b>"},
            b"plain",
            "text",
            "text/plain;charset=utf-8",
        ),
        (
            {"text/plain": b"alt", "text/html": b"<img>", "image/png": PNG},
            PNG,
            "image",
            "image/png",
        ),
        (
            {"text/html": b"<b>literal &amp;</b>"},
            b"<b>literal &amp;</b>",
            "text",
            "text/plain;charset=utf-8",
        ),
        (
            {"text/plain": b"a", "x-special/gnome-copied-files": b"cut\nfile:///tmp/a\n"},
            b"cut\nfile:///tmp/a\n",
            "file",
            "x-special/gnome-copied-files",
        ),
    ],
)
def test_watcher_capture_and_inspect(clipboard_cli, offer, expected, kind, mime):
    root, invoke = clipboard_cli
    captured = invoke(["watch"], offer)
    assert captured.returncode == 0, captured.stderr.decode()
    assert (root / "stored").read_bytes() == expected
    inspected = invoke(["inspect", "1"])
    assert inspected.returncode == 0, inspected.stderr.decode()
    payload = json.loads(inspected.stdout)
    assert payload["payloadKind"] == kind
    assert payload["mimeType"] == mime
    if kind == "text":
        assert payload["preview"] == expected.decode()
        assert payload["searchText"] == expected.decode()
    calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
    if list(offer) == ["text/plain"]:
        assert not any(name == "wl-paste" and "--type" in args for name, args in calls)


def test_direct_store_does_not_append_newline(clipboard_cli):
    root, invoke = clipboard_cli
    captured = invoke(["store"], {"text/plain": b"no final newline"})
    assert captured.returncode == 0
    assert (root / "stored").read_bytes() == b"no final newline"


@pytest.mark.parametrize("config", [{"listFails": True}, {"readFails": "image/png"}])
def test_watcher_keeps_captured_text_when_offer_cannot_be_read(clipboard_cli, config):
    root, invoke = clipboard_cli
    result = invoke(["watch"], {"text/plain": b"captured", "image/png": PNG}, **config)
    assert result.returncode == 0
    assert (root / "stored").read_bytes() == b"captured"


@pytest.mark.parametrize("state", ["sensitive", "nil"])
def test_watcher_skips_sensitive_and_empty_events(clipboard_cli, state):
    root, invoke = clipboard_cli
    result = invoke(
        ["watch"], {"text/plain": b"secret" if state == "sensitive" else b""}, state=state
    )
    assert result.returncode == 0
    assert not (root / "stored").exists()
    calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
    assert not any(name == "cliphist" or args == ["--list-types"] for name, args in calls)


def test_unsupported_offer_is_not_stored(clipboard_cli):
    root, invoke = clipboard_cli
    result = invoke(["watch"], {"application/octet-stream": b"unknown"})
    assert result.returncode != 0
    assert not (root / "stored").exists()


def test_watcher_without_type_environment_reads_selected_representation(clipboard_cli):
    root, invoke = clipboard_cli
    result = invoke(["watch"], {"text/plain": b"literal"}, publishType=False)
    assert result.returncode == 0
    assert (root / "stored").read_bytes() == b"literal"


def test_sensitive_offer_hint_skips_capture(clipboard_cli):
    root, invoke = clipboard_cli
    result = invoke(["watch"], {"text/plain": b"secret", "x-kde-passwordManagerHint": b"secret"})
    assert result.returncode == 0
    assert not (root / "stored").exists()


def test_changed_offer_keeps_the_captured_representation(clipboard_cli):
    root, invoke = clipboard_cli
    result = invoke(
        ["watch"],
        {"image/png": PNG},
        stdinMime="text/plain",
        stdinData=base64.b64encode(b"earlier text").decode(),
    )
    assert result.returncode == 0
    assert (root / "stored").read_bytes() == b"earlier text"


@pytest.mark.parametrize(
    "mime",
    [
        "text/markdown",
        "text/css",
        "text/csv",
        "text/xml",
        "text/x-notes",
        "application/json",
        "application/xml",
        "application/xhtml+xml",
        "text/markdown; charset=UTF-8",
        "text/plain; charset=utf-8",
    ],
)
def test_textual_offers_are_literal_through_watcher_and_inspect(clipboard_cli, mime):
    root, invoke = clipboard_cli
    source = ' \t# 标题\n**bold** <b>&amp;</b> {"a": 1}\r\n'.encode()
    captured = invoke(["watch"], {mime: source})
    assert captured.returncode == 0, captured.stderr.decode()
    assert (root / "stored").read_bytes() == source
    inspected = invoke(["inspect", "1"])
    assert inspected.returncode == 0
    payload = json.loads(inspected.stdout)
    assert payload["payloadKind"] == "text"
    assert payload["textSubtype"] == "plain"
    assert payload["mimeType"] == "text/plain;charset=utf-8"
    assert payload["preview"] == source.decode()
    assert payload["searchText"] == source.decode()
    assert payload["schemaVersion"] == 1
    assert payload["command"] == "clipboard.inspect"
    assert payload["ok"] is True
    assert payload["error"] is None
    assert payload["capabilities"]["singleRepresentation"] is True
    assert payload["capabilities"]["multiMime"] is False
    assert payload["capabilities"]["originalMimePreserved"] is False
    restored = invoke(["restore", "1"])
    assert restored.returncode == 0, restored.stderr.decode()
    assert json.loads(restored.stdout)["mimeType"] == "text/plain;charset=utf-8"
    assert (root / "copied").read_bytes() == source
    assert json.loads((root / "copy-args.json").read_text()) == [
        "--foreground",
        "--type",
        "text/plain;charset=utf-8",
    ]


def test_watcher_prefers_markdown_over_html(clipboard_cli):
    root, invoke = clipboard_cli
    source = b"**literal**"
    result = invoke(
        ["watch"], {"text/html": b"<b>literal</b>", "text/markdown": source}, stdinMime="text/html"
    )
    assert result.returncode == 0
    assert (root / "stored").read_bytes() == source


@pytest.mark.parametrize(
    ("source", "mime"),
    [
        (PNG, "image/png"),
        (b"cut\nfile:///tmp/a\n", "x-special/gnome-copied-files"),
        (b"file:///tmp/a\nfile:///tmp/b\n", "text/uri-list"),
    ],
)
def test_watcher_restores_single_semantic_representation(clipboard_cli, source, mime):
    root, invoke = clipboard_cli
    captured = invoke(["watch"], {mime: source})
    assert captured.returncode == 0
    restored = invoke(["restore", "1"])
    assert restored.returncode == 0, restored.stderr.decode()
    payload = json.loads(restored.stdout)
    assert payload["schemaVersion"] == 1
    assert payload["command"] == "clipboard.restore"
    assert payload["ok"] is True
    assert payload["error"] is None
    assert payload["mimeType"] == mime
    assert payload["capabilities"]["originalMimePreserved"] is False
    assert (root / "copied").read_bytes() == source
    assert json.loads((root / "copy-args.json").read_text()) == ["--foreground", "--type", mime]
