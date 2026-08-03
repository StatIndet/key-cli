#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
build_dir=${KEY_CLI_BUILD_DIR:-"$repo_dir/.build"}

usage() {
    cat <<'EOF'
key-cli source build helper

Usage:
  ./setup.sh doctor
  ./setup.sh configure [--build-dir PATH]
  ./setup.sh build [--build-dir PATH]
  ./setup.sh test [--build-dir PATH]
  ./setup.sh run [--build-dir PATH] -- <key arguments>
  ./setup.sh install [--build-dir PATH]
  ./setup.sh uninstall [--build-dir PATH]

Environment:
  CMAKE_INSTALL_PREFIX (default /usr/local)
  DESTDIR             (honoured by cmake --install)
EOF
}

command_name=${1:-help}
shift || true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --build-dir)
            [[ $# -ge 2 ]] || { printf 'missing --build-dir value\n' >&2; exit 2; }
            build_dir=$2
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        *)
            printf 'unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ "$build_dir" != /* ]]; then
    build_dir="$repo_dir/$build_dir"
fi

doctor() {
    local failed=0
    for command in cmake c++ python3 git; do
        if command -v "$command" >/dev/null 2>&1; then
            printf '[OK]   %s\n' "$command"
        else
            printf '[FAIL] %s\n' "$command"
            failed=1
        fi
    done
    for module in Qt6Core Qt6Gui Qt6Network; do
        if pkg-config --exists "$module" 2>/dev/null; then
            printf '[OK]   pkg-config %s\n' "$module"
        else
            printf '[WARN] pkg-config %s (CMake may still provide it)\n' "$module"
        fi
    done
    return "$failed"
}

configure() {
    local prefix_arg=()
    if [[ -n "${CMAKE_INSTALL_PREFIX:-}" ]]; then
        prefix_arg=(-DCMAKE_INSTALL_PREFIX="$CMAKE_INSTALL_PREFIX")
    fi
    cmake -S "$repo_dir" -B "$build_dir" \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_TESTING=OFF \
        "${prefix_arg[@]}"
}

build() { configure; cmake --build "$build_dir" --parallel; }

test_cmd() {
    local prefix_arg=()
    if [[ -n "${CMAKE_INSTALL_PREFIX:-}" ]]; then
        prefix_arg=(-DCMAKE_INSTALL_PREFIX="$CMAKE_INSTALL_PREFIX")
    fi
    cmake -S "$repo_dir" -B "$build_dir" \
        -DCMAKE_BUILD_TYPE=RelWithDebInfo \
        -DBUILD_TESTING=ON \
        "${prefix_arg[@]}"
    cmake --build "$build_dir" --parallel
    ctest --test-dir "$build_dir" --output-on-failure
}

install_cmd() { build; cmake --install "$build_dir"; }

uninstall_cmd() {
    local manifest="$build_dir/install_manifest.txt"
    [[ -f "$manifest" ]] || { printf 'no install manifest: %s\n' "$manifest" >&2; exit 1; }
    local destdir=${DESTDIR:-}
    while IFS= read -r path; do
        [[ -n "$path" ]] || continue
        local target="$path"
        if [[ -n "$destdir" ]]; then
            target="$destdir$path"
        fi
        [[ -e "$target" || -L "$target" ]] || continue
        rm -f -- "$target"
    done < "$manifest"
}

case "$command_name" in
    help|-h|--help) usage ;;
    doctor) doctor ;;
    configure) configure ;;
    build) build ;;
    test) test_cmd ;;
    run) build; exec "$build_dir/bin/key" "$@" ;;
    install) install_cmd ;;
    uninstall) uninstall_cmd ;;
    *) printf 'unknown command: %s\n' "$command_name" >&2; usage >&2; exit 2 ;;
esac
