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
import socket
import stat
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
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

# Where the opt-in Ephor integration stands (entrypoint.sh ephor_state).
EPHOR_STATES = ("absent", "disabled", "external", "enabled")
# Ephor's MCP gates (agent-proxy in front of a service's MCP tools), by name.
GATE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


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
        state_dir = env.get("REBEKAH_STATE_DIR", "/var/lib/rebekah").strip()
        self.nostoi_bin = env.get("REBEKAH_NOSTOI_BIN", "nostoi").strip() or "nostoi"
        self.nostoi_ledger = env.get(
            "REBEKAH_NOSTOI_LEDGER", state_dir + "/gateway/nostoi.jsonl"
        ).strip()
        self.runtime_snapshot = env.get("REBEKAH_RUNTIME_STATUS", env.get("RAGBAZ_RUNTIME_STATUS", "")).strip()
        # Private, operator-selected read-only artifact directory; never scan the host.
        self.daily_agenda_dir = env.get("REBEKAH_DAILY_AGENDA_DIR", "").strip()

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

        # Ephor is opt-in: the supervisor says where it stands (see
        # ephor_state in entrypoint.sh). Only a bridge supervised here is a
        # backend; otherwise there is nothing on EPHOR_PORT to reach.
        state = env.get("REBEKAH_EPHOR_STATE", "absent").strip()
        self.ephor_state = state if state in EPHOR_STATES else "absent"

        catalogue = {
            "weftmark": (wm_host, wm_port, None),
            "opencode": (oc_host, oc_port,
                         ("opencode", oc_pw) if oc_pw else None),
            "sylvae": (sy_host, sy_port, None),
            "ollama": (ol_host, ol_port, None),
        }
        if self.ephor_state == "enabled":
            catalogue["ephor"] = (ep_host, ep_port, None)
        self.backends = {name: catalogue[name] for name in exposed if name in catalogue}
        # Every loopback backend, exposed or not, for the gateway's own calls
        # (the oversight view and applying decisions from Dash). Exposure only
        # decides what a client may reach through the proxy.
        self.internal = catalogue

        # Human-in-the-loop decisions from Dash, applied locally. Each is off
        # unless its credential is set; the supervisor mints both per boot and
        # gives them to the gateway (and the one backend that checks each) only.
        self.ephor_oversight_token = (
            env.get("EPHOR_OVERSIGHT_TOKEN", "").strip()
            if self.ephor_state == "enabled" else "")
        # Ephor's MCP gates hold agents' tool calls too; the supervisor names
        # each one's reviewer endpoint ("weftmark=127.0.0.1:9102 ..."), always
        # loopback, and gives the gateway their shared reviewer token.
        self.mcp_gates = {}
        if self.ephor_state == "enabled":
            for item in env.get("REBEKAH_MCP_GATES", "").split():
                name, _, hostport = item.partition("=")
                host, _, port = hostport.rpartition(":")
                if GATE_NAME_RE.match(name) and host in LOOPBACK and port.isdigit():
                    self.mcp_gates[name] = (host, int(port))
        self.mcp_gate_token = (
            env.get("REBEKAH_MCP_GATE_OVERSIGHT_TOKEN", "").strip() if self.mcp_gates else "")
        self.weftmark_write_token = env.get("REBEKAH_WEFTMARK_WRITE_TOKEN", "").strip()
        self.unknown_exposed = [
            name for name in exposed if name not in catalogue and name != "ephor"]
        self.ephor_not_enabled = "ephor" in exposed and "ephor" not in catalogue

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
        if self.daily_agenda_dir and not os.path.isabs(self.daily_agenda_dir):
            problems.append("REBEKAH_DAILY_AGENDA_DIR must be an absolute directory path")
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


def internal_json(cfg, name, method, path, body=None, bearer=None, headers=None):
    """A JSON call to a loopback backend from the internal catalogue.

    Returns (status, parsed JSON or None); (None, None) when unreachable. Never
    raises and never follows redirects; bearer tokens travel only in the
    Authorization header.
    """
    backend = cfg.internal.get(name)
    if backend is None:
        return None, None
    host, port, _ = backend
    send = {"Host": "%s:%d" % (host, port), "Accept": "application/json"}
    send.update(headers or {})
    if bearer:
        send["Authorization"] = "Bearer " + bearer
    payload = None
    if body is not None:
        payload = json.dumps(body, separators=(",", ":")).encode()
        send["Content-Type"] = "application/json"
    conn = None
    try:
        conn = http.client.HTTPConnection(host, port, timeout=min(cfg.timeout, 10))
        conn.request(method, path, body=payload, headers=send)
        resp = conn.getresponse()
        raw = resp.read(1024 * 1024 + 1)
        data = None
        if len(raw) <= 1024 * 1024:
            try:
                data = json.loads(raw)
            except ValueError:
                data = None
        return resp.status, data
    except Exception:  # noqa: BLE001 -- degrade, never leak
        return None, None
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


