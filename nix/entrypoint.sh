#!/bin/bash
set -euo pipefail

state_dir="${REBEKAH_STATE_DIR:-/var/lib/rebekah}"
run_dir="${REBEKAH_RUN_DIR:-/run/rebekah}"
workspace="${REBEKAH_WORKSPACE:-/workspace}"
opencode_password_file="$run_dir/opencode-password"
gateway_token_file="$run_dir/gateway-token"
weftmark_write_token_file="$run_dir/weftmark-write-token"

ollama_host="${OLLAMA_HOST:-127.0.0.1:11434}"
default_model="${REBEKAH_OLLAMA_MODEL:-qwen2.5:0.5b}"
opencode_host="${OPENCODE_HOST:-127.0.0.1}"
opencode_port="${OPENCODE_PORT:-4096}"
sylvae_host="${SYLVAE_HOST:-127.0.0.1}"
sylvae_port="${SYLVAE_PORT:-8971}"
weftmark_host="${WEFTMARK_HOST:-127.0.0.1}"
weftmark_port="${WEFTMARK_PORT:-8765}"
ephor_host="${EPHOR_HOST:-127.0.0.1}"
ephor_port="${EPHOR_PORT:-9800}"
# Ephor is opt-in (docs/HUMAN-INTERFACE-PLAN.md §1, §6.8): nothing in the
# baseline suite needs it, and its absence is never a failure. Set
# REBEKAH_EPHOR_ENABLE=1 to supervise the bundled bridge (the image-ephor
# build), or leave it 0 and set EPHOR_URL to use an external deployment.
ephor_enable="${REBEKAH_EPHOR_ENABLE:-0}"

# The gateway is the single authenticated entry point for Rebekah's API
# (invariant #2's "authenticated TLS proxy"). It binds loopback by default;
# operators expose it on the LAN by setting REBEKAH_GATEWAY_HOST + TLS.
gateway_enable="${REBEKAH_GATEWAY_ENABLE:-1}"
gateway_port="${REBEKAH_GATEWAY_PORT:-8080}"

# Treat the explicitly mounted workspace as trusted across service UIDs.
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=safe.directory
export GIT_CONFIG_VALUE_0="$workspace"

service_names=(ollama opencode sylvae weftmark)
pids=()

# Secrets the supervisor hands to named services only. Exported (from the
# image, `docker run -e`, a Podman secret or this script), a variable reaches
# every service it starts, OpenCode and so its agents included: an agent could
# read the admin password, push to Dash as this instance, or decide its own
# review. serve() un-exports them and passes each one, per command, only to the
# processes listed next to it.
private_env=(
  OPENCODE_SERVER_PASSWORD  # opencode, gateway
  REBEKAH_GATEWAY_TOKEN     # gateway
  REBEKAH_ADMIN_PASSWORD    # gateway
  REBEKAH_DASH_PUSH_KEY     # gateway
  EPHOR_OVERSIGHT_TOKEN     # ephor, gateway
  EPHOR_AUTH_TOKEN          # none: rebekah-ephor (docker exec) reads it itself
)

doctor() {
  local failed=0 service
  printf 'rebekah doctor\n'
  for service in "${service_names[@]}"; do
    if [[ -d "$state_dir/$service" ]]; then
      printf 'ok      state/%s\n' "$service"
    else
      printf 'failed  state/%s missing\n' "$service" >&2
      failed=1
    fi
    if command -v "$service" >/dev/null 2>&1; then
      printf 'ok      binary/%s\n' "$service"
    else
      printf 'failed  binary/%s missing\n' "$service" >&2
      failed=1
    fi
  done

  if command -v weftmark-http >/dev/null 2>&1; then
    printf 'ok      binary/weftmark-http\n'
  else
    printf 'failed  binary/weftmark-http missing\n' >&2
    failed=1
  fi

  local ephor
  ephor="$(ephor_state)"
  if [[ "$ephor" == enabled ]] && ! command -v governance-http >/dev/null 2>&1; then
    printf 'failed  ephor/enabled but this image has no governance-http (build .#image-ephor)\n' >&2
    failed=1
  else
    printf 'ok      ephor/%s\n' "$ephor"
  fi

  if command -v rebekah-ephor >/dev/null 2>&1; then
    printf 'ok      binary/rebekah-ephor\n'
  else
    printf 'failed  binary/rebekah-ephor missing\n' >&2
    failed=1
  fi

  if command -v rebekah-gateway >/dev/null 2>&1; then
    printf 'ok      binary/rebekah-gateway\n'
  else
    printf 'failed  binary/rebekah-gateway missing\n' >&2
    failed=1
  fi

  if git -C "$workspace" rev-parse --verify HEAD >/dev/null 2>&1; then
    printf 'ok      workspace/git-head\n'
  else
    printf 'failed  workspace requires a Git repository with a commit\n' >&2
    failed=1
  fi

  if [[ -n "${REBEKAH_CHANGE_SET_ID:-}" ]]; then
    printf 'ok      correlation/change_set_id=%s\n' "$REBEKAH_CHANGE_SET_ID"
  else
    printf 'pending correlation/change_set_id\n'
  fi
  if [[ -n "${REBEKAH_OPENCODE_SESSION_ID:-}" ]]; then
    printf 'ok      correlation/opencode_session_id=%s\n' "$REBEKAH_OPENCODE_SESSION_ID"
  else
    printf 'pending correlation/opencode_session_id\n'
  fi
  if [[ -n "${REBEKAH_SYLVAE_RUN_ID:-}" ]]; then
    printf 'ok      correlation/sylvae_run_id=%s\n' "$REBEKAH_SYLVAE_RUN_ID"
  else
    printf 'pending correlation/sylvae_run_id\n'
  fi
  return "$failed"
}

