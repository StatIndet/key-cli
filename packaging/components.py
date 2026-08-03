#!/usr/bin/env python3
"""Controlled source provider for the three official Clavis components.

This module intentionally has no arbitrary URL or shell-script entry point.
The registry is the allow-list; a source directory may be supplied explicitly
for local development or discovered next to the Clavis source checkout.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from clavis_paths import ClavisPaths


REGISTRY: dict[str, dict[str, Any]] = {
    "keytop": {
        "repository": "keytop",
        "package": "keytop",
        "install": "install",
        "uninstall": "uninstall",
    },
    "clavis-zsh-theme": {
        "repository": "clavis-zsh-theme",
        "package": "clavis-zsh-theme",
        "install": "install",
        "uninstall": "uninstall",
        "apply": "apply",
    },
    "clavis-fcitx5-theme": {
        "repository": "clavis-fcitx5-theme",
        "package": "clavis-fcitx5-theme",
        "install": "install",
        "uninstall": "uninstall",
        "apply": "apply",
    },
}


class ComponentError(RuntimeError):
    pass


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _run(command: list[str], *, cwd: Path | None = None, check: bool = False, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        check=False,
        text=True,
        capture_output=True,
        **kwargs,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ComponentError(f"{shlex_join(command)} failed: {detail}")
    return result


def shlex_join(command: list[str]) -> str:
    import shlex

    return shlex.join(command)


def component_record_path(paths: ClavisPaths) -> Path:
    return paths.state_home / "components.json"


def load_records(paths: ClavisPaths) -> dict[str, Any]:
    path = component_record_path(paths)
    if not path.exists():
        return {"schemaVersion": 1, "components": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ComponentError(f"cannot read component state: {error}") from error
    if value.get("schemaVersion") != 1 or not isinstance(value.get("components"), dict):
        raise ComponentError(f"unsupported component state: {path}")
    return value


def save_records(paths: ClavisPaths, value: dict[str, Any]) -> None:
    path = component_record_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _valid_name(name: str) -> dict[str, Any]:
    try:
        return REGISTRY[name]
    except KeyError as error:
        allowed = ", ".join(sorted(REGISTRY))
        raise ComponentError(f"unsupported Clavis component {name!r}; allowed: {allowed}") from error


def _candidate_paths(name: str) -> list[Path]:
    repository = REGISTRY[name]["repository"]
    candidates: list[Path] = []
    source_root = os.environ.get("CLAVIS_SOURCE_ROOT", "").strip()
    if source_root:
        candidates.append(Path(source_root).expanduser().resolve().parent / repository)
    candidates.append(Path(__file__).resolve().parents[2] / repository)
    candidates.append(Path.cwd().resolve().parent / repository)
    return candidates


def resolve_source(name: str, explicit: str | None = None) -> Path:
    _valid_name(name)
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
    else:
        candidate = next((path for path in _candidate_paths(name) if path.exists()), None)
        if candidate is None:
            raise ComponentError(
                f"official source checkout for {name} was not found; pass --source PATH"
            )
    if not candidate.is_dir() or not (candidate / ".git").exists():
        raise ComponentError(f"component source is not a Git checkout: {candidate}")
    if not (candidate / "setup.sh").is_file():
        raise ComponentError(f"component source has no public setup.sh: {candidate}")
    return candidate


def git_status(source: Path) -> tuple[str, bool, str]:
    commit_result = _run(["git", "-C", str(source), "rev-parse", "HEAD"])
    if commit_result.returncode != 0:
        raise ComponentError(f"cannot read Git commit for {source}")
    status_result = _run(
        ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=normal"]
    )
    if status_result.returncode != 0:
        raise ComponentError(f"cannot inspect Git status for {source}")
    branch_result = _run(["git", "-C", str(source), "branch", "--show-current"])
    branch = branch_result.stdout.strip() or "(detached)"
    return commit_result.stdout.strip(), bool(status_result.stdout.strip()), branch


def _prefix() -> Path:
    value = os.environ.get("CLAVIS_COMPONENT_PREFIX", "").strip() or os.environ.get(
        "CMAKE_INSTALL_PREFIX", ""
    ).strip()
    return Path(value).expanduser().resolve() if value else Path("/usr/local")


def _destdir() -> Path | None:
    value = os.environ.get("DESTDIR", "").strip()
    return Path(value).expanduser().resolve() if value else None


def _installed_target(prefix: Path, name: str) -> Path:
    if name == "keytop":
        return prefix / "bin/keytop"
    if name == "clavis-zsh-theme":
        return prefix / "bin/prompt"
    return prefix / "share/clavis-fcitx5-theme"


def reject_package_owned_target(name: str, prefix: Path, destdir: Path | None) -> None:
    """Refuse source operations over a pacman-owned /usr target."""
    if prefix != Path("/usr"):
        return
    target = _installed_target(prefix, name)
    if destdir is not None:
        target = destdir / target.relative_to("/")
    if not target.exists() or shutil.which("pacman") is None:
        return
    result = _run(["pacman", "-Qo", str(target)])
    if result.returncode == 0:
        owner = result.stdout.strip() or "a pacman package"
        raise ComponentError(f"{target} is managed by pacman ({owner}); source provider refused")


def _setup_environment(prefix: Path, destdir: Path | None) -> dict[str, str]:
    environment = os.environ.copy()
    environment["CMAKE_INSTALL_PREFIX"] = str(prefix)
    if destdir is None:
        environment.pop("DESTDIR", None)
    else:
        environment["DESTDIR"] = str(destdir)
    return environment


def _run_setup(name: str, source: Path, action: str, prefix: Path, destdir: Path | None) -> None:
    spec = _valid_name(name)
    if action not in {spec.get("install"), spec.get("uninstall"), spec.get("apply")}:
        raise ComponentError(f"action {action!r} is not supported by {name}")
    command = [str(source / "setup.sh"), action]
    result = _run(command, cwd=source, env=_setup_environment(prefix, destdir))
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ComponentError(f"{name} {action} failed: {detail}")


def _record(name: str, source: Path, commit: str, dirty: bool, branch: str, prefix: Path, destdir: Path | None) -> dict[str, Any]:
    return {
        "name": name,
        "repository": REGISTRY[name]["repository"],
        "source": str(source),
        "commit": commit,
        "branch": branch,
        "dirty": dirty,
        "prefix": str(prefix),
        "destdir": str(destdir) if destdir is not None else "",
        "installMethod": "source",
        "updatedAt": _now(),
    }


def install_component(paths: ClavisPaths, name: str, source_argument: str | None = None) -> dict[str, Any]:
    source = resolve_source(name, source_argument)
    commit, dirty, branch = git_status(source)
    prefix = _prefix()
    destdir = _destdir()
    reject_package_owned_target(name, prefix, destdir)
    _run_setup(name, source, "install", prefix, destdir)
    records = load_records(paths)
    records["components"][name] = _record(name, source, commit, dirty, branch, prefix, destdir)
    save_records(paths, records)
    return records["components"][name]


def update_component(paths: ClavisPaths, name: str) -> dict[str, Any]:
    _valid_name(name)
    records = load_records(paths)
    old = records["components"].get(name)
    if not isinstance(old, dict):
        raise ComponentError(f"component {name} is not installed by the source provider")
    source = resolve_source(name, str(old.get("source", "")))
    _commit, dirty, _branch = git_status(source)
    if dirty:
        raise ComponentError(f"refusing to update {name}: source checkout has local modifications")
    prefix = Path(str(old.get("prefix", _prefix())))
    destdir_value = str(old.get("destdir", ""))
    destdir = Path(destdir_value) if destdir_value else None
    reject_package_owned_target(name, prefix, destdir)
    pull = _run(["git", "-C", str(source), "pull", "--ff-only"])
    if pull.returncode != 0:
        detail = pull.stderr.strip() or pull.stdout.strip()
        raise ComponentError(f"refusing to update {name}: git pull --ff-only failed: {detail}")
    commit, dirty, branch = git_status(source)
    _run_setup(name, source, "install", prefix, destdir)
    records["components"][name] = _record(name, source, commit, dirty, branch, prefix, destdir)
    save_records(paths, records)
    return records["components"][name]


def uninstall_component(paths: ClavisPaths, name: str) -> None:
    _valid_name(name)
    records = load_records(paths)
    old = records["components"].get(name)
    if not isinstance(old, dict):
        raise ComponentError(f"component {name} is not installed by the source provider")
    source = resolve_source(name, str(old.get("source", "")))
    prefix = Path(str(old.get("prefix", _prefix())))
    destdir_value = str(old.get("destdir", ""))
    destdir = Path(destdir_value) if destdir_value else None
    reject_package_owned_target(name, prefix, destdir)
    _run_setup(name, source, "uninstall", prefix, destdir)
    records["components"].pop(name, None)
    save_records(paths, records)


def status(paths: ClavisPaths, json_output: bool = False) -> int:
    records = load_records(paths)
    result = {name: records["components"].get(name) for name in sorted(REGISTRY)}
    if json_output:
        print(json.dumps({"schemaVersion": 1, "components": result}, ensure_ascii=False, separators=(",", ":")))
    else:
        for name, record in result.items():
            if not record:
                print(f"- {name}: not installed by source provider")
                continue
            dirty = " dirty" if record.get("dirty") else ""
            print(f"- {name}: {record.get('commit', 'unknown')[:12]}{dirty} ({record.get('source', '')})")
    return 0