def runtime_snapshot(path):
    """Bounded host projection; never acquire a Podman socket in the gateway."""
    base = {"schema": "ragbaz.runtime-status.v1", "status": "not-configured",
            "summary": ["Host runtime inventory not configured; Minotaur state is unknown."]}
    if not path:
        return base
    try:
        with open(path, "rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("oversized snapshot")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("schema") != base["schema"]:
            raise ValueError("unknown schema")
        observed = datetime.fromisoformat(data["observed_at"].replace("Z", "+00:00"))
        if observed.tzinfo is None or not isinstance(data.get("components"), dict):
            raise ValueError("invalid observation")
        lines = data.get("summary")
        if not isinstance(lines, list) or len(lines) > 1024 or not all(isinstance(s, str) for s in lines):
            raise ValueError("invalid summary")
        age = (datetime.now(timezone.utc) - observed).total_seconds()
        state = "current" if 0 <= age <= 120 else "stale" if age > 120 else "clock-skew"
        return {**data, "status": state, "age_seconds": age}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {**base, "status": "unavailable", "summary": ["Host runtime inventory unavailable or invalid."]}


AGENDA_MAX_FILE = 1024 * 1024
AGENDA_MAX_VIEW = 384 * 1024
AGENDA_MAX_AGE = 24 * 3600


def _agenda_file(directory, name, maximum=AGENDA_MAX_FILE):
    # Fixed filenames only. Refuse symlinks/FIFOs/devices and bound bytes before JSON parsing.
    fd = os.open(os.path.join(directory, name), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("not a regular file")
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("oversized agenda file")
    return raw


def _agenda_text(value, maximum=12000):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("invalid agenda text")
    return value[:maximum]


def _agenda_time(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("invalid agenda time")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("agenda time requires zone")
    return stamp


def v1_daily_agenda(cfg):
    """Project the generated edition, never its HTML or workstation file links.

    The envelope observation is this read; source_observed_at remains the original
    Git/Frog evidence time. Reads/connector refresh do not run a collector.
    """
    base = {"schema": "rebekah.daily-agenda.v1", "source": "rebekah-gateway",
            "observed_at": _iso_now(), "status": "not-configured", "stale": True,
            "source_observed_at": None, "generated_at": None, "generation_id": None,
            "window_start": None, "window_end": None, "ranking": None,
            "collection_error_count": 0, "items": []}
    directory = cfg.daily_agenda_dir
    if not directory:
        return base
    try:
        manifest = json.loads(_agenda_file(directory, "manifest.json", 64 * 1024))
        if manifest.get("schema") != "ragbaz.daily-agenda-manifest.v1":
            raise ValueError("unknown agenda manifest")
        documents = {}
        for name in ("snapshot.json", "editorial.json"):
            raw = _agenda_file(directory, name)
            record = manifest["files"][name]
            if record["bytes"] != len(raw) or record["sha256"] != hashlib.sha256(raw).hexdigest():
                raise ValueError("agenda generation changed or failed integrity check")
            documents[name] = json.loads(raw)
        snapshot, editorial = documents["snapshot.json"], documents["editorial.json"]
        if snapshot.get("schema") != "ragbaz.daily-agenda.v1" or editorial.get("schema") != "ragbaz.daily-agenda-editorial.v1":
            raise ValueError("unknown agenda source schema")
        observed = _agenda_time(snapshot["observed_at"])
        if manifest["observed_at"] != snapshot["observed_at"]:
            raise ValueError("mixed agenda generation")
        generated_at = manifest.get("generated_at", manifest["observed_at"])
        generated = _agenda_time(generated_at)
        start, end = _agenda_time(snapshot["window_start"]), _agenda_time(snapshot["window_end"])
        now = datetime.now(timezone.utc)
        if not start < end <= observed <= generated or max((observed - now).total_seconds(), (generated - now).total_seconds()) > 300:
            raise ValueError("invalid agenda observation window")
        tasks = snapshot["tasks"]
        if not isinstance(tasks, list) or len(tasks) > 1000:
            raise ValueError("invalid agenda tasks")
        byslug = {t["slug"]: t for t in tasks}
        skipped = {t["slug"]: t["reason"] for t in snapshot["schedule"].get("skipped", [])}
        eligible = {t["slug"] for t in snapshot["schedule"].get("tasks", [])}
        items = []
        for project in snapshot["projects"][:10]:
            name = _agenda_text(project["name"], 100)
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name):
                raise ValueError("invalid project id")
            config = editorial["projects"].get(name, {})
            own = [t for t in tasks if t.get("repo_path") == project["path"]]
            own += [byslug[s] for s in config.get("related", []) if s in byslug and byslug[s] not in own]
            own.sort(key=lambda t: (t["slug"] != config.get("preferred"), t["workflow_status"] != "in_progress", t["priority"], t["slug"]))
            projected_tasks = []
            for task in own[:40]:
                slug = _agenda_text(task["slug"], 120)
                scope = _agenda_text(task.get("what_text"))
                scheduler = ("Eligible at collection time; recheck before claiming" if slug in eligible
                             else "At collection time: " + skipped.get(slug, "not returned as eligible; inspect native state"))
                projected_tasks.append({"id": slug, "title": _agenda_text(task["title"], 300),
                    "status": _agenda_text(task["workflow_status"], 40), "priority": _agenda_text(task["priority"], 20),
                    "owner": _agenda_text(task.get("assigned_agent"), 120), "updated_at": task.get("updated_at"),
                    "why": _agenda_text(task.get("why")), "scope": scope,
                    "scope_truncated": len(task.get("what_text") or "") > len(scope),
                    "steps": [_agenda_text(s, 2000) for s in editorial.get("task_steps", {}).get(slug, [])[:10]],
                    "dependencies": [{"id": _agenda_text(d["depends_on_slug"], 120),
                                      "relation": _agenda_text(d["relation"], 40),
                                      "status": _agenda_text(snapshot["task_statuses"].get(d["depends_on_slug"], "unknown"), 40)}
                                     for d in task.get("dependencies", [])[:40]],
                    "scheduler_note": scheduler})
            concepts = []
            for key in config.get("concepts", [])[:8]:
                concept = editorial["concepts"][key]
                source = _agenda_text(concept.get("source"), 2000)
                url = urllib.parse.urlsplit(source)
                safe_url = source if url.scheme == "https" and url.hostname and not url.username and not url.password else None
                concepts.append({"id": _agenda_text(key, 80), "title": _agenda_text(concept["title"], 200),
                    "brief": _agenda_text(concept["brief"], 1000), "description": _agenda_text(concept["full"], 6000),
                    "source_url": safe_url})
            count = project["commit_count"]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0 or count != len(project["commits"]):
                raise ValueError("invalid commit count")
            preferred_active = any(t["slug"] == config.get("preferred") for t in own)
            next_move = config.get("next") if preferred_active or not own else own[0]["title"]
            items.append({"id": name, "title": name, "commit_count": count, "local_path_count": len(project["dirty"]),
                "head": _agenda_text(project["head"], 64), "last_commit_at": project.get("last_commit_at") or None,
                "recent_work": _agenda_text(project["commits"][0]["subject"] if count else "No commits in the window", 400),
                "next_move": _agenda_text(next_move or "Inspect the project plan and record a bounded next task", 400),
                "note": _agenda_text(config.get("fallback") if not own else "", 3000),
                "tasks_truncated": len(own) > 40, "tasks": projected_tasks, "concepts": concepts})
        result = {**base, "status": "ready", "stale": (now - observed).total_seconds() > AGENDA_MAX_AGE,
                  "source_observed_at": snapshot["observed_at"], "generated_at": generated_at,
                  "generation_id": _agenda_text(manifest["id"], 120), "window_start": snapshot["window_start"],
                  "window_end": snapshot["window_end"], "ranking": _agenda_text(snapshot["ranking"], 400),
                  "collection_error_count": len(snapshot.get("errors", [])), "items": items}
        if len(json.dumps(result).encode()) > AGENDA_MAX_VIEW:
            raise ValueError("oversized agenda projection")
        return result
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError):
        # Never return a half-generation or reveal host paths/parser details.
        return {**base, "status": "unavailable"}


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
        "runtime": runtime_snapshot(cfg.runtime_snapshot),
        "urls": {"console": "/" if cfg.ui_enabled else None,
                 "backends": {nm: "/" + nm + "/" for nm in sorted(cfg.backends)}},
        "workflows": {
            "native_review": "weftmark" in cfg.backends,
            "hitl": {"surface": "dash", "configured": cfg.dash_enabled,
                     "governed_holds": cfg.ephor_state == "enabled"},
            "attestation": "human-only Nostoi CLI; verify with allowed signers and pinned fingerprint",
            "web": {"local": cfg.ui_enabled, "host_status": "live read of mounted snapshot",
                    "dash": "outbound projection" if cfg.dash_enabled else "not-configured",
                    "reads_logged": False, "mutation_audit": "Nostoi intent and result required",
                    "delivery_receipt": False},
        },
        # Ephor is optional: present where it stands, never as a failed
        # baseline service (plan §1, §6.8).
        "ephor": {"state": cfg.ephor_state, "exposed": "ephor" in cfg.backends,
                  "optional": True},
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


def _text(value, limit):
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return text[:limit]


def _iso_ms(ms):
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def gate_rpc(cfg, name, method, params=None):
    """One JSON-RPC call to an MCP gate's reviewer endpoint.

    Returns the parsed reply (with "result" or "error"), or None when the gate
    is unknown or unreachable. The token travels in params, as agent-proxy
    expects; it is never logged.
    """
    gate = cfg.mcp_gates.get(name)
    if gate is None or not cfg.mcp_gate_token:
        return None
    body = dict(params or {})
    body["token"] = cfg.mcp_gate_token
    line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": body},
                      separators=(",", ":")) + "\n"
    try:
        with socket.create_connection(gate, timeout=min(cfg.timeout, 10)) as conn:
            conn.sendall(line.encode())
            raw = conn.makefile("rb").readline(1024 * 1024 + 1)
        reply = json.loads(raw) if len(raw) <= 1024 * 1024 else None
    except (OSError, ValueError):
        return None
    return reply if isinstance(reply, dict) else None