# Where Ephor stands in this container, for doctor and the gateway (never a
# health failure unless an operator enabled it):
#   absent    not in this image and not configured
#   disabled  bundled (image-ephor) but REBEKAH_EPHOR_ENABLE is not 1
#   external  EPHOR_URL points rebekah-ephor at a deployment elsewhere
#   enabled   the bundled bridge is supervised here
ephor_state() {
  if [[ "$ephor_enable" == 1 ]]; then
    printf 'enabled'
  elif [[ -n "${EPHOR_URL:-}" ]]; then
    printf 'external'
  elif command -v governance-http >/dev/null 2>&1; then
    printf 'disabled'
  else
    printf 'absent'
  fi
}

# Resolve the OpenCode server password: an operator-provided value, otherwise
# the per-boot random one written by serve(). Once a password is set OpenCode
# requires HTTP Basic auth (user "opencode") on every endpoint, including
# /global/health, so the health check must authenticate.
opencode_password() {
  if [[ -n "${OPENCODE_SERVER_PASSWORD:-}" ]]; then
    printf '%s' "$OPENCODE_SERVER_PASSWORD"
  elif [[ -r "$opencode_password_file" ]]; then
    cat "$opencode_password_file"
  fi
}

# Resolve the gateway's internal (token) API credential: an operator-provided
# value, otherwise the per-boot random one written by serve(). An operator reads
# it from the root-only file to hand to a LAN client / GUI / HITL guest.
gateway_token() {
  if [[ -n "${REBEKAH_GATEWAY_TOKEN:-}" ]]; then
    printf '%s' "$REBEKAH_GATEWAY_TOKEN"
  elif [[ -r "$gateway_token_file" ]]; then
    cat "$gateway_token_file"
  fi
}

check_url() {
  local name="$1" url="$2"
  shift 2
  if curl --fail --silent --show-error --max-time 2 "$@" "$url" >/dev/null; then
    printf 'ok      service/%s\n' "$name"
  else
    printf 'failed  service/%s url=%s\n' "$name" "$url" >&2
    return 1
  fi
}

health() {
  local failed=0
  check_url ollama "http://$ollama_host/api/version" || failed=1
  local oc_pw
  oc_pw="$(opencode_password)"
  local -a oc_auth=()
  if [[ -n "$oc_pw" ]]; then
    oc_auth=(--user "opencode:$oc_pw")
  fi
  check_url opencode "http://$opencode_host:$opencode_port/global/health" \
    "${oc_auth[@]}" || failed=1
  check_url sylvae "http://$sylvae_host:$sylvae_port/" || failed=1
  check_url weftmark "http://$weftmark_host:$weftmark_port/healthz" || failed=1
  if [[ "$ephor_enable" == 1 ]]; then
    check_url ephor "http://$ephor_host:$ephor_port/health" || failed=1
  fi
  if [[ "$gateway_enable" != 0 ]]; then
    # The gateway always listens on loopback too; check it there. When TLS is
    # configured the listener speaks HTTPS, so probe https and skip cert
    # verification (a LAN cert won't match 127.0.0.1) for this liveness check.
    local gw_scheme=http
    local -a gw_opts=()
    if [[ -n "${REBEKAH_GATEWAY_TLS_CERT:-}" ]]; then
      gw_scheme=https
      gw_opts=(--insecure)
    fi
    check_url gateway "$gw_scheme://127.0.0.1:$gateway_port/healthz" \
      "${gw_opts[@]}" || failed=1
  fi
  return "$failed"
}

