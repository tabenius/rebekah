# CLAUDE.md

Guidance for working in this repository. Rebekah packages a reproducible,
self-hosted OCI image (built with Nix) that supervises four services for
governed agentic software work: **OpenCode**, **Ollama**, **Sylvae**, and
**WeftMark**, plus a fail-closed **Ephor/KAGP** governance connector.

## Layout

- `flake.nix` — inputs and outputs (`packages.<system>.image`, `.weftmark`,
  `.sylvae`; `checks.<system>` = a `shellcheck` derivation plus the weftmark and
  sylvae package builds).
- `nix/image.nix` — the `dockerTools.buildLayeredImage`: contents, `/etc/passwd`
  and `/etc/group`, state-dir modes, entrypoint install, OCI config.
- `nix/entrypoint.sh` — supervisor: `serve` / `doctor` / `health`. Sets up state
  dirs, drops privileges per service, health-checks, forwards termination.
- `nix/ephor-connector.sh` — `rebekah-ephor`: the governance connector (talks to
  Ephor over loopback HTTP, emits normalized evidence, fails closed).
- `nix/govern.sh` — `rebekah-govern`: attaches connector output to WeftMark as
  `governance` evidence and requires it for a review decision.
- `nix/gateway.py` — `rebekah-gateway`: the single authenticated entry point for
  Rebekah's API (password, token and/or OIDC auth), fronting the loopback
  backends. Also serves the built-in web console, `GET /api/info`, and the
  versioned aggregation API (`GET /api/v1/session|system|attention`, and
  `GET /api/v1/change-sets[/{id}]` — the Change Set spine correlating WeftMark
  git/evidence/review/handoff/claims/tasks, with OpenCode/Sylvae link slots) the
  console consumes instead of reverse-engineering each backend — every response
  carries a `schema`, `source`, and `observed_at` (see
  `docs/HUMAN-INTERFACE-PLAN.md` and `docs/UI-INSPIRATION-RAGBAZ-KANBAN.md`).
- `nix/ui/index.html` — the gateway's built-in web console (static, same-origin:
  a "What needs attention?" inbox fed by `/api/v1/attention`, the WeftMark board
  whose change-set cards open a Change Set detail dialog
  (`/api/v1/change-sets/{id}`), four-state service health + an authenticated API
  console).
- `nix/packages/{weftmark,sylvae}.nix` — Python package builds from pinned src.
- `tests/smoke.sh` — end-to-end container test (Docker).
- `tests/ephor-connector.sh` + `tests/ephor-mock.py` — connector unit tests.
- `tests/gateway.sh` + `tests/gateway-oidc.py` — gateway auth/proxy unit test
  (token + fail-closed guards on stdlib; OIDC when PyJWT is present).

## Build & validate

Everything CI runs is reproducible locally with Nix + Docker:

```bash
nix flake check --print-build-logs      # shellcheck + weftmark/sylvae builds+tests
nix build .#image --print-build-logs    # build the OCI image
docker load < result
bash tests/smoke.sh                      # full container smoke test
bash tests/ephor-connector.sh           # connector pass/fail-closed cases
shellcheck --severity=warning nix/*.sh tests/*.sh
```

Fetching the private `ephor-src` input (`github:tabenius/BAZ.AI-governance`)
needs a GitHub token. Locally set `access-tokens = github.com=<token>` in
`nix.conf`; in CI it comes from the `EPHOR_READ_TOKEN` repository secret. To
build without it (e.g. offline), override the unused input:
`nix build .#image --override-input ephor-src path:/tmp/placeholder`.

**Always validate a change in a real container** (build the image, `docker
load`, run `tests/smoke.sh`) before pushing — a runtime regression will not show
up in `nix flake check` alone.

## Runtime architecture

The supervisor (PID-1 via `tini`) runs as root only long enough to create and
`chown` the state dirs, then launches each service under `setpriv` with a
distinct UID/GID and no ambient privileges. All ports are loopback-only.

| Service  | UID:GID       | Port   | State dir                  | Health |
| -------- | ------------- | ------ | -------------------------- | ------ |
| ollama   | 10001:10001   | 11434  | `/var/lib/rebekah/ollama`   | `/api/version` |
| opencode | 10002:10002   | 4096   | `/var/lib/rebekah/opencode` | `/global/health` |
| sylvae   | 10003:10003   | 8971   | `/var/lib/rebekah/sylvae`   | `/` |
| weftmark | 10004:10004   | 8765   | `/var/lib/rebekah/weftmark` | `/healthz` |
| gateway  | 10005:10005   | 8080   | `/var/lib/rebekah/gateway` (`0700`) | `/healthz` |

The four core services bind loopback only. The **gateway** is the exception by
design: it is the one process meant to face the LAN / a GUI / a HITL guest, and
it authenticates every request before forwarding an allow-listed route to a
loopback backend. It still binds loopback by *default*; operators expose it with
`REBEKAH_GATEWAY_HOST` + TLS. Its `0700` state directory holds the SQLite auth DB
(password hashes + sessions), readable only by UID 10005. It needs no extra Linux
capability (it binds a port ≥1024).

