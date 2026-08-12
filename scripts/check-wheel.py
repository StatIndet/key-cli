#!/usr/bin/env python3
"""Check the files that key-cli promises to ship in its wheel."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check-wheel.py WHEEL", file=sys.stderr)
        return 2

    wheel = Path(sys.argv[1])
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        dist_info = sorted(
            name.split("/", 1)[0] for name in names if name.endswith(".dist-info/METADATA")
        )
        if not dist_info:
            raise SystemExit("wheel has no dist-info metadata")
        if "key_cli/__init__.py" not in names:
            raise SystemExit("wheel does not contain the key_cli package")

        entry_points = f"{dist_info[0]}/entry_points.txt"
        if entry_points not in names:
            raise SystemExit("wheel does not contain console entry points")
        if b"key = key_cli:main" not in archive.read(entry_points):
            raise SystemExit("wheel entry point does not expose key_cli:main")

        service_suffixes = (
            ".data/data/lib/systemd/user/clavis-clipboard.service",
            ".data/data/systemd/user/clavis-clipboard.service",
        )
        if not any(name.endswith(service_suffixes) for name in names):
            raise SystemExit("wheel does not contain clavis-clipboard.service")

    print(f"wheel package check passed: {wheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