def _argument_strings(arguments):
    """A held call's arguments as the key=value strings Ephor's bridge uses."""
    if isinstance(arguments, dict):
        return ["%s=%s" % (k, v if isinstance(v, str) else json.dumps(v, sort_keys=True))
                for k, v in sorted(arguments.items())]
    if isinstance(arguments, list):
        return arguments
    return [] if arguments is None else [arguments]


def _hash64(value):
    """A 64-character hex chain hash, or None (what Dash accepts)."""
    if isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value):
        return value
    return None


def _hold_item(held, source, deadline_ms, evidence=None):
    evidence = evidence if isinstance(evidence, dict) else {}
    policy = evidence.get("policy") if isinstance(evidence.get("policy"), dict) else {}
    flags = held.get("risk_flags")
    if flags is None:
        flags = [v.get("rule_id") for v in (policy.get("violations") or []) if isinstance(v, dict)]
    # The chain the hold sits on. Both surfaces report it: the bridge directly,
    # the MCP gate through its evidence (the capture entry's hash). Absent when
    # an older surface omits it; Dash then assumes the chain is intact.
    entry_hash = _hash64(held.get("entry_hash")) or _hash64(evidence.get("hash"))
    chain_valid = held.get("chain_valid")
    if not isinstance(chain_valid, bool):
        chain_valid = entry_hash is not None if (held.get("entry_hash") is not None or evidence.get("hash") is not None) else None
    return {
        "request_id": _text(held.get("request_id"), 80),
        "status": _text(held.get("status"), 40),
        "agent_class": _text(held.get("agent_class") or evidence.get("agent_class"), 80),
        "action": _text(held.get("action"), 200),
        "arguments": [_text(arg, 200) for arg in _argument_strings(held.get("arguments"))[:10]],
        "risk_level": _text(held.get("risk_level") or policy.get("risk_level"), 40),
        "risk_flags": [_text(flag, 80) for flag in (flags or [])[:10]],
        "deadline": _iso_ms(deadline_ms),
        "entry_id": _text(held.get("entry_id") or evidence.get("entry_id"), 80),
        # The session that raised the hold, and its chain entry.
        "session_id": _text(held.get("session_id") or evidence.get("session_id"), 80),
        "entry_hash": entry_hash,
        "chain_valid": chain_valid,
        # Which Ephor surface holds it: "bridge", or the MCP gate in front of
        # a service's tools ("mcp:weftmark"). Decisions go back to the same one.
        "source": source,
    }


