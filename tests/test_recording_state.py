from __future__ import annotations

import os

from key_cli.recording import common
from key_cli.utils.process import capture, executable


def test_matching_error_session_is_recovered_without_signaling(monkeypatch) -> None:
    identity = capture(os.getpid())
    saved = []
    state = {
        "schemaVersion": 1,
        "state": "error",
        "pid": identity.pid,
        "processStartTicks": str(identity.start_ticks),
        "temporaryPath": "",
        "error": {"code": "recorder_start_failed"},
    }
    monkeypatch.setattr(common, "load", lambda kind: dict(state))
    monkeypatch.setattr(common, "save", lambda kind, value: saved.append(dict(value)))

    recovered, active = common.active_state("record", executable(os.getpid()))

    assert active is True
    assert recovered["state"] == "recording"
    assert recovered["error"] is None
    assert saved and saved[-1]["state"] == "recording"
