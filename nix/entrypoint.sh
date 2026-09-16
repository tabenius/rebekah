#!/usr/bin/env bash
set -euo pipefail

state_dir="${REBEKAH_STATE_DIR:-/var/lib/rebekah}"
run_dir="${REBEKAH_RUN_DIR:-/run/rebekah}"
service_names=(ollama opencode sylvae weftmark ephor)

doctor() {
  local failed=0 service

  printf 'rebekah bootstrap doctor\n'
  for service in "${service_names[@]}"; do
    if [[ -d "$state_dir/$service" ]]; then
      printf 'ok      state/%s\n' "$service"
    else
      printf 'failed  state/%s missing\n' "$service" >&2
      failed=1
    fi
  done

  for service in ollama opencode sylvae weftmark; do
    if command -v "$service" >/dev/null 2>&1; then
      printf 'present binary/%s\n' "$service"
    else
      printf 'pending binary/%s\n' "$service"
    fi
  done

  if [[ -n "${REBEKAH_CHANGE_SET_ID:-}" ]]; then
    printf 'ok      correlation/change_set_id=%s\n' "$REBEKAH_CHANGE_SET_ID"
  else
    printf 'pending correlation/change_set_id\n'
  fi

  return "$failed"
}

serve() {
  mkdir -p "$run_dir"
  doctor
  printf 'rebekah bootstrap image ready; full service supervision is the next slice\n'
  exec sleep infinity
}

default_command=serve
if [[ "${0##*/}" == "rebekah-doctor" ]]; then
  default_command=doctor
fi

case "${1:-$default_command}" in
  doctor)
    doctor
    ;;
  serve)
    serve
    ;;
  *)
    exec "$@"
    ;;
esac
