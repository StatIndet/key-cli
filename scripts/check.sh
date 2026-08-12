#!/usr/bin/env bash

set -euo pipefail

script_dir=$(
    CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd
)
repo_root=$(
    CDPATH='' cd -- "${script_dir}/.." && pwd
)
cd "${repo_root}"

if [[ -n "${KEY_CLI_PYTHON:-}" ]]; then
    python_bin=${KEY_CLI_PYTHON}
elif [[ -x "${repo_root}/.venv/bin/python" ]]; then
    python_bin=${repo_root}/.venv/bin/python
else
    python_bin=python3
fi

if ! "${python_bin}" --version >/dev/null 2>&1; then
    printf 'error: usable Python 3 is required (set KEY_CLI_PYTHON to override)\n' >&2
    exit 127
fi

if "${python_bin}" -m ruff --version >/dev/null 2>&1; then
    ruff_command=("${python_bin}" -m ruff)
elif command -v ruff >/dev/null 2>&1; then
    ruff_command=(ruff)
else
    printf 'error: Ruff is required; install the dev extra with python -m pip install -e .[dev]\n' >&2
    exit 127
fi

git -C "${repo_root}" diff --check
"${ruff_command[@]}" format --check "${repo_root}"
"${ruff_command[@]}" check "${repo_root}"
"${python_bin}" -m compileall -q "${repo_root}/src"
"${python_bin}" -m pytest

wheel_dir=$(mktemp -d "${TMPDIR:-/tmp}/key-cli-wheel.XXXXXX")
cleanup() {
    rm -rf -- "${wheel_dir}"
}
trap cleanup EXIT HUP INT TERM

"${python_bin}" -m build --wheel --outdir "${wheel_dir}"
wheel_path=$(find "${wheel_dir}" -maxdepth 1 -type f -name '*.whl' -print -quit)
if [[ -z "${wheel_path}" ]]; then
    printf 'error: wheel build produced no .whl file\n' >&2
    exit 1
fi
"${python_bin}" "${repo_root}/scripts/check-wheel.py" "${wheel_path}"
