#!/usr/bin/env bash
set -euo pipefail

state_dir="${REBEKAH_STATE_DIR:-/var/lib/rebekah}"
run_dir="${REBEKAH_RUN_DIR:-/run/rebekah}"
workspace="${REBEKAH_WORKSPACE:-/workspace}"

ollama_host="${OLLAMA_HOST:-127.0.0.1:11434}"
opencode_host="${OPENCODE_HOST:-127.0.0.1}"
opencode_port="${OPENCODE_PORT:-4096}"
sylvae_host="${SYLVAE_HOST:-127.0.0.1}"
sylvae_port="${SYLVAE_PORT:-8971}"
weftmark_host="${WEFTMARK_HOST:-127.0.0.1}"
weftmark_port="${WEFTMARK_PORT:-8765}"

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
  return "$failed"
}

check_url() {
  local name="$1" url="$2"
  if curl --fail --silent --show-error --max-time 2 "$url" >/dev/null; then
    printf 'ok      service/%s\n' "$name"
  else
    printf 'failed  service/%s url=%s\n' "$name" "$url" >&2
    return 1
  fi
}

health() {
  local failed=0
  check_url ollama "http://$ollama_host/api/version" || failed=1
  check_url opencode "http://$opencode_host:$opencode_port/global/health" || failed=1
  check_url sylvae "http://$sylvae_host:$sylvae_port/" || failed=1
  check_url weftmark "http://$weftmark_host:$weftmark_port/healthz" || failed=1
  return "$failed"
}

run_as() {
  local uid="$1" home="$2"
  shift 2
  HOME="$home" setpriv \
    --reuid "$uid" --regid 10000 --clear-groups --no-new-privs -- "$@" &
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

serve() {
  mkdir -p \
    "$run_dir" \
    "$state_dir/ollama" \
    "$state_dir/opencode" \
    "$state_dir/sylvae/runs" \
    "$state_dir/sylvae/skills" \
    "$state_dir/weftmark"
  chown -R 10001:10000 "$state_dir/ollama"
  chown -R 10002:10000 "$state_dir/opencode"
  chown -R 10003:10000 "$state_dir/sylvae"
  chown -R 10004:10000 "$state_dir/weftmark"
  chmod 0750 "$state_dir"/{ollama,opencode,sylvae,weftmark}
  doctor
  trap stop_services TERM INT

  OLLAMA_MODELS="$state_dir/ollama/models" \
    run_as 10001 "$state_dir/ollama" ollama serve

  XDG_DATA_HOME="$state_dir/opencode/data" \
    XDG_CONFIG_HOME="$state_dir/opencode/config" \
    XDG_CACHE_HOME="$state_dir/opencode/cache" \
    run_as 10002 "$state_dir/opencode" \
      opencode serve --hostname "$opencode_host" --port "$opencode_port"

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
