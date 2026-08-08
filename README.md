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
# First installation only:
sudo python -m installer dist/key_cli-0.2.0-py3-none-any.whl
# If /usr/bin/key or this package is already installed, use:
# sudo python -m installer --overwrite-existing dist/key_cli-0.2.0-py3-none-any.whl
```

The wheel has no Python runtime dependencies. `qs`, `gpu-screen-recorder`,
`slurp`, `ffmpeg`, `ffprobe`, `pactl`, `cliphist`, `wl-copy`, and `wl-paste`
are system executables; `key doctor --json` reports their availability and
affected capabilities. End users should normally install the package through
the distribution package manager or AUR.

The wheel also owns `clavis-clipboard.service`. A standard system installation
provides:

```text
/usr/bin/key
/usr/lib/pythonX.Y/site-packages/key_cli/
/usr/lib/systemd/user/clavis-clipboard.service
```

The service runs `key clipboard watch` in the foreground and is independent of
the Quickshell process. It is enabled once per user session:

```bash
systemctl --user daemon-reload
systemctl --user enable --now clavis-clipboard.service
```

`enable` attaches the service to `niri.service`; `--now` starts the watcher in
the current session. Do not add another `key clipboard watch` to Niri's
`spawn-at-startup`, and do not start a second manual watcher.

For a first installation, use the ordinary installer command above. If
`/usr/bin/key` or the Python package already exists, the ordinary installer
intentionally refuses to overwrite it; use `--overwrite-existing` instead.
After either installation, reload the user systemd manager. For a routine
upgrade, install the newly built wheel, reload the manager, and restart the
existing service:

```bash
python -m build --wheel
sudo python -m installer --overwrite-existing dist/key_cli-0.2.0-py3-none-any.whl
systemctl --user daemon-reload
systemctl --user restart clavis-clipboard.service
```

`installer` 默认拒绝覆盖已存在文件；日常升级必须使用上面的
`--overwrite-existing`。它会同时更新 Python package、`/usr/bin/key` 和 systemd
user unit，不需要另一个 Python unit installer。

Recording state is stored with owner-only permissions under
`$XDG_RUNTIME_DIR/key/`. A stop operation verifies both the saved PID and Linux
process start time before sending the interrupt/terminate/kill sequence. GIF
recording uses a supported temporary MP4 container and converts it with FFmpeg
after stopping; the completion notification is sent only after that conversion
finishes. Screen recording does not send a start notification.

## Tests

```bash
python -m compileall src
python -m build --wheel
python -m pytest
```
