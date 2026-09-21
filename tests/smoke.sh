#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${REBEKAH_IMAGE:-rebekah:latest}"
runtime="${CONTAINER_RUNTIME:-docker}"
name="rebekah-smoke-$BASHPID"
fixture="$(mktemp -d)"
# Scratch dir kept OUTSIDE the fixture: the fixture is bind-mounted as the
# container's /workspace, and WeftMark's evidence run requires a clean git
# worktree. Any host-side scratch file (e.g. the mock port file) written into
# the fixture would appear as an untracked file and make the governance
# evidence step fail with "requires a clean worktree".
scratch="$(mktemp -d)"
mock_pid=""

cleanup() {
  if [[ -n "$mock_pid" ]]; then
    kill "$mock_pid" >/dev/null 2>&1 || true
    wait "$mock_pid" 2>/dev/null || true
  fi
  "$runtime" rm -f "$name" >/dev/null 2>&1 || true
  rm -rf -- "$fixture" "$scratch"
}
trap cleanup EXIT INT TERM

git -C "$fixture" init -q
git -C "$fixture" config user.name "Rebekah smoke test"
git -C "$fixture" config user.email "rebekah-smoke@invalid"
printf '# Rebekah smoke fixture\n' > "$fixture/README.md"
git -C "$fixture" add README.md
git -C "$fixture" commit -qm "fixture"
chmod -R a+rwX "$fixture"

port_file="$scratch/ephor-port"
EPHOR_MOCK_HOST=0.0.0.0 python3 "$repo_root/tests/ephor-mock.py" pass "$port_file" &
mock_pid="$!"
for _ in $(seq 1 50); do
  [[ -s "$port_file" ]] && break
  sleep 0.1
done
[[ -s "$port_file" ]]
ephor_port="$(cat "$port_file")"

"$runtime" run --rm -v "$fixture:/workspace" "$image" doctor

# Least-privilege run: the supervisor needs only CHOWN (set up state dirs),
# SETUID/SETGID (launch each service as its own uid), KILL (forward termination
# to the cross-uid children), and DAC_OVERRIDE (so a root `docker exec` of
# rebekah-govern can write the weftmark-owned ledger). Everything else Docker
# grants root by default is dropped, with no-new-privileges. Running the smoke
# test this way guards the minimal set against regressions.
"$runtime" run -d \
  --name "$name" \
  --add-host host.docker.internal:host-gateway \
  --read-only \
  --cap-drop=ALL \
  --cap-add=CHOWN --cap-add=DAC_OVERRIDE \
  --cap-add=SETUID --cap-add=SETGID --cap-add=KILL \
  --security-opt=no-new-privileges \
  --tmpfs /run/rebekah:rw,noexec,nosuid,size=16m \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -v "$fixture:/workspace" \
  -e REBEKAH_CHANGE_SET_ID=smoke-change-set \
  "$image" >/dev/null

for _ in $(seq 1 90); do
  if "$runtime" exec "$name" rebekah-health >/dev/null 2>&1; then
    "$runtime" exec "$name" rebekah-health
    # Capture doctor output before grepping: piping it straight into `grep -q`
    # lets grep close the pipe on its first match, and under `set -o pipefail`
    # the SIGPIPE'd rebekah-doctor (exit 141) then fails the script racily.
    doctor_out="$("$runtime" exec "$name" rebekah-doctor)"
    grep -q 'correlation/change_set_id=smoke-change-set' <<<"$doctor_out"
    "$runtime" exec "$name" weftmark \
      --repo /workspace --ledger /var/lib/rebekah/weftmark/ledger.jsonl \
      changeset create smoke-change-set \
      --goal "Verify governed Rebekah integration" --scope "contract:governance"
    "$runtime" exec \
      -e EPHOR_URL="http://host.docker.internal:$ephor_port" \
      -e EPHOR_POLICY_REVISION=smoke-v0 \
      "$name" rebekah-govern |
      jq -e '.ready == true and .evidence.evidence.state == "passed"' >/dev/null
    # Containment invariant: a service UID must not be able to read another
    # service's state directory (0750, per-service group). opencode (10002)
    # must be denied the weftmark (10004) and ollama (10001) state dirs, while
    # weftmark can still read its own.
    if "$runtime" exec --user 10002:10002 "$name" ls /var/lib/rebekah/weftmark >/dev/null 2>&1 \
      || "$runtime" exec --user 10002:10002 "$name" ls /var/lib/rebekah/ollama >/dev/null 2>&1; then
      printf 'failed: cross-service state directory is readable (isolation broken)\n' >&2
      exit 1
    fi
    if ! "$runtime" exec --user 10004:10004 "$name" ls /var/lib/rebekah/weftmark >/dev/null 2>&1; then
      printf 'failed: service cannot read its own state directory\n' >&2
      exit 1
    fi

    # API gateway: the authenticated entry point. The per-boot token lives in a
    # root-only (0600) file; the gateway UID (10005) and other service UIDs must
    # not be able to read it. An unauthenticated request is refused; an
    # authenticated one is proxied to the real WeftMark backend.
    if "$runtime" exec --user 10005:10005 "$name" cat /run/rebekah/gateway-token >/dev/null 2>&1 \
      || "$runtime" exec --user 10002:10002 "$name" cat /run/rebekah/gateway-token >/dev/null 2>&1; then
      printf 'failed: gateway token file is readable by a service UID\n' >&2
      exit 1
    fi
    gw_token="$("$runtime" exec "$name" cat /run/rebekah/gateway-token)"
    gw_unauth="$("$runtime" exec "$name" \
      curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
      http://127.0.0.1:8080/weftmark/healthz)"
    if [[ "$gw_unauth" != 401 ]]; then
      printf 'failed: gateway allowed an unauthenticated request (got %s)\n' "$gw_unauth" >&2
      exit 1
    fi
    gw_auth="$("$runtime" exec "$name" \
      curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
      -H "Authorization: Bearer $gw_token" \
      http://127.0.0.1:8080/weftmark/healthz)"
    if [[ "$gw_auth" != 200 ]]; then
      printf 'failed: gateway did not proxy an authenticated request (got %s)\n' "$gw_auth" >&2
      exit 1
    fi
    # An unexposed backend is 404 even with a valid token (opencode is opt-in).
    gw_hidden="$("$runtime" exec "$name" \
      curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
      -H "Authorization: Bearer $gw_token" \
      http://127.0.0.1:8080/opencode/global/health)"
    if [[ "$gw_hidden" != 404 ]]; then
      printf 'failed: gateway exposed an opt-in backend (got %s)\n' "$gw_hidden" >&2
      exit 1
    fi

    printf 'ok: core services, governed WeftMark evidence, service isolation, and authenticated gateway are healthy\n'
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