# Services run with gid == uid (their own primary group), matching the
# per-service groups baked into /etc/group, so no service shares a group with
# another.
run_as() {
  local uid="$1" home="$2"
  shift 2
  HOME="$home" setpriv \
    --reuid "$uid" --regid "$uid" --clear-groups --no-new-privs -- "$@" &
  pids+=("$!")
}

# Stop every service: TERM, then up to REBEKAH_STOP_TIMEOUT seconds (default
# 20, under Docker's and Podman's usual stop timeouts) for them to exit, then
# KILL whatever is left, so a stop is bounded even if a service ignores TERM.
stop_services() {
  local pid alive deadline
  trap - TERM INT
  for pid in "${pids[@]:-}"; do
    kill -TERM "$pid" 2>/dev/null || true
  done
  deadline=$((SECONDS + ${REBEKAH_STOP_TIMEOUT:-20}))
  while ((SECONDS < deadline)); do
    alive=0
    for pid in "${pids[@]:-}"; do
      if kill -0 "$pid" 2>/dev/null; then
        alive=1
        break
      fi
    done
    ((alive)) || break
    sleep 0.2
  done
  for pid in "${pids[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      printf 'rebekah: pid %s ignored TERM, killing it\n' "$pid" >&2
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
  wait || true
}

# A requested stop (TERM from docker/podman stop, INT from Ctrl-C) is not a
# failure: stop the services and exit 0 at once, wherever serve() was, startup
# health loop included. Without the exit, the trap returned into the loop and
# the container ran on until the runtime's stop timeout killed it.
on_stop_signal() {
  printf 'rebekah: stop requested, stopping services\n'
  stop_services
  printf 'rebekah: stopped\n'
  exit 0
}

wait_until_healthy() {
  local _
  for _ in $(seq 1 "${REBEKAH_STARTUP_ATTEMPTS:-60}"); do
    if health >/dev/null 2>&1; then
      health
      return 0
    fi
    sleep 1
  done
  health
  return 1
}

seed_ledger() {
  local ledger="$state_dir/weftmark/ledger.jsonl"
  local change_set_id="${REBEKAH_CHANGE_SET_ID:-}"
  if [[ -z "${REBEKAH_SEED_LEDGER:-}" ]]; then
    return 0
  fi
  if [[ -z "$change_set_id" ]]; then
    printf 'rebekah: REBEKAH_SEED_LEDGER requires REBEKAH_CHANGE_SET_ID\n' >&2
    return 1
  fi
  if [[ -s "$ledger" ]]; then
    printf 'rebekah: seeded ledger already exists, skipping\n'
    return 0
  fi
  printf 'rebekah: seeding WeftMark ledger with Change Set %s\n' "$change_set_id"
  mkdir -p "$(dirname "$ledger")"
  chown 10004:10004 "$(dirname "$ledger")"
  if ! HOME="$state_dir/weftmark" setpriv \
    --reuid 10004 --regid 10004 --clear-groups --no-new-privs -- \
    weftmark --repo "$workspace" --ledger "$ledger" --json \
    changeset create "$change_set_id" \
    --goal "Seeded by Rebekah" --scope "contract:governance"; then
    printf 'rebekah: failed to seed Change Set %s\n' "$change_set_id" >&2
    return 1
  fi
  if ! HOME="$state_dir/weftmark" setpriv \
    --reuid 10004 --regid 10004 --clear-groups --no-new-privs -- \
    weftmark --repo "$workspace" --ledger "$ledger" --json \
    task plan import --source-label rebekah-bootstrap >/dev/null 2>&1; then
    printf 'rebekah: no source plans to import, continuing without plan cards\n' >&2
  fi
  printf 'rebekah: WeftMark ledger seeded\n'
  return 0
}

