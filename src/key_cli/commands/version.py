from __future__ import annotations

import platform
import sys

from .. import __version__
from ..utils.output import Result, ok


def run(args) -> Result:
    payload = {
        "schemaVersion": 1,
        "name": "key-cli",
        "version": __version__,
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "executable": sys.executable,
    }
    return ok("version", f"key-cli {__version__}", **payload)