def v1_oversight(cfg):
    """Actions Ephor holds for a human decision (pending only).

    What a reviewer needs to decide: what is held, why (risk), until when, and
    the action's arguments, each bounded. Read from the local Ephor whether or
    not it is exposed through the gateway. Without an enabled Ephor there is
    nothing to hold: an empty, current view that says so, never a stale one.
    """
    enabled = cfg.ephor_state == "enabled"
    stale = False
    items = []
    if enabled:
        status, data = internal_json(
            cfg, "ephor", "POST", "/oversight/list", {"status": "pending", "limit": 100})
        if status == 200 and isinstance(data, dict):
            for held in (data.get("items") or [])[:100]:
                if isinstance(held, dict):
                    items.append(_hold_item(held, "bridge", held.get("deadline_ms")))
        else:
            stale = True
        for name in sorted(cfg.mcp_gates):
            reply = gate_rpc(cfg, name, "oversight.list")
            actions = ((reply or {}).get("result") or {}).get("actions")
            if not isinstance(actions, list):
                stale = True  # one surface down: what it holds is unknown
                continue
            for held in actions[:100]:
                if isinstance(held, dict) and held.get("status") == "pending":
                    items.append(_hold_item(held, "mcp:" + name, held.get("deadline_unix_ms"),
                                            held.get("evidence")))
        items = items[:100]
    return {
        "schema": "rebekah.oversight.v1",
        "observed_at": _iso_now(),
        # The gateway builds this envelope, like every /api/v1 view (Dash
        # checks it); the holds themselves come from the local Ephor, when
        # there is one (it is opt-in).
        "source": "rebekah-gateway",
        "origin": "ephor",
        "enabled": enabled,
        "ephor": cfg.ephor_state,
        "count": len(items),
        "stale": stale,
        # Whether decisions from Dash can be applied here at all.
        "decisions": {
            "oversight": bool(cfg.ephor_oversight_token),
            "review": bool(cfg.weftmark_write_token),
        },
        "items": items,
    }


