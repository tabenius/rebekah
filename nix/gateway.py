#!/usr/bin/env python3
"""rebekah-gateway: the single authenticated entry point for Rebekah's API.

Rebekah's four services (OpenCode, Ollama, Sylvae, WeftMark) bind loopback only
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
import hmac
import http.client
import os
import ssl
import sys
import threading
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
        exposed = env.get("REBEKAH_GATEWAY_EXPOSE", "weftmark").split()

        oc_host = env.get("OPENCODE_HOST", "127.0.0.1").strip() or "127.0.0.1"
        oc_port = int(env.get("OPENCODE_PORT", "4096"))
        sy_host = env.get("SYLVAE_HOST", "127.0.0.1").strip() or "127.0.0.1"
        sy_port = int(env.get("SYLVAE_PORT", "8971"))
        wm_host = env.get("WEFTMARK_HOST", "127.0.0.1").strip() or "127.0.0.1"
        wm_port = int(env.get("WEFTMARK_PORT", "8765"))
        ol_host, ol_port = _split_hostport(
            env.get("OLLAMA_HOST", "127.0.0.1:11434").strip(), 11434
        )
        oc_pw = env.get("OPENCODE_SERVER_PASSWORD", "")

        catalogue = {
            "weftmark": (wm_host, wm_port, None),
            "opencode": (oc_host, oc_port,
                         ("opencode", oc_pw) if oc_pw else None),
            "sylvae": (sy_host, sy_port, None),
            "ollama": (ol_host, ol_port, None),
        }
        self.backends = {name: catalogue[name] for name in exposed if name in catalogue}
        self.unknown_exposed = [name for name in exposed if name not in catalogue]

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
        if not self.token_enabled and not self.oidc_enabled:
            problems.append(
                "no auth configured: set REBEKAH_GATEWAY_TOKEN (internal) and/or "
                "REBEKAH_OIDC_ISSUER + REBEKAH_OIDC_AUDIENCE (external)"
            )
        if self.tls_cert and not self.tls_key:
            problems.append("REBEKAH_GATEWAY_TLS_CERT set without REBEKAH_GATEWAY_TLS_KEY")
        if self.tls_key and not self.tls_cert:
            problems.append("REBEKAH_GATEWAY_TLS_KEY set without REBEKAH_GATEWAY_TLS_CERT")
        if not self.backends:
            problems.append(
                "no exposed backends: set REBEKAH_GATEWAY_EXPOSE to a subset of "
                "weftmark opencode sylvae ollama"
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


class Authenticator:
    def __init__(self, cfg):
        self.cfg = cfg
        self.oidc = OidcVerifier(cfg) if cfg.oidc_enabled else None

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
        if self.oidc is not None:
            sub = self.oidc.verify(credential)
            if sub is not None:
                return "oidc:" + sub
        return None


# --- proxy -------------------------------------------------------------------

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

        def _handle(self):
            if self.path in ("/healthz", "/healthz/"):
                # Unauthenticated liveness only -- reveals nothing.
                self._fail(200, "ok")
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
    if cfg.oidc_enabled:
        schemes.append("oidc")
    scheme = "https" if cfg.tls_enabled else "http"
    sys.stderr.write(
        "rebekah-gateway: listening on %s://%s:%d (auth=%s, expose=%s)\n"
        % (scheme, cfg.host, cfg.port, ",".join(schemes), ",".join(sorted(cfg.backends)))
    )
    httpd = build_server(cfg, auth)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