serve() {
  local name
  for name in "${private_env[@]}"; do
    # shellcheck disable=SC2163 # un-export the variable *named* by $name
    export -n "$name"
  done

  mkdir -p \
    "$run_dir" \
    "$state_dir/ollama" \
    "$state_dir/opencode" \
    "$state_dir/sylvae/runs" \
    "$state_dir/sylvae/skills" \
    "$state_dir/weftmark" \
    "$state_dir/ephor" \
    "$state_dir/gateway"
  # chmod while root owns these dirs, so the mode change needs no CAP_FOWNER
  # and the container can run without it. On a restart with a persistent state
  # volume they already belong to the service UIDs, so take them back first
  # (CAP_CHOWN; the services are not running yet). chown -R then hands them out
  # again and preserves the mode.
  chown 0:0 "$state_dir"/{ollama,opencode,sylvae,weftmark,ephor,gateway}
  chmod 0750 "$state_dir"/{ollama,opencode,sylvae,weftmark,ephor}
  # The gateway state holds the SQLite auth DB (password hashes + sessions):
  # tighter than the others (0700), readable only by the gateway UID.
  chmod 0700 "$state_dir/gateway"
  chown -R 10001:10001 "$state_dir/ollama"
  chown -R 10002:10002 "$state_dir/opencode"
  chown -R 10003:10003 "$state_dir/sylvae"
  chown -R 10004:10004 "$state_dir/weftmark"
  chown -R 10006:10006 "$state_dir/ephor"
  chown -R 10005:10005 "$state_dir/gateway"
  # Seed an offline-safe OpenCode configuration once. Defining Ollama here
  # does not disable online providers; credentials added later remain available.
  # An operator-created config always wins and is never overwritten.
  local opencode_config="$state_dir/opencode/config/opencode/opencode.json"
  if [[ ! -s "$opencode_config" ]]; then
    mkdir -p "$(dirname "$opencode_config")"
    jq -n --arg model "$default_model" '{
      model: ("ollama/" + $model),
      small_model: ("ollama/" + $model),
      provider: {
        ollama: {
          npm: "@ai-sdk/openai-compatible",
          name: "Ollama (local, managed by Rebekah)",
          options: {baseURL: "http://127.0.0.1:11434/v1"},
          models: {($model): {name: ($model + " (local)")}}
        }
      }
    }' > "$opencode_config"
    chmod 0600 "$opencode_config"
    chown -R 10002:10002 "$state_dir/opencode/config"
  fi

  doctor
  seed_ledger
  trap on_stop_signal TERM INT

  # Secure the OpenCode HTTP surface. Unauthenticated, any co-tenant service
  # (or anything else reaching container loopback) can drive OpenCode, which has
  # workspace access and runs agents. Use the operator-provided password or mint
  # a per-boot random one, and persist it root-only (0600) so the health check
  # and an operator can read it while the service UIDs cannot.
  if [[ -z "${OPENCODE_SERVER_PASSWORD:-}" ]]; then
    OPENCODE_SERVER_PASSWORD="$(head -c 24 /dev/urandom | base64 | tr -d '\n=')"
  fi
  ( umask 077; printf '%s' "$OPENCODE_SERVER_PASSWORD" >"$opencode_password_file" )

  OLLAMA_MODELS="$state_dir/ollama/models" \
    run_as 10001 "$state_dir/ollama" ollama serve

  OPENCODE_SERVER_PASSWORD="$OPENCODE_SERVER_PASSWORD" \
    XDG_DATA_HOME="$state_dir/opencode/data" \
    XDG_CONFIG_HOME="$state_dir/opencode/config" \
    XDG_CACHE_HOME="$state_dir/opencode/cache" \
    run_as 10002 "$state_dir/opencode" \
      opencode serve --hostname "$opencode_host" --port "$opencode_port"

  SYLVAE_OLLAMA_MODEL="$default_model" \
    OLLAMA_API_BASE="http://127.0.0.1:11434" \
    run_as 10003 "$state_dir/sylvae" \
    sylvae review \
      --runs-dir "$state_dir/sylvae/runs" \
      --skills-dir "$state_dir/sylvae/skills" \
      --host "$sylvae_host" --port "$sylvae_port"

  # Human-in-the-loop decisions from RAGBAZ Dash (nix/gateway.py) need two
  # per-boot credentials: one for Ephor's reviewer routes and one for
  # WeftMark's review control route. Each goes only to the gateway and to the
  # one backend that checks it, as a per-command environment variable (not
  # exported), so no other service UID, OpenCode's agents included, inherits
  # it: the actions being reviewed cannot decide their own review.
  local oversight_token="" weftmark_write_token="" wm_help ephor
  local -a weftmark_control=()
  ephor="$(ephor_state)"
  # No Ephor here, no reviewer credential: the gateway then reports oversight
  # as not enabled instead of offering decisions nothing could apply.
  if [[ "$ephor" == enabled ]]; then
    oversight_token="${EPHOR_OVERSIGHT_TOKEN:-$(head -c 24 /dev/urandom | base64 | tr -d '\n=')}"
  fi
  # Only a WeftMark with the review capability accepts it; an older one would
  # refuse to start, so enable it only when it is there.
  wm_help="$(weftmark-http --help 2>/dev/null || true)"
  if [[ "$wm_help" =~ [{,]review[,}] ]]; then
    weftmark_write_token="$(head -c 24 /dev/urandom | base64 | tr -d '\n=')"
    # A fresh root-owned file each boot, chmod before chown: once WeftMark owns
    # it, changing its mode would need CAP_FOWNER, which the supervisor lacks
    # (a restart finds the previous boot's file still owned by 10004).
    rm -f "$weftmark_write_token_file"
    ( umask 077; printf '%s' "$weftmark_write_token" >"$weftmark_write_token_file" )
    chmod 0400 "$weftmark_write_token_file"
    chown 10004:10004 "$weftmark_write_token_file"
    weftmark_control=(--write-token-file "$weftmark_write_token_file" --write-capability review)
  fi

  run_as 10004 "$state_dir/weftmark" \
    weftmark-http \
      --repo "$workspace" \
      --ledger "$state_dir/weftmark/ledger.jsonl" \
      --host "$weftmark_host" --port "$weftmark_port" \
      "${weftmark_control[@]}"

  # Ephor's local governance bridge (KAGP protocol) runs only when an operator
  # opted in with REBEKAH_EPHOR_ENABLE=1; doctor has already refused an image
  # that does not bundle it.
  if [[ "$ephor" == enabled ]]; then
    EPHOR_OVERSIGHT_TOKEN="$oversight_token" run_as 10006 "$state_dir/ephor" \
      governance-http \
        --listen "$ephor_host:$ephor_port" \
        --node-id "${EPHOR_NODE_ID:-rebekah}"
  fi

  # The authenticated API gateway. It fronts the loopback backends with a single
  # authenticated entry (token and/or OIDC) and is the only service meant to face
  # the LAN / a GUI / a HITL guest. If no token is supplied, mint a per-boot one
  # and persist it root-only (0600) so an operator can read it while the gateway
  # UID (10005) and the other service UIDs cannot. The token is passed to the
  # gateway via its environment, never on argv.
  if [[ "$gateway_enable" != 0 ]]; then
    if [[ -z "${REBEKAH_GATEWAY_TOKEN:-}" ]]; then
      REBEKAH_GATEWAY_TOKEN="$(head -c 24 /dev/urandom | base64 | tr -d '\n=')"
    fi
    ( umask 077; printf '%s' "$REBEKAH_GATEWAY_TOKEN" >"$gateway_token_file" )
    OPENCODE_SERVER_PASSWORD="$OPENCODE_SERVER_PASSWORD" \
      REBEKAH_GATEWAY_TOKEN="$REBEKAH_GATEWAY_TOKEN" \
      REBEKAH_ADMIN_PASSWORD="${REBEKAH_ADMIN_PASSWORD:-}" \
      REBEKAH_DASH_PUSH_KEY="${REBEKAH_DASH_PUSH_KEY:-}" \
      EPHOR_OVERSIGHT_TOKEN="$oversight_token" \
      REBEKAH_EPHOR_STATE="$ephor" \
      REBEKAH_WEFTMARK_WRITE_TOKEN="$weftmark_write_token" \
      run_as 10005 "$run_dir" rebekah-gateway
  fi

  if ! wait_until_healthy; then
    printf 'rebekah: services failed to become healthy\n' >&2
    stop_services
    return 1
  fi

  printf 'rebekah: all core services healthy\n'
  if wait -n "${pids[@]}"; then
    printf 'rebekah: a core service exited unexpectedly\n' >&2
  else
    printf 'rebekah: a core service failed\n' >&2
  fi
  stop_services
  return 1
}

default_command=serve
case "${0##*/}" in
  rebekah-doctor) default_command=doctor ;;
  rebekah-health) default_command=health ;;
esac

case "${1:-$default_command}" in
  doctor) doctor ;;
  health) health ;;
  serve) serve ;;
  *) exec "$@" ;;
esac
