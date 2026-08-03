#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))
from clavis_paths import ClavisPaths  # noqa: E402
from components import ComponentError, install_component, uninstall_component, update_component  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="key-cli-components-") as directory:
        root = Path(directory)
        source = root / "keytop"
        source.mkdir()
        (source / "setup.sh").write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "target=\"${CMAKE_INSTALL_PREFIX:?}/component-marker\"\n"
            "case \"${1:-}\" in\n"
            "  install) mkdir -p \"$(dirname \"$target\")\"; printf installed > \"$target\";;\n"
            "  uninstall) rm -f -- \"$target\";;\n"
            "  *) exit 2;;\n"
            "esac\n",
            encoding="utf-8",
        )
        (source / "setup.sh").chmod(0o755)
        subprocess.run(["git", "-C", str(source), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.name", "Component Test"], check=True)
        subprocess.run(["git", "-C", str(source), "add", "setup.sh"], check=True)
        subprocess.run(["git", "-C", str(source), "commit", "-qm", "initial"], check=True)

        home = root / "home"
        environment = {
            "HOME": str(home),
            "CLAVIS_INSTALL_PREFIX": str(root / "clavis"),
            "CLAVIS_BIN_HOME": str(root / "bin"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_DATA_HOME": str(root / "data"),
            "XDG_STATE_HOME": str(root / "state"),
            "XDG_CACHE_HOME": str(root / "cache"),
            "CLAVIS_COMPONENT_PREFIX": str(root / "prefix"),
        }
        old = os.environ.copy()
        os.environ.update(environment)
        try:
            paths = ClavisPaths.from_environment()
            record = install_component(paths, "keytop", str(source))
            assert record["dirty"] is False
            assert (root / "prefix/component-marker").read_text() == "installed"
            (source / "local-change").write_text("dirty", encoding="utf-8")
            try:
                update_component(paths, "keytop")
            except ComponentError as error:
                assert "local modifications" in str(error)
            else:
                raise AssertionError("dirty source update was accepted")
            uninstall_component(paths, "keytop")
            assert not (root / "prefix/component-marker").exists()
        finally:
            os.environ.clear()
            os.environ.update(old)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
