from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from key_cli.utils.process import capture, matches, wait_for_identity


def test_current_process_identity_is_verifiable() -> None:
    identity = capture(os.getpid())
    assert identity.start_ticks > 0
    assert matches(identity, identity.executable)


def test_identity_wait_returns_a_verifiable_process() -> None:
    identity = wait_for_identity(os.getpid())
    assert identity.start_ticks > 0
    assert matches(identity, identity.executable)


def test_identity_wait_can_wait_for_the_recorder_argument(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    marker.touch()
    tail = shutil.which("tail")
    assert tail is not None
    process = subprocess.Popen([tail, "-f", str(marker)])
    try:
        identity = wait_for_identity(process.pid, "tail", str(marker))
        assert identity.start_ticks > 0
        assert matches(identity, "tail", str(marker))
    finally:
        process.terminate()
        process.wait(timeout=2)
