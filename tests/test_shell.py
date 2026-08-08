from __future__ import annotations

import os
from types import SimpleNamespace

from key_cli.commands import shell
from key_cli.utils import executable


def test_shell_propagates_the_invoking_key_path(tmp_path, monkeypatch) -> None:
    key = tmp_path / "key"
    key.write_text("#!/bin/sh\n", encoding="utf-8")
    key.chmod(0o700)
    monkeypatch.setenv("CLAVIS_KEY", "/usr/bin/stale-key")
    monkeypatch.setattr(executable.sys, "argv", [str(key)])
    monkeypatch.setattr(shell, "qs_command", lambda: "/usr/bin/qs")

    captured = {}

    def fake_run(argv, *, check, env):
        captured["argv"] = argv
        captured["env"] = env
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(shell.subprocess, "run", fake_run)
    result = shell.run_qs(["-c", "clavis", "-n"], "shell.start")

    assert result.exit_code == 0
    assert captured["argv"] == ["/usr/bin/qs", "-c", "clavis", "-n"]
    assert captured["env"]["CLAVIS_KEY"] == os.path.realpath(key)
