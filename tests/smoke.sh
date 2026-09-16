#!/usr/bin/env bash
set -euo pipefail

image="${REBEKAH_IMAGE:-rebekah:bootstrap}"
runtime="${CONTAINER_RUNTIME:-docker}"
name="rebekah-smoke-$$"

cleanup() {
  "$runtime" rm -f "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

"$runtime" run --rm "$image" doctor

"$runtime" run -d \
  --name "$name" \
  --read-only \
  --tmpfs /run/rebekah:rw,noexec,nosuid,size=16m \
  -e REBEKAH_CHANGE_SET_ID=smoke-change-set \
  "$image" >/dev/null

for _ in $(seq 1 20); do
  if "$runtime" exec "$name" rebekah-doctor | grep -q 'correlation/change_set_id=smoke-change-set'; then
    printf 'ok: Rebekah bootstrap smoke test passed\n'
    exit 0
  fi
  sleep 0.25
done

printf 'failed: Rebekah bootstrap did not become ready\n' >&2
"$runtime" logs "$name" >&2 || true
exit 1
