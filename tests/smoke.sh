#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${REBEKAH_IMAGE:-rebekah:latest}"
runtime="${CONTAINER_RUNTIME:-docker}"
name="rebekah-smoke-$"
fixture="$(mktemp -d)"
mock_pid=""

cleanup() {
  if [[ -n "$mock_pid" ]]; then
    kill "$mock_pid" >/dev/null 2>&1 || true
    wait "$mock_pid" 2>/dev/null || true
  fi
  "$runtime" rm -f "$name" >/dev/null 2>&1 || true
  rm -rf -- "$fixture"
}
trap cleanup EXIT INT TERM

git -C "$fixture" init -q
git -C "$fixture" config user.name "Rebekah smoke test"
git -C "$fixture" config user.email "rebekah-smoke@invalid"
printf '# Rebekah smoke fixture\n' > "$fixture/README.md"
git -C "$fixture" add README.md
git -C "$fixture" commit -qm "fixture"
chmod -R a+rwX "$fixture"

port_file="$fixture/ephor-port"
EPHOR_MOCK_HOST=0.0.0.0 python3 "$repo_root/tests/ephor-mock.py" pass "$port_file" &
mock_pid="$!"
for _ in $(seq 1 50); do
  [[ -s "$port_file" ]] && break
  sleep 0.1
done
[[ -s "$port_file" ]]
ephor_port="$(cat "$port_file")"

"$runtime" run --rm -v "$fixture:/workspace" "$image" doctor

"$runtime" run -d \
  --name "$name" \
  --add-host host.docker.internal:host-gateway \
  --read-only \
  --tmpfs /run/rebekah:rw,noexec,nosuid,size=16m \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -v "$fixture:/workspace" \
  -e REBEKAH_CHANGE_SET_ID=smoke-change-set \
  "$image" >/dev/null

for _ in $(seq 1 90); do
  if "$runtime" exec "$name" rebekah-health >/dev/null 2>&1; then
    "$runtime" exec "$name" rebekah-health
    "$runtime" exec "$name" rebekah-doctor |
      grep -q 'correlation/change_set_id=smoke-change-set'
    "$runtime" exec "$name" weftmark \
      --repo /workspace --ledger /var/lib/rebekah/weftmark/ledger.jsonl \
      changeset create smoke-change-set \
      --goal "Verify governed Rebekah integration" --scope "contract:governance"
    "$runtime" exec \
      -e EPHOR_URL="http://host.docker.internal:$ephor_port" \
      -e EPHOR_POLICY_REVISION=smoke-v0 \
      "$name" rebekah-govern |
      jq -e '.ready == true and .evidence.evidence.state == "passed"' >/dev/null
    printf 'ok: core services and governed WeftMark evidence are healthy\n'
    exit 0
  fi
  if [[ "$("$runtime" inspect -f '{{.State.Running}}' "$name")" != true ]]; then
    break
  fi
  sleep 1
done

printf 'failed: Rebekah core services did not become healthy\n' >&2
"$runtime" logs "$name" >&2 || true
exit 1
