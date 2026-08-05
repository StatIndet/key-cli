from __future__ import annotations

import os

from key_cli.utils.process import capture, matches, wait_for_identity


def test_current_process_identity_is_verifiable() -> None:
    identity = capture(os.getpid())
    assert identity.start_ticks > 0
    assert matches(identity, identity.executable)


def test_identity_wait_returns_a_verifiable_process() -> None:
    identity = wait_for_identity(os.getpid())
    assert identity.start_ticks > 0
    assert matches(identity, identity.executable)
