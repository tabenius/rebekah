#!/usr/bin/env bash
# rebekah-mcp-bridge: only the allowed UID reaches the served command, and
# bytes pass through untouched both ways.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bridge="$repo_root/nix/mcp-bridge.py"
work="$(mktemp -d)"
pids=()
cleanup() {
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  rm -rf -- "$work"
}
trap cleanup EXIT INT TERM

wait_socket() {
  for _ in $(seq 1 50); do [[ -S "$1" ]] && return 0; sleep 0.1; done
  return 1
}

# Allowed: this test's own UID. `cat` echoes each JSON-RPC line back.
python3 "$bridge" serve "$work/ok.sock" "$(id -u)" -- cat 2>"$work/ok.log" &
pids+=("$!")
wait_socket "$work/ok.sock"
line='{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
reply="$(printf '%s\n' "$line" | timeout 5 python3 "$bridge" connect "$work/ok.sock")"
[[ "$reply" == "$line" ]] || { printf 'failed: round trip gave %q\n' "$reply" >&2; exit 1; }

# Refused: any other UID (here, one that is not ours) gets nothing and the
# command never starts.
python3 "$bridge" serve "$work/no.sock" 4242424 -- sh -c "touch '$work/started'; cat" 2>"$work/no.log" &
pids+=("$!")
wait_socket "$work/no.sock"
reply="$(printf '%s\n' "$line" | timeout 5 python3 "$bridge" connect "$work/no.sock" || true)"
[[ -z "$reply" && ! -e "$work/started" ]] || { printf 'failed: an unlisted uid reached the command\n' >&2; exit 1; }
grep -q "refused a connection from uid $(id -u)" "$work/no.log"

python3 "$bridge" bogus >/dev/null 2>&1 && { printf 'failed: bad usage accepted\n' >&2; exit 1; }
printf 'ok: the MCP bridge serves only the allowed uid, byte for byte\n'
