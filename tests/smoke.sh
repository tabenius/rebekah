#!/usr/bin/env bash
set -euo pipefail

image="${REBEKAH_IMAGE:-rebekah:latest}"
runtime="${CONTAINER_RUNTIME:-docker}"
name="rebekah-smoke-$BASHPID"
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

# Least-privilege run: the supervisor needs only CHOWN (set up state dirs),
# SETUID/SETGID (launch each service as its own uid), KILL (forward termination
# to the cross-uid children), and DAC_OVERRIDE (so a root `docker exec` of
# rebekah-govern can write the weftmark-owned ledger). Everything else Docker
# grants root by default is dropped, with no-new-privileges. Running the smoke
# test this way guards the minimal set against regressions.
"$runtime" run -d \
  --name "$name" \
  --read-only \
  --cap-drop=ALL \
  --cap-add=CHOWN --cap-add=DAC_OVERRIDE \
  --cap-add=SETUID --cap-add=SETGID --cap-add=KILL \
  --security-opt=no-new-privileges \
  --tmpfs /run/rebekah:rw,noexec,nosuid,size=16m \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m,mode=1777 \
  -v "$fixture:/workspace" \
  -e REBEKAH_CHANGE_SET_ID=smoke-change-set \
  -e REBEKAH_ADMIN_PASSWORD=smoke-admin-pw \
  "$image" >/dev/null

