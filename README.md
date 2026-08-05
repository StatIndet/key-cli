# key-cli

`key-cli` is the small, stable Python command boundary for Clavis. It owns
external command orchestration, Quickshell lifecycle/IPC forwarding, screen and
audio-file recording, and the cliphist backend. Long-running reactive models,
native QML plugins, and system metrics belong to the other Clavis projects.

## Commands

```text
key shell
key ipc
key record
key audio
key clipboard
key doctor
key version
```

`key shell` maps directly to the installed Quickshell configuration named
`clavis`; it does not discover a source tree or build anything. `key ipc` is the
stable user-facing wrapper around the same configuration.

## Development and packaging

This is a standard Python `src`-layout project with a `key` console script:

```bash
python -m key_cli --help
python -m build --wheel
sudo python -m installer dist/*.whl
```

The wheel has no Python runtime dependencies. `qs`, `gpu-screen-recorder`,
`slurp`, `ffmpeg`, `ffprobe`, `pactl`, `cliphist`, `wl-copy`, and `wl-paste`
are system executables; `key doctor --json` reports their availability and
affected capabilities. End users should normally install the package through
the distribution package manager or AUR.

Recording state is stored with owner-only permissions under
`$XDG_RUNTIME_DIR/key/`. A stop operation verifies both the saved PID and Linux
process start time before sending the interrupt/terminate/kill sequence.

## Tests

```bash
python -m compileall src
python -m build --wheel
python -m pytest
```
