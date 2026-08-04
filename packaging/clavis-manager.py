#!/usr/bin/env python3
"""Release, profile and migration operations behind the stable `key` CLI."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import signal
import shutil
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Any, NoReturn

from clavis_paths import ClavisPaths, PathConfigurationError
from components import (
    ComponentError,
    install_component,
    status as component_status,
    uninstall_component,
    update_component,
)


MANIFEST_SCHEMA = 1
RELEASE_PATTERN = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})(?:\.(\d+))?$")
REQUIRED_PROTOCOLS = {"core": 1, "clipboard": 2}
REQUIRED_DATA_SCHEMAS = {"config": 1, "manifest": 1, "profile": 1}
MANAGED_NIRI_USER_SERVICES = (
    "clavis-shell.service",
    "clavis-clipboard.service",
)
OBSOLETE_USER_SERVICES = ("clavis-cliphist.service",)
ACTIVE_SHELL_SCHEMA = 1
SOURCE_MARKERS = ("shell.qml", "core", "packaging")
SHELL_START_TIMEOUT_SECONDS = 8.0
SHELL_START_POLL_SECONDS = 0.05
SHELL_LOG_MAX_BYTES = 2 * 1024 * 1024
SHELL_LOG_BACKUPS = 3
SHELL_LOG_TAIL_LINES = 50
SHELL_LOG_NAMES = {
    "release": "shell-release.log",
    "development": "shell-dev.log",
    "development-native": "shell-dev-native.log",
}
SHELL_MODE_ARGUMENTS = {
    "release": "release",
    "dev": "development",
    "dev-native": "development-native",
}


class ClavisError(RuntimeError):
    pass


def fail(message: str, code: int = 1) -> NoReturn:
    print(f"key: {message}", file=sys.stderr)
    raise SystemExit(code)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def release_key(value: str) -> tuple[int, int, int, int]:
    match = RELEASE_PATTERN.fullmatch(value)
    if match is None:
        raise ClavisError(
            f"invalid release {value!r}; expected YYYY.MM.DD or YYYY.MM.DD.N"
        )
    year, month, day = (int(match.group(index)) for index in range(1, 4))
    dt.date(year, month, day)
    revision = int(match.group(4) or "0")
    return year, month, day, revision


def atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_json(path: Path, value: Any, mode: int = 0o644) -> None:
    atomic_write(
        path,
        (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode(),
        mode,
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": sha256(path),
        "mode": stat.S_IMODE(path.stat().st_mode),
    }


def _snapshot_path(path: Path) -> tuple[str, bytes | str | None, int]:
    if path.is_symlink():
        return "symlink", os.readlink(path), 0
    if not path.exists():
        return "absent", None, 0
    if not path.is_file():
        raise ClavisError(f"managed path is not a regular file: {path}")
    return "file", path.read_bytes(), stat.S_IMODE(path.stat().st_mode)


def _restore_snapshot(
    path: Path, snapshot: tuple[str, bytes | str | None, int]
) -> None:
    kind, value, mode = snapshot
    if path.exists() or path.is_symlink():
        if path.is_dir() and not path.is_symlink():
            raise ClavisError(f"cannot restore managed file over directory: {path}")
        path.unlink()
    if kind == "absent":
        return
    if kind == "file":
        assert isinstance(value, bytes)
        atomic_write(path, value, mode)
        return
    assert kind == "symlink" and isinstance(value, str)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.restore-{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(value)
    os.replace(temporary, path)


def _record_for_path(records: list[dict[str, Any]], path: Path) -> dict[str, Any] | None:
    return next(
        (record for record in records if record.get("path") == str(path)), None
    )


def _require_replaceable(
    path: Path, desired: bytes, owned_record: dict[str, Any] | None
) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_file():
        raise ClavisError(f"install conflict at {path}: not a regular managed file")
    if path.read_bytes() == desired:
        return
    if owned_record is not None and sha256(path) == owned_record.get("sha256"):
        return
    raise ClavisError(
        f"install conflict at {path}: existing file is not owned by this manifest"
    )


def release_file_records(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            records.append(
                {
                    "path": str(path.relative_to(root)),
                    "kind": "symlink",
                    "target": os.readlink(path),
                }
            )
        elif path.is_file():
            record = file_record(path)
            record["path"] = str(path.relative_to(root))
            records.append(record)
    return records


def release_directory_records(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": str(path.relative_to(root)),
            "mode": stat.S_IMODE(path.stat().st_mode),
        }
        for path in sorted(root.rglob("*"))
        if path.is_dir() and not path.is_symlink()
    ]


def _release_owned_directories(entry: dict[str, Any]) -> set[str]:
    recorded = entry.get("directories")
    if isinstance(recorded, list):
        return {
            str(Path(record["path"]))
            for record in recorded
            if isinstance(record, dict) and isinstance(record.get("path"), str)
        }
    # Schema-v1 manifests created before directory records can still safely
    # remove only directories proven to be parents of recorded files.
    directories: set[str] = set()
    for record in entry.get("files", []):
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            continue
        parent = Path(record["path"]).parent
        while parent != Path("."):
            directories.add(str(parent))
            parent = parent.parent
    return directories


def _release_tree_matches_partial(final: Path, partial: Path) -> bool:
    def signature(record: dict[str, Any], read_only: bool) -> tuple[Any, ...]:
        if record.get("kind") == "symlink":
            return "symlink", record.get("target")
        mode = int(record["mode"])
        if read_only:
            mode &= ~0o222
        return "file", record["sha256"], mode

    expected_files = {
        record["path"]: signature(record, True)
        for record in release_file_records(partial)
    }
    actual_files = {
        record["path"]: signature(record, False)
        for record in release_file_records(final)
    }
    expected_directories = {
        record["path"]: int(record["mode"]) & ~0o222
        for record in release_directory_records(partial)
    }
    actual_directories = {
        record["path"]: int(record["mode"])
        for record in release_directory_records(final)
    }
    return (
        expected_files == actual_files
        and expected_directories == actual_directories
    )


def default_manifest(paths: ClavisPaths) -> dict[str, Any]:
    return {
        "schemaVersion": MANIFEST_SCHEMA,
        "installPrefix": str(paths.install_prefix),
        "activeRelease": "",
        "previousRelease": "",
        "releases": {},
        "launcher": None,
        "components": {},
        "userUnits": [],
        "profiles": [],
        "systemIntegrations": {},
        "updatedAt": utc_now(),
    }


def load_manifest(paths: ClavisPaths) -> dict[str, Any]:
    if not paths.manifest.exists():
        return default_manifest(paths)
    try:
        value = json.loads(paths.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ClavisError(f"cannot read install manifest: {error}") from error
    if value.get("schemaVersion") != MANIFEST_SCHEMA:
        raise ClavisError("unsupported install manifest schema")
    if value.get("installPrefix") != str(paths.install_prefix):
        raise ClavisError("install manifest belongs to a different install prefix")
    # Schema v1 once carried external-theme export records. The command and its
    # ownership model are gone; silently drop the obsolete field while loading.
    value.pop("exports", None)
    return value


def read_release_metadata(root: Path) -> dict[str, Any]:
    metadata_path = root / "release.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ClavisError(f"invalid release metadata at {metadata_path}: {error}") from error
    release = str(metadata.get("release", ""))
    release_key(release)
    protocols = metadata.get("protocols")
    if not isinstance(protocols, dict):
        raise ClavisError("release metadata has no protocol map")
    for name, required in REQUIRED_PROTOCOLS.items():
        if protocols.get(name) != required:
            raise ClavisError(
                f"release protocol {name!r} is {protocols.get(name)!r}, expected {required}"
            )
    schemas = metadata.get("dataSchemas")
    if not isinstance(schemas, dict):
        raise ClavisError("release metadata has no data schema map")
    for name, required in REQUIRED_DATA_SCHEMAS.items():
        if schemas.get(name) != required:
            raise ClavisError(
                f"release data schema {name!r} is {schemas.get(name)!r}, expected {required}"
            )
    if metadata.get("component") not in {None, "quickshell"}:
        raise ClavisError("release metadata component is not quickshell")
    for field in ("minimumKeyCli", "minimumKeytop", "shellProtocol"):
        if field not in metadata:
            raise ClavisError(f"release metadata has no {field} field")
    return metadata


def validate_release(root: Path, expected_release: str | None = None) -> dict[str, Any]:
    if not root.is_dir() or root.is_symlink():
        raise ClavisError(f"release root is not a real directory: {root}")
    metadata = read_release_metadata(root)
    if expected_release is not None and metadata["release"] != expected_release:
        raise ClavisError(
            f"release metadata says {metadata['release']}, expected {expected_release}"
        )
    required = (
        root / "share/clavis/qml/shell.qml",
        root / "lib/qml/Clavis",
        root / "lib/qml/M3Shapes",
        root / "lib/qml/Clavis/Runtime/qmldir",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise ClavisError("incomplete release; missing: " + ", ".join(missing))
    if (root / "bin/key").exists() or (root / "share/clavis/bin/key").exists():
        raise ClavisError("release must not contain a second key CLI")
    return metadata


def resolve_active_release(paths: ClavisPaths) -> Path:
    current = paths.current_release
    if not current.is_symlink():
        raise ClavisError(f"no active release symlink at {current}")
    target = current.resolve(strict=True)
    releases = paths.releases_home.resolve(strict=True)
    if target.parent != releases:
        raise ClavisError(f"current release escapes the releases directory: {target}")
    validate_release(target)
    return target


def release_environment(paths: ClavisPaths, release_root: Path) -> dict[str, str]:
    result = os.environ.copy()
    result.update(paths.as_environment(release_root))
    metadata = read_release_metadata(release_root)
    result["CLAVIS_SHELL_RELEASE"] = metadata["release"]
    result["CLAVIS_SHELL_COMMIT"] = str(metadata.get("commit", "unknown"))
    result["CLAVIS_RUNTIME_MODE"] = "release"
    result.pop("CLAVIS_SOURCE_ROOT", None)
    path_entries = [entry for entry in result.get("PATH", "").split(os.pathsep) if entry]
    if str(paths.bin_home) in path_entries:
        path_entries.remove(str(paths.bin_home))
    path_entries.insert(0, str(paths.bin_home))
    result["PATH"] = os.pathsep.join(path_entries)
    return result


def executable(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise ClavisError(f"required executable is missing: {name}")
    return found


def active_shell_path(paths: ClavisPaths) -> Path:
    return paths.runtime_home / "active-shell.json"


def _process_start_ticks(pid: int) -> int | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        return int(fields[21])
    except (OSError, ValueError, IndexError):
        return None


def _process_is_quickshell(pid: int, qml_root: Path | None = None) -> bool:
    try:
        arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    if not arguments or Path(os.fsdecode(arguments[0])).name not in {
        "qs",
        "quickshell",
    }:
        return False
    if qml_root is None:
        return True
    decoded = [os.fsdecode(argument) for argument in arguments[1:] if argument]
    for index, argument in enumerate(decoded[:-1]):
        if argument in {"--path", "-p"}:
            try:
                return Path(decoded[index + 1]).resolve() == qml_root.resolve()
            except OSError:
                return False
    return False


def _remove_active_shell(paths: ClavisPaths, token: str | None = None) -> None:
    path = active_shell_path(paths)
    if token is not None:
        try:
            recorded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if recorded.get("token") != token:
            return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def read_active_shell(paths: ClavisPaths) -> dict[str, Any] | None:
    path = active_shell_path(paths)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        pid = int(value["pid"])
        start_ticks = int(value["processStartTicks"])
        qml_root = Path(str(value["qmlRoot"]))
        if (
            value.get("schemaVersion") != ACTIVE_SHELL_SCHEMA
            or value.get("mode")
            not in {"release", "development", "development-native"}
            or not isinstance(value.get("token"), str)
            or not value["token"]
            or not qml_root.is_absolute()
            or _process_start_ticks(pid) != start_ticks
            or not _process_is_quickshell(pid, qml_root)
        ):
            raise ValueError("stale active shell metadata")
        return value
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        _remove_active_shell(paths)
        return None


def _source_tree_is_valid(path: Path) -> bool:
    return (
        (path / ".git").exists()
        and (path / SOURCE_MARKERS[0]).is_file()
        and all((path / marker).is_dir() for marker in SOURCE_MARKERS[1:])
    )


def locate_source_tree(start: Path, override: str | None = None) -> Path:
    if override:
        candidate = Path(override).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        candidate = candidate.resolve()
        if _source_tree_is_valid(candidate):
            return candidate
        raise ClavisError(f"not a valid Clavis source tree: {candidate}")
    candidate = start.resolve()
    for directory in (candidate, *candidate.parents):
        if _source_tree_is_valid(directory):
            return directory
    raise ClavisError(
        "Cannot locate the Clavis source tree.\n"
        "Run this command inside the Clavis repository."
    )


def _git_identity(source_root: Path) -> tuple[str, bool]:
    commit = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip() or "unknown"
    status = subprocess.run(
        [
            "git",
            "-C",
            str(source_root),
            "status",
            "--porcelain",
            "--untracked-files=normal",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return commit, bool(status.stdout.strip()) or status.returncode != 0


def _development_environment(
    paths: ClavisPaths,
    release_root: Path,
    source_root: Path,
    native: bool,
    build_log: Any = None,
) -> tuple[dict[str, str], Path, Path, dict[str, Any]]:
    environment = release_environment(paths, release_root)
    environment.pop("CLAVIS_RELEASE_ROOT", None)
    build_root = source_root / ".build/dev"
    if native:
        build_options: dict[str, Any] = {
            "cwd": source_root,
            "env": os.environ.copy(),
            "check": False,
        }
        if build_log is not None:
            build_options.update(stdout=build_log, stderr=subprocess.STDOUT)
        build = subprocess.run(
            [str(source_root / "setup.sh"), "dev-build", "--build-dir", str(build_root)],
            **build_options,
        )
        if build.returncode != 0:
            raise ClavisError(
                "native development build failed; the installed release was not changed"
            )
        backend_key = paths.stable_key
        native_import = build_root / "lib/qml"
        environment["CLAVIS_MANAGER"] = str(
            source_root / "packaging/clavis-manager.py"
        )
    else:
        backend_key = paths.stable_key
        native_import = release_root / "lib/qml"
        environment.pop("CLAVIS_MANAGER", None)
    required = (
        native_import / "Clavis/Runtime/qmldir",
        native_import / "M3Shapes/qmldir",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise ClavisError(
            "development native components are incomplete: " + ", ".join(missing)
        )
    if not backend_key.is_file() or not os.access(backend_key, os.X_OK):
        raise ClavisError(f"stable key CLI is not executable: {backend_key}")
    metadata = read_release_metadata(release_root)
    environment.update(
        {
            "CLAVIS_SOURCE_ROOT": str(source_root),
            "CLAVIS_RUNTIME_MODE": "development-native" if native else "development",
            "CLAVIS_KEY": str(backend_key),
            "CLAVIS_QML_IMPORT_HOME": str(native_import),
            "CLAVIS_SHELL_RELEASE": str(metadata.get("release", "")),
            "CLAVIS_SHELL_COMMIT": str(metadata.get("commit", "")),
        }
    )
    path_entries = [
        entry for entry in environment.get("PATH", "").split(os.pathsep) if entry
    ]
    backend_bin = str(backend_key.parent)
    path_entries = [entry for entry in path_entries if entry != backend_bin]
    environment["PATH"] = os.pathsep.join([backend_bin, *path_entries])
    for variable in ("QML_IMPORT_PATH", "QML2_IMPORT_PATH"):
        entries = [
            entry
            for entry in environment.get(variable, "").split(os.pathsep)
            if entry
        ]
        blocked_imports = {
            (release_root / "lib/qml").resolve(),
            native_import.resolve(),
        }
        entries = [
            entry
            for entry in entries
            if Path(entry).resolve() not in blocked_imports
        ]
        environment[variable] = os.pathsep.join([str(native_import), *entries])
    return environment, backend_key, native_import, metadata


def _list_instances(qs: str, qml_root: Path) -> list[dict[str, Any]]:
    result = subprocess.run(
        [qs, "list", "--json", "--path", str(qml_root)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    try:
        instances = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(instances, list):
        return []
    return [item for item in instances if isinstance(item, dict)]


def _kill_instance(qs: str, pid: int) -> None:
    original_start_ticks = _process_start_ticks(pid)
    if original_start_ticks is None:
        return
    result = subprocess.run([qs, "kill", "--pid", str(pid)], check=False)
    if result.returncode != 0:
        raise ClavisError(f"unable to stop Quickshell instance {pid}")
    for _attempt in range(50):
        if _process_start_ticks(pid) != original_start_ticks:
            return
        time.sleep(0.05)
    raise ClavisError(f"Quickshell instance {pid} did not exit")


def _shell_mode_label(mode: str) -> str:
    return {
        "release": "release",
        "development": "development",
        "development-native": "native development",
    }[mode]


def _shell_log_path(paths: ClavisPaths, mode: str) -> Path:
    try:
        filename = SHELL_LOG_NAMES[mode]
    except KeyError as error:
        raise ClavisError(f"unknown Shell runtime mode: {mode}") from error
    return paths.logs_home / filename


def _ensure_shell_log_directory(paths: ClavisPaths) -> None:
    directory = paths.logs_home
    if directory.is_symlink():
        raise ClavisError(f"Shell log directory must not be a symlink: {directory}")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not directory.is_dir():
        raise ClavisError(f"Shell log path is not a directory: {directory}")
    directory.chmod(0o700)


def _rotate_live_shell_log(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        return
    candidates = [
        Path(f"{path}.{index}") for index in range(1, SHELL_LOG_BACKUPS + 1)
    ]
    if any(
        candidate.is_symlink()
        or (candidate.exists() and not candidate.is_file())
        for candidate in candidates
    ):
        return
    oldest = Path(f"{path}.{SHELL_LOG_BACKUPS}")
    if oldest.exists():
        oldest.unlink()
    for index in range(SHELL_LOG_BACKUPS - 1, 0, -1):
        source = Path(f"{path}.{index}")
        if source.exists():
            os.replace(source, Path(f"{path}.{index + 1}"))
    backup = Path(f"{path}.1")
    shutil.copyfile(path, backup)
    backup.chmod(0o600)
    flags = os.O_WRONLY | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    os.close(descriptor)
    path.chmod(0o600)


def run_shell_log_monitor(
    paths: ClavisPaths,
    pid: int,
    start_ticks: int,
    token: str,
    log_path: Path,
) -> int:
    while _process_start_ticks(pid) == start_ticks:
        try:
            if log_path.is_file() and log_path.stat().st_size >= SHELL_LOG_MAX_BYTES:
                _rotate_live_shell_log(log_path)
        except OSError as error:
            try:
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(f"Log monitor error: {error}\n")
            except OSError:
                pass
        time.sleep(1)
    _remove_active_shell(paths, token)
    return 0


def _start_shell_log_monitor(
    environment: dict[str, str],
    pid: int,
    start_ticks: int,
    token: str,
    log_path: Path,
) -> None:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "shell-log-monitor",
        "--pid",
        str(pid),
        "--start-ticks",
        str(start_ticks),
        "--token",
        token,
        "--log-path",
        str(log_path),
    ]
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        start_new_session=True,
        close_fds=True,
    )


def _open_shell_log(paths: ClavisPaths, mode: str) -> tuple[Path, Any]:
    _ensure_shell_log_directory(paths)
    path = _shell_log_path(paths, mode)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ClavisError(f"Shell log path is not a regular file: {path}")
    flags = os.O_CREAT | os.O_WRONLY | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    os.fchmod(descriptor, 0o600)
    return path, os.fdopen(descriptor, "a", encoding="utf-8", buffering=1)


def _tail_log(path: Path, line_count: int = SHELL_LOG_TAIL_LINES) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return list(deque(handle, maxlen=line_count))
    except OSError:
        return []


def _report_background_failure(
    mode: str,
    log_path: Path,
    reason: str,
    replaced: bool,
) -> int:
    print(
        f"Failed to start Clavis {_shell_mode_label(mode)} Shell: {reason}",
        file=sys.stderr,
    )
    lines = _tail_log(log_path)
    if lines:
        print(f"\nLast {min(len(lines), SHELL_LOG_TAIL_LINES)} log lines:", file=sys.stderr)
        for line in lines:
            print(f"  {line.rstrip()}", file=sys.stderr)
    print(f"\nFull log: {log_path}", file=sys.stderr)
    if replaced:
        print(
            "The previous Shell was stopped. Restore the installed release with:\n"
            "  key shell --replace",
            file=sys.stderr,
        )
    return 1


def _report_log_setup_failure(paths: ClavisPaths, mode: str, error: Exception) -> int:
    path = _shell_log_path(paths, mode)
    print(
        f"Failed to start Clavis {_shell_mode_label(mode)} Shell: "
        f"cannot create the launch log: {error}",
        file=sys.stderr,
    )
    print(f"\nLog path: {path}", file=sys.stderr)
    return 1


def _latest_shell_log(paths: ClavisPaths, mode: str | None) -> Path:
    if mode is not None:
        return _shell_log_path(paths, SHELL_MODE_ARGUMENTS[mode])
    active = read_active_shell(paths)
    if active is not None:
        recorded = active.get("logPath")
        if isinstance(recorded, str) and recorded:
            candidate = Path(recorded)
            allowed = {
                _shell_log_path(paths, runtime_mode)
                for runtime_mode in SHELL_LOG_NAMES
            }
            if (
                candidate in allowed
                and candidate.is_file()
                and not candidate.is_symlink()
            ):
                return candidate
    candidates = [
        _shell_log_path(paths, runtime_mode)
        for runtime_mode in SHELL_LOG_NAMES
    ]
    existing = [
        path for path in candidates if path.is_file() and not path.is_symlink()
    ]
    if not existing:
        raise ClavisError(f"no Clavis Shell log exists in {paths.logs_home}")
    return max(existing, key=lambda path: path.stat().st_mtime_ns)


def run_shell_logs(paths: ClavisPaths, arguments: list[str]) -> int:
    if arguments.count("--follow") > 1:
        raise ClavisError("duplicate Shell logs option: --follow")
    mode_options = [
        argument
        for argument in arguments
        if argument == "--mode" or argument.startswith("--mode=")
    ]
    if len(mode_options) > 1:
        raise ClavisError("duplicate Shell logs option: --mode")
    parser = argparse.ArgumentParser(
        prog="key shell logs",
        description="Show the active or most recent Clavis Shell log.",
    )
    parser.add_argument("--follow", action="store_true", help="follow log updates")
    parser.add_argument(
        "--mode",
        choices=sorted(SHELL_MODE_ARGUMENTS),
        help="select release, dev, or dev-native logs",
    )
    parsed = parser.parse_args(arguments)
    _ensure_shell_log_directory(paths)
    path = _latest_shell_log(paths, parsed.mode)
    if not path.is_file() or path.is_symlink():
        raise ClavisError(f"Clavis Shell log does not exist: {path}")
    if parsed.follow:
        tail = executable("tail")
        try:
            return subprocess.run(
                [tail, "-n", "80", "-F", str(path)], check=False
            ).returncode
        except KeyboardInterrupt:
            return 130
    for line in _tail_log(path, 80):
        print(line, end="")
    return 0


def _reject_duplicate_shell_options(arguments: list[str]) -> None:
    tracked = {
        "--dev",
        "--native",
        "--source",
        "--replace",
        "--foreground",
        "--no-duplicate",
    }
    seen: set[str] = set()
    for argument in arguments:
        name = argument.split("=", 1)[0]
        if name not in tracked:
            continue
        if name in seen:
            raise ClavisError(f"duplicate Shell option: {name}")
        seen.add(name)


def _validate_quickshell_arguments(arguments: list[str]) -> None:
    forbidden = {
        "--path",
        "-p",
        "--config",
        "-c",
        "--manifest",
        "-m",
        "--daemonize",
        "-d",
        "--no-duplicate",
        "-n",
    }
    for argument in arguments:
        name = argument.split("=", 1)[0]
        if name in forbidden:
            raise ClavisError(
                f"Quickshell option {name} is managed by `key shell` and cannot be passed after --"
            )


def _shell_arguments(arguments: list[str]) -> tuple[argparse.Namespace, list[str]]:
    if "--" in arguments:
        separator = arguments.index("--")
        own_arguments = arguments[:separator]
        passthrough = arguments[separator + 1 :]
    else:
        own_arguments = arguments
        passthrough = []
    _reject_duplicate_shell_options(own_arguments)
    parser = argparse.ArgumentParser(
        prog="key shell",
        usage="key shell [--dev [--native] [--source PATH]] [--replace] "
        "[--foreground] [--no-duplicate] [-- QUICKSHELL_OPTIONS...]",
        description="Start Clavis Shell in the background by default.",
        epilog="Use --foreground to keep Quickshell attached and show live logs. "
        "Use `key shell logs --help` for saved background logs.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--dev", action="store_true", help="run QML and resources from source"
    )
    parser.add_argument(
        "--native",
        action="store_true",
        help="incrementally build and use native components",
    )
    parser.add_argument("--source", metavar="PATH", help="explicit Clavis source tree")
    parser.add_argument(
        "--replace", action="store_true", help="replace the active Clavis Shell"
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="run in the foreground with live stdout/stderr",
    )
    parser.add_argument(
        "--no-duplicate",
        action="store_true",
        help="succeed if this exact Shell is already running",
    )
    parsed = parser.parse_args(own_arguments)
    if parsed.native and not parsed.dev:
        raise ClavisError("--native requires --dev")
    if parsed.source and not parsed.dev:
        raise ClavisError("--source requires --dev")
    _validate_quickshell_arguments(passthrough)
    if parsed.no_duplicate:
        passthrough.insert(0, "--no-duplicate")
    return parsed, passthrough


def _wait_for_shell_registration(
    process: subprocess.Popen[Any],
    qs: str,
    qml_root: Path,
    timeout: float = SHELL_START_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            return (
                False,
                "Quickshell exited during startup with status "
                f"{_exit_status(return_code)}",
            )
        if any(
            str(instance.get("pid", "")).isdigit()
            and int(instance["pid"]) == process.pid
            for instance in _list_instances(qs, qml_root)
        ):
            return True, ""
        time.sleep(SHELL_START_POLL_SECONDS)
    return False, f"Quickshell did not register within {timeout:g} seconds"


def _exit_status(return_code: int) -> int:
    return 128 + (-return_code) if return_code < 0 else return_code


def _stop_failed_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        process.wait()
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _registered_metadata(
    process: subprocess.Popen[Any],
    qml_root: Path,
    metadata: dict[str, Any],
    log_path: Path | None,
) -> tuple[dict[str, Any], str] | tuple[None, str]:
    start_ticks = _process_start_ticks(process.pid)
    if start_ticks is None:
        return None, "unable to verify the Quickshell process identity"
    token = f"{process.pid}-{start_ticks}-{os.urandom(8).hex()}"
    registered = dict(metadata)
    registered.update(
        {
            "schemaVersion": ACTIVE_SHELL_SCHEMA,
            "pid": process.pid,
            "processStartTicks": start_ticks,
            "qmlRoot": str(qml_root),
            "startedAt": utc_now(),
            "token": token,
            "logPath": str(log_path) if log_path is not None else "",
        }
    )
    return registered, token


def _write_launch_context(
    handle: Any,
    mode: str,
    command: list[str],
    qml_root: Path,
    metadata: dict[str, Any],
    commit: str,
    dirty: bool,
) -> None:
    handle.write("\n=== Clavis Shell launch ===\n")
    handle.write(f"Started at: {utc_now()}\n")
    handle.write(f"Runtime mode: {mode}\n")
    handle.write(f"Release: {metadata.get('release', '')}\n")
    handle.write(f"Source root: {metadata.get('sourceRoot') or '(installed release)'}\n")
    handle.write(f"QML root: {qml_root}\n")
    handle.write(f"Backend key: {metadata.get('backendKey', '')}\n")
    handle.write(f"Native QML import root: {metadata.get('nativeImportRoot', '')}\n")
    handle.write(f"Git commit: {commit}\n")
    handle.write(f"Working tree dirty: {'yes' if dirty else 'no'}\n")
    handle.write(f"Command: {shlex.join(command)}\n")
    handle.flush()


def _launch_background_shell(
    paths: ClavisPaths,
    command: list[str],
    qs: str,
    qml_root: Path,
    environment: dict[str, str],
    metadata: dict[str, Any],
    commit: str,
    dirty: bool,
    log_path: Path,
    log_handle: Any,
    replaced: bool,
) -> int:
    mode = str(metadata["mode"])
    if os.fstat(log_handle.fileno()).st_size >= SHELL_LOG_MAX_BYTES:
        _rotate_live_shell_log(log_path)
    _write_launch_context(log_handle, mode, command, qml_root, metadata, commit, dirty)
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=environment,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as error:
        log_handle.write(f"Launcher error: {error}\n")
        log_handle.close()
        return _report_background_failure(mode, log_path, str(error), replaced)
    log_handle.write(f"PID: {process.pid}\n")
    registered, reason = _wait_for_shell_registration(process, qs, qml_root)
    if not registered:
        _stop_failed_process(process)
        log_handle.write(f"Startup failed: {reason}\n")
        log_handle.close()
        return _report_background_failure(mode, log_path, reason, replaced)
    runtime_metadata, token = _registered_metadata(process, qml_root, metadata, log_path)
    if runtime_metadata is None:
        _stop_failed_process(process)
        log_handle.write(f"Startup failed: {token}\n")
        log_handle.close()
        return _report_background_failure(mode, log_path, token, replaced)
    try:
        atomic_json(active_shell_path(paths), runtime_metadata, 0o600)
        verified = read_active_shell(paths)
        if verified is None or int(verified["pid"]) != process.pid:
            raise ClavisError("runtime metadata verification failed")
        _start_shell_log_monitor(
            environment,
            process.pid,
            int(runtime_metadata["processStartTicks"]),
            token,
            log_path,
        )
    except (OSError, ClavisError) as error:
        _stop_failed_process(process)
        _remove_active_shell(paths, token)
        log_handle.write(f"Startup failed: {error}\n")
        log_handle.close()
        return _report_background_failure(mode, log_path, str(error), replaced)
    log_handle.write("Startup verified.\n")
    log_handle.close()
    print(f"Started Clavis {_shell_mode_label(mode)} Shell (PID {process.pid}).")
    print(f"Log: {log_path}")
    return 0


def _launch_foreground_shell(
    paths: ClavisPaths,
    command: list[str],
    qs: str,
    qml_root: Path,
    environment: dict[str, str],
    metadata: dict[str, Any],
    commit: str,
    dirty: bool,
    replaced: bool,
) -> int:
    mode = str(metadata["mode"])
    _write_launch_context(sys.stdout, mode, command, qml_root, metadata, commit, dirty)
    try:
        process = subprocess.Popen(command, env=environment, close_fds=True)
    except OSError as error:
        print(
            f"Failed to start Clavis {_shell_mode_label(mode)} Shell: {error}",
            file=sys.stderr,
        )
        if replaced:
            print(
                "Restore the installed release with: key shell --replace",
                file=sys.stderr,
            )
        return 1
    print(f"PID: {process.pid}", flush=True)
    previous_handlers: dict[int, Any] = {}

    def forward(signum: int, _frame: Any) -> None:
        if process.poll() is None:
            process.send_signal(signum)

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous_handlers[signum] = signal.signal(signum, forward)
    token = ""
    try:
        registered, reason = _wait_for_shell_registration(process, qs, qml_root)
        if not registered:
            _stop_failed_process(process)
            print(
                f"Failed to start Clavis {_shell_mode_label(mode)} Shell: {reason}",
                file=sys.stderr,
            )
            if replaced:
                print(
                    "Restore the installed release with: key shell --replace",
                    file=sys.stderr,
                )
            return 1
        runtime_metadata, token_or_reason = _registered_metadata(
            process, qml_root, metadata, None
        )
        if runtime_metadata is None:
            _stop_failed_process(process)
            print(f"Failed to register Clavis Shell: {token_or_reason}", file=sys.stderr)
            if replaced:
                print(
                    "Restore the installed release with: key shell --replace",
                    file=sys.stderr,
                )
            return 1
        token = token_or_reason
        try:
            atomic_json(active_shell_path(paths), runtime_metadata, 0o600)
            verified = read_active_shell(paths)
            if verified is None or int(verified["pid"]) != process.pid:
                raise ClavisError("runtime metadata verification failed")
        except (OSError, ClavisError) as error:
            _stop_failed_process(process)
            print(f"Failed to register Clavis Shell: {error}", file=sys.stderr)
            if replaced:
                print(
                    "Restore the installed release with: key shell --replace",
                    file=sys.stderr,
                )
            return 1
        return _exit_status(process.wait())
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        if token:
            _remove_active_shell(paths, token)


def _stop_managed_shell_service_for_development(paths: ClavisPaths) -> None:
    unit = paths.user_systemd_home / "clavis-shell.service"
    if not unit.is_file() or unit.is_symlink() or not shutil.which("systemctl"):
        return
    result = subprocess.run(
        ["systemctl", "--user", "stop", "clavis-shell.service"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ClavisError(
            result.stderr.strip()
            or "unable to stop the production Clavis Shell service"
        )


def run_shell(paths: ClavisPaths, arguments: list[str]) -> int:
    if arguments and arguments[0] == "logs":
        return run_shell_logs(paths, arguments[1:])
    options, qs_arguments = _shell_arguments(arguments)
    release = resolve_active_release(paths)
    release_metadata = read_release_metadata(release)
    release_qml = release / "share/clavis/qml"
    source_root: Path | None = None
    backend_key = paths.stable_key
    native_import = release / "lib/qml"
    environment = release_environment(paths, release)
    mode = "release"
    qml_root = release_qml
    commit = str(release_metadata.get("commit", "unknown"))
    dirty = bool(release_metadata.get("sourceDirty", False))
    if options.dev:
        mode = "development-native" if options.native else "development"

    log_path: Path | None = None
    log_handle: Any = None
    if not options.foreground:
        try:
            log_path, log_handle = _open_shell_log(paths, mode)
        except (ClavisError, OSError) as error:
            return _report_log_setup_failure(paths, mode, error)

    if options.dev:
        try:
            source_root = locate_source_tree(Path.cwd(), options.source)
            qml_root = source_root
            if log_handle is not None:
                candidate_native = (
                    source_root / ".build/dev/lib/qml"
                    if options.native
                    else release / "lib/qml"
                )
                candidate_key = paths.stable_key
                log_handle.write(
                    "\n=== Clavis Shell preparation ===\n"
                    f"Started at: {utc_now()}\n"
                    f"Runtime mode: {mode}\n"
                    f"Release: {release_metadata.get('release', '')}\n"
                    f"Source root: {source_root}\n"
                    f"QML root: {qml_root}\n"
                    f"Backend key: {candidate_key}\n"
                    f"Native QML import root: {candidate_native}\n"
                )
            environment, backend_key, native_import, _handshake = (
                _development_environment(
                    paths,
                    release,
                    source_root,
                    options.native,
                    log_handle if options.native else None,
                )
            )
            commit, dirty = _git_identity(source_root)
        except ClavisError as error:
            if options.foreground:
                raise
            assert log_path is not None and log_handle is not None
            log_handle.write(
                f"\n=== Clavis Shell launch preparation failed ===\n"
                f"Started at: {utc_now()}\nRuntime mode: {mode}\nError: {error}\n"
            )
            log_handle.close()
            return _report_background_failure(mode, log_path, str(error), False)
    else:
        environment["CLAVIS_KEY"] = str(backend_key)
        path_entries = [
            entry for entry in environment.get("PATH", "").split(os.pathsep) if entry
        ]
        release_bin = str(backend_key.parent)
        environment["PATH"] = os.pathsep.join(
            [release_bin, *(entry for entry in path_entries if entry != release_bin)]
        )

    if options.dev and options.replace:
        _stop_managed_shell_service_for_development(paths)

    def fail_static_check(reason: str) -> int:
        if options.foreground:
            raise ClavisError(reason)
        assert log_path is not None and log_handle is not None
        log_handle.write(
            f"\n=== Clavis Shell static check failed ===\n"
            f"Started at: {utc_now()}\n"
            f"Runtime mode: {mode}\n"
            f"Release: {release_metadata.get('release', '')}\n"
            f"Source root: {source_root or '(installed release)'}\n"
            f"QML root: {qml_root}\n"
            f"Backend key: {backend_key}\n"
            f"Native QML import root: {native_import}\n"
            f"Error: {reason}\n"
        )
        log_handle.close()
        return _report_background_failure(mode, log_path, reason, False)

    qs = shutil.which("qs")
    if qs is None:
        return fail_static_check("required executable is missing: qs")
    if not qml_root.is_dir() or qml_root.is_symlink():
        return fail_static_check(f"QML root is not a real directory: {qml_root}")
    shell_entry = qml_root / "shell.qml"
    if not shell_entry.is_file() or shell_entry.is_symlink():
        return fail_static_check(f"Clavis shell.qml is missing: {shell_entry}")
    if not backend_key.is_file() or not os.access(backend_key, os.X_OK):
        return fail_static_check(f"stable key CLI is not executable: {backend_key}")

    command = [qs, "--path", str(qml_root), *qs_arguments]

    def close_pending_log() -> None:
        nonlocal log_handle
        if log_handle is not None and not log_handle.closed:
            log_handle.close()

    active = read_active_shell(paths)
    replaced = False
    if active is not None:
        active_pid = int(active["pid"])
        same = Path(str(active["qmlRoot"])) == qml_root and active.get("mode") == mode
        if same and options.no_duplicate and not options.replace:
            close_pending_log()
            print(f"Clavis Shell is already running ({mode}, pid {active_pid}).")
            return 0
        if not options.replace:
            close_pending_log()
            raise ClavisError(
                f"a Clavis Shell is already running ({active['mode']}, pid {active_pid}); "
                "rerun with --replace to switch instances"
            )
        _kill_instance(qs, active_pid)
        replaced = True
        active_token = active.get("token")
        if isinstance(active_token, str) and active_token:
            _remove_active_shell(paths, active_token)
    else:
        conflicting: list[dict[str, Any]] = _list_instances(qs, qml_root)
        if options.dev:
            conflicting.extend(_list_instances(qs, release_qml))
        unique = {
            int(item["pid"]): item
            for item in conflicting
            if str(item.get("pid", "")).isdigit()
        }
        if unique and options.no_duplicate and not options.replace and all(
            str(item.get("config_path", "")).startswith(str(qml_root))
            for item in unique.values()
        ):
            close_pending_log()
            print(f"Clavis Shell is already running ({mode}).")
            return 0
        if unique and not options.replace:
            close_pending_log()
            raise ClavisError(
                "a Clavis Quickshell instance is already running; "
                "rerun with --replace to switch instances"
            )
        for pid in unique:
            _kill_instance(qs, pid)
            replaced = True

    metadata = {
        "mode": mode,
        "backendKey": str(backend_key),
        "nativeImportRoot": str(native_import),
        "release": str(release_metadata.get("release", "")),
        "sourceRoot": str(source_root) if source_root is not None else "",
    }
    if options.foreground:
        return _launch_foreground_shell(
            paths,
            command,
            qs,
            qml_root,
            environment,
            metadata,
            commit,
            dirty,
            replaced,
        )
    assert log_path is not None and log_handle is not None
    return _launch_background_shell(
        paths,
        command,
        qs,
        qml_root,
        environment,
        metadata,
        commit,
        dirty,
        log_path,
        log_handle,
        replaced,
    )


def run_ipc(paths: ClavisPaths, arguments: list[str]) -> int:
    if not arguments or arguments[0] == "list":
        arguments = ["show"]
    release = resolve_active_release(paths)
    qml_root = release / "share/clavis/qml"
    active = read_active_shell(paths)
    weather_preview = arguments == ["call", "sidebar", "previewWeather"]
    if weather_preview and (
        active is None
        or active.get("mode") not in {"development", "development-native"}
    ):
        raise ClavisError(
            "sidebar weather preview is only available in a development Shell; "
            "start it with `key shell --dev --replace`"
        )
    if active is not None:
        command = [executable("qs"), "ipc", "--pid", str(active["pid"]), *arguments]
    else:
        command = [executable("qs"), "--path", str(qml_root), "ipc", *arguments]
    os.execvpe(command[0], command, release_environment(paths, release))
    return 127


def _systemd_quote(path: Path) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def user_unit_payloads(paths: ClavisPaths, release: Path) -> list[tuple[Path, bytes]]:
    source = release / "share/clavis/systemd/user"
    payloads = []
    for template in sorted(source.glob("*.service")):
        content = template.read_text(encoding="utf-8").replace(
            "@CLAVIS_KEY@", _systemd_quote(paths.stable_key)
        )
        destination = paths.user_systemd_home / template.name
        payloads.append((destination, content.encode()))
    return payloads


def install_user_units(payloads: list[tuple[Path, bytes]]) -> list[dict[str, Any]]:
    records = []
    for destination, content in payloads:
        atomic_write(destination, content)
        records.append(file_record(destination))
    return records


def reload_user_units() -> None:
    if os.environ.get("CLAVIS_SKIP_SYSTEMD") != "1" and shutil.which("systemctl"):
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def enable_niri_user_units() -> None:
    if os.environ.get("CLAVIS_SKIP_SYSTEMD") == "1" or not shutil.which("systemctl"):
        return
    result = subprocess.run(
        ["systemctl", "--user", "enable", *MANAGED_NIRI_USER_SERVICES],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ClavisError(
            result.stderr.strip() or "unable to enable Clavis Niri user services"
        )


def _make_release_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        path.chmod(mode & ~0o222)
    root.chmod(stat.S_IMODE(root.stat().st_mode) & ~0o222)


def _safe_partial(paths: ClavisPaths, partial: Path, release: str) -> None:
    expected = paths.releases_home / f"{release}.partial"
    if partial != expected:
        raise ClavisError(f"partial release must be exactly {expected}")


def finalize_install(paths: ClavisPaths, partial: Path, release: str) -> int:
    release_key(release)
    paths.releases_home.mkdir(parents=True, exist_ok=True)
    _safe_partial(paths, partial, release)
    metadata = validate_release(partial, release)

    manifest = load_manifest(paths)
    unit_payloads = user_unit_payloads(paths, partial)
    old_unit_records = manifest.get("userUnits", [])
    if not isinstance(old_unit_records, list):
        old_unit_records = []
    new_unit_paths = {str(destination) for destination, _content in unit_payloads}
    obsolete_unit_paths = {
        Path(record["path"])
        for record in old_unit_records
        if isinstance(record, dict)
        and isinstance(record.get("path"), str)
        and record["path"] not in new_unit_paths
    }
    obsolete_unit_paths.update(
        paths.user_systemd_home / name for name in OBSOLETE_USER_SERVICES
    )
    for destination, content in unit_payloads:
        _require_replaceable(
            destination, content, _record_for_path(old_unit_records, destination)
        )
    if paths.current_release.exists() and not paths.current_release.is_symlink():
        raise ClavisError(
            f"install conflict at {paths.current_release}: expected a symlink"
        )

    final = paths.releases_home / release
    created_final = False
    if final.exists():
        installed_metadata = validate_release(final, release)
        if installed_metadata.get("commit") != metadata.get("commit"):
            raise ClavisError(
                f"immutable release {release} already exists with a different commit"
            )
        if installed_metadata.get("sourceFingerprint") != metadata.get(
            "sourceFingerprint"
        ):
            raise ClavisError(
                f"immutable release {release} already exists with different source contents"
            )
        if not _release_tree_matches_partial(final, partial):
            raise ClavisError(
                f"immutable release {release} differs from the freshly built release"
            )
        shutil.rmtree(partial)
    else:
        os.replace(partial, final)
        _make_release_read_only(final)
        created_final = True

    previous = str(manifest.get("activeRelease", ""))
    previous_root = paths.releases_home / previous if previous else None
    profile_dirs = [
        paths.config_home / "overrides",
        paths.profile_config_home,
        paths.data_home / "wallpapers",
        paths.profile_home / "generated",
        paths.state_home / "backups",
        paths.state_home / "migrations",
        paths.state_home / "update-history",
        paths.cache_home / "colors",
        paths.cache_home / "thumbnails",
        paths.cache_home / "temporary",
        paths.runtime_home / "session",
        paths.runtime_home / "locks",
        paths.runtime_home / "sockets",
    ]
    for directory in profile_dirs:
        directory.mkdir(parents=True, exist_ok=True)
    manifest["previousRelease"] = previous if previous != release else manifest.get(
        "previousRelease", ""
    )
    manifest["activeRelease"] = release
    manifest.setdefault("releases", {})[release] = {
        "path": str(final),
        "commit": metadata.get("commit", "unknown"),
        "sourceDirty": bool(metadata.get("sourceDirty", False)),
        "sourceFingerprint": metadata.get("sourceFingerprint", "unknown"),
        "installedAt": utc_now(),
        "protocols": metadata["protocols"],
        "dataSchemas": metadata["dataSchemas"],
        "files": release_file_records(final),
        "directories": release_directory_records(final),
    }
    profiles = manifest.setdefault("profiles", [])
    profile_record = {
        "name": paths.profile_name,
        "path": str(paths.profile_home),
        "configPath": str(paths.profile_config_home),
    }
    for index, record in enumerate(profiles):
        if isinstance(record, dict) and record.get("name") == paths.profile_name:
            profiles[index] = profile_record
            break
    else:
        profiles.append(profile_record)
    manifest["updatedAt"] = utc_now()

    managed_paths = [
        *(destination for destination, _content in unit_payloads),
        *obsolete_unit_paths,
        paths.active_release_file,
        paths.manifest,
        paths.current_release,
    ]
    snapshots = {path: _snapshot_path(path) for path in managed_paths}
    try:
        unit_records = install_user_units(unit_payloads)
        for obsolete in obsolete_unit_paths:
            if obsolete.exists() or obsolete.is_symlink():
                obsolete.unlink()
        manifest["launcher"] = None
        manifest["userUnits"] = unit_records
        atomic_write(paths.active_release_file, f"{release}\n".encode())
        atomic_json(paths.manifest, manifest)

        paths.install_prefix.mkdir(parents=True, exist_ok=True)
        temporary_link = paths.install_prefix / ".current.next"
        if temporary_link.exists() or temporary_link.is_symlink():
            temporary_link.unlink()
        temporary_link.symlink_to(Path("releases") / release)
        os.replace(temporary_link, paths.current_release)
    except Exception:
        for path in reversed(managed_paths):
            _restore_snapshot(path, snapshots[path])
        if created_final and final.is_dir() and not final.is_symlink():
            _make_tree_owner_writable(final)
            shutil.rmtree(final)
        reload_user_units()
        raise
    reload_user_units()
    if unit_payloads:
        enable_niri_user_units()
    if os.environ.get("CLAVIS_SKIP_SYSTEMD") != "1" and shutil.which("systemctl"):
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", *OBSOLETE_USER_SERVICES],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    restart_long_running(paths, previous_root)
    print(f"Installed Clavis {release} at {final}")
    print(f"Stable command: {paths.stable_key}")
    return 0


def _verify_record(root: Path, record: dict[str, Any]) -> bool:
    path = root / record["path"]
    if record.get("kind") == "symlink":
        return path.is_symlink() and os.readlink(path) == record.get("target")
    return (
        path.is_file()
        and not path.is_symlink()
        and sha256(path) == record.get("sha256")
        and stat.S_IMODE(path.stat().st_mode) == record.get("mode")
    )


def _verify_directory(root: Path, record: dict[str, Any]) -> bool:
    path = root / record["path"]
    return (
        path.is_dir()
        and not path.is_symlink()
        and stat.S_IMODE(path.stat().st_mode) == record.get("mode")
    )


def verify_manifest_release(
    paths: ClavisPaths, release: str, manifest: dict[str, Any]
) -> Path:
    entry = manifest.get("releases", {}).get(release)
    if not isinstance(entry, dict):
        raise ClavisError(f"release {release} is not recorded in the install manifest")
    root = paths.releases_home / release
    metadata = validate_release(root, release)
    if metadata.get("commit") != entry.get("commit"):
        raise ClavisError(f"release {release} commit does not match the manifest")
    failures = [
        record["path"]
        for record in entry.get("files", [])
        if not _verify_record(root, record)
    ]
    failures.extend(
        record["path"]
        for record in entry.get("directories", [])
        if not _verify_directory(root, record)
    )
    if failures:
        raise ClavisError(
            f"release {release} failed integrity checks: " + ", ".join(failures[:5])
        )
    return root


def restart_long_running(paths: ClavisPaths, old_root: Path | None) -> None:
    qs = shutil.which("qs")
    manual_shell_running = False
    if qs and old_root is not None:
        old_qml = old_root / "share/clavis/qml"
        active = read_active_shell(paths)
        target_pid: int | None = None
        active_token = ""
        if active is not None and Path(str(active.get("qmlRoot", ""))) == old_qml:
            target_pid = int(active["pid"])
            token = active.get("token")
            active_token = token if isinstance(token, str) else ""
        else:
            instances = _list_instances(qs, old_qml)
            pids = {
                int(instance["pid"])
                for instance in instances
                if str(instance.get("pid", "")).isdigit()
            }
            if len(pids) == 1:
                target_pid = pids.pop()
            elif len(pids) > 1:
                print(
                    "Warning: multiple old Clavis Shell instances were found; "
                    "none were stopped automatically.",
                    file=sys.stderr,
                )
        if target_pid is not None:
            try:
                _kill_instance(qs, target_pid)
            except ClavisError as error:
                print(
                    f"Warning: the old Clavis Shell was not stopped: {error}",
                    file=sys.stderr,
                )
            else:
                manual_shell_running = True
                if active_token:
                    _remove_active_shell(paths, active_token)

    if manual_shell_running:
        restarted = subprocess.run(
            [str(paths.stable_key), "shell", "--no-duplicate"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        if restarted.returncode != 0:
            print(
                "Warning: the updated release was activated, but Clavis Shell "
                f"did not restart: {restarted.stderr.strip()}",
                file=sys.stderr,
            )


def rollback(paths: ClavisPaths, requested: str | None, dry_run: bool) -> int:
    manifest = load_manifest(paths)
    current_name = str(manifest.get("activeRelease", ""))
    if not current_name:
        raise ClavisError("no active release is recorded")
    target_name = requested or str(manifest.get("previousRelease", ""))
    if not target_name:
        candidates = [
            name for name in manifest.get("releases", {}) if name != current_name
        ]
        candidates.sort(key=release_key, reverse=True)
        if not candidates:
            raise ClavisError("no previous release is available")
        target_name = candidates[0]
    release_key(target_name)
    target = verify_manifest_release(paths, target_name, manifest)
    old_root = verify_manifest_release(paths, current_name, manifest)
    target_schemas = read_release_metadata(target).get("dataSchemas", {})
    current_schemas = read_release_metadata(old_root).get("dataSchemas", {})
    if target_schemas != current_schemas:
        raise ClavisError(
            "rollback refused because the target release uses incompatible mutable data schemas"
        )
    if dry_run:
        print(f"Would switch {current_name} -> {target_name}")
        return 0

    temporary_link = paths.install_prefix / ".current.next"
    if temporary_link.exists() or temporary_link.is_symlink():
        temporary_link.unlink()
    temporary_link.symlink_to(Path("releases") / target_name)
    os.replace(temporary_link, paths.current_release)
    manifest["activeRelease"] = target_name
    manifest["previousRelease"] = current_name
    manifest["updatedAt"] = utc_now()
    atomic_write(paths.active_release_file, f"{target_name}\n".encode())
    atomic_json(paths.manifest, manifest)
    restart_long_running(paths, old_root)
    print(f"Rolled back Clavis {current_name} -> {target_name}")
    return 0


def release_command(
    paths: ClavisPaths,
    action: str,
    requested: str | None,
    dry_run: bool,
    json_output: bool,
) -> int:
    manifest = load_manifest(paths)
    releases = manifest.get("releases", {})
    if action == "list":
        ordered = sorted(releases, key=release_key, reverse=True)
        result = {
            "activeRelease": manifest.get("activeRelease", ""),
            "previousRelease": manifest.get("previousRelease", ""),
            "releases": [
                {
                    "release": name,
                    "commit": releases[name].get("commit", "unknown"),
                    "active": name == manifest.get("activeRelease"),
                }
                for name in ordered
            ],
        }
        if json_output:
            print(json.dumps(result, separators=(",", ":")))
        else:
            for entry in result["releases"]:
                marker = "*" if entry["active"] else " "
                print(f"{marker} {entry['release']}  {entry['commit']}")
        return 0

    if requested is None:
        raise ClavisError("release remove requires a release name")
    release_key(requested)
    if requested == manifest.get("activeRelease"):
        raise ClavisError("refusing to remove the active release; roll back first")
    root = verify_manifest_release(paths, requested, manifest)
    entry = releases[requested]
    recorded_paths = {
        str(Path(record["path"])) for record in entry.get("files", [])
    }
    owned_directories = _release_owned_directories(entry)
    unknown = [
        path
        for path in root.rglob("*")
        if str(path.relative_to(root))
        not in (recorded_paths | owned_directories)
    ]
    if unknown:
        raise ClavisError(
            "release contains unrecorded entries and was preserved: "
            + ", ".join(str(path) for path in unknown[:5])
        )
    if dry_run:
        print(f"Would remove verified immutable release {requested} at {root}")
        return 0

    _make_tree_owner_writable(root)
    for record in reversed(entry.get("files", [])):
        path = root / record["path"]
        if record.get("kind") == "symlink" and path.is_symlink():
            path.unlink()
        elif path.is_file() and not path.is_symlink():
            path.unlink()
    for relative in sorted(
        owned_directories,
        key=lambda value: len(Path(value).parts),
        reverse=True,
    ):
        (root / relative).rmdir()
    root.rmdir()
    del releases[requested]
    if manifest.get("previousRelease") == requested:
        candidates = sorted(
            (name for name in releases if name != manifest.get("activeRelease")),
            key=release_key,
            reverse=True,
        )
        manifest["previousRelease"] = candidates[0] if candidates else ""
    manifest["updatedAt"] = utc_now()
    atomic_json(paths.manifest, manifest)
    print(f"Removed Clavis release {requested}")
    return 0


def _remove_recorded_file(record: dict[str, Any], dry_run: bool) -> tuple[bool, str]:
    path = Path(record["path"])
    if not path.exists() and not path.is_symlink():
        return True, "missing"
    if record.get("kind") == "symlink":
        if not path.is_symlink():
            return False, "not the recorded symlink"
        if os.readlink(path) != record.get("target"):
            return False, "modified"
        if not dry_run:
            path.unlink()
        return True, "removed"
    if not path.is_file() or path.is_symlink():
        return False, "not a regular file"
    if sha256(path) != record.get("sha256"):
        return False, "modified"
    if not dry_run:
        path.unlink()
    return True, "removed"


def _remove_empty_parents(path: Path, stop: Path) -> None:
    current = path
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _make_tree_owner_writable(root: Path) -> None:
    if not root.is_dir() or root.is_symlink():
        return
    for path in [
        root,
        *(
            item
            for item in root.rglob("*")
            if item.is_dir() and not item.is_symlink()
        ),
    ]:
        mode = stat.S_IMODE(path.stat().st_mode)
        path.chmod(mode | stat.S_IWUSR | stat.S_IXUSR)


def _make_owned_directories_writable(
    root: Path, relative_directories: set[str]
) -> None:
    if not root.is_dir() or root.is_symlink():
        return
    root.chmod(stat.S_IMODE(root.stat().st_mode) | stat.S_IWUSR | stat.S_IXUSR)
    for relative in relative_directories:
        path = root / relative
        if path.is_dir() and not path.is_symlink():
            path.chmod(
                stat.S_IMODE(path.stat().st_mode) | stat.S_IWUSR | stat.S_IXUSR
            )


def _validate_purge_root(paths: ClavisPaths, target: Path, label: str) -> None:
    if not target.is_absolute():
        raise ClavisError(f"refusing to purge non-absolute {label} path: {target}")
    normalized = Path(os.path.normpath(target))
    dangerous = {
        Path("/"),
        paths.home,
        paths.install_prefix,
        paths.bin_home,
    }
    if normalized in dangerous or len(normalized.parts) < 3:
        raise ClavisError(f"refusing to purge unsafe {label} path: {target}")
    if target.is_symlink():
        raise ClavisError(f"refusing to purge symlinked {label} path: {target}")


def uninstall(
    paths: ClavisPaths,
    dry_run: bool,
    purge_cache: bool,
    purge_config: bool,
    purge_data: bool,
) -> int:
    manifest = load_manifest(paths)
    for enabled, target, label in (
        (purge_cache, paths.cache_home, "cache"),
        (purge_config, paths.config_home, "config"),
        (purge_data, paths.data_home, "data"),
    ):
        if enabled:
            _validate_purge_root(paths, target, label)
    preserved: list[dict[str, Any]] = []

    def preserve_file(
        path: Path,
        reason: str,
        *,
        owned: bool,
        expected_sha256: str | None = None,
        restoration: dict[str, Any] | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "kind": "file",
            "path": str(path),
            "reason": reason,
            "owned": owned,
        }
        if expected_sha256:
            record["expectedSha256"] = expected_sha256
        if restoration:
            record.update(restoration)
        preserved.append(record)

    for record in manifest.get("preservedItems", []):
        if not isinstance(record, dict):
            continue
        if record.get("kind") != "file":
            continue
        path_value = record.get("path")
        if not isinstance(path_value, str) or not path_value:
            continue
        path = Path(path_value)
        if not path.exists() and not path.is_symlink():
            continue
        expected = record.get("expectedSha256")
        if record.get("owned") is True and isinstance(expected, str):
            removed, reason = _remove_recorded_file(
                {"path": str(path), "sha256": expected}, dry_run
            )
            if removed:
                if not dry_run:
                    _remove_empty_parents(path.parent, paths.install_prefix)
                continue
            preserve_file(
                path,
                reason,
                owned=True,
                expected_sha256=expected,
            )
        else:
            preserved.append(record)
    if dry_run:
        print(f"Would remove Clavis program files recorded in {paths.manifest}")
    else:
        for service in (*MANAGED_NIRI_USER_SERVICES, *OBSOLETE_USER_SERVICES):
            if (os.environ.get("CLAVIS_SKIP_SYSTEMD") != "1"
                    and shutil.which("systemctl")):
                subprocess.run(
                    ["systemctl", "--user", "disable", "--now", service], check=False
                )

    for record in manifest.get("userUnits", []):
        removed, reason = _remove_recorded_file(record, dry_run)
        if not removed:
            preserve_file(
                Path(record["path"]),
                reason,
                owned=True,
                expected_sha256=record.get("sha256"),
            )
    launcher = manifest.get("launcher")
    if isinstance(launcher, dict):
        removed, reason = _remove_recorded_file(launcher, dry_run)
        if not removed:
            preserve_file(
                Path(launcher["path"]),
                reason,
                owned=True,
                expected_sha256=launcher.get("sha256"),
            )

    for release, entry in manifest.get("releases", {}).items():
        root = paths.releases_home / release
        recorded_paths = {
            str(Path(record["path"]))
            for record in entry.get("files", [])
            if isinstance(record, dict) and isinstance(record.get("path"), str)
        }
        owned_directories = _release_owned_directories(entry)
        for path in root.rglob("*"):
            relative = str(path.relative_to(root))
            if relative in recorded_paths or relative in owned_directories:
                continue
            preserve_file(
                path,
                "unrecorded-directory"
                if path.is_dir() and not path.is_symlink()
                else "unrecorded",
                owned=False,
            )
        if not dry_run:
            _make_owned_directories_writable(root, owned_directories)
        for record in reversed(entry.get("files", [])):
            absolute_record = dict(record)
            absolute_record["path"] = str(root / record["path"])
            removed, reason = _remove_recorded_file(absolute_record, dry_run)
            if not removed:
                preserve_file(
                    Path(absolute_record["path"]),
                    reason,
                    owned=True,
                    expected_sha256=record.get("sha256"),
                )
        if not dry_run:
            for relative in sorted(
                owned_directories,
                key=lambda value: len(Path(value).parts),
                reverse=True,
            ):
                try:
                    (root / relative).rmdir()
                except OSError:
                    pass
            try:
                root.rmdir()
            except OSError:
                pass

    if not dry_run:
        if paths.current_release.is_symlink():
            paths.current_release.unlink()
        if paths.active_release_file.exists():
            paths.active_release_file.unlink()
        if purge_cache and paths.cache_home.exists():
            shutil.rmtree(paths.cache_home)
        if purge_config and paths.config_home.exists():
            shutil.rmtree(paths.config_home)
        if purge_data and paths.data_home.exists():
            shutil.rmtree(paths.data_home)
        preserved = [
            record
            for record in preserved
            if record.get("kind") != "file"
            or Path(str(record.get("path", ""))).exists()
            or Path(str(record.get("path", ""))).is_symlink()
        ]
        if preserved:
            manifest["activeRelease"] = ""
            manifest["previousRelease"] = ""
            manifest["releases"] = {}
            manifest["launcher"] = None
            manifest["userUnits"] = []
            manifest["preservedItems"] = preserved
            manifest["updatedAt"] = utc_now()
            atomic_json(paths.manifest, manifest)
        elif paths.manifest.exists():
            paths.manifest.unlink()

    if preserved:
        print("Preserved files that were modified or not regular files:")
        for item in preserved:
            if item.get("kind") == "file":
                print(f"  {item.get('path')} ({item.get('reason', 'preserved')})")
    print("Dry run complete." if dry_run else "Clavis program uninstall complete.")
    return 0


def legacy_report(paths: ClavisPaths) -> dict[str, Any]:
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", paths.home / ".config"))
    xdg_data = Path(os.environ.get("XDG_DATA_HOME", paths.home / ".local/share"))
    old_cache = paths.home / ".cache/quickshell"
    candidates = [
        ("source_checkout", paths.home / ".config/quickshell"),
        ("system_key", Path("/usr/local/bin/key")),
        ("system_qml_clavis_lib64", Path("/usr/lib64/qt6/qml/Clavis")),
        ("system_qml_shapes_lib64", Path("/usr/lib64/qt6/qml/M3Shapes")),
        ("system_qml_clavis_lib", Path("/usr/lib/qt6/qml/Clavis")),
        ("system_qml_shapes_lib", Path("/usr/lib/qt6/qml/M3Shapes")),
        ("user_unit", xdg_config / "systemd/user/clavis-cliphist.service"),
        ("legacy_settings", old_cache / "personalization.json"),
        ("legacy_state", old_cache),
        ("legacy_matugen", paths.home / ".cache/quickshell-dev-colorscheme"),
        ("legacy_niri_colors", xdg_config / "niri/colors.kdl"),
        ("legacy_kitty_colors", xdg_config / "kitty/themes/Matugen.conf"),
        ("legacy_btop_theme", xdg_config / "btop/themes/matugen.theme"),
        ("legacy_cava_theme", xdg_config / "cava/themes/matugen"),
        ("legacy_yazi_theme", xdg_config / "yazi/theme.toml"),
        ("legacy_fcitx5_theme", xdg_data / "fcitx5/themes/Matugen"),
    ]
    entries = []
    for kind, path in candidates:
        entries.append(
            {
                "kind": kind,
                "path": str(path),
                "exists": path.exists() or path.is_symlink(),
                "ownership": "unknown" if path.exists() else "absent",
            }
        )
    return {
        "schemaVersion": 1,
        "command": "doctor legacy",
        "ok": True,
        "entries": entries,
        "note": "No legacy path is deleted automatically.",
    }


def migrate_legacy(paths: ClavisPaths, dry_run: bool) -> int:
    legacy = paths.home / ".cache/quickshell"
    mappings = {
        legacy / "personalization.json": paths.config_home / "config.json",
        legacy / "ui-preferences.json": paths.config_home / "ui-preferences.json",
        legacy / "quick-toggles.json": paths.config_home / "quick-toggles.json",
        legacy / "tray.json": paths.config_home / "tray.json",
        legacy / "idle-policy.json": paths.config_home / "idle-policy.json",
    }
    actions = []
    for source, destination in mappings.items():
        if not source.is_file():
            continue
        if destination.exists():
            actions.append(
                {"source": str(source), "destination": str(destination), "result": "conflict"}
            )
            continue
        actions.append(
            {"source": str(source), "destination": str(destination), "result": "would-copy" if dry_run else "copied"}
        )
        if not dry_run:
            atomic_write(destination, source.read_bytes(), stat.S_IMODE(source.stat().st_mode))
    report = {
        "schemaVersion": 1,
        "createdAt": utc_now(),
        "dryRun": dry_run,
        "actions": actions,
        "legacy": legacy_report(paths),
    }
    if not dry_run:
        report_path = paths.state_home / "migrations" / f"legacy-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        atomic_json(report_path, report)
        print(f"Migration report: {report_path}")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def update_command(paths: ClavisPaths, artifact: str | None) -> int:
    if artifact is None:
        raise ClavisError(
            "online updates are not enabled: no signed artifact provider is configured; install a local source release with ./setup.sh install"
        )
    raise ClavisError(
        "artifact installation is reserved until release signatures and archive traversal checks are available; the current release was not changed"
    )


def install_component_command(
    paths: ClavisPaths, name: str, source: str | None
) -> int:
    record = install_component(paths, name, source)
    print(
        f"Installed {name} from {record['source']} at {record['prefix']}"
    )
    return 0


def update_component_command(paths: ClavisPaths, name: str) -> int:
    record = update_component(paths, name)
    print(f"Updated {name} to {record['commit']}")
    return 0


def uninstall_component_command(paths: ClavisPaths, name: str) -> int:
    uninstall_component(paths, name)
    print(f"Uninstalled {name}")
    return 0


def parser_for(command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"key {command}")
    if command == "rollback":
        parser.add_argument("release", nargs="?")
        parser.add_argument("--dry-run", action="store_true")
    elif command == "release":
        parser.add_argument("action", choices=["list", "remove", "install-finalize"])
        parser.add_argument("release", nargs="?")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--partial", type=Path)
    elif command == "uninstall":
        parser.add_argument("component", nargs="?")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--purge-cache", action="store_true")
        parser.add_argument("--purge-config", action="store_true")
        parser.add_argument("--purge-data", action="store_true")
    elif command == "doctor":
        parser.add_argument("topic", choices=["legacy"])
        parser.add_argument("--json", action="store_true")
    elif command == "migrate":
        parser.add_argument("topic", choices=["legacy"])
        parser.add_argument("--dry-run", action="store_true")
    elif command == "update":
        parser.add_argument("component", nargs="?")
        parser.add_argument("--artifact")
    elif command == "install":
        parser.add_argument("component")
        parser.add_argument("--source")
    elif command == "component":
        parser.add_argument("action", choices=["status"])
        parser.add_argument("--json", action="store_true")
    elif command in {"shell", "ipc"}:
        parser.add_argument("arguments", nargs=argparse.REMAINDER)
    elif command == "shell-log-monitor":
        parser.add_argument("--pid", required=True, type=int)
        parser.add_argument("--start-ticks", required=True, type=int)
        parser.add_argument("--token", required=True)
        parser.add_argument("--log-path", required=True, type=Path)
    elif command == "install-finalize":
        parser.add_argument("--partial", required=True, type=Path)
        parser.add_argument("--release", required=True)
    else:
        raise ClavisError(f"unsupported management command: {command}")
    return parser


def main(argv: list[str]) -> int:
    if not argv:
        fail("management command is required", 2)
    command, arguments = argv[0], argv[1:]
    try:
        paths = ClavisPaths.from_environment()
        if command in {"shell", "ipc"}:
            parsed = argparse.Namespace(arguments=arguments)
        else:
            parsed = parser_for(command).parse_args(arguments)
        if command == "shell":
            return run_shell(paths, parsed.arguments)
        if command == "ipc":
            return run_ipc(paths, parsed.arguments)
        if command == "shell-log-monitor":
            allowed_logs = {
                _shell_log_path(paths, mode).absolute() for mode in SHELL_LOG_NAMES
            }
            log_path = parsed.log_path.absolute()
            if (
                log_path not in allowed_logs
                or paths.logs_home.is_symlink()
                or log_path.is_symlink()
                or not log_path.is_file()
            ):
                raise ClavisError(f"invalid Shell log monitor path: {log_path}")
            if (
                parsed.pid <= 0
                or parsed.start_ticks <= 0
                or re.fullmatch(r"[0-9]+-[0-9]+-[0-9a-f]{16}", parsed.token)
                is None
            ):
                raise ClavisError("invalid Shell log monitor identity")
            return run_shell_log_monitor(
                paths,
                parsed.pid,
                parsed.start_ticks,
                parsed.token,
                log_path,
            )
        if command == "rollback":
            return rollback(paths, parsed.release, parsed.dry_run)
        if command == "release":
            if parsed.action == "install-finalize":
                if parsed.release is None or parsed.partial is None:
                    raise ClavisError(
                        "release install-finalize requires RELEASE and --partial PATH"
                    )
                return finalize_install(paths, parsed.partial, parsed.release)
            return release_command(
                paths,
                parsed.action,
                parsed.release,
                parsed.dry_run,
                parsed.json,
            )
        if command == "uninstall":
            if parsed.component:
                if any((parsed.dry_run, parsed.purge_cache, parsed.purge_config, parsed.purge_data)):
                    raise ClavisError("component uninstall does not accept release purge options")
                return uninstall_component_command(paths, parsed.component)
            return uninstall(
                paths,
                parsed.dry_run,
                parsed.purge_cache,
                parsed.purge_config,
                parsed.purge_data,
            )
        if command == "doctor":
            report = legacy_report(paths)
            if parsed.json:
                print(json.dumps(report, separators=(",", ":")))
            else:
                print("Legacy Clavis installation report:")
                for entry in report["entries"]:
                    marker = "FOUND" if entry["exists"] else "clear"
                    print(f"  [{marker:5}] {entry['kind']}: {entry['path']}")
                print("No legacy path was changed.")
            return 0
        if command == "migrate":
            return migrate_legacy(paths, parsed.dry_run)
        if command == "update":
            if parsed.component:
                if parsed.artifact:
                    raise ClavisError("component update cannot be combined with --artifact")
                return update_component_command(paths, parsed.component)
            return update_command(paths, parsed.artifact)
        if command == "install-finalize":
            return finalize_install(paths, parsed.partial, parsed.release)
        if command == "install":
            return install_component_command(paths, parsed.component, parsed.source)
        if command == "component":
            return component_status(paths, parsed.json)
    except (ClavisError, ComponentError, PathConfigurationError, OSError, ValueError) as error:
        fail(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