for _ in $(seq 1 90); do
  if "$runtime" exec "$name" rebekah-health >/dev/null 2>&1; then
    "$runtime" exec "$name" rebekah-health
    # Capture doctor output before grepping: piping it straight into `grep -q`
    # lets grep close the pipe on its first match, and under `set -o pipefail`
    # the SIGPIPE'd rebekah-doctor (exit 141) then fails the script racily.
    doctor_out="$("$runtime" exec "$name" rebekah-doctor)"
    grep -q 'correlation/change_set_id=smoke-change-set' <<<"$doctor_out"
    # Out-of-box model contract: OpenCode and Sylvae share the same local
    # Ollama model. The smoke image carries no weights, so assert configuration
    # here; v-BAZ separately tests cached/pulled model provisioning.
    "$runtime" exec "$name" jq -e \
      '.model == "ollama/qwen2.5:0.5b" and .small_model == "ollama/qwen2.5:0.5b" and
       .provider.ollama.options.baseURL == "http://127.0.0.1:11434/v1"' \
      /var/lib/rebekah/opencode/config/opencode/opencode.json >/dev/null
    "$runtime" exec "$name" weftmark \
      --repo /workspace --ledger /var/lib/rebekah/weftmark/ledger.jsonl \
      changeset create smoke-change-set \
      --goal "Verify governed Rebekah integration" --scope "contract:governance"
    "$runtime" exec \
      -e EPHOR_POLICY_REVISION=smoke-v0 \
      "$name" rebekah-govern |
      jq -e '.ready == true and .evidence.evidence.state == "passed"' >/dev/null
    # Containment invariant: a service UID must not be able to read another
    # service's state directory (0750, per-service group). opencode (10002)
    # must be denied the weftmark (10004), ollama (10001), and Ephor (10006)
    # state dirs, while
    # weftmark can still read its own.
    if "$runtime" exec --user 10002:10002 "$name" ls /var/lib/rebekah/weftmark >/dev/null 2>&1 \
      || "$runtime" exec --user 10002:10002 "$name" ls /var/lib/rebekah/ollama >/dev/null 2>&1 \
      || "$runtime" exec --user 10002:10002 "$name" ls /var/lib/rebekah/ephor >/dev/null 2>&1; then
      printf 'failed: cross-service state directory is readable (isolation broken)\n' >&2
      exit 1
    fi
    if ! "$runtime" exec --user 10004:10004 "$name" ls /var/lib/rebekah/weftmark >/dev/null 2>&1 \
      || ! "$runtime" exec --user 10006:10006 "$name" ls /var/lib/rebekah/ephor >/dev/null 2>&1; then
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
    # An unexposed backend is 404 even with a valid token (sylvae stays opt-in).
    gw_hidden="$("$runtime" exec "$name" \
      curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
      -H "Authorization: Bearer $gw_token" \
      http://127.0.0.1:8080/sylvae/)"
    if [[ "$gw_hidden" != 404 ]]; then
      printf 'failed: gateway exposed an opt-in backend (got %s)\n' "$gw_hidden" >&2
      exit 1
    fi
    # OpenCode is exposed by default now; reaching it through the gateway also
    # proves the gateway injects OpenCode's Basic auth (the client only holds the
    # gateway token). /global/health returns 200 once OpenCode is authenticated.
    gw_oc="$("$runtime" exec "$name" \
      curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
      -H "Authorization: Bearer $gw_token" \
      http://127.0.0.1:8080/opencode/global/health)"
    if [[ "$gw_oc" != 200 ]]; then
      printf 'failed: gateway did not proxy OpenCode with injected Basic auth (got %s)\n' "$gw_oc" >&2
      exit 1
    fi
    # Web console: served static (unauthenticated shell), and /api/info authed.
    gw_ui="$("$runtime" exec "$name" \
      curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8080/ui/)"
    if [[ "$gw_ui" != 200 ]]; then
      printf 'failed: gateway did not serve the web console (got %s)\n' "$gw_ui" >&2
      exit 1
    fi
    gw_info="$("$runtime" exec "$name" \
      curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8080/api/info)"
    if [[ "$gw_info" != 401 ]]; then
      printf 'failed: /api/info was not authenticated (got %s)\n' "$gw_info" >&2
      exit 1
    fi

    # SQLite login: the seeded admin (password injected above) can log in, and the
    # minted session token authenticates the API. Wrong password is refused.
    gw_badlogin="$("$runtime" exec "$name" \
      curl -s -o /dev/null -w '%{http_code}' --max-time 5 -H 'Content-Type: application/json' \
      -X POST --data '{"username":"admin","password":"wrong"}' http://127.0.0.1:8080/api/login)"
    if [[ "$gw_badlogin" != 401 ]]; then
      printf 'failed: gateway accepted a bad password (got %s)\n' "$gw_badlogin" >&2
      exit 1
    fi
    gw_sess="$("$runtime" exec "$name" \
      curl -s --max-time 5 -H 'Content-Type: application/json' \
      -X POST --data '{"username":"admin","password":"smoke-admin-pw"}' http://127.0.0.1:8080/api/login \
      | sed -n 's/.*"token": *"\([^"]*\)".*/\1/p')"
    if [[ -z "$gw_sess" ]]; then
      printf 'failed: SQLite login did not return a session token\n' >&2
      exit 1
    fi
    gw_sess_info="$("$runtime" exec "$name" \
      curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
      -H "Authorization: Bearer $gw_sess" http://127.0.0.1:8080/api/info)"
    if [[ "$gw_sess_info" != 200 ]]; then
      printf 'failed: SQLite session token did not authenticate (got %s)\n' "$gw_sess_info" >&2
      exit 1
    fi
    # The auth DB must be readable only by the gateway UID (10005), not others.
    if "$runtime" exec --user 10002:10002 "$name" cat /var/lib/rebekah/gateway/auth.db >/dev/null 2>&1; then
      printf 'failed: gateway auth DB is readable by another service UID\n' >&2
      exit 1
    fi

    # Secrets reach only the services that need them. OpenCode runs agents, so
    # its environment must hold no gateway, admin or oversight credential, even
    # though this run passes REBEKAH_ADMIN_PASSWORD with -e.
    oc_env="$("$runtime" exec --user 10002:10002 "$name" sh -c '
      for p in /proc/[0-9]*; do
        [ "${p#/proc/}" = "$$" ] && continue  # not this reader, whose script names it
        case "$(tr "\0" " " < "$p/cmdline" 2>/dev/null)" in
          *"opencode serve"*) tr "\0" "\n" < "$p/environ" ;;
        esac
      done')"
    if [[ -z "$oc_env" ]] || grep -qE '^(REBEKAH_ADMIN_PASSWORD|REBEKAH_GATEWAY_TOKEN|REBEKAH_DASH_PUSH_KEY|EPHOR_OVERSIGHT_TOKEN|REBEKAH_WEFTMARK_WRITE_TOKEN)=' <<<"$oc_env"; then
      printf 'failed: OpenCode can read a credential meant for another service\n' >&2
      exit 1
    fi
    # Human-in-the-loop: pending Ephor holds, for Dash, behind the gateway's auth.
    gw_oversight="$("$runtime" exec "$name" \
      curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8080/api/v1/oversight)"
    if [[ "$gw_oversight" != 401 ]]; then
      printf 'failed: /api/v1/oversight was not authenticated (got %s)\n' "$gw_oversight" >&2
      exit 1
    fi
    "$runtime" exec "$name" curl -sf --max-time 5 -H "Authorization: Bearer $gw_token" \
      http://127.0.0.1:8080/api/v1/oversight |
      jq -e '.schema == "rebekah.oversight.v1" and .stale == false and .decisions.oversight == true' >/dev/null

    # Restart on the same state: the entrypoint must set up state directories
    # the service UIDs already own, still without CAP_FOWNER.
    "$runtime" restart -t 30 "$name" >/dev/null
    for _ in $(seq 1 90); do
      if "$runtime" exec "$name" rebekah-health >/dev/null 2>&1; then
        restarted=1
        break
      fi
      if [[ "$("$runtime" inspect -f '{{.State.Running}}' "$name")" != true ]]; then
        break
      fi
      sleep 1
    done
    if [[ "${restarted:-}" != 1 ]]; then
      printf 'failed: Rebekah did not become healthy again after a restart\n' >&2
      "$runtime" logs --tail 40 "$name" >&2 || true
      exit 1
    fi

    # A requested stop is prompt and clean: the supervisor forwards TERM, waits
    # for its services, and exits 0 well inside the runtime's stop timeout.
    stop_started=$SECONDS
    "$runtime" stop -t 30 "$name" >/dev/null
    stop_seconds=$((SECONDS - stop_started))
    stop_code="$("$runtime" inspect -f '{{.State.ExitCode}}' "$name")"
    if ((stop_seconds > 15)) || [[ "$stop_code" != 0 ]]; then
      printf 'failed: stop took %ss with exit code %s (want <= 15s, 0)\n' \
        "$stop_seconds" "$stop_code" >&2
      "$runtime" logs --tail 20 "$name" >&2 || true
      exit 1
    fi

    printf 'ok: core services including Ephor, governed WeftMark evidence, service isolation, authenticated gateway, SQLite login, web console, oversight view, scoped secrets, restart, and clean stop are healthy\n'
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
