<p align="center">
  <a href="https://github.com/tabenius/WeftMark">
    <img src="https://raw.githubusercontent.com/tabenius/WeftMark/main/assets/weftmark.svg" alt="WeftMark logo" width="240">
  </a>
</p>

<h1 align="center">Rebekah</h1>

<p align="center">
  A reproducible, self-hosted runtime for governed agentic software work.
</p>

> [!IMPORTANT]
> Rebekah is an executable bootstrap, not yet a production-ready distribution.
> The core image builds and its four supervised services pass an end-to-end
> container smoke test.

## Purpose

Rebekah builds a Docker-compatible OCI image with **Nix**. It combines local
inference, agent execution, engineering provenance, and durable review evidence
without creating another source of truth.

The current image contains:

- **OpenCode server** — provider-neutral interactive agent sessions.
- **Ollama server** — local model inference.
- **[WeftMark](https://github.com/tabenius/WeftMark)** — Change Sets, semantic
  scopes, Git lineage, evidence, handoff, review, and readiness.
- **[Sylvae](https://github.com/tabenius/sylvae)** — portable `SKILL.md`
  execution with durable run evidence.

The image also includes the fail-closed **Ephor/KAGP connector**. Ephor is the
**Konsonans AI Governance Platform (KAGP)**, maintained in
[`tabenius/BAZ.AI-governance`](https://github.com/tabenius/BAZ.AI-governance).

## System boundary

| Component | Authoritative responsibility | Runtime status |
| --- | --- | --- |
| OpenCode | Interactive agent sessions and workspace access | Packaged and supervised |
| Ollama | Local model inference | Packaged and supervised |
| Sylvae | Skill execution and run evidence | Packaged and supervised |
| WeftMark | Engineering provenance, review, evidence policy, and readiness | Packaged and supervised |
| Ephor/KAGP | Governance policy, risk, oversight, and audit-chain records | Fail-closed connector implemented |
| Rebekah | Packaging, isolation, wiring, lifecycle, and integration verification | Implemented bootstrap |

Each runtime service has a distinct UID, a distinct primary group (gid == uid),
and its own state directory, so a `0750` state directory is readable only by the
owning service. The supervisor starts services with `no-new-privileges` and
`--clear-groups`, binds them to container loopback, checks their real health
endpoints, forwards termination, and fails when any required service exits.

## Runtime topology

```mermaid
flowchart TB
    Client["LAN client / GUI / HITL guest"]
    G["rebekah-gateway :8080<br/>token + OIDC auth"]
    R["Rebekah supervisor"]
    O["Ollama :11434"]
    C["OpenCode :4096"]
    S["Sylvae :8971"]
    W["WeftMark :8765"]
    Client -->|"Bearer token / OIDC JWT"| G
    R --> O
    R --> C
    R --> S
    R --> W
    R --> G
    G -.->|allow-listed routes| W
```

The five core services bind **loopback only**. Reaching them from outside the
container goes through the **[API gateway](#api-gateway)** — the one process that
authenticates every request and forwards only allow-listed routes to a loopback
backend. Secrets must be injected at runtime; they must not enter the image or
Nix store.

The OpenCode server is not left open on loopback. The supervisor sets
`OPENCODE_SERVER_PASSWORD` — the value you provide, or a per-boot random one —
and writes it root-only to `/run/rebekah/opencode-password`; OpenCode then
requires HTTP Basic auth (user `opencode`) on every endpoint. Set the variable
explicitly if you need a stable password for a client, or read the generated
one from that file inside the container.

WeftMark operates on the Git repository mounted at `/workspace` and requires a
valid `HEAD`. Persistent service data lives under `/var/lib/rebekah`.

## API gateway

To use Rebekah from the local network, a GUI, or a human-in-the-loop guest, the
supervisor runs **`rebekah-gateway`** (UID `10005`, port `8080`) — the single
authenticated entry point that fronts the loopback services. It offers the two
schemes flagship agent/kanban orchestrators use, and a request is accepted if
**either** enabled scheme authenticates it:

| Scheme | Typical use | Credential | Enable with |
| --- | --- | --- | --- |
| **password** | default browser sign-in (username + password) | `POST /api/login` → opaque bearer session | on by default (`REBEKAH_AUTH_PASSWORD=1`); seeds an `admin` user |
| **token** | internal / LAN / CI / service-to-service | `Authorization: Bearer <token>` (constant-time compared) | `REBEKAH_GATEWAY_TOKEN` (or a per-boot one is minted) |
| **oidc** | external / SSO / human GUI guest | `Authorization: Bearer <JWT>` verified against the issuer's JWKS | `REBEKAH_OIDC_ISSUER` + `REBEKAH_OIDC_AUDIENCE` |

**Signing in (default).** The console shows a username/password form. On first
boot the gateway seeds an `admin` user (`REBEKAH_ADMIN_USER`) into a SQLite DB in
its `0700` state dir, hashing passwords with `scrypt`. Set `REBEKAH_ADMIN_PASSWORD`
for a known password; otherwise a random one is generated and **logged once** at
startup — read it with `docker logs <container> | grep 'seeded admin'`. Login
mints an opaque bearer session (`REBEKAH_SESSION_TTL`, default 12h); `POST
/api/logout` revokes it. Only session-token hashes are stored.

It **fails closed**: it refuses to start if bound beyond loopback without TLS, if
no auth scheme is configured (never an open proxy), or with no exposed backend;
an unexposed/unknown route returns `404`, an unauthenticated request `401`, and
an upstream error `502` — backend details are never leaked. PyJWT is imported
lazily, so the token path (and the whole off-grid story) works on the Python
standard library alone.

### Web console

The gateway serves a built-in, dependency-free **web console** (open `/` or
`/ui/`). It's a single static page — no CDN, no build step, so it works
off-grid — that authenticates with the same token or OIDC bearer and gives a
human-in-the-loop guest one screen over the products:

- a **board** rendering WeftMark's live Kanban projection (`/v0/kanban`) across
  its `backlog → active → review → ready → done` lanes, with readiness, evidence
  counts and attention flags;
- an **OpenCode** panel listing agent sessions (shown when `opencode` is exposed);
- an **Ollama** panel listing local models and pulling new ones (shown when
  `ollama` is exposed);
- **service health** tiles for each exposed backend;
- an **API console** to send an authenticated request to any exposed backend.

The product panels appear only for backends that `/api/info` reports as exposed,
so the console tracks `REBEKAH_GATEWAY_EXPOSE`.

The page itself is public (it's just the app shell); every data call it makes
goes back through the authenticated proxy, and `GET /api/info` (which tells the
page which backends are exposed) requires auth. Static serving is `GET`/`HEAD`
only and path-traversal-safe, under a strict `Content-Security-Policy`
(`connect-src 'self'`). Disable it with `REBEKAH_GATEWAY_UI=0`.

Only **allow-listed** backends are reachable. `REBEKAH_GATEWAY_EXPOSE` defaults
to `weftmark opencode ollama`, so the console's board, OpenCode and Ollama
panels all work out of the box; `sylvae` and the governance-sensitive `ephor`
API stay opt-in. Note that exposing
`opencode` means the gateway credential can drive agents — narrow the list (e.g.
`REBEKAH_GATEWAY_EXPOSE=weftmark`) if that isn't wanted. Each backend is reached
under its own prefix (`/weftmark/…`, `/opencode/…`); the gateway strips the
prefix and re-authenticates to OpenCode itself, so a client never handles the
OpenCode password.

```bash
# Loopback by default. Read the minted token (root-only) and call WeftMark:
tok=$(docker exec <container> cat /run/rebekah/gateway-token)
docker exec <container> curl -s -H "Authorization: Bearer $tok" \
  http://127.0.0.1:8080/weftmark/healthz
```

Expose it on the LAN **only with TLS** (plaintext tokens must never cross the
network). Provide a certificate and, optionally, expose more backends:

```bash
docker run ... -p 8443:8443 \
  -e REBEKAH_GATEWAY_HOST=0.0.0.0 \
  -e REBEKAH_GATEWAY_PORT=8443 \
  -e REBEKAH_GATEWAY_TLS_CERT=/var/lib/rebekah/tls/cert.pem \
  -e REBEKAH_GATEWAY_TLS_KEY=/var/lib/rebekah/tls/key.pem \
  -e REBEKAH_GATEWAY_TOKEN="$MY_TOKEN" \
  -e REBEKAH_GATEWAY_EXPOSE="weftmark opencode" \
  rebekah:latest
```

For SSO / HITL guests, point it at your identity provider instead of (or
alongside) the token:

```bash
  -e REBEKAH_OIDC_ISSUER=https://idp.example.org/ \
  -e REBEKAH_OIDC_AUDIENCE=rebekah \
  # optional: -e REBEKAH_OIDC_REQUIRED_SCOPE=rebekah.use \
  #           -e REBEKAH_OIDC_ALLOWED_SUBJECTS="alice@example.org bob@example.org"
```

Set `REBEKAH_GATEWAY_ENABLE=0` to run without the gateway (loopback services
only).

### Reporting to RAGBAZ Dash

[RAGBAZ Dash](https://dash.ragbaz.cc/) shows an instance's Work, Review and
System views. It gets them in either or both of two ways:

- **Dash pulls**: the gateway has a public https address (a Cloudflare Tunnel,
  say). Link it in Dash with that address and a gateway token.
- **The instance pushes**: no public address needed (behind NAT or a
  firewall). In Dash, an owner or admin of the instance issues a push key;
  set it on the container:

```bash
  -e REBEKAH_DASH_URL=https://dash.ragbaz.cc \
  -e REBEKAH_DASH_PUSH_KEY="rbkp_…" \
  # optional: -e REBEKAH_DASH_POLL_INTERVAL=30   (seconds, >= 10)
  #           -e REBEKAH_DASH_PUSH_INTERVAL=300  (heartbeat, >= the poll interval)
```

The gateway then sends Dash its `/api/v1/system`, `/attention` and
`/change-sets` payloads whenever they change and at least every
`REBEKAH_DASH_PUSH_INTERVAL` seconds, and checks every
`REBEKAH_DASH_POLL_INTERVAL` seconds whether someone clicked **Refresh** in
Dash, pushing at once if so. It only calls out, over verified TLS, to that one
origin; it opens no port and never follows redirects. It backs off when Dash
is unreachable, and says so once in its log if Dash rejects the key (rotate it
in Dash and update the variable). Setting only one of the two variables, a
non-https URL, or a malformed key stops the gateway from starting.

## Correlation spine

A governed unit of work must remain traceable across participating services:

| Authority | Correlated identifier |
| --- | --- |
| WeftMark | Change Set ID — the workflow subject |
| OpenCode | Session ID |
| Sylvae | `run_id` |
| Ephor/KAGP | `entry_id` and `chain_hash` |

Ephor governance decisions enter WeftMark as typed `security:governance` evidence
(record kind `ephor:governance`). Ephor supplies the policy decision
and tamper-evident chain reference; WeftMark remains responsible for deciding
whether a Change Set is `READY`.

See the [bootstrap integration contract](docs/bootstrap-contract.md) for the
correlation envelope, deployment boundary, evidence shape, fail-closed
semantics, and acceptance test.

## Build

Nix with flakes enabled and a Docker-compatible runtime are required.

```bash
nix flake check
nix build .#image
docker load < result
```

### Published image

CI publishes the OCI image to **`ghcr.io/tabenius/rebekah`** (`.github/workflows/publish-image.yml`):
`:latest` on `main`, `:<tag>` on `v*` tags, plus a `sha-<short>` tag. Tagged
builds also attach the image tarball (`rebekah-image.tar.gz`) as a release asset
for offline/air-gapped installs. This is what
[v-BAZ](https://github.com/tabenius/v-BAZ) pulls to run Rebekah as its default AI
orchestration/governance platform (registry pull, ESP-staged tarball fallback).

The flake lock pins nixpkgs, WeftMark, Sylvae, and the BAZ.AI-governance
(Ephor/KAGP) source for reproducible evaluation. If `ephor-src` is not yet in
your local `flake.lock`, run `nix flake lock` (or `nix flake update ephor-src`)
once to record the pin.

`tabenius/BAZ.AI-governance` is private, so fetching `ephor-src` requires a
GitHub token with read access to it. Locally, add it to `~/.config/nix/nix.conf`
as `access-tokens = github.com=<token>` (or `NIX_CONFIG`). In CI, set the
`EPHOR_READ_TOKEN` repository secret to a PAT (or fine-grained token) with read
access to that repo; the workflow passes it to Nix as the github.com access
token. The default `GITHUB_TOKEN` cannot read another private repository, so
without this secret `nix flake check` fails with a `404` on `ephor-src`.

## Run

Create a persistent state volume and mount a real Git repository:

```bash
docker volume create rebekah-state

docker run --rm \
  --read-only \
  --cap-drop=ALL \
  --cap-add=CHOWN --cap-add=DAC_OVERRIDE \
  --cap-add=SETUID --cap-add=SETGID --cap-add=KILL \
  --security-opt=no-new-privileges \
  --tmpfs /run/rebekah:rw,noexec,nosuid,size=16m \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m,mode=1777 \
  --mount type=volume,src=rebekah-state,dst=/var/lib/rebekah \
  --mount type=bind,src="$PWD",dst=/workspace \
  -e REBEKAH_CHANGE_SET_ID=your-change-set-id \
  rebekah:latest
```

The entrypoint initializes volume ownership for the five isolated service UIDs.
The mounted workspace is the only Git safe-directory exception configured by
the runtime.

Run with least privilege. The supervisor needs only five Linux capabilities —
`CHOWN` (set up state directories), `SETUID`/`SETGID` (launch each service as
its own uid), `KILL` (forward termination to the cross-uid children), and
`DAC_OVERRIDE` (so a root `docker exec` of `rebekah-govern` can write the
weftmark-owned ledger) — so the example drops everything else Docker grants root
by default and adds `no-new-privileges`. The container's smoke test runs under
exactly this set.

The image ships **no model weights**, but it does ship a consistent local-model
contract. `REBEKAH_OLLAMA_MODEL` defaults to `qwen2.5:0.5b`; on first boot
Rebekah writes an OpenCode configuration that uses that model through the local
Ollama API, and Sylvae receives the same default. Existing OpenCode configuration
is never overwritten, and online providers remain available when their runtime
credentials are supplied.

Pull the model into the persistent state volume before requesting inference; it
is retained across runs. v-BAZ does this automatically: it uses the ESP-cached
model off-grid, or pulls the configured model when connectivity is available.
For a standalone Rebekah container:

```bash
docker exec <container-name> ollama pull llama3.2
```

Useful diagnostics:

```bash
docker run --rm --mount type=bind,src="$PWD",dst=/workspace rebekah:latest doctor
docker exec <container-name> rebekah-health
```

## Ephor/KAGP governance

Rebekah packages and supervises KAGP's Rust `governance-http` bridge on loopback
by default. Evaluate a Change Set against that in-container service:

```bash
# EPHOR_URL already defaults to http://127.0.0.1:9800 in the image
export REBEKAH_CHANGE_SET_ID=cs-example
export REBEKAH_SYLVAE_RUN_ID=run-example
rebekah-ephor evaluate
```

To use an external KAGP deployment instead, set `REBEKAH_EPHOR_ENABLE=0` and
provide `EPHOR_URL`; the supervisor then skips the internal bridge and its health
probe. Add `ephor` to `REBEKAH_GATEWAY_EXPOSE` only when authenticated remote
access to the governance API is intentionally required.

For the Cloudflare Worker API, also set `EPHOR_API_STYLE=worker`. An optional
`EPHOR_AUTH_TOKEN` is sent as a bearer token. Successful evaluations produce
`cc.ragbaz.rebekah.governance-evidence.v0` JSON with the Ephor entry ID and a
normalized `sha256:` chain hash. Every denial, hold, transport error, malformed
response, invalid hash, or missing configuration exits nonzero and records a
non-passed evidence state.

## Verify

After loading the image, run the same immutable-root integration test used by
CI:

```bash
bash tests/smoke.sh
```

The test creates a disposable Git repository, starts the container with a
read-only root filesystem, and requires successful health responses from all
five services, including a real Ephor capture/finalize governance cycle.

## Repository layout

- `flake.nix` exposes the OCI image and package checks.
- `flake.lock` pins every source input.
- `nix/image.nix` defines the image, identities, volumes, health check, and
  OCI metadata.
- `nix/entrypoint.sh` implements supervision, diagnostics, health checks, and
  shutdown.
- `nix/gateway.py` is the authenticated API gateway (`rebekah-gateway`).
- `nix/ui/` is the gateway's built-in web console (static, served same-origin).
- `nix/packages/` packages WeftMark, Sylvae, and Ephor/KAGP from pinned sources.
- `tests/smoke.sh` verifies the loaded image through Docker.
- `tests/gateway.sh` (+ `tests/gateway-oidc.py`) unit-tests the gateway's auth,
  routing, and fail-closed guards.
- `tests/dash-push.py` tests the push client for RAGBAZ Dash against mock
  WeftMark and Dash servers.
- `docs/bootstrap-contract.md` defines integration semantics and acceptance
  criteria.
- `docs/ephor-governance-worker.md` references the merged Ephor governance
  Worker (endpoints, bindings, audit chain) the connector calls into.
- `.github/workflows/ci.yml` checks the connector and gateway auth, builds and loads the image, and runs the service smoke test.

## Current status

**Core runtime complete.** The reproducible image packages and supervises
OpenCode, Ollama, Sylvae, WeftMark, and KAGP's Ephor governance bridge. The image
also provides `rebekah-ephor`, which calls the bridge's capture/finalize boundary
and emits normalized typed evidence.
CI validates approval plus fail-closed denial, hold, malformed-response,
unavailable-service, and missing-configuration paths.

**Governance attachment complete.** `rebekah-govern` attaches the connector
output through WeftMark's real evidence interface as dedicated
`security:governance` evidence, requires it for the review decision, and the
smoke test proves the readiness effect. See
[docs/ephor-governance-worker.md](docs/ephor-governance-worker.md) for the
merged reference implementation the connector calls into.