# --- decisions from Dash (human in the loop) --------------------------------
# Dash hands queued decisions to this instance on its poll/push responses; the
# gateway applies each to the local Ephor or WeftMark and reports the result.
# The instance stays the authority: Ephor and WeftMark enforce their own rules,
# and the gateway refuses what they would not see, e.g. approving a hold that
# is no longer pending or is past its deadline.

COMMAND_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
CHANGE_SET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
QUEUE_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
ACTOR_RE = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}$")
COMMAND_KINDS = ("oversight.decide", "oversight.defer", "oversight.escalate", "review.record")


def _reason(value, limit=2000):
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def _nostoi_event(cfg, *, kind, actor, subject, body):
    """Append one bounded gateway event; any failure blocks the protected call."""
    writer = getattr(cfg, "nostoi_writer", None)
    if callable(writer):
        return writer(kind=kind, actor=actor, subject=subject, body=body)
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 4096:
        raise ValueError("gateway audit event exceeds 4096 bytes")
    parent = os.path.dirname(cfg.nostoi_ledger) or "."
    os.makedirs(parent, mode=0o700, exist_ok=True)
    try:
        completed = subprocess.run(
            [cfg.nostoi_bin, "append", cfg.nostoi_ledger, "--kind", kind,
             "--actor", actor, "--subject", subject, "--body", encoded, "--json"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Nostoi audit writer unavailable") from error
    if completed.returncode != 0:
        raise RuntimeError("Nostoi refused the audit append")
    try:
        report = json.loads(completed.stdout)
    except ValueError as error:
        raise RuntimeError("Nostoi returned an invalid append receipt") from error
    if not isinstance(report, dict) or not isinstance(report.get("digest"), str):
        raise RuntimeError("Nostoi returned an incomplete append receipt")
    return report


def _decision_intent(cfg, command, *, operation, details):
    cid = command["id"]
    actor = command["requested_by"]
    return _nostoi_event(
        cfg,
        kind="rebekah.decision.requested",
        actor=actor,
        subject=cid,
        body={"command_id": cid, "operation": operation, **details},
    )


def _decision_outcome(cfg, command, intent, result):
    try:
        _nostoi_event(
            cfg,
            kind="rebekah.decision.completed" if result.get("ok") else "rebekah.decision.failed",
            actor=command["requested_by"],
            subject=command["id"],
            body={
                "intent_digest": intent["digest"],
                "ok": bool(result.get("ok")),
                "outcome": _text(result.get("outcome"), 40) or "unknown",
                "error": _text(result.get("error"), 80) or "",
            },
        )
    except Exception:  # the prior durable intent makes the remote result unknown
        result.update(ok=False, outcome="unknown", error="audit_outcome_unavailable")


def _decision_chain(action):
    """The Ephor chain event that recorded a decision, when the reply carries one.

    Both surfaces attach the append receipt to the decided action
    (`decision_evidence`): its entry id and hash. Dash stores them on the
    command so an auditor can walk from its decision row to the chain.
    """
    evidence = action.get("decision_evidence") if isinstance(action, dict) else None
    if not isinstance(evidence, dict):
        return None
    entry_id = evidence.get("entry_id")
    entry_hash = _hash64(evidence.get("hash"))
    if not isinstance(entry_id, str) and entry_hash is None:
        return None
    chain = {}
    if isinstance(entry_id, str):
        chain["entry_id"] = entry_id[:80]
    if entry_hash is not None:
        chain["entry_hash"] = entry_hash
    return chain


def apply_command(cfg, command, now_ms=None):
    """Apply one decision from Dash; returns {id, ok, outcome?, error?, chain?}."""
    cid = command.get("id") if isinstance(command, dict) else None
    if not isinstance(cid, str) or not COMMAND_ID_RE.match(cid):
        return None  # not addressable: nothing to report back to
    result = {"id": cid, "ok": False}
    kind = command.get("kind")
    params = command.get("params") if isinstance(command.get("params"), dict) else {}
    actor = command.get("requested_by")
    if kind not in COMMAND_KINDS:
        result["error"] = "unknown_kind"
        return result
    if not isinstance(actor, str) or not ACTOR_RE.match(actor):
        result["error"] = "bad_actor"
        return result
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms

    if kind == "review.record":
        cs = params.get("change_set_id")
        if not isinstance(cs, str) or not CHANGE_SET_RE.match(cs) or ".." in cs:
            result["error"] = "bad_change_set"
            return result
        if not cfg.weftmark_write_token:
            result["error"] = "review_not_enabled"
            return result
        body = {"review_id": "dash-" + cid, "author_id": actor}
        changes = _reason(params.get("request_changes"))
        if changes:
            body["request_changes"] = changes
        try:
            intent = _decision_intent(
                cfg, command, operation=kind,
                details={"change_set_id": cs,
                         "request_changes_sha256": hashlib.sha256(
                             (changes or "").encode("utf-8")).hexdigest()},
            )
        except Exception:
            result["error"] = "audit_unavailable"
            return result
        status, data = internal_json(
            cfg, "weftmark", "POST",
            "/v0/control/changes/%s/reviews" % urllib.parse.quote(cs, safe=""),
            body, bearer=cfg.weftmark_write_token,
            headers={"Idempotency-Key": "dash-" + cid})
        if status == 200 and isinstance(data, dict):
            decision = ((data.get("control") or {}).get("result") or {}).get("decision") or {}
            result.update(ok=True, outcome=_text(decision.get("outcome"), 40))
        else:
            result["error"] = _text((data or {}).get("error"), 80) or (
                "weftmark_unreachable" if status is None else "weftmark_http_%d" % status)
        _decision_outcome(cfg, command, intent, result)
        return result

    request_id = params.get("request_id")
    if not isinstance(request_id, str) or not REQUEST_ID_RE.match(request_id):
        result["error"] = "bad_request_id"
        return result
    if not cfg.ephor_oversight_token:
        result["error"] = "oversight_not_enabled"
        return result
    rationale = _reason(params.get("rationale"))
    if not rationale:
        result["error"] = "rationale_required"
        return result
    # Find which Ephor surface holds it: the bridge, or one of the MCP gates.
    status, data = internal_json(
        cfg, "ephor", "GET", "/oversight/fetch/%s" % urllib.parse.quote(request_id, safe=""))
    held = (data or {}).get("action") if status == 200 and isinstance(data, dict) else None
    gate = None
    unreachable = status is None
    deadline = held.get("deadline_ms") if isinstance(held, dict) else None
    if not isinstance(held, dict):
        # Look it up in each gate's list: asking a gate for a hold it does not
        # have is logged there as a rejected request.
        for name in sorted(cfg.mcp_gates):
            reply = gate_rpc(cfg, name, "oversight.list")
            actions = ((reply or {}).get("result") or {}).get("actions")
            if not isinstance(actions, list):
                unreachable = True
                continue
            action = next((a for a in actions if isinstance(a, dict)
                           and a.get("request_id") == request_id), None)
            if action is not None:
                held, gate, deadline = action, name, action.get("deadline_unix_ms")
                break
    if not isinstance(held, dict):
        result["error"] = "ephor_unreachable" if unreachable else "hold_not_found"
        return result
    if held.get("status") != "pending":
        result.update(error="not_pending", outcome=_text(held.get("status"), 40))
        return result

    if kind == "oversight.decide":
        decision = params.get("decision")
        if decision not in ("approved", "denied"):
            result["error"] = "bad_decision"
            return result
        if decision == "approved" and isinstance(deadline, int) and now_ms > deadline:
            # Past its deadline a hold defaults to deny; never approve it late.
            result["error"] = "deadline_passed"
            return result
        path = "/oversight/decide"
        body = {"request_id": request_id, "decision": decision,
                "reviewer": actor, "rationale": rationale}
    elif kind == "oversight.defer":
        minutes = params.get("defer_minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 1440:
            result["error"] = "bad_defer_minutes"
            return result
        path = "/oversight/defer"
        body = {"request_id": request_id, "defer_ms": minutes * 60000,
                "rationale": "%s (deferred by %s)" % (rationale, actor)}
    else:
        queue = params.get("target_queue")
        if not isinstance(queue, str) or not QUEUE_RE.match(queue):
            result["error"] = "bad_target_queue"
            return result
        path = "/oversight/escalate"
        body = {"request_id": request_id, "target_queue": queue,
                "rationale": "%s (escalated by %s)" % (rationale, actor)}
    # The Dash command id travels with the decision: as an idempotency key, and
    # as a structured field Ephor can record in its chain (external_ref),
    # rather than buried in the rationale text.
    body = dict(body, external_ref=cid)

    try:
        intent = _decision_intent(
            cfg, command, operation=kind,
            details={
                "request_id": request_id,
                "decision": _text(body.get("decision"), 16) or "",
                "rationale_sha256": hashlib.sha256(rationale.encode("utf-8")).hexdigest(),
                "rationale_chars": len(rationale),
                "target_queue": _text(body.get("target_queue"), 80) or "",
                "defer_ms": body.get("defer_ms", 0),
            },
        )
    except Exception:
        result["error"] = "audit_unavailable"
        return result

    if gate is not None:
        # agent-proxy's reviewer RPC: the same verbs, approve/deny spelled its way.
        if kind == "oversight.decide":
            body = dict(body, decision="approve" if body["decision"] == "approved" else "deny")
        reply = gate_rpc(cfg, gate, path.replace("/oversight/", "oversight."), body)
        action = ((reply or {}).get("result") or {}).get("action")
        if isinstance(action, dict):
            result.update(ok=True, outcome=_text(action.get("status"), 40))
            chain = _decision_chain(action)
            if chain is not None:
                result["chain"] = chain
        elif reply is None:
            result["error"] = "ephor_unreachable"
        elif ((reply.get("error") or {}).get("code")) == -32010:
            result["error"] = "ephor_refused_credential"
        else:
            result["error"] = "gate_refused"
        _decision_outcome(cfg, command, intent, result)
        return result

    status, data = internal_json(cfg, "ephor", "POST", path, body,
                                 bearer=cfg.ephor_oversight_token,
                                 headers={"Idempotency-Key": "dash-" + cid})
    if status == 200 and isinstance(data, dict):
        action = data.get("action")
        result.update(ok=True, outcome=_text(action.get("status") if isinstance(action, dict) else None, 40))
        chain = _decision_chain(action)
        if chain is not None:
            result["chain"] = chain
    elif status in (401, 403):
        result["error"] = "ephor_refused_credential"
    else:
        result["error"] = _text((data or {}).get("error"), 120) or (
            "ephor_unreachable" if status is None else "ephor_http_%d" % status)
    _decision_outcome(cfg, command, intent, result)
    return result


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

        def _serve_v1_oversight(self):
            if self._authed() is None:
                return
            self._json(200, v1_oversight(cfg))

        def _serve_v1_daily_agenda(self):
            if self._authed() is None:
                return
            if self.command not in ("GET", "HEAD"):
                self._fail(405, "method not allowed")
                return
            self._json(200, v1_daily_agenda(cfg))

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
            if raw_path in ("/api/v1/oversight", "/api/v1/oversight/"):
                self._serve_v1_oversight()
                return
            if raw_path in ("/api/v1/daily-agenda", "/api/v1/daily-agenda/"):
                self._serve_v1_daily_agenda()
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
# clicked Refresh in Dash), pushing at once when it is set. Holds have
# deadlines: with Ephor enabled it looks for new ones every DASH_WATCH seconds
# and pushes them at once, and it polls as often as Dash's poll_interval asks
# (down to DASH_MIN_POLL) while a hold waits or a decision is on its way. Nothing listens:
# no inbound port is opened. The push key is sent only in the Authorization
# header to that one origin over verified TLS, and never logged.

DASH_PUSH_KEY_RE = re.compile(r"^rbkp_[0-9a-f]{12}_[A-Za-z0-9_-]{43}$")
DASH_ENVELOPE = "rebekah.dash-push.v1"
DASH_MAX_RESPONSE = 64 * 1024
DASH_MAX_PUSH = 512 * 1024  # Dash connector's envelope byte cap
DASH_TIMEOUT = 10
MAX_COMMANDS = 20  # decisions taken from one Dash response
MAX_REMEMBERED = 500
DASH_MAX_BACKOFF = 900
# Holds have deadlines, so with Ephor enabled the pusher looks at the local
# views this often (loopback only) and pushes a new hold at once, rather than
# at its next poll. Dash may also ask for polls as often as DASH_MIN_POLL
# (its poll_interval) while a hold waits or a decision is on its way.
DASH_WATCH = 5
DASH_MIN_POLL = 5


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
        self.last_call = None  # last push or poll: Dash is called no more often than asked
        self.poll_every = cfg.dash_poll
        self.refresh = False
        self.failures = 0
        self._state = None  # last logged state, so the log shows transitions only
        # Decisions from Dash: results not yet acknowledged, and every result
        # of the last MAX_REMEMBERED commands, so a command Dash sends again
        # (its acknowledgement was lost) is reported, never applied twice.
        self.unreported = {}
        self.applied = {}

    def views(self):
        kanban = fetch_kanban(self.cfg)
        views = {
            "system": v1_system(self.cfg, self.auth),
            "attention": v1_attention(kanban),
            "change-sets": v1_changesets(kanban),
            "oversight": v1_oversight(self.cfg),
            "daily-agenda": v1_daily_agenda(self.cfg),
        }
        # A large optional edition must not prevent the existing work/review
        # views from reaching Dash. Check the aggregate, not just each view.
        body = {"schema": DASH_ENVELOPE, "views": views}
        if len(json.dumps(body, separators=(",", ":")).encode()) > DASH_MAX_PUSH:
            agenda = views["daily-agenda"]
            views["daily-agenda"] = {"schema": agenda["schema"], "source": "rebekah-gateway",
                "observed_at": agenda["observed_at"], "status": "unavailable", "stale": True,
                "source_observed_at": None, "collection_error_count": 0, "items": []}
        return views

    def take_commands(self, data):
        """Apply the decisions in a Dash response once each; True if any ran."""
        commands = (data or {}).get("commands")
        if not isinstance(commands, list):
            return False
        ran = False
        for command in commands[:MAX_COMMANDS]:
            cid = command.get("id") if isinstance(command, dict) else None
            if cid in self.applied:
                self.unreported.setdefault(cid, self.applied[cid])
                continue
            result = apply_command(self.cfg, command)
            if result is None:
                continue
            ran = True
            self.applied[cid] = result
            self.unreported[cid] = result
            self.log("applied %s from Dash: %s" % (
                command.get("kind"), "ok" if result.get("ok") else result.get("error")))
        while len(self.applied) > MAX_REMEMBERED:
            self.applied.pop(next(iter(self.applied)))
        return ran

    def report(self):
        """Send unacknowledged results to Dash; failures are retried next round."""
        if not self.unreported:
            return
        try:
            status, data, _ = self._call(
                "POST", "/api/connector/results", {"results": list(self.unreported.values())})
        except (OSError, http.client.HTTPException):
            return
        if status == 200:
            acked = (data or {}).get("acknowledged")
            for cid in (acked if isinstance(acked, list) else list(self.unreported)):
                self.unreported.pop(cid, None)

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
        if not due and self.last_call is not None and now - self.last_call < self.poll_every:
            return self._wait(now)  # nothing new to say, and not yet time to ask
        self.last_call = now
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
        self.poll_every = self._poll_interval(data)
        # A decision changes what the views show: push them again at once.
        decided = self.take_commands(data)
        self.report()
        if due:
            self.last_digest = digest
            self.last_push = now
            self.refresh = bool((data or {}).get("refresh_requested")) or decided
            self._note("ok", "pushing to %s" % self.cfg.dash_url)
            return 0 if decided else self._wait(now)
        self.refresh = bool((data or {}).get("refresh_requested")) or decided
        self._note("ok", "pushing to %s" % self.cfg.dash_url)
        # Someone asked for fresh data: push now, not at the next poll.
        return 0 if self.refresh else self._wait(now)

    def _poll_interval(self, data):
        """Dash's poll_interval, kept within [DASH_MIN_POLL, the configured poll]."""
        asked = (data or {}).get("poll_interval")
        if isinstance(asked, bool) or not isinstance(asked, int):
            return self.cfg.dash_poll
        return min(max(asked, DASH_MIN_POLL), self.cfg.dash_poll)

    def _wait(self, now):
        """Seconds to the next round: the next poll, or sooner to watch for holds."""
        until_poll = max(self.poll_every - (now - (self.last_call or now)), 1)
        if self.cfg.ephor_state == "enabled":
            return min(DASH_WATCH, until_poll)
        return until_poll

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
    if cfg.ephor_not_enabled:
        sys.stderr.write("rebekah-gateway: not exposing ephor: Ephor is %s here "
                         "(opt in with REBEKAH_EPHOR_ENABLE=1)\n" % cfg.ephor_state)

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
