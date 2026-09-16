# Rebekah bootstrap integration contract

Status: **implemented through the normalized Ephor evidence boundary (v0)**

This document defines the smallest useful Rebekah integration. It is a
composition contract, not a claim that every named component already implements
every field or endpoint described here.

## Product boundary

Rebekah owns:

- reproducible packaging and pinned dependencies;
- service startup, health checks, restart policy, and shutdown ordering;
- explicitly declared local service discovery;
- persistent-volume and secret boundaries;
- correlation identifiers passed between components;
- an end-to-end integration smoke test.

Rebekah does **not** become an alternative authority for agent sessions,
execution evidence, governance decisions, or engineering readiness.

| Component | Authoritative responsibility |
| --- | --- |
| OpenCode | Interactive agent sessions and agent-facing workspace access |
| Ollama | Local model inference |
| Sylvae | Portable skill execution and run evidence |
| WeftMark | Change Sets, semantic scopes, Git lineage, review, handoff, evidence policy, and readiness |
| Ephor/KAGP | Risk classification, policy decisions, human oversight, and tamper-evident governance records |
| Rebekah | Reproducible composition, isolation, wiring, lifecycle, and integration verification |

## Correlation spine

One governed unit of work must remain traceable across the system.

```text
WeftMark Change Set ID
        │
        ├── OpenCode session ID
        ├── Sylvae run_id
        └── Ephor entry_id
                └── chain_hash
```

The WeftMark Change Set ID is the workflow subject. Provider-specific IDs remain
attributes of that subject and must not silently replace it.

The initial correlation envelope is:

```json
{
  "schema": "cc.ragbaz.rebekah.correlation.v0",
  "change_set_id": "cs-...",
  "opencode_session_id": "session-...",
  "sylvae_run_id": "run-...",
  "ephor_entry_id": "entry-...",
  "ephor_chain_hash": "sha256:..."
}
```

Unknown values may be absent while work is in progress. They must not be
represented as successful, approved, or verified.

## Governance evidence

Ephor should be integrated into WeftMark as typed evidence rather than as a
second readiness authority.

A future evidence record should carry, at minimum:

```yaml
schema: cc.ragbaz.rebekah.governance-evidence.v0
kind: ephor:governance
subject: cs-...
state: passed
sylvae_run_id: run-...
ephor_entry_id: entry-...
chain_hash: sha256:...
policy_revision: ...
evaluated_at: ...
```

The exact WeftMark evidence schema remains WeftMark-owned. The example records
the semantic requirements Rebekah must preserve when adapters are implemented.

A WeftMark evidence policy may require `ephor:governance` in `passed` state
before a high-risk Change Set can become `READY`. Ephor supplies the policy
decision and chain reference; WeftMark decides readiness.

## Semantic scope to risk

WeftMark semantic scopes are natural inputs to Ephor policy. For example:

```yaml
scope_risk:
  "contract:*": high
  "contract:tenant-authentication": high
  "contract:payment-routing": high
```

Mapping must be explicit, versioned, and fail closed for scopes whose required
classification cannot be evaluated. A missing policy engine, unavailable
adapter, or unknown scope is not equivalent to approval.

## Deployment boundary

The bootstrap deployment follows these constraints:

- internal services bind to loopback or a private container network;
- remote access is provided only through an authenticated TLS proxy or secure
  tunnel;
- only declared interfaces are exposed;
- secrets are injected at runtime and are not stored in the image or Nix store;
- persistent state is separated by component responsibility;
- provider commands are launched as argument vectors, never through a shell;
- health states distinguish at least `starting`, `ready`, `degraded`, and
  `failed`;
- evidence states distinguish `missing`, `unsupported`, `unavailable`,
  `failed`, and `passed`.

## Implemented Ephor transport

`rebekah-ephor` implements the transport boundary against KAGP's Rust
`governance-http` bridge by default:

- `POST /capture` records the correlated Change Set action;
- `POST /finalize` closes the entry and returns its audit-chain hash;
- `EPHOR_API_STYLE=worker` selects the Cloudflare Worker finalize route;
- `EPHOR_AUTH_TOKEN` is injected only at runtime as a bearer token;
- returned hashes are validated and normalized to `sha256:<hex>`;
- typed evidence is written for both success and failure paths.

Only an affirmative, structurally valid capture followed by a structurally valid
finalization can emit `state: passed`. Denial, human-approval hold, unavailable
transport, malformed JSON, missing identifiers, and invalid hashes emit a
non-passed state and return a nonzero exit status.

The remaining adapter work is WeftMark-owned schema discovery and attachment:
the normalized record must be submitted through WeftMark's real evidence API,
then demonstrated to affect Change Set readiness.

## Bootstrap smoke test

The first milestone is complete when an automated test can demonstrate:

1. Start the pinned Rebekah image.
2. Verify Ollama, OpenCode, Sylvae, and WeftMark readiness independently.
3. Create or load a WeftMark Change Set.
4. Start a correlated OpenCode/Sylvae execution.
5. Preserve the Sylvae `run_id`.
6. Obtain an Ephor governance record for the same subject.
7. Attach the Ephor entry and chain hash as typed WeftMark evidence.
8. Verify that required governance evidence affects readiness.
9. Verify that unavailable or failed governance never becomes `passed`.
10. Shut down cleanly without losing declared persistent state.

The fixture may initially use deterministic test doubles for external inference
or governance transport, but it must exercise the real correlation and evidence
boundaries.

## Planned repository slices

```text
.
├── flake.nix
├── flake.lock
├── README.md
├── docs/
│   └── bootstrap-contract.md
├── nix/
│   ├── image.nix
│   ├── services/
│   └── checks/
└── tests/
    └── smoke/
```

The flake, Nix-built image, isolated service identities, core service smoke
check, and fail-closed Ephor transport now exist. The next slice attaches the
normalized governance record through WeftMark's real evidence interface and
verifies its readiness effect.