WeftMark operates on the Git repo mounted at `/workspace` (requires a valid
`HEAD`). The image ships no model weights — `ollama pull` is required before
inference.

## Security invariants — do not regress

These are enforced by the runtime and, where noted, guarded by tests. Preserve
them in any change:

1. **Per-service isolation.** Each service has its own UID *and* primary group
   (`gid == uid`); state dirs are `0750`, so no service can read another's state
   directory. Services run with `setpriv --clear-groups --no-new-privs`. Do not
   reintroduce a shared group or widen state-dir modes. Guarded by
   `tests/smoke.sh` (opencode must be denied the weftmark/ollama state dirs).
2. **Loopback only.** All services bind `127.0.0.1`. Remote access belongs behind
   an authenticated TLS proxy, never by binding `0.0.0.0`.
3. **OpenCode HTTP is authenticated.** OpenCode's server is not left open on
   loopback (any co-tenant service could otherwise drive it). The supervisor
   sets `OPENCODE_SERVER_PASSWORD` — operator-provided, or a per-boot random one
   — and persists it root-only at `$run_dir/opencode-password` (`0600`), so the
   health check and an operator can read it but the service UIDs cannot.
   OpenCode then requires HTTP Basic auth (user `opencode`) on every endpoint,
   and the health check authenticates. Do not remove the password or widen the
   file's mode.
4. **Fail-closed governance connector** (`nix/ephor-connector.sh`):
   - Only ever reaches an http(s) endpoint — non-`http(s)` `EPHOR_URL` fails
     closed; curl runs with `--proto '=http,https'` and no `-L`.
   - `entry_id` from the Ephor response is constrained to `^[A-Za-z0-9._-]+$`
     before being interpolated into the finalize URL.
   - `EPHOR_AUTH_TOKEN` is passed via a curl config on stdin, never on argv
     (argv is readable via `/proc/<pid>/cmdline`).
   - Any denial, hold, transport error, malformed response, invalid chain hash,
     or missing config exits non-zero with non-passed evidence.
5. **Evidence binds to a clean commit.** WeftMark's `evidence run` requires a
   clean worktree; keep scratch files out of `/workspace`.
6. **Secrets never enter the image or Nix store** — inject at runtime only.
7. **Authenticated, fail-closed API gateway** (`nix/gateway.py`,
   `rebekah-gateway`, UID 10005): the only process allowed to face the network.
   - Three auth schemes, any sufficient: a **SQLite username/password** login
     (the default browser sign-in — `scrypt` hashes + opaque bearer sessions in
     the `0700` state dir; seeds a default `admin`, password provided via
     `REBEKAH_ADMIN_PASSWORD` or generated + logged once); a static bearer
     **token** (internal / LAN / CI; constant-time compared); and **OIDC** JWT
     bearer verified against the issuer's JWKS (external / SSO / HITL). sqlite3 +
     scrypt are stdlib and PyJWT is imported lazily, so the token/password paths
     are stdlib-only. `/api/login` mints a session; `/api/logout` revokes it;
     `/api/auth` (unauthenticated) advertises which methods to offer, revealing
     no secret. Only session-token *hashes* are stored.
   - Fails closed: refuses to start when bound beyond loopback without TLS, when
     no auth scheme is configured (never an open proxy), or with no exposed
     backend; an unexposed/unknown route is `404`, an unauthenticated request is
     `401`, an upstream error is `502` — backend details are never leaked.
   - Only allow-listed backends are reachable (`REBEKAH_GATEWAY_EXPOSE`, default
     `weftmark opencode ollama`; `sylvae` opt-in); the gateway re-authenticates
     to OpenCode itself and never forwards the client's `Authorization` to a
     backend.
   - The per-boot token is persisted root-only (`0600`,
     `/run/rebekah/gateway-token`) and passed to the gateway via env, never
     argv. Guarded by `tests/gateway.sh` and `tests/smoke.sh`. No extra
     capability is required; do not add one.
   - The built-in web console (`nix/ui/`) is served static and same-origin
     (`GET`/`HEAD` only, path-traversal-safe, strict CSP with `connect-src
     'self'`). The page shell is public; every data call it makes is
     authenticated, and `GET /api/info` requires auth. The console renders
     untrusted backend data via `textContent`/`createElement`, never
     `innerHTML` — keep it that way.
8. **Least-privilege run.** The supervisor needs only five Linux capabilities:
   `CHOWN` (set up state dirs), `SETUID`/`SETGID` (launch each service as its own
   uid), `KILL` (forward termination to the cross-uid children), and
   `DAC_OVERRIDE` (a root `docker exec` of `rebekah-govern` writes the
   weftmark-owned ledger). The README run example and `tests/smoke.sh` run with
   `--cap-drop=ALL` plus exactly those five and `--security-opt=no-new-privileges`;
   keep `serve()`'s `chmod` before its `chown` so no `CAP_FOWNER` is needed, and
   don't add capabilities without updating both.

## Conventions

- Shell scripts must pass `shellcheck --severity=warning` (it is a flake check).
- Keep changes minimal and validated; prefer adding a test that guards a fixed
  invariant (see the connector cases and the smoke isolation check).
- Regenerate `flake.lock` with the Nix tooling (`nix flake update <input>`),
  never by hand.
