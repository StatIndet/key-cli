#!/usr/bin/env bash
set -euo pipefail

key_binary=${1:?key binary is required}
fixture=${2:?weather fixture is required}
test_dir=$(mktemp -d /tmp/key-cli-weather-command.XXXXXX)
cleanup() { rm -rf -- "$test_dir"; }
trap cleanup EXIT HUP INT TERM

# The development Shell starts key from the Clavis checkout, not key-cli.
# Verify provider discovery is anchored to the key executable, not $PWD.
output=$(cd "$test_dir" && \
    HOME="$test_dir/home" XDG_CACHE_HOME="$test_dir/cache" \
    "$key_binary" weather --json --fixture "$fixture")
python3 -c '
import json, sys
payload = json.loads(sys.argv[1])
assert payload["valid"] is True
assert payload["current"]
assert payload["daily"]
' "$output"

printf 'Key weather command discovery test passed\n'
