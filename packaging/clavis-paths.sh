#!/usr/bin/env bash

# Shared shell implementation of the ClavisPaths environment contract.
# Call clavis_paths_init before using the exported CLAVIS_* variables.

clavis_paths_require_absolute() {
    local variable_name=$1
    local value=$2
    if [[ -n "$value" && "$value" != /* ]]; then
        printf '%s must be an absolute path: %s\n' "$variable_name" "$value" >&2
        return 1
    fi
}

clavis_paths_init() {
    if [[ -z "${HOME:-}" ]]; then
        printf 'HOME must be set\n' >&2
        return 1
    fi
    clavis_paths_require_absolute HOME "$HOME"

    local config_base=${XDG_CONFIG_HOME:-$HOME/.config}
    local data_base=${XDG_DATA_HOME:-$HOME/.local/share}
    local state_base=${XDG_STATE_HOME:-$HOME/.local/state}
    local cache_base=${XDG_CACHE_HOME:-$HOME/.cache}
    local runtime_base=${XDG_RUNTIME_DIR:-${CLAVIS_CACHE_HOME:-$cache_base/clavis}/runtime}

    clavis_paths_require_absolute XDG_CONFIG_HOME "$config_base"
    clavis_paths_require_absolute XDG_DATA_HOME "$data_base"
    clavis_paths_require_absolute XDG_STATE_HOME "$state_base"
    clavis_paths_require_absolute XDG_CACHE_HOME "$cache_base"
    clavis_paths_require_absolute XDG_RUNTIME_DIR "$runtime_base"

    CLAVIS_BIN_HOME=${CLAVIS_BIN_HOME:-$HOME/.local/bin}
    CLAVIS_INSTALL_PREFIX=${CLAVIS_INSTALL_PREFIX:-$HOME/.local/lib/clavis}
    CLAVIS_CONFIG_HOME=${CLAVIS_CONFIG_HOME:-$config_base/clavis}
    CLAVIS_DATA_HOME=${CLAVIS_DATA_HOME:-$data_base/clavis}
    CLAVIS_STATE_HOME=${CLAVIS_STATE_HOME:-$state_base/clavis}
    CLAVIS_CACHE_HOME=${CLAVIS_CACHE_HOME:-$cache_base/clavis}
    CLAVIS_RUNTIME_HOME=${CLAVIS_RUNTIME_HOME:-$runtime_base/clavis}
    CLAVIS_PROFILE=${CLAVIS_PROFILE:-default}

    local variable
    for variable in \
        CLAVIS_BIN_HOME CLAVIS_INSTALL_PREFIX CLAVIS_CONFIG_HOME \
        CLAVIS_DATA_HOME CLAVIS_STATE_HOME CLAVIS_CACHE_HOME \
        CLAVIS_RUNTIME_HOME; do
        clavis_paths_require_absolute "$variable" "${!variable}"
    done
    if [[ -z "$CLAVIS_PROFILE" || "$CLAVIS_PROFILE" == "." \
        || "$CLAVIS_PROFILE" == ".." || "$CLAVIS_PROFILE" == *'/'* \
        || "$CLAVIS_PROFILE" == *\\* ]]; then
        printf 'invalid CLAVIS_PROFILE: %s\n' "$CLAVIS_PROFILE" >&2
        return 1
    fi

    CLAVIS_PROFILE_CONFIG_HOME=${CLAVIS_PROFILE_CONFIG_HOME:-$CLAVIS_CONFIG_HOME/profiles/$CLAVIS_PROFILE}
    CLAVIS_PROFILE_HOME=${CLAVIS_PROFILE_HOME:-$CLAVIS_DATA_HOME/profiles/$CLAVIS_PROFILE}
    CLAVIS_GENERATED_HOME=${CLAVIS_GENERATED_HOME:-$CLAVIS_PROFILE_HOME/generated}
    CLAVIS_QML_IMPORT_HOME=${CLAVIS_QML_IMPORT_HOME:-$CLAVIS_INSTALL_PREFIX/current/lib/qml}
    for variable in \
        CLAVIS_PROFILE_CONFIG_HOME CLAVIS_PROFILE_HOME \
        CLAVIS_GENERATED_HOME CLAVIS_QML_IMPORT_HOME; do
        clavis_paths_require_absolute "$variable" "${!variable}"
    done
    export CLAVIS_BIN_HOME CLAVIS_INSTALL_PREFIX CLAVIS_CONFIG_HOME
    export CLAVIS_DATA_HOME CLAVIS_STATE_HOME CLAVIS_CACHE_HOME
    export CLAVIS_RUNTIME_HOME CLAVIS_PROFILE CLAVIS_PROFILE_HOME
    export CLAVIS_PROFILE_CONFIG_HOME CLAVIS_GENERATED_HOME
    export CLAVIS_QML_IMPORT_HOME
}
