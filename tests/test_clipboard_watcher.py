from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from key_cli.clipboard import backend


def test_watcher_lock_is_single_instance_and_status_uses_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    first = backend.acquire_watcher_lock()
    try:
        assert first.name.endswith("clipboard-watch.lock")
        assert first.fileno() >= 0
        assert os.get_inheritable(first.fileno()) is True
        assert backend.watcher_running() is True
        with pytest.raises(BlockingIOError):
            backend.acquire_watcher_lock()
    finally:
        first.close()

    assert backend.watcher_running() is False


def test_clipboard_status_has_capabilities_without_watcher(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(
        backend,
        "executable",
        lambda name: f"/usr/bin/{name}",
    )

    result = backend.run_command(SimpleNamespace(action="status"))
    payload = result.json()

    assert result.exit_code != 0
    assert payload["schemaVersion"] == 1
    assert payload["watcherRunning"] is False
    assert payload["capabilities"] == {
        "inspect": True,
        "preview": True,
        "mimeRestore": True,
        "mimeAwareStore": True,
    }
    assert payload["error"]["code"] == "cliphist_watcher_inactive"


def test_clipboard_service_is_packaged_by_key_cli() -> None:
    unit = Path(__file__).parents[1] / "systemd/user/clavis-clipboard.service"
    text = unit.read_text(encoding="utf-8")
    assert "ExecStart=key clipboard watch" in text
    assert "Requisite=niri.service" in text
    assert "PartOf=niri.service" in text
    assert "WantedBy=niri.service" in text
