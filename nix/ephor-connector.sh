#!/bin/bash
set -euo pipefail

ephor_url="${EPHOR_URL:-}"
api_style="${EPHOR_API_STYLE:-governance-http}"
output="${REBEKAH_GOVERNANCE_EVIDENCE:-/tmp/rebekah-governance-evidence.json}"
change_set_id="${REBEKAH_CHANGE_SET_ID:-}"
session_id="${REBEKAH_OPENCODE_SESSION_ID:-${change_set_id:-rebekah-unknown}}"
sylvae_run_id="${REBEKAH_SYLVAE_RUN_ID:-}"
agent_class="${EPHOR_AGENT_CLASS:-RebekahAgent}"
policy_revision="${EPHOR_POLICY_REVISION:-unknown}"
timeout="${EPHOR_TIMEOUT_SECONDS:-10}"
auth_args=()

if [[ -n "${EPHOR_AUTH_TOKEN:-}" ]]; then
  auth_args=(-H "Authorization: Bearer $EPHOR_AUTH_TOKEN")
fi

write_evidence() {
  local state="$1" entry_id="${2:-}" chain_hash="${3:-}" reason="${4:-}"
  mkdir -p "$(dirname "$output")"
  jq -n \
    --arg schema "cc.ragbaz.rebekah.governance-evidence.v0" \
    --arg kind "ephor:governance" \
    --arg subject "$change_set_id" \
    --arg state "$state" \
    --arg sylvae_run_id "$sylvae_run_id" \
    --arg ephor_entry_id "$entry_id" \
    --arg chain_hash "$chain_hash" \
    --arg policy_revision "$policy_revision" \
    --arg evaluated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg reason "$reason" \
    '{
      schema: $schema,
      kind: $kind,
      subject: $subject,
      state: $state,
      evaluated_at: $evaluated_at,
      policy_revision: $policy_revision
    }
    + (if $sylvae_run_id == "" then {} else {sylvae_run_id: $sylvae_run_id} end)
    + (if $ephor_entry_id == "" then {} else {ephor_entry_id: $ephor_entry_id} end)
    + (if $chain_hash == "" then {} else {chain_hash: $chain_hash} end)
    + (if $reason == "" then {} else {reason: $reason} end)' > "$output"
}

fail_closed() {
  local state="$1" reason="$2" entry_id="${3:-}" chain_hash="${4:-}"
  write_evidence "$state" "$entry_id" "$chain_hash" "$reason"
  printf 'rebekah-ephor: %s\n' "$reason" >&2
  return 1
}

post_json() {
  local url="$1" body="$2"
  curl --fail --silent --show-error \
    --connect-timeout "$timeout" --max-time "$timeout" \
    -H "Content-Type: application/json" "${auth_args[@]}" \
    --data "$body" "$url"
}

evaluate() {
  local action="${1:-rebekah.change-set.evaluate}"
  local result_summary="${2:-Governance evaluation completed}"

  if [[ -z "$ephor_url" ]]; then
    fail_closed unavailable "EPHOR_URL is not configured"
    return 1
  fi
  if [[ -z "$change_set_id" ]]; then
    fail_closed failed "REBEKAH_CHANGE_SET_ID is required"
    return 1
  fi

  local capture_body capture_response entry_id decision allowed
  capture_body="$(jq -cn \
    --arg session_id "$session_id" \
    --arg agent_class "$agent_class" \
    --arg action "$action" \
    --arg change_set_id "$change_set_id" \
    --arg sylvae_run_id "$sylvae_run_id" \
    '{
      session_id: $session_id,
      sessionId: $session_id,
      agent_class: $agent_class,
      agentClass: $agent_class,
      action: $action,
      arguments: [
        ("change_set_id=" + $change_set_id),
        ("sylvae_run_id=" + $sylvae_run_id)
      ],
      caller_stack: ["rebekah.ephor-connector"]
    }')"

  if ! capture_response="$(post_json "$ephor_url/capture" "$capture_body")"; then
    fail_closed unavailable "Ephor capture endpoint is unavailable"
    return 1
  fi
  if ! jq -e 'type == "object"' >/dev/null 2>&1 <<<"$capture_response"; then
    fail_closed failed "Ephor returned malformed capture JSON"
    return 1
  fi

  entry_id="$(jq -r '.entry_id // .entryId // empty' <<<"$capture_response")"
  decision="$(jq -r '.status // empty' <<<"$capture_response")"
  allowed="$(jq -r 'if has("allowed") then (.allowed|tostring) elif has("accepted") then (.accepted|tostring) else "" end' <<<"$capture_response")"

  if [[ -z "$entry_id" ]]; then
    fail_closed failed "Ephor capture response omitted entry_id"
    return 1
  fi
  if [[ "$decision" == "hold" ]]; then
    fail_closed failed "Ephor requires human approval" "$entry_id"
    return 1
  fi
  if [[ "$allowed" != "true" ]]; then
    fail_closed failed "Ephor did not approve the action" "$entry_id"
    return 1
  fi

  local finalize_url finalize_body finalize_response raw_hash chain_hash
  if [[ "$api_style" == "worker" ]]; then
    finalize_url="$ephor_url/capture/$entry_id/finalize"
    finalize_body="$(jq -cn --arg outcome success --arg resultSummary "$result_summary"       '{outcome: $outcome, resultSummary: $resultSummary}')"
  elif [[ "$api_style" == "governance-http" ]]; then
    finalize_url="$ephor_url/finalize"
    finalize_body="$(jq -cn --arg entry_id "$entry_id" --arg outcome success       --arg result_summary "$result_summary"       '{entry_id: $entry_id, outcome: $outcome, result_summary: $result_summary}')"
  else
    fail_closed failed "Unsupported EPHOR_API_STYLE: $api_style" "$entry_id"
    return 1
  fi

  if ! finalize_response="$(post_json "$finalize_url" "$finalize_body")"; then
    fail_closed unavailable "Ephor finalize endpoint is unavailable" "$entry_id"
    return 1
  fi
  raw_hash="$(jq -r '.hash // empty' <<<"$finalize_response" 2>/dev/null || true)"
  if [[ ! "$raw_hash" =~ ^(sha256:)?[0-9a-fA-F]{64}$ ]]; then
    fail_closed failed "Ephor finalize response omitted a valid chain hash" "$entry_id"
    return 1
  fi
  chain_hash="${raw_hash#sha256:}"
  chain_hash="sha256:${chain_hash,,}"

  write_evidence passed "$entry_id" "$chain_hash" ""
  cat "$output"
}

case "${1:-evaluate}" in
  evaluate)
    shift || true
    evaluate "$@"
    ;;
  evidence)
    [[ -f "$output" ]] || {
      printf 'rebekah-ephor: evidence file not found: %s\n' "$output" >&2
      exit 1
    }
    cat "$output"
    ;;
  *)
    printf 'usage: rebekah-ephor [evaluate [action [result-summary]]|evidence]\n' >&2
    exit 64
    ;;
esac
