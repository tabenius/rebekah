#!/usr/bin/env bash
set -euo pipefail

image="${REBEKAH_IMAGE:-rebekah:latest}"
runtime="${CONTAINER_RUNTIME:-docker}"
name="rebekah-smoke-$$"
fixture="$(mktemp -d)"

cleanup() {
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

"$runtime" run --rm -v "$fixture:/workspace" "$image" doctor

"$runtime" run -d \
  --name "$name" \
  --read-only \
  --tmpfs /run/rebekah:rw,noexec,nosuid,size=16m \
  -v "$fixture:/workspace" \
  -e REBEKAH_CHANGE_SET_ID=smoke-change-set \
  "$image" >/dev/null

for _ in $(seq 1 90); do
  if "$runtime" exec "$name" rebekah-health >/dev/null 2>&1; then
    "$runtime" exec "$name" rebekah-health
    "$runtime" exec "$name" rebekah-doctor |
      grep -q 'correlation/change_set_id=smoke-change-set'
    printf 'ok: all Rebekah core services are healthy\n'
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
