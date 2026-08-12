from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from ..recording.common import (
    active_state,
    new_state,
    now_ms,
    output_directory,
    process_fields,
    response,
    spawn,
)
from ..recording.state import StateError, load, locked, save
from ..utils.output import (
    DEPENDENCY_FAILURE,
    GENERAL_FAILURE,
    POSTPROCESS_FAILURE,
    RECORDER_START_FAILURE,
    RECORDER_STOP_FAILURE,
    Result,
    error,
    fail,
)
from ..utils.process import matches, stop_verified, wait_for_identity


GEOMETRY = re.compile(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$")


def _geometry(value: str) -> str | None:
    match = GEOMETRY.fullmatch(value.strip())
    if not match or int(match.group(1)) <= 0 or int(match.group(2)) <= 0:
        return None
    return (
        f"{int(match.group(1))}x{int(match.group(2))}+{int(match.group(3))}+{int(match.group(4))}"
    )


def _select_region() -> str | None:
    slurp = shutil.which("slurp")
    if not slurp:
        return None
    try:
        process = subprocess.run(
            [slurp, "-f", "%wx%h+%x+%y"], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    return _geometry(process.stdout.strip()) if process.returncode == 0 else None


def _notify(title: str, body: str) -> None:
    notify = shutil.which("notify-send")
    if notify:
        try:
            subprocess.Popen(
                [notify, "-a", "Clavis Shell", title, body],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass


def _gif_filter(fps: int) -> str:
    safe_fps = max(1, min(240, int(fps or 60)))
    return (
        f"fps={safe_fps},split[s0][s1];"
        "[s0]palettegen=stats_mode=diff[p];"
        "[s1][p]paletteuse=dither=sierra2_4a"
    )


def _convert_gif(temporary: Path, output: Path, fps: int) -> tuple[bool, str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False, "ffmpeg is required to process GIF recordings"

    command = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(temporary),
        "-filter_complex",
        _gif_filter(fps),
        "-an",
        "-f",
        "gif",
        str(output),
    ]
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return False, str(exc)

    if process.returncode != 0:
        message = (process.stderr or process.stdout or "ffmpeg GIF conversion failed").strip()
        return False, message[-1024:]
    try:
        if not output.is_file() or output.stat().st_size == 0:
            return False, "ffmpeg produced an empty GIF"
    except OSError as exc:
        return False, str(exc)
    return True, ""


def run(args) -> Result:
    try:
        with locked("record"):
            if args.action == "start":
                return start(args)
            if args.action == "status":
                return status(args)
            if args.action == "stop":
                return stop(args)
            if args.action in {"pause", "resume"}:
                return toggle_pause(args)
    except StateError as exc:
        return fail(f"record.{args.action}", 4, "recording_busy", str(exc))
    except RuntimeError as exc:
        return fail(f"record.{args.action}", 5, "runtime_directory_unavailable", str(exc))
    return fail("record", 2, "usage_error", "unknown record action")


def start(args) -> Result:
    state = load("record")
    _, active = active_state("record", "gpu-screen-recorder", state.get("temporaryPath", ""))
    if active:
        return response(
            state,
            "record.start",
            ok_value=False,
            error_value=error("recording_already_active", "A screen recording is already active"),
            exitCode=4,
        )

    gsr = shutil.which("gpu-screen-recorder")
    if not gsr:
        return fail(
            "record.start",
            DEPENDENCY_FAILURE,
            "dependency_missing",
            "gpu-screen-recorder is not installed",
            exitCode=DEPENDENCY_FAILURE,
        )
    if args.type not in {"video", "gif"}:
        return fail(
            "record.start", 2, "usage_error", "recording type must be video or gif", exitCode=2
        )
    if args.type == "gif" and not shutil.which("ffmpeg"):
        return fail(
            "record.start",
            DEPENDENCY_FAILURE,
            "dependency_missing",
            "ffmpeg is required for GIF recording",
            dependency="ffmpeg",
            exitCode=DEPENDENCY_FAILURE,
        )
    geometry = _geometry(args.geometry) if args.geometry else None
    if args.target == "region" and geometry is None:
        geometry = _select_region()
    if args.target == "region" and geometry is None:
        return fail(
            "record.start",
            2,
            "selection_cancelled",
            "screen region selection was cancelled",
            cancelled=True,
            exitCode=2,
        )
    if not 1 <= args.fps <= 240:
        return fail("record.start", 2, "usage_error", "fps must be between 1 and 240", exitCode=2)
    directory = output_directory(args.output, "record")
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not os.access(directory, os.W_OK):
            raise OSError("output directory is not writable")
    except OSError as exc:
        return fail(
            "record.start",
            GENERAL_FAILURE,
            "output_directory_unwritable",
            str(exc),
            exitCode=GENERAL_FAILURE,
        )

    suffix = "gif" if args.type == "gif" else "mp4"
    temporary_suffix = "mp4" if args.type == "gif" else suffix
    stem = f"recording_{datetime.now().strftime('%Y%m%d_%H-%M-%S')}_{os.getpid()}"
    output = directory / f"{stem}.{suffix}"
    temporary = directory / f".{stem}.partial.{temporary_suffix}"
    state = new_state("record")
    state.update(
        {
            "state": "starting",
            "type": args.type,
            "target": {"type": args.target, "geometry": geometry},
            "fps": args.fps,
            "audio": args.audio,
            "temporaryPath": str(temporary),
            "outputPath": str(output),
        }
    )
    command = ["-w"]
    if args.target == "region":
        command += ["region", "-region", geometry or ""]
    else:
        command += ["screen"]
    command += ["-f", str(args.fps)]
    if args.audio != "none":
        command += ["-a", "default_output", "-ac", "aac"]
    command += ["-o", str(temporary)]
    process, start_error = spawn(
        gsr, command, Path(temporary).with_suffix(Path(temporary).suffix + ".log")
    )
    if process is None:
        state["state"] = "error"
        state["error"] = error("recorder_start_failed", start_error or "unable to start recorder")
        save("record", state)
        return response(
            state,
            "record.start",
            ok_value=False,
            error_value=state["error"],
            exitCode=RECORDER_START_FAILURE,
        )
    identity = wait_for_identity(process.pid, "gpu-screen-recorder", str(temporary))
    state.update(
        {
            "state": "recording",
            "pid": process.pid,
            "processStartTicks": str(identity.start_ticks),
            "processStartedAtMs": now_ms(),
        }
    )
    if not identity.start_ticks or not matches(identity, "gpu-screen-recorder", str(temporary)):
        # This PID was created by this start operation.  If its identity is
        # still verifiable, stop it before reporting failure so a startup
        # race cannot leave an untracked recorder running.
        if identity.start_ticks and matches(identity, "gpu-screen-recorder"):
            stop_verified(identity, "gpu-screen-recorder")
        state["state"] = "error"
        state["error"] = error(
            "recorder_start_failed", "recorder exited before its identity could be verified"
        )
        state["pid"] = 0
        state["processStartTicks"] = None
        save("record", state)
        return response(
            state,
            "record.start",
            ok_value=False,
            error_value=state["error"],
            exitCode=RECORDER_START_FAILURE,
        )
    save("record", state)
    return response(state, "record.start", ok_value=True, error_value=None, exitCode=0)


def status(args) -> Result:
    state, active = active_state(
        "record", "gpu-screen-recorder", load("record").get("temporaryPath", "")
    )
    return response(
        state,
        "record.status",
        ok_value=not state.get("error"),
        error_value=state.get("error"),
        exitCode=0 if not state.get("error") else 5,
    )


def _finalize(state: dict) -> Result:
    temporary = Path(state.get("temporaryPath", ""))
    output = Path(state.get("outputPath", ""))
    if not temporary.is_file() or temporary.stat().st_size == 0:
        state["state"] = "error"
        state["error"] = error("recording_output_invalid", "recording output is missing or empty")
        state["pid"] = 0
        save("record", state)
        return response(
            state,
            "record.stop",
            ok_value=False,
            error_value=state["error"],
            exitCode=POSTPROCESS_FAILURE,
        )
    try:
        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if state.get("type") == "gif":
            converted, conversion_error = _convert_gif(
                temporary,
                output,
                int(state.get("fps") or 60),
            )
            if not converted:
                try:
                    output.unlink(missing_ok=True)
                except OSError:
                    pass
                state["state"] = "error"
                state["error"] = error("gif_conversion_failed", conversion_error)
                state["pid"] = 0
                state["processStartTicks"] = None
                save("record", state)
                return response(
                    state,
                    "record.stop",
                    ok_value=False,
                    error_value=state["error"],
                    exitCode=POSTPROCESS_FAILURE,
                )
            temporary.unlink()
        else:
            os.replace(temporary, output)
    except OSError as exc:
        state["state"] = "error"
        state["error"] = error("recording_finalize_failed", str(exc))
        save("record", state)
        return response(
            state,
            "record.stop",
            ok_value=False,
            error_value=state["error"],
            exitCode=POSTPROCESS_FAILURE,
        )
    state.update(
        {
            "state": "completed",
            "pid": 0,
            "processStartTicks": None,
            "completedAtMs": now_ms(),
            "temporaryPath": "",
            "error": None,
        }
    )
    save("record", state)
    if getattr(_finalize, "copy_to_clipboard", False):
        wl_copy = shutil.which("wl-copy")
        if wl_copy:
            subprocess.run(
                [wl_copy, "--type", "text/uri-list"],
                input=(output.resolve().as_uri() + "\n").encode(),
                check=False,
            )
    _notify("Recording stopped", str(output))
    return response(state, "record.stop", ok_value=True, error_value=None, exitCode=0)


def stop(args) -> Result:
    state = load("record")
    identity = process_fields(state)
    if state.get("state") not in {"recording", "paused", "starting"}:
        return response(
            state,
            "record.stop",
            ok_value=False,
            error_value=error("no_active_recording", "There is no active screen recording"),
            exitCode=5,
        )
    if not matches(identity, "gpu-screen-recorder", state.get("temporaryPath", "")):
        state["state"] = "error"
        state["error"] = error(
            "recorder_identity_mismatch", "refusing to stop an unverified recorder process"
        )
        save("record", state)
        return response(
            state,
            "record.stop",
            ok_value=False,
            error_value=state["error"],
            exitCode=RECORDER_STOP_FAILURE,
        )
    state["state"] = "stopping"
    save("record", state)
    stopped, forced, reason = stop_verified(
        identity, "gpu-screen-recorder", state.get("temporaryPath", "")
    )
    if not stopped:
        state["state"] = "error"
        state["error"] = error("recorder_stop_failed", reason)
        save("record", state)
        return response(
            state,
            "record.stop",
            ok_value=False,
            error_value=state["error"],
            exitCode=RECORDER_STOP_FAILURE,
        )
    state["state"] = "finalizing"
    if forced:
        state["stopForced"] = True
    save("record", state)
    _finalize.copy_to_clipboard = bool(args.clipboard)
    return _finalize(state)


def toggle_pause(args) -> Result:
    state = load("record")
    identity = process_fields(state)
    if state.get("state") not in {"recording", "paused"} or not matches(
        identity, "gpu-screen-recorder", state.get("temporaryPath", "")
    ):
        return response(
            state,
            f"record.{args.action}",
            ok_value=False,
            error_value=error("no_active_recording", "There is no verified screen recording"),
            exitCode=5,
        )
    try:
        os.kill(identity.pid, __import__("signal").SIGUSR2)
    except OSError as exc:
        return response(
            state,
            f"record.{args.action}",
            ok_value=False,
            error_value=error("recorder_pause_failed", str(exc)),
            exitCode=GENERAL_FAILURE,
        )
    state["state"] = "paused" if args.action == "pause" else "recording"
    save("record", state)
    return response(state, f"record.{args.action}", ok_value=True, error_value=None, exitCode=0)
