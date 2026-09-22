#!/bin/bash
set -euo pipefail

state_dir="${REBEKAH_STATE_DIR:-/var/lib/rebekah}"
run_dir="${REBEKAH_RUN_DIR:-/run/rebekah}"
workspace="${REBEKAH_WORKSPACE:-/workspace}"
opencode_password_file="$run_dir/opencode-password"
gateway_token_file="$run_dir/gateway-token"

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
ephor_enable="${REBEKAH_EPHOR_ENABLE:-1}"

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

  if command -v governance-http >/dev/null 2>&1; then
    printf 'ok      binary/governance-http\n'
  else
    printf 'failed  binary/governance-http missing\n' >&2
    failed=1
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
  if [[ "$ephor_enable" != 0 ]]; then
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

stop_services() {
  local pid
  trap - TERM INT
  for pid in "${pids[@]:-}"; do
    kill -TERM "$pid" 2>/dev/null || true
  done
  wait || true
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
  mkdir -p \
    "$run_dir" \
    "$state_dir/ollama" \
    "$state_dir/opencode" \
    "$state_dir/sylvae/runs" \
    "$state_dir/sylvae/skills" \
    "$state_dir/weftmark" \
    "$state_dir/ephor" \
    "$state_dir/gateway"
  # chmod before chown: while root still owns these dirs the mode change needs
  # no CAP_FOWNER, so the container can run without it. chown -R preserves the
  # mode.
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
  trap stop_services TERM INT

  # Secure the OpenCode HTTP surface. Unauthenticated, any co-tenant service
  # (or anything else reaching container loopback) can drive OpenCode, which has
  # workspace access and runs agents. Use the operator-provided password or mint
  # a per-boot random one, and persist it root-only (0600) so the health check
  # and an operator can read it while the service UIDs cannot.
  if [[ -z "${OPENCODE_SERVER_PASSWORD:-}" ]]; then
    OPENCODE_SERVER_PASSWORD="$(head -c 24 /dev/urandom | base64 | tr -d '\n=')"
  fi
  export OPENCODE_SERVER_PASSWORD
  ( umask 077; printf '%s' "$OPENCODE_SERVER_PASSWORD" >"$opencode_password_file" )

  OLLAMA_MODELS="$state_dir/ollama/models" \
    run_as 10001 "$state_dir/ollama" ollama serve

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

  run_as 10004 "$state_dir/weftmark" \
    weftmark-http \
      --repo "$workspace" \
      --ledger "$state_dir/weftmark/ledger.jsonl" \
      --host "$weftmark_host" --port "$weftmark_port"

  # KAGP's local governance bridge is supervised in-container by default. Set
  # REBEKAH_EPHOR_ENABLE=0 and EPHOR_URL to use an external deployment instead.
  if [[ "$ephor_enable" != 0 ]]; then
    run_as 10006 "$state_dir/ephor" \
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
    export REBEKAH_GATEWAY_TOKEN
    ( umask 077; printf '%s' "$REBEKAH_GATEWAY_TOKEN" >"$gateway_token_file" )
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
