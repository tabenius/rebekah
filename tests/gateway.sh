#!/usr/bin/env bash
# rebekah-gateway auth/proxy unit test (no Docker, no Nix).
#
# The token path, routing, fail-closed guards and error mapping use only the
# Python standard library, so they run anywhere python3 does. The OIDC path
# needs PyJWT + cryptography (present in the image's gateway Python); when they
# are missing those cases are skipped, not failed.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
gateway="$repo_root/nix/gateway.py"
python="${PYTHON:-python3}"
work="$(mktemp -d)"
pids=()

cleanup() {
  for pid in "${pids[@]:-}"; do kill "$pid" >/dev/null 2>&1 || true; done
  rm -rf -- "$work"
}
trap cleanup EXIT INT TERM

pass() { printf '  \033[32mok\033[0m   %s\n' "$1"; }
skip() { printf '  \033[33mskip\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; exit 1; }

# A free TCP port (bind :0, read it back, release it).
free_port() {
  "$python" - <<'PY'
import socket
s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()
PY
}

# Mock upstream: echoes the path it received so we can assert prefix stripping.
start_upstream() {
  local port="$1"
  "$python" - "$port" <<'PY' &
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
port = int(sys.argv[1])
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def _do(self):
        body = ("UPSTREAM path=%s" % self.path).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    do_GET = _do
    do_POST = _do
    def log_message(self, *a): pass
ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
PY
  pids+=("$!")
}

wait_url() {
  local url="$1" _
  for _ in $(seq 1 50); do
    curl -fsS --max-time 1 "$url" >/dev/null 2>&1 && return 0
    sleep 0.1
  done
  return 1
}

code() { curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$@"; }
body() { curl -s --max-time 5 "$@"; }

# === 1. fail-closed guards (no server should come up) ========================
printf '\n== fail-closed guards ==\n'

# Non-loopback bind without TLS must refuse to start.
if REBEKAH_GATEWAY_HOST=0.0.0.0 REBEKAH_GATEWAY_TOKEN=x \
   "$python" "$gateway" >/dev/null 2>"$work/e1"; then
  fail "gateway started on 0.0.0.0 without TLS (should refuse)"
fi
grep -q 'without TLS' "$work/e1" && pass "refuses non-loopback bind without TLS" \
  || fail "wrong error for non-loopback/no-TLS: $(cat "$work/e1")"

# No auth configured at all must refuse to start.
if REBEKAH_GATEWAY_EXPOSE=weftmark "$python" "$gateway" >/dev/null 2>"$work/e2"; then
  fail "gateway started with no auth configured (should refuse)"
fi
grep -q 'no auth configured' "$work/e2" && pass "refuses to run as an open proxy" \
  || fail "wrong error for no-auth: $(cat "$work/e2")"

# === 2. token auth + routing (stdlib only) ==================================
printf '\n== token auth + routing ==\n'
up_port="$(free_port)"; gw_port="$(free_port)"
start_upstream "$up_port"
wait_url "http://127.0.0.1:$up_port/" || fail "mock upstream did not start"

token="s3cr3t-$(date +%s)"
REBEKAH_GATEWAY_HOST=127.0.0.1 REBEKAH_GATEWAY_PORT="$gw_port" \
  REBEKAH_GATEWAY_TOKEN="$token" REBEKAH_GATEWAY_EXPOSE="weftmark" \
  REBEKAH_GATEWAY_UI_DIR="$repo_root/nix/ui" \
  WEFTMARK_HOST=127.0.0.1 WEFTMARK_PORT="$up_port" \
  "$python" "$gateway" >"$work/gw.log" 2>&1 &
pids+=("$!")
wait_url "http://127.0.0.1:$gw_port/healthz" || fail "gateway did not start (token mode)"
pass "starts and serves /healthz unauthenticated"

[ "$(code "http://127.0.0.1:$gw_port/weftmark/healthz")" = 401 ] \
  && pass "no credential -> 401" || fail "missing-auth not 401"
[ "$(code -H 'Authorization: Bearer wrong' "http://127.0.0.1:$gw_port/weftmark/x")" = 401 ] \
  && pass "wrong token -> 401" || fail "wrong-token not 401"

good="$(body -H "Authorization: Bearer $token" "http://127.0.0.1:$gw_port/weftmark/healthz")"
[ "$good" = "UPSTREAM path=/healthz" ] \
  && pass "valid token -> 200 and prefix stripped ($good)" \
  || fail "prefix strip / proxy wrong: '$good'"

# Root of a backend maps to '/'.
root="$(body -H "Authorization: Bearer $token" "http://127.0.0.1:$gw_port/weftmark")"
[ "$root" = "UPSTREAM path=/" ] && pass "/<backend> maps to '/'" || fail "root map wrong: '$root'"

# Query string preserved.
q="$(body -H "Authorization: Bearer $token" "http://127.0.0.1:$gw_port/weftmark/a?b=c")"
[ "$q" = "UPSTREAM path=/a?b=c" ] && pass "query string preserved" || fail "query lost: '$q'"

# A backend that is NOT exposed is 404 even with a valid token (no info leak).
[ "$(code -H "Authorization: Bearer $token" "http://127.0.0.1:$gw_port/opencode/x")" = 404 ] \
  && pass "unexposed backend -> 404" || fail "unexposed backend not 404"
[ "$(code -H "Authorization: Bearer $token" "http://127.0.0.1:$gw_port/nope")" = 404 ] \
  && pass "unknown route -> 404" || fail "unknown route not 404"

# Oversized body is rejected before proxying (separate instance for the cap).
big="$(head -c 2048 /dev/zero | tr '\0' 'x')"
gw2_port="$(free_port)"
REBEKAH_GATEWAY_PORT="$gw2_port" REBEKAH_GATEWAY_TOKEN="$token" \
  REBEKAH_GATEWAY_EXPOSE="weftmark" REBEKAH_GATEWAY_MAX_BODY=1024 \
  WEFTMARK_HOST=127.0.0.1 WEFTMARK_PORT="$up_port" \
  "$python" "$gateway" >"$work/gw2.log" 2>&1 &
pids+=("$!")
wait_url "http://127.0.0.1:$gw2_port/healthz" || fail "size-cap gateway did not start"
[ "$(code -H "Authorization: Bearer $token" -X POST --data "$big" \
     "http://127.0.0.1:$gw2_port/weftmark/x")" = 413 ] \
  && pass "body over cap -> 413" || fail "oversized body not 413"

# === 2b. web console (static UI) + /api/info ================================
printf '\n== web console + /api/info ==\n'
base="http://127.0.0.1:$gw_port"
[ "$(code "$base/")" = 200 ] && pass "/ serves the console (200)" || fail "/ not 200"
ui_body="$(body "$base/ui/")"
printf '%s' "$ui_body" | grep -q "Rebekah Console" \
  && pass "/ui/ serves the console HTML" || fail "/ui/ missing console markup"
printf '%s' "$ui_body" | grep -q 'role="tabpanel"' \
  && pass "console exposes accessible tab panels" || fail "console missing tabpanel semantics"
printf '%s' "$ui_body" | grep -q '>Advanced<' \
  && pass "raw API tools are under Advanced" || fail "console missing Advanced navigation"
printf '%s' "$ui_body" | grep -q 'optional governance integration' \
  && pass "Ephor is presented as optional" || fail "console does not mark Ephor optional"
curl -sI --max-time 5 "$base/ui/" | grep -qi 'content-type: text/html' \
  && pass "console served as text/html" || fail "console content-type wrong"

# /api/info is authenticated and reports the exposed backends.
[ "$(code "$base/api/info")" = 401 ] && pass "/api/info needs auth -> 401" || fail "/api/info not 401"
info_body="$(body -H "Authorization: Bearer $token" "$base/api/info")"
printf '%s' "$info_body" | grep -q '"weftmark"' \
  && pass "/api/info lists exposed backends" || fail "/api/info missing expose: $info_body"

# Path traversal out of the UI dir must not serve host files.
trav="$(body --path-as-is "$base/ui/../../../../../../etc/passwd")"
printf '%s' "$trav" | grep -q 'root:' \
  && fail "path traversal escaped the UI dir!" || pass "path traversal is contained"

# The default REBEKAH_GATEWAY_EXPOSE must include opencode + ollama so the
# console's product panels appear out of the box.
def_port="$(free_port)"
REBEKAH_GATEWAY_PORT="$def_port" REBEKAH_GATEWAY_TOKEN="$token" \
  REBEKAH_GATEWAY_UI_DIR="$repo_root/nix/ui" \
  "$python" "$gateway" >"$work/gwd.log" 2>&1 &
pids+=("$!")
wait_url "http://127.0.0.1:$def_port/healthz" || fail "default-expose gateway did not start"
def_info="$(body -H "Authorization: Bearer $token" "http://127.0.0.1:$def_port/api/info")"
if printf '%s' "$def_info" | grep -q '"opencode"' && printf '%s' "$def_info" | grep -q '"ollama"'; then
  pass "default expose includes opencode + ollama"
else
  fail "default expose missing opencode/ollama: $def_info"
fi
if printf '%s' "$def_info" | grep -q '"ephor"'; then
  fail "default expose unexpectedly includes sensitive Ephor API: $def_info"
else
  pass "Ephor API remains opt-in"
fi

# === 3. OIDC auth (needs PyJWT + cryptography) ==============================
printf '\n== OIDC (external) auth ==\n'
if ! "$python" -c 'import jwt, cryptography' >/dev/null 2>&1; then
  skip "PyJWT/cryptography not available in $python; OIDC path covered by in-image smoke"
else
  issuer="https://issuer.example.org"
  audience="rebekah"
  "$python" "$repo_root/tests/gateway-oidc.py" "$work" "$issuer" "$audience"
  jwks_port="$(free_port)"; oidc_gw_port="$(free_port)"
  "$python" -m http.server --directory "$work" "$jwks_port" >/dev/null 2>&1 &
  pids+=("$!")
  wait_url "http://127.0.0.1:$jwks_port/jwks.json" || fail "jwks server did not start"

  REBEKAH_GATEWAY_PORT="$oidc_gw_port" REBEKAH_GATEWAY_EXPOSE="weftmark" \
    REBEKAH_OIDC_ISSUER="$issuer" REBEKAH_OIDC_AUDIENCE="$audience" \
    REBEKAH_OIDC_JWKS_URI="http://127.0.0.1:$jwks_port/jwks.json" \
    WEFTMARK_HOST=127.0.0.1 WEFTMARK_PORT="$up_port" \
    "$python" "$gateway" >"$work/gwo.log" 2>&1 &
  pids+=("$!")
  wait_url "http://127.0.0.1:$oidc_gw_port/healthz" || fail "OIDC gateway did not start"
  pass "starts in OIDC-only mode"

  jgood="$(cat "$work/good.jwt")"
  out="$(body -H "Authorization: Bearer $jgood" "http://127.0.0.1:$oidc_gw_port/weftmark/z")"
  [ "$out" = "UPSTREAM path=/z" ] && pass "valid OIDC JWT -> 200" || fail "valid JWT not proxied: '$out'"
  for bad in expired badaud badsig; do
    c="$(code -H "Authorization: Bearer $(cat "$work/$bad.jwt")" \
          "http://127.0.0.1:$oidc_gw_port/weftmark/z")"
    [ "$c" = 401 ] && pass "$bad JWT -> 401" || fail "$bad JWT not rejected ($c)"
  done
  # A static token is NOT accepted when only OIDC is enabled.
  [ "$(code -H "Authorization: Bearer $token" "http://127.0.0.1:$oidc_gw_port/weftmark/z")" = 401 ] \
    && pass "static token rejected in OIDC-only mode" || fail "static token wrongly accepted"
fi

printf '\n\033[32mGATEWAY TESTS PASSED\033[0m\n'
