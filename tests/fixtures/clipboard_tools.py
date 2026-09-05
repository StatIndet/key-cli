"""Finite command doubles for clipboard capture integration tests.

No real clipboard or user history is accessed. The wl-paste double models one
watch event, including its default text preference and newline behavior.
"""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

root = Path(os.environ["CLIPBOARD_TEST_DIR"])
config = json.loads((root / "offer.json").read_text())
program = Path(sys.argv[0]).name
args = sys.argv[1:]
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps([program, args]) + "\n")

if program == "cliphist":
    if args == ["store"]:
        (root / "stored").write_bytes(sys.stdin.buffer.read())
    elif args == ["decode"]:
        sys.stdin.buffer.read()
        sys.stdout.buffer.write((root / "stored").read_bytes())
    else:
        sys.exit(2)
elif program == "wl-paste":
    offer = config["offer"]
    if "--watch" in args:
        mime = config.get("stdinMime") or next(
            (m for m in ("text/plain;charset=utf-8", "text/plain") if m in offer),
            next(iter(offer), ""),
        )
        env = os.environ.copy()
        env["CLIPBOARD_STATE"] = config.get("state", "data")
        if config.get("publishType", True):
            env["CLIPBOARD_TYPE"] = mime
        else:
            env.pop("CLIPBOARD_TYPE", None)
        payload = base64.b64decode(config.get("stdinData", offer.get(mime, "")))
        result = subprocess.run(args[args.index("--watch") + 1 :], input=payload, env=env)
        sys.exit(result.returncode)
    elif args == ["--list-types"]:
        if config.get("listFails"):
            sys.exit(1)
        print("\n".join(offer))
    elif "--type" in args:
        mime = args[args.index("--type") + 1]
        if mime == config.get("readFails"):
            sys.exit(1)
        sys.stdout.buffer.write(base64.b64decode(offer[mime]))
        if "--no-newline" not in args and mime.startswith("text/"):
            sys.stdout.buffer.write(b"\n")
    else:
        sys.exit(2)
else:
    sys.exit(2)
