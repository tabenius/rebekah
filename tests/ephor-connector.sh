#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
connector="$repo_root/nix/ephor-connector.sh"
fixture="$(mktemp -d)"
server_pid=""

cleanup() {
  if [[ -n "$server_pid" ]]; then
    kill "$server_pid" >/dev/null 2>&1 || true
    wait "$server_pid" 2>/dev/null || true
  fi
  rm -rf -- "$fixture"
}
trap cleanup EXIT INT TERM

start_server() {
  local mode="$1"
  local port_file="$fixture/port"
  rm -f "$port_file"
  python3 "$repo_root/tests/ephor-mock.py" "$mode" "$port_file" &
  server_pid="$!"
  for _ in $(seq 1 50); do
    [[ -s "$port_file" ]] && break
    sleep 0.1
  done
  [[ -s "$port_file" ]]
  EPHOR_URL="http://127.0.0.1:$(cat "$port_file")"
  export EPHOR_URL
}

stop_server() {
  kill "$server_pid" >/dev/null 2>&1 || true
  wait "$server_pid" 2>/dev/null || true
  server_pid=""
}

run_case() {
  local mode="$1" expected_state="$2" expected_exit="$3" api_style="${4:-governance-http}"
  local evidence="$fixture/$mode.json"
  start_server "$mode"
  set +e
  REBEKAH_CHANGE_SET_ID=cs-test \
  REBEKAH_SYLVAE_RUN_ID=run-test \
  REBEKAH_GOVERNANCE_EVIDENCE="$evidence" \
  EPHOR_API_STYLE="$api_style" \
    bash "$connector" evaluate >/dev/null 2>&1
  local actual_exit="$?"
  set -e
  stop_server

  if [[ "$expected_exit" == "zero" ]]; then
    [[ "$actual_exit" -eq 0 ]]
  else
    [[ "$actual_exit" -ne 0 ]]
  fi
  jq -e --arg state "$expected_state" '.state == $state and .subject == "cs-test"' "$evidence" >/dev/null
}

run_case pass passed zero
run_case bad-entry-id failed nonzero
run_case deny failed nonzero
run_case hold failed nonzero
run_case malformed failed nonzero
run_case http-error unavailable nonzero
run_case pass passed zero worker
run_case invalid-hash failed nonzero governance-http
run_case invalid-hash failed nonzero worker

evidence="$fixture/missing-csid.json"
start_server pass
set +e
REBEKAH_GOVERNANCE_EVIDENCE="$evidence" \
  bash "$connector" evaluate >/dev/null 2>&1
status="$?"
set -e
stop_server
[[ "$status" -ne 0 ]]
jq -e '.state == "failed" and .reason == "REBEKAH_CHANGE_SET_ID is required"' "$evidence" >/dev/null

evidence="$fixture/unsupported-style.json"
start_server pass
set +e
REBEKAH_CHANGE_SET_ID=cs-test REBEKAH_GOVERNANCE_EVIDENCE="$evidence" \
  EPHOR_API_STYLE=magic bash "$connector" evaluate >/dev/null 2>&1
status="$?"
set -e
stop_server
[[ "$status" -ne 0 ]]
jq -e '.state == "failed" and .reason == "Unsupported EPHOR_API_STYLE: magic"' "$evidence" >/dev/null

evidence="$fixture/non-http.json"
set +e
REBEKAH_CHANGE_SET_ID=cs-test REBEKAH_GOVERNANCE_EVIDENCE="$evidence" \
  EPHOR_URL="file:///etc/passwd" bash "$connector" evaluate >/dev/null 2>&1
status="$?"
set -e
[[ "$status" -ne 0 ]]
jq -e '.state == "unavailable" and .reason == "EPHOR_URL must be an http(s) URL"' "$evidence" >/dev/null

unset EPHOR_URL
evidence="$fixture/unconfigured.json"
set +e
REBEKAH_CHANGE_SET_ID=cs-test REBEKAH_GOVERNANCE_EVIDENCE="$evidence" \
  bash "$connector" evaluate >/dev/null 2>&1
status="$?"
set -e
[[ "$status" -ne 0 ]]
jq -e '.state == "unavailable" and .reason == "Ephor is not enabled (set REBEKAH_EPHOR_ENABLE=1 or EPHOR_URL)"' "$evidence" >/dev/null

# Opting in to the bundled bridge (REBEKAH_EPHOR_ENABLE=1) needs no EPHOR_URL:
# the connector reaches EPHOR_HOST:EPHOR_PORT, where the supervisor runs it.
start_server pass
ephor_port="${EPHOR_URL##*:}"
unset EPHOR_URL
evidence="$fixture/opt-in.json"
REBEKAH_CHANGE_SET_ID=cs-test REBEKAH_GOVERNANCE_EVIDENCE="$evidence" \
  REBEKAH_EPHOR_ENABLE=1 EPHOR_HOST=127.0.0.1 EPHOR_PORT="$ephor_port" \
  bash "$connector" evaluate >/dev/null 2>&1
stop_server
jq -e '.state == "passed"' "$evidence" >/dev/null

printf 'ok: Ephor connector passes and fails closed\n'
