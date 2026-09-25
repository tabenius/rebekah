#!/bin/bash
# rebekah-sylvae-evidence: record a Sylvae skill run as WeftMark evidence on a
# Change Set, attributed to the run's own identity.
#
# The bridge (docs/WEFTMARK-RUNTIME-LINKS-SCOPE.md) closes without importing
# mutable results, guessing joins, or making Rebekah a second evidence
# authority: it preallocates a Sylvae run id, hands the SAME identity to
# WeftMark as `--producer-id sylvae:run/<id>`, and runs the real
# `sylvae run ... --run-id <id>` command THROUGH `weftmark evidence run`. WeftMark
# executes the command, binds its pass/fail to the current commit, and stores the
# producer id verbatim. WeftMark's Change Set detail then surfaces it as an
# evidence producer, and rebekah-gateway resolves it into the change set's
# `related.sylvae` link.
set -euo pipefail

workspace="${REBEKAH_WORKSPACE:-/workspace}"
state_dir="${REBEKAH_STATE_DIR:-/var/lib/rebekah}"
ledger="${WEFTMARK_LEDGER:-$state_dir/weftmark/ledger.jsonl}"
change_set_id="${REBEKAH_CHANGE_SET_ID:-}"
skill_path="${1:-${REBEKAH_SYLVAE_SKILL:-}}"
skill_input="${2:-${REBEKAH_SYLVAE_INPUT:-}}"
backend="${REBEKAH_SYLVAE_BACKEND:-auto}"
model="${REBEKAH_SYLVAE_MODEL:-}"
# A skill run yields a pass/fail proof; "test" is the least-wrong default kind.
evidence_kind="${REBEKAH_SYLVAE_EVIDENCE_KIND:-test}"
result_dir="${REBEKAH_RUN_DIR:-/tmp}"

if [[ -z "$change_set_id" ]]; then
  printf 'rebekah-sylvae-evidence: REBEKAH_CHANGE_SET_ID is required\n' >&2
  exit 64
fi
if [[ -z "$skill_path" ]]; then
  printf 'rebekah-sylvae-evidence: skill path required (argv[1] or REBEKAH_SYLVAE_SKILL)\n' >&2
  exit 64
fi
if [[ -z "$skill_input" ]]; then
  printf 'rebekah-sylvae-evidence: skill input required (argv[2] or REBEKAH_SYLVAE_INPUT)\n' >&2
  exit 64
fi

# Preallocate the run identity so the SAME id names the run in Sylvae and
# attributes the evidence in WeftMark. A v4 UUID hex is what `sylvae --run-id`
# accepts; the kernel RNG gives one without a Python dependency.
if [[ -r /proc/sys/kernel/random/uuid ]]; then
  run_id="$(tr -d '-' < /proc/sys/kernel/random/uuid | tr -d '\n')"
else
  run_id="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
fi
runtime_ref="sylvae:run/$run_id"
evidence_id="${REBEKAH_SYLVAE_EVIDENCE_ID:-sylvae-$run_id}"

mkdir -p "$result_dir"
evidence_result="$result_dir/sylvae-weftmark-evidence.json"

# The model flag is optional: only pass it when set, so Sylvae uses the
# backend's default otherwise.
model_args=()
if [[ -n "$model" ]]; then
  model_args=(--model "$model")
fi

set +e
weftmark --repo "$workspace" --ledger "$ledger" --json \
  --producer-id "$runtime_ref" --producer-kind worker \
  evidence run "$change_set_id" \
  --id "$evidence_id" \
  --kind "$evidence_kind" \
  --cwd "$workspace" \
  --command sylvae run "$skill_path" \
    --backend "$backend" --input "$skill_input" --run-id "$run_id" "${model_args[@]}" \
  >"$evidence_result"
evidence_status="$?"
set -e

jq -n \
  --slurpfile evidence "$evidence_result" \
  --arg run_id "$run_id" \
  --arg runtime_ref "$runtime_ref" \
  --arg change_set_id "$change_set_id" \
  --argjson evidence_exit "$evidence_status" \
  '{
    schema: "cc.ragbaz.rebekah.sylvae-evidence.v0",
    change_set_id: $change_set_id,
    run_id: $run_id,
    runtime_ref: $runtime_ref,
    evidence: ($evidence[0] // null),
    evidence_exit: $evidence_exit,
    recorded: ($evidence_exit == 0)
  }'

exit "$evidence_status"
