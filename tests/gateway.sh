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

# Each case sets the auth scheme it exercises explicitly; default the SQLite
# login OFF here so token/OIDC cases behave as before and nothing touches the
# real /var/lib state dir. The dedicated password section turns it back on.
export REBEKAH_AUTH_PASSWORD=0

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

# Mock WeftMark: serves a kanban projection so we can assert the gateway's
# /api/v1/attention aggregation. /healthz lets wait_url probe it.
start_kanban() {
  local port="$1"
  "$python" - "$port" <<'PY' &
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
port = int(sys.argv[1])
KANBAN = {
    "schema": "weftmark.kanban-projection.v0",
    "task_change_set_links": [
        {"task_id": "t-9", "change_set_id": "cs-1",
         "claim_id": "claim-1", "binding_state": "in_progress"},
    ],
    "cards": [
        {"kind": "change_set", "id": "cs-1", "title": "Add gateway",
         "lane": "review", "lifecycle_state": "review", "readiness": "unreviewed",
         "git": {"branch": "weft/gateway", "head_sha": "91f8e8b09892a210",
                 "observed_at": "2026-08-19T12:58:00+00:00",
                 "dirty_paths": ["nix/gateway.py"]},
         "claims": {"active_ids": ["claim-1"]}, "scope_collisions": [],
         "evidence": {"total": 3, "current": 2, "obsolete": 0, "failed": 0, "unavailable": 1},
         "review": {"id": "review-1", "outcome": "unreviewed", "is_current": True},
         "handoff": None, "attention": ["dirty_worktree"]},
        {"kind": "change_set", "id": "cs-2", "title": "Quiet one",
         "lane": "active", "lifecycle_state": "active", "readiness": "ready",
         "git": {"branch": "weft/quiet", "head_sha": "abc", "dirty_paths": []},
         "claims": {"active_ids": []}, "scope_collisions": [],
         "evidence": {"total": 1, "current": 1}, "review": None, "handoff": None,
         "attention": []},
    ],
    "plan_cards": [
        {"kind": "task", "id": "t-9", "title": "Wire API", "lane": "backlog",
         "task_state": "todo", "change_set_ids": ["cs-1"], "attention": ["blocked"]},
    ],
}
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def do_GET(self):
        if self.path.split("?", 1)[0] == "/v0/kanban":
            body = json.dumps(KANBAN).encode()
        else:
            body = b"UPSTREAM path=%s" % self.path.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
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
# Disable the default SQLite login too, so no auth scheme remains.
if REBEKAH_GATEWAY_EXPOSE=weftmark REBEKAH_AUTH_PASSWORD=0 "$python" "$gateway" >/dev/null 2>"$work/e2"; then
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
# Grep the served page from a file, not `printf ... | grep -q`: under
# `set -o pipefail`, grep -q short-circuits at the first match and closes the
# pipe, so printf takes SIGPIPE and fails the whole pipeline once the body
# outgrows what fits before grep exits. A file has no upstream pipe.
ui_file="$work/ui.html"
body "$base/ui/" > "$ui_file"
has() { grep -q "$1" "$ui_file"; }
has "Rebekah Console" \
  && pass "/ui/ serves the console HTML" || fail "/ui/ missing console markup"
has 'role="tabpanel"' \
  && pass "console exposes accessible tab panels" || fail "console missing tabpanel semantics"
has '>Advanced<' \
  && pass "raw API tools are under Advanced" || fail "console missing Advanced navigation"
has 'id="panel-attention"' \
  && pass "console leads with an attention inbox" || fail "console missing attention panel"
has '/api/v1/attention' \
  && pass "console consumes the versioned attention API" || fail "console does not call /api/v1/attention"
has '/api/v1/system' \
  && pass "System panel consumes the versioned system API" || fail "console does not call /api/v1/system"
if has 'Needs attention' && has 'Installing' && has 'Offline'; then
  pass "service health renders four states with remedies"
else
  fail "console missing four-state service health"
fi
has 'id="csDialog"' \
  && pass "console ships a Change Set detail dialog" || fail "console missing change-set dialog"
has '/api/v1/change-sets/' \
  && pass "console opens change sets via the versioned API" || fail "console does not call change-set detail"
if has '>Work<' && has '>Review<' && has '>Runs<'; then
  pass "primary nav uses goal labels (Work/Review/Runs)"
else
  fail "console nav not renamed to goal labels"
fi
has 'id="laneFilter"' \
  && pass "board offers a mobile lane filter" || fail "console missing mobile lane filter"
has 'optional governance integration' \
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

# === 2d. SQLite username/password login ====================================
printf '\n== SQLite password login ==\n'
pw_port="$(free_port)"
REBEKAH_GATEWAY_PORT="$pw_port" REBEKAH_AUTH_PASSWORD=1 REBEKAH_AUTH_DB="$work/auth.db" \
  REBEKAH_ADMIN_USER=admin REBEKAH_ADMIN_PASSWORD="s3kritpw" \
  REBEKAH_GATEWAY_EXPOSE=weftmark WEFTMARK_HOST=127.0.0.1 WEFTMARK_PORT="$up_port" \
  REBEKAH_GATEWAY_UI_DIR="$repo_root/nix/ui" \
  "$python" "$gateway" >"$work/gwpw.log" 2>&1 &
pids+=("$!")
wait_url "http://127.0.0.1:$pw_port/healthz" || fail "password gateway did not start"
pbase="http://127.0.0.1:$pw_port"

# The gateway starts with password login as its ONLY auth scheme (no token).
pa="$(body "$pbase/api/auth")"
printf '%s' "$pa" | grep -qE '"password": *true' \
  && pass "/api/auth advertises password login" || fail "no password advert: $pa"

# Wrong password is rejected.
[ "$(code -H 'Content-Type: application/json' -X POST \
     --data '{"username":"admin","password":"nope"}' "$pbase/api/login")" = 401 ] \
  && pass "bad password -> 401" || fail "bad password not 401"

# Correct password mints a session token that authenticates the API.
lt="$(body -H 'Content-Type: application/json' -X POST \
      --data '{"username":"admin","password":"s3kritpw"}' "$pbase/api/login")"
sess="$(printf '%s' "$lt" | sed -n 's/.*"token": *"\([^"]*\)".*/\1/p')"
[ -n "$sess" ] && pass "login returns a session token" || fail "no session token: $lt"
[ "$(code -H "Authorization: Bearer $sess" "$pbase/api/info")" = 200 ] \
  && pass "session token authenticates /api/info" || fail "session token rejected"
[ "$(code "$pbase/api/info")" = 401 ] \
  && pass "no session -> 401 (never open)" || fail "open without a session"

# Logout revokes the session.
code -X POST -H "Authorization: Bearer $sess" "$pbase/api/logout" >/dev/null
[ "$(code -H "Authorization: Bearer $sess" "$pbase/api/info")" = 401 ] \
  && pass "logout revokes the session" || fail "session still valid after logout"

# === 2e. versioned aggregation API (/api/v1/*) =============================
printf '\n== versioned aggregation API ==\n'
kb_port="$(free_port)"; v1_port="$(free_port)"
start_kanban "$kb_port"
wait_url "http://127.0.0.1:$kb_port/v0/kanban" || fail "kanban mock did not start"

v1tok="v1tok-$(date +%s)"
REBEKAH_GATEWAY_PORT="$v1_port" REBEKAH_GATEWAY_TOKEN="$v1tok" \
  REBEKAH_GATEWAY_EXPOSE="weftmark" \
  WEFTMARK_HOST=127.0.0.1 WEFTMARK_PORT="$kb_port" \
  "$python" "$gateway" >"$work/gwv1.log" 2>&1 &
pids+=("$!")
wait_url "http://127.0.0.1:$v1_port/healthz" || fail "v1 gateway did not start"
vbase="http://127.0.0.1:$v1_port"
auth_hdr="Authorization: Bearer $v1tok"

# Every /api/v1/* endpoint is authenticated (no open aggregation surface).
for ep in session system attention; do
  [ "$(code "$vbase/api/v1/$ep")" = 401 ] \
    && pass "/api/v1/$ep requires auth" || fail "/api/v1/$ep not 401 unauth"
done

sess="$(body -H "$auth_hdr" "$vbase/api/v1/session")"
printf '%s' "$sess" | grep -q '"schema": "rebekah.session.v1"' \
  && pass "/api/v1/session carries its schema" || fail "session schema wrong: $sess"
printf '%s' "$sess" | grep -q '"profile": "administrator"' \
  && pass "/api/v1/session reports a role profile" || fail "no profile: $sess"
printf '%s' "$sess" | grep -q '"observed_at"' \
  && pass "/api/v1/session is timestamped" || fail "no observed_at: $sess"

sys="$(body -H "$auth_hdr" "$vbase/api/v1/system")"
printf '%s' "$sys" | grep -q '"schema": "rebekah.system.v1"' \
  && pass "/api/v1/system carries its schema" || fail "system schema wrong: $sys"
printf '%s' "$sys" | grep -q '"weftmark"' \
  && pass "/api/v1/system lists exposed backends" || fail "no backends: $sys"
printf '%s' "$sys" | grep -q '"optional": true' \
  && pass "/api/v1/system marks Ephor optional" || fail "ephor not optional: $sys"

att="$(body -H "$auth_hdr" "$vbase/api/v1/attention")"
printf '%s' "$att" | grep -q '"schema": "rebekah.attention.v1"' \
  && pass "/api/v1/attention carries its schema" || fail "attention schema wrong: $att"
printf '%s' "$att" | grep -q '"dirty_worktree"' \
  && pass "/api/v1/attention aggregates change-set reasons" || fail "no cs reason: $att"
printf '%s' "$att" | grep -q '"blocked"' \
  && pass "/api/v1/attention aggregates task reasons" || fail "no task reason: $att"
printf '%s' "$att" | grep -qE '"count": *2' \
  && pass "/api/v1/attention counts only cards needing attention" || fail "wrong count: $att"
printf '%s' "$att" | grep -qE '"stale": *false' \
  && pass "/api/v1/attention is fresh when the backend answers" || fail "unexpected stale: $att"

# When WeftMark is unreachable, attention degrades to stale (never a 5xx).
stale_port="$(free_port)"
REBEKAH_GATEWAY_PORT="$stale_port" REBEKAH_GATEWAY_TOKEN="$v1tok" \
  REBEKAH_GATEWAY_EXPOSE="weftmark" \
  WEFTMARK_HOST=127.0.0.1 WEFTMARK_PORT="$(free_port)" \
  "$python" "$gateway" >"$work/gwv1s.log" 2>&1 &
pids+=("$!")
wait_url "http://127.0.0.1:$stale_port/healthz" || fail "stale-case gateway did not start"
sres="$(code -H "$auth_hdr" "http://127.0.0.1:$stale_port/api/v1/attention")"
sbody="$(body -H "$auth_hdr" "http://127.0.0.1:$stale_port/api/v1/attention")"
[ "$sres" = 200 ] && printf '%s' "$sbody" | grep -qE '"stale": *true' \
  && pass "/api/v1/attention degrades to stale, not 5xx" || fail "no graceful degrade: $sres $sbody"

# Change Set list + detail (the correlated spine). Uses the same $v1_port gateway
# backed by the enriched kanban mock. Detail body goes to a file (pipefail-safe).
[ "$(code "$vbase/api/v1/change-sets")" = 401 ] \
  && pass "/api/v1/change-sets requires auth" || fail "change-sets not 401 unauth"
csl="$work/csl.json"; body -H "$auth_hdr" "$vbase/api/v1/change-sets" > "$csl"
grep -q '"schema": "rebekah.change-set-list.v1"' "$csl" \
  && pass "/api/v1/change-sets carries its schema" || fail "cs list schema wrong: $(cat "$csl")"
grep -q '"cs-1"' "$csl" && ! grep -q '"t-9"' "$csl" \
  && pass "/api/v1/change-sets lists change sets, not tasks" || fail "cs list contents wrong: $(cat "$csl")"

[ "$(code -H "$auth_hdr" "$vbase/api/v1/change-sets/nope")" = 404 ] \
  && pass "unknown change set -> 404" || fail "unknown change set not 404"

csd="$work/csd.json"; body -H "$auth_hdr" "$vbase/api/v1/change-sets/cs-1" > "$csd"
grep -q '"schema": "rebekah.change-set.v1"' "$csd" \
  && pass "change set detail carries its schema" || fail "cs detail schema wrong: $(cat "$csd")"
grep -q '"weft/gateway"' "$csd" \
  && pass "detail surfaces the git branch" || fail "cs detail missing git: $(cat "$csd")"
if grep -q '"review-1"' "$csd" && grep -q '"claim-1"' "$csd" && grep -q '"t-9"' "$csd"; then
  pass "detail links review, claim, and correlated task"
else
  fail "cs detail missing correlated links: $(cat "$csd")"
fi
grep -q '"opencode"' "$csd" && grep -q '"sylvae"' "$csd" && grep -qE '"linked": *false' "$csd" \
  && pass "detail declares OpenCode/Sylvae link slots (absent, not fabricated)" \
  || fail "cs detail missing related slots: $(cat "$csd")"

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
