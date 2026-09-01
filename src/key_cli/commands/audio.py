from __future__ import annotations

import json
import os
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


def _run_json(program: str, arguments: list[str]) -> tuple[dict | list | None, str]:
    try:
        process = subprocess.run([program, *arguments], capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if process.returncode != 0:
        return None, (process.stderr or b"failed").decode(errors="replace").strip()
    try:
        return json.loads(process.stdout.decode(errors="replace")), ""
    except json.JSONDecodeError as exc:
        return None, str(exc)


def resolve_source(source_type: str) -> tuple[dict | None, dict | None]:
    pactl = shutil.which("pactl")
    if not pactl:
        return None, error("dependency_missing", "pactl is not installed", dependency="pactl")
    info, message = _run_json(pactl, ["-f", "json", "info"])
    if info is None:
        return None, error("audio_server_unavailable", message)
    sources, message = _run_json(pactl, ["-f", "json", "list", "sources"])
    sinks, message = _run_json(pactl, ["-f", "json", "list", "sinks"])
    if not isinstance(info, dict) or not isinstance(sources, list) or not isinstance(sinks, list):
        return None, error("invalid_pactl_json", "pactl returned an unexpected JSON shape")

    def prop(item: dict, name: str) -> str:
        return str((item.get("properties") or {}).get(name) or "")

    def usable(item: dict) -> bool:
        if str(item.get("state", "")) == "UNAVAILABLE":
            return False
        active = str(item.get("active_port") or "")
        for port in item.get("ports") or []:
            if port.get("name") == active and str(port.get("availability")) == "not available":
                return False
        return True

    def by_name(items: list, name: str) -> dict:
        return next((item for item in items if item.get("name") == name), {})

    if source_type == "mic":
        preferred = by_name(sources, str(info.get("default_source_name") or ""))
        if not preferred or preferred.get("name", "").endswith(".monitor") or not usable(preferred):
            preferred = next(
                (
                    item
                    for item in sources
                    if not str(item.get("name", "")).endswith(".monitor") and usable(item)
                ),
                {},
            )
        if not preferred:
            return None, error("microphone_unavailable", "no usable microphone source is available")
        node = prop(preferred, "node.name") or str(preferred.get("name") or "")
        return {
            "type": "mic",
            "name": str(preferred.get("name") or ""),
            "nodeName": node,
            "description": str(preferred.get("description") or ""),
            "captureSink": False,
        }, None

    sink = by_name(sinks, str(info.get("default_sink_name") or ""))
    monitor_name = str(sink.get("monitor_source") or "")
    monitor = by_name(sources, monitor_name)
    if (
        not sink
        or not monitor
        or not str(monitor.get("name", "")).endswith(".monitor")
        or not usable(monitor)
    ):
        return None, error(
            "system_monitor_unavailable",
            "default output has no usable monitor source",
            defaultSink=info.get("default_sink_name"),
            monitorSource=monitor_name,
        )
    return {
        "type": "system",
        "name": monitor_name,
        "nodeName": prop(sink, "node.name") or str(sink.get("name") or ""),
        "description": str(sink.get("description") or ""),
        "captureSink": True,
    }, None


def run(args) -> Result:
    try:
        with locked("audio"):
            if args.action == "start":
                return start(args)
            if args.action == "status":
                return status(args)
            if args.action == "stop":
                return stop(args)
    except StateError as exc:
        return fail(f"audio.{args.action}", 4, "recording_busy", str(exc))
    except RuntimeError as exc:
        return fail(f"audio.{args.action}", 5, "runtime_directory_unavailable", str(exc))
    return fail("audio", 2, "usage_error", "unknown audio action")


def start(args) -> Result:
    state = load("audio")
    _, active = active_state("audio", "ffmpeg", state.get("temporaryPath", ""))
    if active:
        return response(
            state,
            "audio.start",
            ok_value=False,
            error_value=error(
                "audio_recording_already_active", "an audio recording is already active"
            ),
            exitCode=4,
        )
    if args.source not in {"mic", "system"}:
        return fail("audio.start", 2, "usage_error", "source must be mic or system", exitCode=2)
    missing = [name for name in ("ffmpeg", "ffprobe", "pactl") if not shutil.which(name)]
    if missing:
        return fail(
            "audio.start",
            DEPENDENCY_FAILURE,
            "dependency_missing",
            "missing: " + ", ".join(missing),
            dependency=missing[0],
            exitCode=DEPENDENCY_FAILURE,
        )
    source, source_error = resolve_source(args.source)
    if not source:
        return response(
            state, "audio.start", ok_value=False, error_value=source_error, exitCode=GENERAL_FAILURE
        )
    directory = output_directory(args.output, "audio")
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not os.access(directory, os.W_OK):
            raise OSError("output directory is not writable")
    except OSError as exc:
        return fail(
            "audio.start",
            GENERAL_FAILURE,
            "output_directory_unwritable",
            str(exc),
            exitCode=GENERAL_FAILURE,
        )
    session = new_state("audio")
    label = "system" if args.source == "system" else "microphone"
    stem = f"{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{session['sessionId'][:8]}"
    output = directory / f"{stem}.m4a"
    temporary = directory / f".{stem}.partial.m4a"
    session.update(
        {
            "state": "starting",
            "source": source,
            "temporaryPath": str(temporary),
            "outputPath": str(output),
        }
    )
    ffmpeg = shutil.which("ffmpeg")
    command = [
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "warning",
        "-f",
        "pulse",
        "-i",
        source["name"],
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-y",
        str(temporary),
    ]
    process, start_error = spawn(ffmpeg, command)
    if process is None:
        session["state"] = "error"
        session["error"] = error(
            "audio_recorder_start_failed", start_error or "unable to start ffmpeg"
        )
        save("audio", session)
        return response(
            session,
            "audio.start",
            ok_value=False,
            error_value=session["error"],
            exitCode=RECORDER_START_FAILURE,
        )
    identity = wait_for_identity(process.pid, "ffmpeg", str(temporary))
    session.update(
        {
            "state": "recording",
            "pid": process.pid,
            "processStartTicks": str(identity.start_ticks),
            "processStartedAtMs": now_ms(),
        }
    )
    if not identity.start_ticks or not matches(identity, "ffmpeg", str(temporary)):
        # This PID was created by this start operation.  If its identity is
        # still verifiable, stop it before reporting failure so a startup
        # race cannot leave an untracked ffmpeg process running.
        if identity.start_ticks and matches(identity, "ffmpeg"):
            stop_verified(identity, "ffmpeg")
        session["state"] = "error"
        session["error"] = error(
            "audio_recorder_start_failed", "ffmpeg exited before its identity could be verified"
        )
        session["pid"] = 0
        session["processStartTicks"] = None
        save("audio", session)
        return response(
            session,
            "audio.start",
            ok_value=False,
            error_value=session["error"],
            exitCode=RECORDER_START_FAILURE,
        )
    save("audio", session)
    return response(session, "audio.start", ok_value=True, error_value=None, exitCode=0)


def status(args) -> Result:
    state, _ = active_state("audio", "ffmpeg", load("audio").get("temporaryPath", ""))
    return response(
        state,
        "audio.status",
        ok_value=not state.get("error"),
        error_value=state.get("error"),
        exitCode=0 if not state.get("error") else 5,
    )


def _validate_and_finalize(state: dict) -> Result:
    temporary = Path(state.get("temporaryPath", ""))
    output = Path(state.get("outputPath", ""))
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        state["state"] = "error"
        state["error"] = error(
            "dependency_missing", "ffprobe is not installed", dependency="ffprobe"
        )
        save("audio", state)
        return response(
            state,
            "audio.stop",
            ok_value=False,
            error_value=state["error"],
            exitCode=DEPENDENCY_FAILURE,
        )
    if not temporary.is_file() or temporary.stat().st_size == 0:
        state["state"] = "error"
        state["error"] = error("audio_output_invalid", "audio output is missing or empty")
        save("audio", state)
        return response(
            state,
            "audio.stop",
            ok_value=False,
            error_value=state["error"],
            exitCode=POSTPROCESS_FAILURE,
        )
    try:
        probe = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type",
                "-of",
                "json",
                str(temporary),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        data = json.loads(probe.stdout or "{}")
        duration = float((data.get("format") or {}).get("duration") or 0)
        streams = data.get("streams") or []
        if (
            probe.returncode != 0
            or duration <= 0
            or not any(item.get("codec_type") == "audio" for item in streams)
        ):
            raise ValueError("ffprobe found no non-empty audio stream")
        os.replace(temporary, output)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        state["state"] = "error"
        state["error"] = error("audio_output_invalid", str(exc))
        save("audio", state)
        return response(
            state,
            "audio.stop",
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
            "duration": duration,
        }
    )
    save("audio", state)
    return response(state, "audio.stop", ok_value=True, error_value=None, exitCode=0)


