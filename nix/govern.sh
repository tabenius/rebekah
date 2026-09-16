#!/bin/bash
set -euo pipefail

workspace="${REBEKAH_WORKSPACE:-/workspace}"
state_dir="${REBEKAH_STATE_DIR:-/var/lib/rebekah}"
ledger="${WEFTMARK_LEDGER:-$state_dir/weftmark/ledger.jsonl}"
change_set_id="${REBEKAH_CHANGE_SET_ID:-}"
evidence_id="${REBEKAH_GOVERNANCE_EVIDENCE_ID:-ephor-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
result_dir="${REBEKAH_RUN_DIR:-/tmp}"
evidence_result="$result_dir/ephor-weftmark-evidence.json"
review_result="$result_dir/ephor-weftmark-review.json"

if [[ -z "$change_set_id" ]]; then
  printf 'rebekah-govern: REBEKAH_CHANGE_SET_ID is required\n' >&2
  exit 64
fi

mkdir -p "$result_dir"

set +e
weftmark --repo "$workspace" --ledger "$ledger" --json \
  evidence run "$change_set_id" \
  --id "$evidence_id" \
  --kind security \
  --cwd "$workspace" \
  --command rebekah-ephor evaluate "${1:-rebekah.change-set.evaluate}" \
  >"$evidence_result"
evidence_status="$?"

weftmark --repo "$workspace" --ledger "$ledger" --json \
  review create "$change_set_id" \
  --id "review-$evidence_id" \
  --author rebekah-ephor \
  --require security \
  >"$review_result"
review_status="$?"
set -e

jq -n \
  --slurpfile evidence "$evidence_result" \
  --slurpfile review "$review_result" \
  --argjson evidence_exit "$evidence_status" \
  --argjson review_exit "$review_status" \
  '{
    schema: "cc.ragbaz.rebekah.governance-review.v0",
    evidence: ($evidence[0] // null),
    review: ($review[0] // null),
    evidence_exit: $evidence_exit,
    review_exit: $review_exit,
    ready: ($evidence_exit == 0 and $review_exit == 0)
  }'

if [[ "$evidence_status" -ne 0 || "$review_status" -ne 0 ]]; then
  exit 1
fi
