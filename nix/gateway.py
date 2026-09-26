#!/usr/bin/env python3
"""rebekah-gateway: the single authenticated entry point for Rebekah's API.

Rebekah's five services (OpenCode, Ollama, Sylvae, WeftMark, Ephor) bind loopback only
(security invariant #2). This gateway is the "authenticated TLS proxy" that
invariant points at: it is the one process that may face the local network / a
GUI / a human-in-the-loop guest, it authenticates every request, and it forwards
only the allow-listed routes to the loopback backends.

Auth follows what flagship agent/kanban orchestrators use, offering both the
common *internal* and the common *external* scheme; a request is authorized if
EITHER enabled scheme accepts it:

  * token  (internal) -- a static ``Authorization: Bearer <token>`` API token,
    the near-universal machine / LAN / CI credential. Constant-time compared.
  * oidc   (external) -- an ``Authorization: Bearer <JWT>`` access token from an
    OIDC / OAuth2 identity provider (SSO), verified against the issuer's JWKS.
    This is the scheme flagship projects expose to human GUI users.

It fails closed:
  * binding beyond loopback without TLS is refused (never ship tokens in the
    clear on the LAN);
  * with no auth scheme configured at all it refuses to start (never an open
    proxy);
  * an unknown / not-exposed route is 404, an unauthenticated request is 401,
    an upstream error is 502 -- backend details are never leaked.

PyJWT is imported lazily, only when OIDC is configured, so the token path (and
the whole off-grid story) works with the Python standard library alone.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import mimetypes
import os
import posixpath
import re
import secrets
import sqlite3
import ssl
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- configuration (runtime env only; no secrets baked into the image) -------

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
# Hop-by-hop headers must not be forwarded (RFC 7230 6.1), plus the ones that
# carry the *client's* credential to the gateway -- the gateway is the trust
# boundary and re-authenticates to backends itself.
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host", "authorization",
}


def _split_hostport(value, default_port):
    host, _, port = value.partition(":")
    return host or "127.0.0.1", int(port) if port else default_port


def _int_env(env, name, default):
    try:
        return int(env.get(name, str(default)))
    except ValueError:
        return -1  # reported by validate()


def _iso_now():
    # RFC 3339 / ISO 8601 UTC, for the versioned API's observed_at field.
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Config:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.host = env.get("REBEKAH_GATEWAY_HOST", "127.0.0.1").strip() or "127.0.0.1"
        self.port = int(env.get("REBEKAH_GATEWAY_PORT", "8080"))
        self.tls_cert = env.get("REBEKAH_GATEWAY_TLS_CERT", "").strip()
        self.tls_key = env.get("REBEKAH_GATEWAY_TLS_KEY", "").strip()
        self.max_body = int(env.get("REBEKAH_GATEWAY_MAX_BODY", str(32 * 1024 * 1024)))
        self.timeout = float(env.get("REBEKAH_GATEWAY_TIMEOUT", "120"))

        # Internal (token) scheme.
        self.token = env.get("REBEKAH_GATEWAY_TOKEN", "")

        # External (OIDC) scheme.
        self.oidc_issuer = env.get("REBEKAH_OIDC_ISSUER", "").strip()
        self.oidc_audience = env.get("REBEKAH_OIDC_AUDIENCE", "").strip()
        self.oidc_jwks_uri = env.get("REBEKAH_OIDC_JWKS_URI", "").strip()
        self.oidc_algorithms = (
            env.get("REBEKAH_OIDC_ALGORITHMS", "RS256 RS384 RS512 ES256 ES384").split()
        )
        self.oidc_required_scope = env.get("REBEKAH_OIDC_REQUIRED_SCOPE", "").strip()
        self.oidc_allowed_subjects = set(
            env.get("REBEKAH_OIDC_ALLOWED_SUBJECTS", "").split()
        )

        # Which backends are reachable through the gateway. WeftMark (the
        # coordination / evidence / review board -- the "kanban" surface) is the
        # safe default; OpenCode drives agents so it is opt-in.
        exposed = env.get("REBEKAH_GATEWAY_EXPOSE", "weftmark opencode ollama").split()

        oc_host = env.get("OPENCODE_HOST", "127.0.0.1").strip() or "127.0.0.1"
        oc_port = int(env.get("OPENCODE_PORT", "4096"))
        sy_host = env.get("SYLVAE_HOST", "127.0.0.1").strip() or "127.0.0.1"
        sy_port = int(env.get("SYLVAE_PORT", "8971"))
        wm_host = env.get("WEFTMARK_HOST", "127.0.0.1").strip() or "127.0.0.1"
        wm_port = int(env.get("WEFTMARK_PORT", "8765"))
        ol_host, ol_port = _split_hostport(
            env.get("OLLAMA_HOST", "127.0.0.1:11434").strip(), 11434
        )
        ep_host = env.get("EPHOR_HOST", "127.0.0.1").strip() or "127.0.0.1"
        ep_port = int(env.get("EPHOR_PORT", "9800"))
        oc_pw = env.get("OPENCODE_SERVER_PASSWORD", "")

        catalogue = {
            "weftmark": (wm_host, wm_port, None),
            "opencode": (oc_host, oc_port,
                         ("opencode", oc_pw) if oc_pw else None),
            "sylvae": (sy_host, sy_port, None),
            "ollama": (ol_host, ol_port, None),
            "ephor": (ep_host, ep_port, None),
        }
        self.backends = {name: catalogue[name] for name in exposed if name in catalogue}
        self.unknown_exposed = [name for name in exposed if name not in catalogue]

        # Built-in web console (served static, same-origin). On by default; the
        # data calls it makes still go through the authenticated proxy.
        self.ui_enabled = env.get("REBEKAH_GATEWAY_UI", "1").strip() not in ("0", "")
        self.ui_dir = env.get(
            "REBEKAH_GATEWAY_UI_DIR", "/usr/local/share/rebekah/ui"
        ).strip()

        # SQLite username/password login (the default browser sign-in). On first
        # start it seeds a default admin user; a successful login mints an opaque
        # bearer session token. Password hashing is scrypt (stdlib). No secret is
        # baked into the image -- the admin password is provided at runtime or a
        # random one is generated and logged once.
        self.password_enabled = env.get("REBEKAH_AUTH_PASSWORD", "1").strip() not in ("0", "")
        self.auth_db = env.get(
            "REBEKAH_AUTH_DB", "/var/lib/rebekah/gateway/auth.db"
        ).strip()
        self.admin_user = env.get("REBEKAH_ADMIN_USER", "admin").strip() or "admin"
        self.admin_password = env.get("REBEKAH_ADMIN_PASSWORD", "")
        self.session_ttl = int(env.get("REBEKAH_SESSION_TTL", str(12 * 3600)))

        # Pushing to RAGBAZ Dash (optional). For an instance with no public
        # address: the gateway calls out to Dash over https with a push key
        # issued there, and Dash never connects in. Both must be set, or neither.
        self.dash_url = env.get("REBEKAH_DASH_URL", "").strip().rstrip("/")
        self.dash_push_key = env.get("REBEKAH_DASH_PUSH_KEY", "").strip()
        self.dash_poll = _int_env(env, "REBEKAH_DASH_POLL_INTERVAL", 30)
        self.dash_heartbeat = _int_env(env, "REBEKAH_DASH_PUSH_INTERVAL", 300)

    @property
    def dash_enabled(self):
        return bool(self.dash_url or self.dash_push_key)

    @property
    def tls_enabled(self):
        return bool(self.tls_cert and self.tls_key)

    @property
    def oidc_enabled(self):
        return bool(self.oidc_issuer and self.oidc_audience)

    @property
    def token_enabled(self):
        return bool(self.token)

    def jwks_uri(self):
        if self.oidc_jwks_uri:
            return self.oidc_jwks_uri
        return self.oidc_issuer.rstrip("/") + "/.well-known/jwks.json"

    def validate(self):
        """Return a list of fatal misconfigurations (fail closed on any)."""
        problems = []
        if self.host not in LOOPBACK and not self.tls_enabled:
            problems.append(
                "refusing to bind %s without TLS: set REBEKAH_GATEWAY_TLS_CERT/"
                "_KEY, or bind 127.0.0.1 and front it with a TLS proxy" % self.host
            )
        if not (self.token_enabled or self.oidc_enabled or self.password_enabled):
            problems.append(
                "no auth configured: keep REBEKAH_AUTH_PASSWORD=1 (SQLite login, "
                "the default), or set REBEKAH_GATEWAY_TOKEN (internal) and/or "
                "REBEKAH_OIDC_ISSUER + REBEKAH_OIDC_AUDIENCE (external)"
            )
        if self.tls_cert and not self.tls_key:
            problems.append("REBEKAH_GATEWAY_TLS_CERT set without REBEKAH_GATEWAY_TLS_KEY")
        if self.tls_key and not self.tls_cert:
            problems.append("REBEKAH_GATEWAY_TLS_KEY set without REBEKAH_GATEWAY_TLS_CERT")
        if self.dash_enabled:
            problems.extend(dash_problems(self))
        if not self.backends:
            problems.append(
                "no exposed backends: set REBEKAH_GATEWAY_EXPOSE to a subset of "
                "weftmark opencode sylvae ollama ephor"
            )
        return problems


# --- OIDC verification (lazy: PyJWT only loaded when OIDC is configured) ------

class OidcVerifier:
    def __init__(self, cfg):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._jwk_client = None

    def _client(self):
        # Built once, then reused; PyJWKClient caches keys and refreshes on
        # unknown kid, so JWKS is not refetched per request.
        if self._jwk_client is None:
            with self._lock:
                if self._jwk_client is None:
                    import jwt  # noqa: PLC0415 (lazy by design)
                    self._jwk_client = jwt.PyJWKClient(self.cfg.jwks_uri())
        return self._jwk_client

    def verify(self, token):
        """Return the subject on a valid token, else None."""
        import jwt  # noqa: PLC0415
        try:
            signing_key = self._client().get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=self.cfg.oidc_algorithms,
                audience=self.cfg.oidc_audience,
                issuer=self.cfg.oidc_issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except Exception:  # noqa: BLE001 -- any failure denies, fail closed
            return None
        sub = claims.get("sub")
        if not sub:
            return None
        if self.cfg.oidc_allowed_subjects and sub not in self.cfg.oidc_allowed_subjects:
            return None
        if self.cfg.oidc_required_scope:
            scopes = set(str(claims.get("scope", "")).split()) | set(claims.get("scp", []) or [])
            if self.cfg.oidc_required_scope not in scopes:
                return None
        return sub


# --- SQLite username/password login ------------------------------------------

class PasswordStore:
    """SQLite-backed users + opaque bearer sessions; scrypt password hashing.

    Only session-token *hashes* are stored, so a leaked DB never yields a live
    bearer directly. All access is serialized under a lock (one shared
    connection across the gateway's threads).
    """

    def __init__(self, path, session_ttl):
        self.session_ttl = session_ttl
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        # The parent directory is normally 0700 in the image, but keep the
        # credential database private even when an operator chooses another
        # state path or copies it out of the volume.
        os.chmod(path, 0o600)
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS users "
                "(username TEXT PRIMARY KEY, salt BLOB, hash BLOB, created INTEGER)")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS sessions "
                "(token_hash TEXT PRIMARY KEY, username TEXT, expires INTEGER)")
            self._db.commit()

    @staticmethod
    def _derive(password, salt):
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)

    def user_count(self):
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def upsert_user(self, username, password):
        salt = secrets.token_bytes(16)
        derived = self._derive(password, salt)
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO users (username, salt, hash, created) VALUES (?,?,?,?)",
                (username, salt, derived, int(time.time())))
            self._db.commit()

    def verify(self, username, password):
        with self._lock:
            row = self._db.execute(
                "SELECT salt, hash FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            # Spend comparable work so a missing user isn't a timing oracle.
            self._derive(password, b"\x00" * 16)
            return False
        return hmac.compare_digest(self._derive(password, row[0]), row[1])

    def create_session(self, username):
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires = int(time.time()) + self.session_ttl
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO sessions (token_hash, username, expires) VALUES (?,?,?)",
                (token_hash, username, expires))
            self._db.commit()
        return token, expires

    def principal_for_token(self, token):
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._lock:
            row = self._db.execute(
                "SELECT username, expires FROM sessions WHERE token_hash=?", (token_hash,)).fetchone()
        if not row:
            return None
        if row[1] < int(time.time()):
            with self._lock:
                self._db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
                self._db.commit()
            return None
        return row[0]

    def revoke(self, token):
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._lock:
            self._db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
            self._db.commit()


class Authenticator:
    def __init__(self, cfg):
        self.cfg = cfg
        self.oidc = OidcVerifier(cfg) if cfg.oidc_enabled else None
        self.passwords = None
        if cfg.password_enabled:
            try:
                self.passwords = PasswordStore(cfg.auth_db, cfg.session_ttl)
                if self.passwords.user_count() == 0:
                    self._seed_admin(cfg)
            except Exception as exc:  # noqa: BLE001 -- degrade, never crash
                sys.stderr.write(
                    "rebekah-gateway: password login unavailable (%s)\n" % exc)
                self.passwords = None

    def _seed_admin(self, cfg):
        """Seed the first user. A generated password goes to a 0600 file in the
        gateway's own 0700 state dir, never to the log: container logs end up in
        the host journal, readable long after "shown once". If the file cannot
        be written, nothing is seeded and the next start tries again."""
        if cfg.admin_password:
            self.passwords.upsert_user(cfg.admin_user, cfg.admin_password)
            return
        pw = secrets.token_urlsafe(18)
        path = os.path.join(os.path.dirname(os.path.abspath(cfg.auth_db)),
                            "initial-admin-password")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                         0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(pw + "\n")
            os.chmod(path, 0o600)
        except OSError as exc:
            sys.stderr.write(
                "rebekah-gateway: could not write %s (%s); admin user not "
                "seeded, set REBEKAH_ADMIN_PASSWORD or fix the state dir\n"
                % (path, exc.strerror or exc))
            return
        self.passwords.upsert_user(cfg.admin_user, pw)
        sys.stderr.write(
            "rebekah-gateway: seeded admin user %r; its generated password is in "
            "%s (read it, then delete the file)\n" % (cfg.admin_user, path))

    @property
    def password_active(self):
        return self.passwords is not None

    def principal(self, authorization_header):
        """Return a principal string if the request is authorized, else None."""
        if not authorization_header:
            return None
        scheme, _, credential = authorization_header.partition(" ")
        if scheme.lower() != "bearer":
            return None
        credential = credential.strip()
        if not credential:
            return None
        if self.cfg.token_enabled and hmac.compare_digest(credential, self.cfg.token):
            return "token:local"
        if self.passwords is not None:
            user = self.passwords.principal_for_token(credential)
            if user is not None:
                return "user:" + user
        if self.oidc is not None:
            sub = self.oidc.verify(credential)
            if sub is not None:
                return "oidc:" + sub
        return None

    def login(self, username, password):
        """Verify credentials and mint a session; returns (token, expires) or None."""
        if self.passwords is None or not username or not password:
            return None
        if not self.passwords.verify(username, password):
            return None
        return self.passwords.create_session(username)


# --- proxy -------------------------------------------------------------------

# --- versioned aggregation payloads (docs/HUMAN-INTERFACE-PLAN.md §10) -------
# Built here, outside the request handler, so the Dash pusher (below) sends
# exactly what GET /api/v1/{system,attention,change-sets} serves.

def backend_get_json(cfg, name, path):
    """Internal GET to a loopback backend (auth injected as the proxy does).

    Returns parsed JSON or None on any failure -- callers degrade to a "stale"
    result, never an error.
    """
    backend = cfg.backends.get(name)
    if backend is None:
        return None
    host, port, upstream_auth = backend
    headers = {"Host": "%s:%d" % (host, port)}
    if upstream_auth is not None:
        user, pw = upstream_auth
        headers["Authorization"] = "Basic " + base64.b64encode(
            ("%s:%s" % (user, pw)).encode()).decode()
    conn = None
    try:
        conn = http.client.HTTPConnection(host, port, timeout=min(cfg.timeout, 5))
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        if resp.status != 200:
            return None
        return json.loads(raw)
    except Exception:  # noqa: BLE001 -- degrade, never leak
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def fetch_kanban(cfg):
    """WeftMark's kanban projection, or None when it is not exposed/reachable."""
    if "weftmark" not in cfg.backends:
        return None
    return backend_get_json(cfg, "weftmark", "/v0/kanban")


def auth_schemes(cfg, auth):
    schemes = []
    if cfg.token_enabled:
        schemes.append("token")
    if auth.password_active:
        schemes.append("password")
    if cfg.oidc_enabled:
        schemes.append("oidc")
    return schemes


def v1_system(cfg, auth):
    backends = {nm: {"route": "/" + nm + "/"} for nm in sorted(cfg.backends)}
    return {
        "schema": "rebekah.system.v1",
        "observed_at": _iso_now(),
        "source": "rebekah-gateway",
        "service": "rebekah-gateway",
        "auth": auth_schemes(cfg, auth),
        "ui": cfg.ui_enabled,
        "backends": backends,
        # Ephor is optional: present it as installed-or-not, never as a
        # failed baseline service (plan §1, §6.8).
        "ephor": {"exposed": "ephor" in cfg.backends, "optional": True},
    }


def v1_attention(kanban):
    items = []
    stale = not isinstance(kanban, dict)
    if not stale:
        for card in (kanban.get("cards") or []):
            for reason in (card.get("attention") or []):
                items.append({
                    "id": card.get("id"), "kind": "change_set",
                    "title": card.get("title") or card.get("id"),
                    "lane": card.get("lane"), "reason": reason,
                    "change_set": card.get("id"), "source": "weftmark",
                })
        for card in (kanban.get("plan_cards") or []):
            for reason in (card.get("attention") or []):
                items.append({
                    "id": card.get("id"), "kind": "task",
                    "title": card.get("title") or card.get("id"),
                    "lane": card.get("lane"), "reason": reason,
                    "source": "weftmark",
                })
    return {
        "schema": "rebekah.attention.v1",
        "observed_at": _iso_now(),
        "source": "rebekah-gateway",
        "count": len(items),
        "stale": stale,
        "items": items,
    }


def v1_changesets(kanban):
    stale = not isinstance(kanban, dict)
    items = []
    if not stale:
        for card in (kanban.get("cards") or []):
            if card.get("kind") != "change_set":
                continue
            ev = card.get("evidence") or {}
            items.append({
                "id": card.get("id"),
                "title": card.get("title") or card.get("id"),
                "lane": card.get("lane"),
                "lifecycle_state": card.get("lifecycle_state"),
                "readiness": card.get("readiness"),
                "evidence": {"total": ev.get("total"), "current": ev.get("current")},
                "attention": card.get("attention") or [],
            })
    return {
        "schema": "rebekah.change-set-list.v1",
        "observed_at": _iso_now(),
        "source": "rebekah-gateway",
        "count": len(items),
        "stale": stale,
        "items": items,
    }


def make_handler(cfg, auth):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "rebekah-gateway"
        sys_version = ""

        def log_message(self, fmt, *args):  # to stderr, no credentials
            sys.stderr.write("gateway: " + (fmt % args) + "\n")

        def _write_head(self, status, headers, body_len=None):
            self.send_response_only(status)
            self.send_header("X-Rebekah-Gateway", "1")
            for key, value in headers:
                self.send_header(key, value)
            if body_len is not None:
                self.send_header("Content-Length", str(body_len))
            self.end_headers()

        def _fail(self, status, message, extra_headers=()):
            body = (message + "\n").encode()
            self._write_head(status, list(extra_headers) + [
                ("Content-Type", "text/plain; charset=utf-8"),
            ], len(body))
            if self.command != "HEAD":
                self.wfile.write(body)

        def _serve_ui(self, rel):
            # Static, same-origin console. GET/HEAD only; path-sanitized so a
            # request can never escape the UI directory.
            if self.command not in ("GET", "HEAD"):
                self._fail(405, "method not allowed")
                return
            clean = posixpath.normpath("/" + rel).lstrip("/")
            if not clean or clean == ".":
                clean = "index.html"
            full = os.path.realpath(os.path.join(cfg.ui_dir, clean))
            root = os.path.realpath(cfg.ui_dir)
            if full != root and not full.startswith(root + os.sep):
                self._fail(404, "not found")
                return
            try:
                with open(full, "rb") as handle:
                    payload = handle.read()
            except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
                self._fail(404, "not found")
                return
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in (
                "application/javascript", "application/json",
            ):
                ctype += "; charset=utf-8"
            headers = [
                ("Content-Type", ctype),
                ("X-Content-Type-Options", "nosniff"),
                ("Referrer-Policy", "no-referrer"),
                ("Content-Security-Policy",
                 "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
                 "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                 "base-uri 'none'; frame-ancestors 'none'"),
            ]
            self._write_head(200, headers, len(payload))
            if self.command != "HEAD":
                self.wfile.write(payload)

        def _json(self, status, obj):
            body = json.dumps(obj).encode()
            self._write_head(status, [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Cache-Control", "no-store"),
            ], len(body))
            if self.command != "HEAD":
                self.wfile.write(body)

        def _serve_auth(self):
            # Unauthenticated: which login methods the console should offer.
            # No secret, credential, or backend list is revealed here.
            self._json(200, {
                "password": auth.password_active,
                "token_auth": cfg.token_enabled,
            })

        def _authed(self):
            principal = auth.principal(self.headers.get("Authorization"))
            if principal is None:
                self._fail(
                    401, "unauthorized",
                    extra_headers=[("WWW-Authenticate", 'Bearer realm="rebekah"')],
                )
                return None
            return principal

        def _backend_get_json(self, name, path):
            return backend_get_json(cfg, name, path)

        # --- versioned aggregation API (docs/HUMAN-INTERFACE-PLAN.md §10) -----
        # The console consumes these instead of reverse-engineering each backend.
        # Every response carries a schema version, source, and observed_at.

        def _serve_v1_session(self):
            principal = self._authed()
            if principal is None:
                return
            scheme, _, name = principal.partition(":")
            # Single-tenant projection: the gateway does not yet enforce
            # sub-capabilities -- every authenticated request is allowed -- so the
            # effective capability set is the full one. This is an interface
            # projection (plan §5.2); tighten it as server-side authz lands.
            self._json(200, {
                "schema": "rebekah.session.v1",
                "observed_at": _iso_now(),
                "source": "rebekah-gateway",
                "principal": name or scheme,
                "auth": scheme,
                "profile": "administrator",
                "capabilities": ["observe", "review", "operate", "administer"],
            })

        def _serve_v1_system(self):
            if self._authed() is None:
                return
            self._json(200, v1_system(cfg, auth))

        def _serve_v1_attention(self):
            if self._authed() is None:
                return
            self._json(200, v1_attention(fetch_kanban(cfg)))

        # --- Change Set as the visible spine (plan §11 / §14.6) --------------
        # /api/v1/change-sets[/{id}] projects WeftMark's kanban into a change-set
        # list and a correlated detail: git, evidence, review, handoff, claims,
        # scope collisions, and cross-system links. OpenCode/Sylvae link slots
        # are declared but only populated when a real reference exists -- never
        # fabricated from a guessed join.
        def _serve_v1_changesets(self):
            if self._authed() is None:
                return
            self._json(200, v1_changesets(fetch_kanban(cfg)))

        def _serve_v1_changeset(self, cs_id):
            if self._authed() is None:
                return
            data = self._backend_get_json("weftmark", "/v0/kanban") \
                if "weftmark" in cfg.backends else None
            if not isinstance(data, dict):
                # Cannot confirm the id exists while the backend is unreachable.
                self._fail(502, "upstream unavailable")
                return
            card = None
            for c in (data.get("cards") or []):
                if c.get("kind") == "change_set" and c.get("id") == cs_id:
                    card = c
                    break
            if card is None:
                self._fail(404, "not found")
                return

            links = []
            git = card.get("git") or {}
            branch = git.get("branch")
            if branch:
                links.append({"system": "weftmark", "kind": "git_branch",
                              "id": branch, "head_sha": git.get("head_sha")})
            review = card.get("review")
            if isinstance(review, dict) and review.get("id"):
                links.append({"system": "weftmark", "kind": "review",
                              "id": review.get("id"), "state": review.get("outcome"),
                              "current": review.get("is_current")})
            handoff = card.get("handoff")
            if isinstance(handoff, dict) and handoff.get("id"):
                links.append({"system": "weftmark", "kind": "handoff",
                              "id": handoff.get("id"), "current": handoff.get("is_current")})
            for claim_id in ((card.get("claims") or {}).get("active_ids") or []):
                links.append({"system": "weftmark", "kind": "claim", "id": claim_id})

            # Correlated tasks: the plan cards that name this change set, plus the
            # authoritative task<->change-set link records.
            tasks = []
            for pc in (data.get("plan_cards") or []):
                if cs_id in (pc.get("change_set_ids") or []):
                    tasks.append({"system": "weftmark", "kind": "task",
                                  "id": pc.get("id"), "title": pc.get("title"),
                                  "state": pc.get("task_state")})
            for link in (data.get("task_change_set_links") or []):
                if link.get("change_set_id") == cs_id and \
                        not any(t["id"] == link.get("task_id") for t in tasks):
                    tasks.append({"system": "weftmark", "kind": "task",
                                  "id": link.get("task_id"),
                                  "state": link.get("binding_state")})
            for t in tasks:
                links.append(t)

            # OpenCode / Sylvae correlation, resolved from WeftMark runtime
            # identity. The Change Set detail endpoint
            # (weftmark.kanban-projection.v0, /v0/kanban/changes/{id}) carries two
            # seams, both namespaced by the producing/claiming tool as
            # "sylvae:run/<id>" or "opencode:session/<id>":
            #   - evidence_refs: producer id + artifact uris of PAST runs (what
            #     has run against this change set);
            #   - claims.active[].session: the session of a worker holding the
            #     change set RIGHT NOW, before it has produced any evidence.
            # A match resolves to a real link; absent (older WeftMark pin, or no
            # such id) -> "not linked yet", never a fabricated join. Evidence
            # refs are preferred order; an active-claim ref is tagged active:true
            # so a consumer can distinguish "working now" from "has run".
            # See docs/WEFTMARK-RUNTIME-LINKS-SCOPE.md.
            detail = self._backend_get_json(
                "weftmark", "/v0/kanban/changes/" + urllib.parse.quote(cs_id, safe="")
            ) if "weftmark" in cfg.backends else None
            detail_card = detail["card"] if (
                isinstance(detail, dict) and isinstance(detail.get("card"), dict)
            ) else {}
            evidence_refs = detail_card.get("evidence_refs") or []
            active_claims = (detail_card.get("claims") or {}).get("active") or []
            related = {}
            for system in ("opencode", "sylvae"):
                refs = []
                seen = set()
                prefix = system + ":"
                for ref in evidence_refs:
                    if not isinstance(ref, dict):
                        continue
                    candidates = [(ref.get("producer") or {}).get("id")]
                    candidates.extend(ref.get("artifacts") or [])
                    for cand in candidates:
                        if isinstance(cand, str) \
                                and cand.startswith(prefix) and cand not in seen:
                            seen.add(cand)
                            refs.append({"id": cand, "evidence": ref.get("id")})
                for claim in active_claims:
                    if not isinstance(claim, dict):
                        continue
                    session = claim.get("session")
                    if isinstance(session, str) \
                            and session.startswith(prefix) and session not in seen:
                        seen.add(session)
                        refs.append({"id": session, "claim": claim.get("id"),
                                     "active": True})
                if refs:
                    related[system] = {"linked": True, "refs": refs}
                else:
                    related[system] = {
                        "linked": False,
                        "reason": "No %s reference on this change set's evidence "
                                  "or active claims yet." % system,
                    }

            self._json(200, {
                "schema": "rebekah.change-set.v1",
                "observed_at": _iso_now(),
                "source": "rebekah-gateway",
                "stale": False,
                "id": card.get("id"),
                "title": card.get("title") or card.get("id"),
                "lane": card.get("lane"),
                "lifecycle_state": card.get("lifecycle_state"),
                "readiness": card.get("readiness"),
                "git": {"branch": git.get("branch"), "head_sha": git.get("head_sha"),
                        "observed_at": git.get("observed_at"),
                        "dirty_paths": git.get("dirty_paths") or []},
                "evidence": card.get("evidence") or {},
                "review": review,
                "handoff": handoff,
                "scope_collisions": card.get("scope_collisions") or [],
                "attention": card.get("attention") or [],
                "links": links,
                "related": related,
            })

        def _serve_login(self):
            if self.command != "POST":
                self._fail(405, "method not allowed")
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > 64 * 1024:
                self._fail(413, "payload too large")
                return
            try:
                data = json.loads(self.rfile.read(length) if length else b"{}")
                username = str(data.get("username", ""))
                password = str(data.get("password", ""))
            except Exception:  # noqa: BLE001
                self._fail(400, "invalid json")
                return
            result = auth.login(username, password)
            if result is None:
                self._fail(401, "invalid credentials")
                return
            token, expires = result
            self._json(200, {"token": token, "user": username, "expires": expires})

        def _serve_logout(self):
            if self.command != "POST":
                self._fail(405, "method not allowed")
                return
            scheme, _, cred = self.headers.get("Authorization", "").partition(" ")
            if scheme.lower() == "bearer" and cred.strip() and auth.passwords is not None:
                auth.passwords.revoke(cred.strip())
            self._fail(200, "ok")

        def _serve_info(self):
            principal = auth.principal(self.headers.get("Authorization"))
            if principal is None:
                self._fail(
                    401, "unauthorized",
                    extra_headers=[("WWW-Authenticate", 'Bearer realm="rebekah"')],
                )
                return
            schemes = []
            if cfg.token_enabled:
                schemes.append("token")
            if auth.password_active:
                schemes.append("password")
            if cfg.oidc_enabled:
                schemes.append("oidc")
            body = json.dumps({
                "service": "rebekah-gateway",
                "expose": sorted(cfg.backends),
                "auth": schemes,
                "ui": cfg.ui_enabled,
            }).encode()
            self._write_head(200, [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Cache-Control", "no-store"),
            ], len(body))
            if self.command != "HEAD":
                self.wfile.write(body)

        def _handle(self):
            raw_path = self.path.split("?", 1)[0]
            if self.path in ("/healthz", "/healthz/"):
                # Unauthenticated liveness only -- reveals nothing.
                self._fail(200, "ok")
                return

            if cfg.ui_enabled:
                if raw_path in ("/", "/ui", "/ui/"):
                    if self.command not in ("GET", "HEAD"):
                        self._fail(405, "method not allowed")
                        return
                    self._serve_ui("index.html")
                    return
                if raw_path.startswith("/ui/"):
                    self._serve_ui(raw_path[len("/ui/"):])
                    return

            if raw_path in ("/api/auth", "/api/auth/"):
                self._serve_auth()
                return
            if raw_path in ("/api/login", "/api/login/"):
                self._serve_login()
                return
            if raw_path in ("/api/logout", "/api/logout/"):
                self._serve_logout()
                return
            if raw_path in ("/api/v1/session", "/api/v1/session/"):
                self._serve_v1_session()
                return
            if raw_path in ("/api/v1/system", "/api/v1/system/"):
                self._serve_v1_system()
                return
            if raw_path in ("/api/v1/attention", "/api/v1/attention/"):
                self._serve_v1_attention()
                return
            if raw_path in ("/api/v1/change-sets", "/api/v1/change-sets/"):
                self._serve_v1_changesets()
                return
            if raw_path.startswith("/api/v1/change-sets/"):
                cs_id = urllib.parse.unquote(raw_path[len("/api/v1/change-sets/"):]).strip("/")
                if not cs_id or "/" in cs_id:
                    self._fail(404, "not found")
                    return
                self._serve_v1_changeset(cs_id)
                return
            if raw_path in ("/api/info", "/api/info/"):
                self._serve_info()
                return

            first = self.path.lstrip("/").split("/", 1)[0].split("?", 1)[0]
            backend = cfg.backends.get(first)
            if backend is None:
                self._fail(404, "not found")
                return

            principal = auth.principal(self.headers.get("Authorization"))
            if principal is None:
                self._fail(
                    401, "unauthorized",
                    extra_headers=[("WWW-Authenticate", 'Bearer realm="rebekah"')],
                )
                return

            length = int(self.headers.get("Content-Length") or 0)
            if length > cfg.max_body:
                self._fail(413, "payload too large")
                return
            body = self.rfile.read(length) if length else b""

            host, port, upstream_auth = backend
            # Strip the "/<backend>" prefix; forward the remainder (with query).
            prefix = "/" + first
            upstream_path = self.path[len(prefix):] or "/"
            if not upstream_path.startswith("/"):
                upstream_path = "/" + upstream_path

            fwd = {}
            for key in self.headers.keys():
                if key.lower() in HOP_BY_HOP:
                    continue
                fwd[key] = self.headers.get(key)
            fwd["Host"] = "%s:%d" % (host, port)
            fwd["X-Forwarded-By"] = "rebekah-gateway"
            if upstream_auth is not None:
                user, pw = upstream_auth
                token = base64.b64encode(("%s:%s" % (user, pw)).encode()).decode()
                fwd["Authorization"] = "Basic " + token

            try:
                conn = http.client.HTTPConnection(host, port, timeout=cfg.timeout)
                conn.request(self.command, upstream_path, body=body, headers=fwd)
                resp = conn.getresponse()
                payload = resp.read()
            except Exception:  # noqa: BLE001 -- never leak upstream errors
                self._fail(502, "bad gateway")
                return
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

            out_headers = [
                (k, v) for (k, v) in resp.getheaders()
                if k.lower() not in HOP_BY_HOP and k.lower() != "content-length"
            ]
            self._write_head(resp.status, out_headers, len(payload))
            if self.command != "HEAD":
                self.wfile.write(payload)

        # All methods route through _handle.
        do_GET = _handle
        do_POST = _handle
        do_PUT = _handle
        do_DELETE = _handle
        do_PATCH = _handle
        do_HEAD = _handle
        do_OPTIONS = _handle

    return Handler


# --- pushing to RAGBAZ Dash (optional; outbound only) --------------------------
# An instance behind NAT has no address Dash could pull from. With
# REBEKAH_DASH_URL + REBEKAH_DASH_PUSH_KEY set, a background thread sends Dash
# the same three /api/v1 payloads the gateway serves, whenever they change and
# at least every REBEKAH_DASH_PUSH_INTERVAL seconds, and polls Dash every
# REBEKAH_DASH_POLL_INTERVAL seconds for a "refresh requested" flag (someone
# clicked Refresh in Dash), pushing at once when it is set. Nothing listens:
# no inbound port is opened. The push key is sent only in the Authorization
# header to that one origin over verified TLS, and never logged.

DASH_PUSH_KEY_RE = re.compile(r"^rbkp_[0-9a-f]{12}_[A-Za-z0-9_-]{43}$")
DASH_ENVELOPE = "rebekah.dash-push.v1"
DASH_MAX_RESPONSE = 64 * 1024
DASH_TIMEOUT = 10
DASH_MAX_BACKOFF = 900


def dash_problems(cfg):
    problems = []
    if not (cfg.dash_url and cfg.dash_push_key):
        problems.append("set both REBEKAH_DASH_URL and REBEKAH_DASH_PUSH_KEY, or neither")
        return problems
    url = urllib.parse.urlsplit(cfg.dash_url)
    host = (url.hostname or "").lower()
    if url.scheme == "http" and host in LOOPBACK:
        pass  # a local Dash (wrangler dev) or a test double
    elif url.scheme != "https":
        problems.append("REBEKAH_DASH_URL must be https:// (plain http only to loopback)")
    if not host or url.username or url.password or url.query or url.fragment:
        problems.append("REBEKAH_DASH_URL must be a plain origin, e.g. https://dash.ragbaz.cc")
    if not DASH_PUSH_KEY_RE.match(cfg.dash_push_key):
        problems.append("REBEKAH_DASH_PUSH_KEY is not a Dash push key (rbkp_...)")
    if cfg.dash_poll < 10:
        problems.append("REBEKAH_DASH_POLL_INTERVAL must be an integer >= 10 (seconds)")
    if cfg.dash_heartbeat < max(cfg.dash_poll, 10):
        problems.append("REBEKAH_DASH_PUSH_INTERVAL must be an integer >= the poll interval")
    return problems


class DashPusher:
    """Push this instance's /api/v1 views to Dash; see the section comment."""

    def __init__(self, cfg, auth, clock=time.monotonic, log=None):
        self.cfg = cfg
        self.auth = auth
        self.clock = clock
        self.log = log or (lambda msg: sys.stderr.write("rebekah-gateway: dash: " + msg + "\n"))
        url = urllib.parse.urlsplit(cfg.dash_url)
        self._https = url.scheme == "https"
        self._host = url.hostname
        self._port = url.port or (443 if self._https else 80)
        self._prefix = url.path.rstrip("/")
        self.last_digest = None
        self.last_push = None
        self.refresh = False
        self.failures = 0
        self._state = None  # last logged state, so the log shows transitions only

    def views(self):
        kanban = fetch_kanban(self.cfg)
        return {
            "system": v1_system(self.cfg, self.auth),
            "attention": v1_attention(kanban),
            "change-sets": v1_changesets(kanban),
        }

    @staticmethod
    def digest(views):
        # What changed, ignoring the timestamps every build carries.
        stable = {k: {f: v for f, v in view.items() if f != "observed_at"} for k, view in views.items()}
        return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()

    def _note(self, state, msg):
        if state != self._state:
            self._state = state
            self.log(msg)

    def _call(self, method, path, body=None):
        """(status, parsed JSON or None, Retry-After seconds or None). Raises OSError."""
        headers = {
            "Authorization": "Bearer " + self.cfg.dash_push_key,
            "Accept": "application/json",
            "User-Agent": "rebekah-gateway/dash-push",
        }
        payload = None
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        if self._https:
            conn = http.client.HTTPSConnection(
                self._host, self._port, timeout=DASH_TIMEOUT,
                context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(self._host, self._port, timeout=DASH_TIMEOUT)
        try:
            # http.client never follows redirects; a 3xx is just a failure here.
            conn.request(method, self._prefix + path, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read(DASH_MAX_RESPONSE + 1)
            retry = resp.getheader("Retry-After")
        finally:
            conn.close()
        data = None
        if len(raw) <= DASH_MAX_RESPONSE:
            try:
                data = json.loads(raw)
            except ValueError:
                data = None
        try:
            retry = int(retry) if retry is not None else None
        except ValueError:
            retry = None
        return resp.status, data if isinstance(data, dict) else None, retry

    def _failed(self, why):
        self.failures += 1
        self._note("failing", "cannot reach Dash (%s); retrying with backoff" % why)
        return min(self.cfg.dash_poll * (2 ** min(self.failures, 10)), DASH_MAX_BACKOFF)

    def step(self):
        """One round: push if due, else poll for a refresh request.

        Returns the seconds to wait before the next round.
        """
        now = self.clock()
        views = self.views()
        digest = self.digest(views)
        due = (self.refresh or digest != self.last_digest or self.last_push is None
               or now - self.last_push >= self.cfg.dash_heartbeat)
        try:
            if due:
                status, data, retry = self._call(
                    "POST", "/api/connector/push", {"schema": DASH_ENVELOPE, "views": views})
            else:
                status, data, retry = self._call("GET", "/api/connector/pending")
        except (OSError, http.client.HTTPException) as exc:
            return self._failed(type(exc).__name__)

        if status == 401 or status == 403:
            self.failures += 1
            self._note("rejected", "Dash rejected the push key (HTTP %d); issue a new one in Dash "
                                   "and update REBEKAH_DASH_PUSH_KEY" % status)
            return DASH_MAX_BACKOFF
        if status == 429:
            return max(retry or (data or {}).get("retry_after") or self.cfg.dash_poll, 1)
        if status != 200:
            error = (data or {}).get("error") or "HTTP %d" % status
            return self._failed("Dash answered %s" % error)

        self.failures = 0
        if due:
            self.last_digest = digest
            self.last_push = now
            self.refresh = bool((data or {}).get("refresh_requested"))
            self._note("ok", "pushing to %s" % self.cfg.dash_url)
            return self.cfg.dash_poll
        self.refresh = bool((data or {}).get("refresh_requested"))
        self._note("ok", "pushing to %s" % self.cfg.dash_url)
        # Someone asked for fresh data: push now, not at the next poll.
        return 0 if self.refresh else self.cfg.dash_poll

    def run(self, stop):
        delay = 1  # let the backends come up
        while not stop.wait(delay):
            try:
                delay = self.step()
            except Exception as exc:  # noqa: BLE001 -- never kill the gateway
                delay = self._failed(type(exc).__name__)


def start_dash_pusher(cfg, auth):
    stop = threading.Event()
    pusher = DashPusher(cfg, auth)
    thread = threading.Thread(target=pusher.run, args=(stop,), name="dash-push", daemon=True)
    thread.start()
    return stop


def build_server(cfg, auth):
    httpd = ThreadingHTTPServer((cfg.host, cfg.port), make_handler(cfg, auth))
    httpd.daemon_threads = True
    if cfg.tls_enabled:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=cfg.tls_cert, keyfile=cfg.tls_key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    return httpd


def main(argv=None):
    cfg = Config()
    problems = cfg.validate()
    if problems:
        for problem in problems:
            sys.stderr.write("rebekah-gateway: " + problem + "\n")
        return 2
    for name in cfg.unknown_exposed:
        sys.stderr.write("rebekah-gateway: ignoring unknown backend %r\n" % name)

    auth = Authenticator(cfg)
    schemes = []
    if cfg.token_enabled:
        schemes.append("token")
    if auth.password_active:
        schemes.append("password")
    if cfg.oidc_enabled:
        schemes.append("oidc")
    scheme = "https" if cfg.tls_enabled else "http"
    sys.stderr.write(
        "rebekah-gateway: listening on %s://%s:%d (auth=%s, expose=%s)\n"
        % (scheme, cfg.host, cfg.port, ",".join(schemes), ",".join(sorted(cfg.backends)))
    )
    httpd = build_server(cfg, auth)
    if cfg.dash_enabled:
        start_dash_pusher(cfg, auth)
        sys.stderr.write(
            "rebekah-gateway: pushing to %s every %ds (checks every %ds)\n"
            % (cfg.dash_url, cfg.dash_heartbeat, cfg.dash_poll)
        )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
