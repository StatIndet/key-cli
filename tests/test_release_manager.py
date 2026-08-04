#!/usr/bin/env python3
from __future__ import annotations

import json
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))
from clavis_paths import ClavisPaths  # noqa: E402
MANAGER_SPEC = importlib.util.spec_from_file_location(
    "clavis_manager", Path(__file__).resolve().parents[1] / "packaging/clavis-manager.py"
)
assert MANAGER_SPEC and MANAGER_SPEC.loader
MANAGER = importlib.util.module_from_spec(MANAGER_SPEC)
sys.modules["clavis_manager"] = MANAGER
MANAGER_SPEC.loader.exec_module(MANAGER)
finalize_install = MANAGER.finalize_install
load_manifest = MANAGER.load_manifest
release_command = MANAGER.release_command
rollback = MANAGER.rollback
resolve_active_release = MANAGER.resolve_active_release


def create_partial(paths: ClavisPaths, release: str, commit: str) -> Path:
    partial = paths.releases_home / f"{release}.partial"
    (partial / "share/clavis/qml").mkdir(parents=True)
    (partial / "lib/qml/Clavis/Runtime").mkdir(parents=True)
    (partial / "lib/qml/M3Shapes").mkdir(parents=True)
    (partial / "share/clavis/qml/shell.qml").write_text("import QtQuick\nItem {}\n", encoding="utf-8")
    (partial / "lib/qml/Clavis/Runtime/qmldir").write_text("module Clavis.Runtime\n", encoding="utf-8")
    (partial / "lib/qml/M3Shapes/qmldir").write_text("module M3Shapes\n", encoding="utf-8")
    metadata = {
        "component": "quickshell",
        "release": release,
        "version": release,
        "commit": commit,
        "channel": "test",
        "sourceFingerprint": commit,
        "buildTime": "2026-08-03T00:00:00Z",
        "minimumKeyCli": "0.1.0",
        "minimumKeytop": "0.1.0",
        "shellProtocol": 1,
        "qmlRoot": "share/clavis/qml",
        "pluginRoot": "lib/qml",
        "assetsRoot": "share/clavis/assets",
        "protocols": {"core": 1, "clipboard": 2},
        "dataSchemas": {"config": 1, "manifest": 1, "profile": 1},
    }
    (partial / "release.json").write_text(json.dumps(metadata), encoding="utf-8")
    return partial


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="key-cli-release-") as directory:
        root = Path(directory)
        old = os.environ.copy()
        os.environ.update(
            {
                "HOME": str(root / "home"),
                "CLAVIS_INSTALL_PREFIX": str(root / "clavis"),
                "CLAVIS_BIN_HOME": str(root / "bin"),
                "CLAVIS_KEY": str(root / "system-bin/key"),
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_DATA_HOME": str(root / "data"),
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CACHE_HOME": str(root / "cache"),
                "CLAVIS_SKIP_SYSTEMD": "1",
            }
        )
        try:
            paths = ClavisPaths.from_environment()
            assert paths.stable_key == root / "system-bin/key"
            first = create_partial(paths, "2026.08.03", "first")
            finalize_install(paths, first, "2026.08.03")
            assert resolve_active_release(paths).name == "2026.08.03"
            assert not (paths.current_release / "bin/key").exists()
            assert paths.as_environment(resolve_active_release(paths))["CLAVIS_KEY"] \
                == str(root / "system-bin/key")

            second = create_partial(paths, "2026.08.03.1", "second")
            finalize_install(paths, second, "2026.08.03.1")
            assert resolve_active_release(paths).name == "2026.08.03.1"
            rollback(paths, "2026.08.03", False)
            assert resolve_active_release(paths).name == "2026.08.03"
            manifest = load_manifest(paths)
            assert manifest["activeRelease"] == "2026.08.03"
            assert manifest["launcher"] is None
            assert all("bin/key" not in record["path"] for record in manifest["releases"]["2026.08.03"]["files"])
            release_command(paths, "remove", "2026.08.03.1", False, False)
            assert not (paths.releases_home / "2026.08.03.1").exists()
        finally:
            os.environ.clear()
            os.environ.update(old)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
