#!/usr/bin/env bash
# Unit test for the OpenCode->WeftMark evidence bridge (nix/opencode-evidence.sh).
# WeftMark is stubbed on PATH so the test asserts, without a container: the
# fail-closed guards, and that a valid run attributes the evidence to
# "opencode:session/<id>" and forwards the operator's verification command.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bridge="$repo_root/nix/opencode-evidence.sh"
fixture="$(mktemp -d)"
trap 'rm -rf -- "$fixture"' EXIT INT TERM

# Stub weftmark: record its argv, emit a JSON evidence record on stdout (the
# bridge slurps it), and exit with a controllable status.
bindir="$fixture/bin"
mkdir -p "$bindir"
cat > "$bindir/weftmark" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$WEFTMARK_ARGV_LOG"
printf '{"id":"stub-ev","state":"passed"}\n'
exit "${WEFTMARK_STUB_EXIT:-0}"
STUB
chmod +x "$bindir/weftmark"

argv_log="$fixture/argv"
run_bridge() {
  # Isolated env: stubbed weftmark first on PATH, results in the fixture.
  env PATH="$bindir:$PATH" \
    WEFTMARK_ARGV_LOG="$argv_log" \
    REBEKAH_WORKSPACE="$fixture/ws" \
    REBEKAH_STATE_DIR="$fixture/state" \
    REBEKAH_RUN_DIR="$fixture/run" \
    "$@" \
    bash "$bridge" "${bridge_args[@]}"
}

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

# --- fail-closed guards (exit 64), before weftmark is ever invoked ---------
rm -f "$argv_log"
bridge_args=()
set +e
run_bridge REBEKAH_OPENCODE_SESSION_ID=ses-1 REBEKAH_OPENCODE_COMMAND="true" \
  >/dev/null 2>&1
[[ "$?" -eq 64 ]] || fail "missing change set should exit 64"
set -e
[[ ! -e "$argv_log" ]] || fail "weftmark must not run without a change set"

bridge_args=()
set +e
run_bridge REBEKAH_CHANGE_SET_ID=cs-1 REBEKAH_OPENCODE_COMMAND="true" \
  >/dev/null 2>&1
[[ "$?" -eq 64 ]] || fail "missing session id should exit 64"
set -e

bridge_args=()
set +e
run_bridge REBEKAH_CHANGE_SET_ID=cs-1 REBEKAH_OPENCODE_SESSION_ID='bad id/;rm' \
  REBEKAH_OPENCODE_COMMAND="true" >/dev/null 2>&1
[[ "$?" -eq 64 ]] || fail "malformed session id should exit 64"
set -e

bridge_args=()
set +e
run_bridge REBEKAH_CHANGE_SET_ID=cs-1 REBEKAH_OPENCODE_SESSION_ID=ses-1 \
  >/dev/null 2>&1
[[ "$?" -eq 64 ]] || fail "missing verification command should exit 64"
set -e
[[ ! -e "$argv_log" ]] || fail "weftmark must not run without a command"

# --- happy path: attributes evidence to the session and runs the command ----
rm -f "$argv_log"
bridge_args=(pytest -q)
summary="$(run_bridge REBEKAH_CHANGE_SET_ID=cs-1 REBEKAH_OPENCODE_SESSION_ID=ses_abc123)"
grep -qx -- '--producer-id' "$argv_log" || fail "no --producer-id passed"
grep -qx -- 'opencode:session/ses_abc123' "$argv_log" \
  || fail "producer id not namespaced to the session"
grep -qx -- 'worker' "$argv_log" || fail "producer kind should be worker"
grep -qx -- 'cs-1' "$argv_log" || fail "change set not passed"
grep -qx -- 'pytest' "$argv_log" || fail "verification command not forwarded"
grep -qx -- '-q' "$argv_log" || fail "verification command args not forwarded"
printf '%s' "$summary" | jq -e \
  '.schema == "cc.ragbaz.rebekah.opencode-evidence.v0"
   and .runtime_ref == "opencode:session/ses_abc123"
   and .session_id == "ses_abc123"
   and .recorded == true' >/dev/null \
  || fail "summary JSON wrong on success"

# --- failure propagation: weftmark non-zero -> bridge non-zero, recorded:false
bridge_args=(false)
set +e
summary="$(run_bridge REBEKAH_CHANGE_SET_ID=cs-1 REBEKAH_OPENCODE_SESSION_ID=ses_abc123 \
  WEFTMARK_STUB_EXIT=2)"
rc="$?"
set -e
[[ "$rc" -eq 2 ]] || fail "bridge should propagate weftmark's exit status"
printf '%s' "$summary" | jq -e '.recorded == false' >/dev/null \
  || fail "recorded should be false when weftmark fails"

# --- REBEKAH_OPENCODE_COMMAND fallback when argv is empty --------------------
rm -f "$argv_log"
bridge_args=()
run_bridge REBEKAH_CHANGE_SET_ID=cs-1 REBEKAH_OPENCODE_SESSION_ID=ses_abc123 \
  REBEKAH_OPENCODE_COMMAND="make check" >/dev/null
grep -qx -- 'make' "$argv_log" || fail "env command fallback not word-split"
grep -qx -- 'check' "$argv_log" || fail "env command fallback arg missing"

printf 'ok      opencode-evidence bridge: guards + attribution + propagation\n'
