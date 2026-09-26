#!/bin/bash
# rebekah-opencode-evidence: record a verification command as WeftMark evidence
# on a Change Set, attributed to the OpenCode session that produced the change.
#
# This is the OpenCode half of the runtime-links thread
# (docs/WEFTMARK-RUNTIME-LINKS-SCOPE.md), symmetric to rebekah-sylvae-evidence.
# It differs in one principled way: OpenCode owns its session id (the agent
# runtime mints it when a session starts), so the coordinator RECEIVES that id
# in REBEKAH_OPENCODE_SESSION_ID rather than preallocating one. It hands the
# same identity to WeftMark as `--producer-id opencode:session/<id>` and runs
# the operator's verification command THROUGH `weftmark evidence run`. WeftMark
# executes the command, binds its pass/fail to the current clean commit, and
# stores the producer id verbatim. WeftMark's Change Set detail then surfaces it
# as an evidence producer, and rebekah-gateway resolves it into the change set's
# `related.opencode` link.
#
# The bridge never reaches into OpenCode's HTTP surface or imports mutable
# session state: the session id is the only join key, and WeftMark stays the
# single evidence authority.
set -euo pipefail

workspace="${REBEKAH_WORKSPACE:-/workspace}"
state_dir="${REBEKAH_STATE_DIR:-/var/lib/rebekah}"
ledger="${WEFTMARK_LEDGER:-$state_dir/weftmark/ledger.jsonl}"
change_set_id="${REBEKAH_CHANGE_SET_ID:-}"
session_id="${REBEKAH_OPENCODE_SESSION_ID:-}"
# A verification command yields a pass/fail proof; "test" is the least-wrong
# default kind, matching rebekah-sylvae-evidence.
evidence_kind="${REBEKAH_OPENCODE_EVIDENCE_KIND:-test}"
result_dir="${REBEKAH_RUN_DIR:-/tmp}"

if [[ -z "$change_set_id" ]]; then
  printf 'rebekah-opencode-evidence: REBEKAH_CHANGE_SET_ID is required\n' >&2
  exit 64
fi
if [[ -z "$session_id" ]]; then
  printf 'rebekah-opencode-evidence: REBEKAH_OPENCODE_SESSION_ID is required (OpenCode mints it)\n' >&2
  exit 64
fi
# The session id is stored verbatim as the evidence producer id and prefixes the
# link the gateway resolves. Constrain it to a safe identifier charset so a
# malformed value can never smuggle shell/URL metacharacters into either — the
# same fail-closed discipline the Ephor connector applies to its entry_id.
if [[ ! "$session_id" =~ ^[A-Za-z0-9._-]+$ ]]; then
  printf 'rebekah-opencode-evidence: REBEKAH_OPENCODE_SESSION_ID must match [A-Za-z0-9._-]\n' >&2
  exit 64
fi

# The verification command comes from argv, or REBEKAH_OPENCODE_COMMAND as a
# fallback (shell-word split). Without one there is nothing to prove, so fail
# closed rather than record empty evidence.
cmd=("$@")
if [[ ${#cmd[@]} -eq 0 && -n "${REBEKAH_OPENCODE_COMMAND:-}" ]]; then
  read -r -a cmd <<< "$REBEKAH_OPENCODE_COMMAND"
fi
if [[ ${#cmd[@]} -eq 0 ]]; then
  printf 'rebekah-opencode-evidence: verification command required (argv or REBEKAH_OPENCODE_COMMAND)\n' >&2
  exit 64
fi

producer_id="opencode:session/$session_id"
evidence_id="${REBEKAH_OPENCODE_EVIDENCE_ID:-opencode-$session_id-$(date -u +%Y%m%dT%H%M%SZ)-$$}"

mkdir -p "$result_dir"
evidence_result="$result_dir/opencode-weftmark-evidence.json"

set +e
weftmark --repo "$workspace" --ledger "$ledger" --json \
  --producer-id "$producer_id" --producer-kind worker \
  evidence run "$change_set_id" \
  --id "$evidence_id" \
  --kind "$evidence_kind" \
  --cwd "$workspace" \
  --command "${cmd[@]}" \
  >"$evidence_result"
evidence_status="$?"
set -e

jq -n \
  --slurpfile evidence "$evidence_result" \
  --arg session_id "$session_id" \
  --arg producer_id "$producer_id" \
  --arg change_set_id "$change_set_id" \
  --argjson evidence_exit "$evidence_status" \
  '{
    schema: "cc.ragbaz.rebekah.opencode-evidence.v0",
    change_set_id: $change_set_id,
    session_id: $session_id,
    runtime_ref: $producer_id,
    evidence: ($evidence[0] // null),
    evidence_exit: $evidence_exit,
    recorded: ($evidence_exit == 0)
  }'

exit "$evidence_status"
