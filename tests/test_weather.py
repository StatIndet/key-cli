#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROVIDER = ROOT / "packaging/weather.py"
FIXTURE = Path(__file__).resolve().parent / "fixtures/weather.json"


def run(*arguments: str, cache: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(PROVIDER), "--json", *arguments, "--cache", str(cache)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(cache.parent)},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="key-cli-weather-") as directory:
        cache = Path(directory) / "forecast.json"
        fresh = run("--fixture", str(FIXTURE), "--refresh", cache=cache)
        assert fresh["valid"] is True
        assert fresh["status"] == "fresh"
        assert fresh["current"]["weatherText"] == "Partly cloudy"
        assert fresh["current"]["airQuality"] == {}
        assert len(fresh["daily"]) == 1
        cached = run(cache=cache)
        assert cached["status"] == "cache"

        # A malformed fixture must preserve the last usable snapshot as stale.
        broken = Path(directory) / "broken.json"
        broken.write_text("{}", encoding="utf-8")
        stale = run("--fixture", str(broken), "--refresh", cache=cache)
        assert stale["status"] == "stale"
        assert stale["valid"] is True
        assert "fixture" in stale["errorMessage"]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