def stop(args) -> Result:
    state = load("audio")
    identity = process_fields(state)
    if state.get("state") not in {"recording", "starting"}:
        return response(
            state,
            "audio.stop",
            ok_value=False,
            error_value=error("no_active_audio_recording", "there is no active audio recording"),
            exitCode=5,
        )
    if not matches(identity, "ffmpeg", state.get("temporaryPath", "")):
        state["state"] = "error"
        state["error"] = error(
            "audio_recorder_identity_mismatch", "refusing to stop an unverified ffmpeg process"
        )
        save("audio", state)
        return response(
            state,
            "audio.stop",
            ok_value=False,
            error_value=state["error"],
            exitCode=RECORDER_STOP_FAILURE,
        )
    state["state"] = "stopping"
    save("audio", state)
    stopped, forced, reason = stop_verified(identity, "ffmpeg", state.get("temporaryPath", ""))
    if not stopped:
        state["state"] = "error"
        state["error"] = error("audio_recorder_stop_failed", reason)
        save("audio", state)
        return response(
            state,
            "audio.stop",
            ok_value=False,
            error_value=state["error"],
            exitCode=RECORDER_STOP_FAILURE,
        )
    state["state"] = "finalizing"
    state["stopForced"] = forced
    save("audio", state)
    return _validate_and_finalize(state)
