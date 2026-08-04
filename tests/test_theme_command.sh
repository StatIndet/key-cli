#!/usr/bin/env bash
set -euo pipefail

key_binary=$1
test_dir=$(mktemp -d /tmp/key-cli-theme-test.XXXXXX)
cleanup() { rm -rf -- "$test_dir"; }
trap cleanup EXIT

mkdir -p "$test_dir/bin"
cat > "$test_dir/bin/zsh-theme" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${KEY_THEME_ARGS:?}"
printf 'delegated:%s\n' "$*"
printf 'delegated-error\n' >&2
if [[ "$1" == "toggle" ]]; then
    exit 17
fi
EOF
chmod +x "$test_dir/bin/zsh-theme"

export KEY_THEME_ARGS="$test_dir/args"
PATH="$test_dir/bin:$PATH" "$key_binary" theme zsh status --json >"$test_dir/stdout" 2>"$test_dir/stderr"
grep -Fxq 'delegated:status --json' "$test_dir/stdout"
grep -Fxq 'delegated-error' "$test_dir/stderr"
grep -Fxq 'status --json' "$test_dir/args"

if PATH="$test_dir/bin:$PATH" "$key_binary" theme zsh toggle path; then
    printf 'delegated exit code was not preserved\n' >&2
    exit 1
else
    [[ $? -eq 17 ]]
fi

missing_path="$test_dir/missing"
if PATH="$missing_path" CLAVIS_ZSH_THEME_COMMAND=zsh-theme "$key_binary" theme zsh status >/dev/null 2>"$test_dir/missing.err"; then
    printf 'missing zsh-theme was accepted\n' >&2
    exit 1
fi
grep -Fq 'Install the clavis-zsh-theme component' "$test_dir/missing.err"

printf 'key theme zsh delegation tests passed\n'
