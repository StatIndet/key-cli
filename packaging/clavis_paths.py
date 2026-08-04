"""Canonical XDG and release paths used by Clavis installation tooling."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class PathConfigurationError(ValueError):
    pass


def _absolute_env(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        raise PathConfigurationError(f"{name} must be an absolute path: {value}")
    return path


def _xdg_path(override: str, variable: str, fallback: Path) -> Path:
    direct = _absolute_env(override)
    if direct is not None:
        return direct
    base = _absolute_env(variable) or fallback
    return base / "clavis"


@dataclass(frozen=True)
class ClavisPaths:
    home: Path
    bin_home: Path
    key_path: Path
    install_prefix: Path
    config_home: Path
    data_home: Path
    state_home: Path
    cache_home: Path
    runtime_home: Path
    profile_name: str
    profile_config_home: Path
    profile_home: Path
    generated_home: Path
    qml_import_home: Path

    @classmethod
    def from_environment(cls) -> "ClavisPaths":
        home = _absolute_env("HOME")
        if home is None:
            raise PathConfigurationError("HOME must be set to an absolute path")
        bin_home = _absolute_env("CLAVIS_BIN_HOME") or home / ".local/bin"
        key_path = _absolute_env("CLAVIS_KEY") or bin_home / "key"
        install_prefix = (
            _absolute_env("CLAVIS_INSTALL_PREFIX")
            or home / ".local/lib/clavis"
        )
        config_home = _xdg_path(
            "CLAVIS_CONFIG_HOME", "XDG_CONFIG_HOME", home / ".config"
        )
        data_home = _xdg_path(
            "CLAVIS_DATA_HOME", "XDG_DATA_HOME", home / ".local/share"
        )
        state_home = _xdg_path(
            "CLAVIS_STATE_HOME", "XDG_STATE_HOME", home / ".local/state"
        )
        cache_home = _xdg_path(
            "CLAVIS_CACHE_HOME", "XDG_CACHE_HOME", home / ".cache"
        )
        runtime_home = _absolute_env("CLAVIS_RUNTIME_HOME")
        if runtime_home is None:
            runtime_base = _absolute_env("XDG_RUNTIME_DIR")
            runtime_home = (
                runtime_base / "clavis"
                if runtime_base is not None
                else cache_home / "runtime/clavis"
            )
        profile_name = os.environ.get("CLAVIS_PROFILE", "default").strip()
        if (
            not profile_name
            or profile_name in {".", ".."}
            or "/" in profile_name
            or "\\" in profile_name
        ):
            raise PathConfigurationError(f"invalid CLAVIS_PROFILE: {profile_name!r}")
        profile_home = (
            _absolute_env("CLAVIS_PROFILE_HOME")
            or data_home / "profiles" / profile_name
        )
        profile_config_home = (
            _absolute_env("CLAVIS_PROFILE_CONFIG_HOME")
            or config_home / "profiles" / profile_name
        )
        generated_home = (
            _absolute_env("CLAVIS_GENERATED_HOME")
            or profile_home / "generated"
        )
        qml_import_home = (
            _absolute_env("CLAVIS_QML_IMPORT_HOME")
            or install_prefix / "current/lib/qml"
        )
        return cls(
            home=home,
            bin_home=bin_home,
            key_path=key_path,
            install_prefix=install_prefix,
            config_home=config_home,
            data_home=data_home,
            state_home=state_home,
            cache_home=cache_home,
            runtime_home=runtime_home,
            profile_name=profile_name,
            profile_config_home=profile_config_home,
            profile_home=profile_home,
            generated_home=generated_home,
            qml_import_home=qml_import_home,
        )

    @property
    def releases_home(self) -> Path:
        return self.install_prefix / "releases"

    @property
    def current_release(self) -> Path:
        return self.install_prefix / "current"

    @property
    def stable_key(self) -> Path:
        return self.key_path

    @property
    def manifest(self) -> Path:
        return self.state_home / "install-manifest.json"

    @property
    def active_release_file(self) -> Path:
        return self.state_home / "active-release"

    @property
    def logs_home(self) -> Path:
        return self.state_home / "logs"

    @property
    def components_manifest(self) -> Path:
        return self.state_home / "components.json"

    @property
    def user_systemd_home(self) -> Path:
        xdg_config = _absolute_env("XDG_CONFIG_HOME") or self.home / ".config"
        return xdg_config / "systemd/user"

    def as_environment(self, release_root: Path) -> dict[str, str]:
        qml_import = release_root / "lib/qml"
        result = {
            "CLAVIS_BIN_HOME": str(self.bin_home),
            "CLAVIS_INSTALL_PREFIX": str(self.install_prefix),
            "CLAVIS_RELEASE_ROOT": str(release_root),
            "CLAVIS_CONFIG_HOME": str(self.config_home),
            "CLAVIS_DATA_HOME": str(self.data_home),
            "CLAVIS_STATE_HOME": str(self.state_home),
            "CLAVIS_CACHE_HOME": str(self.cache_home),
            "CLAVIS_RUNTIME_HOME": str(self.runtime_home),
            "CLAVIS_PROFILE": self.profile_name,
            "CLAVIS_PROFILE_CONFIG_HOME": str(self.profile_config_home),
            "CLAVIS_PROFILE_HOME": str(self.profile_home),
            "CLAVIS_GENERATED_HOME": str(self.generated_home),
            "CLAVIS_QML_IMPORT_HOME": str(qml_import),
            "CLAVIS_KEY": str(self.stable_key),
        }
        for variable in ("QML_IMPORT_PATH", "QML2_IMPORT_PATH"):
            existing = os.environ.get(variable, "")
            entries = [entry for entry in existing.split(":") if entry]
            if str(qml_import) not in entries:
                entries.insert(0, str(qml_import))
            result[variable] = ":".join(entries)
        return result
