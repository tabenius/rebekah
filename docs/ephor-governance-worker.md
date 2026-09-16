# Ephor governance Worker (merged reference)

Status: **implemented reference, merged to `tabenius/BAZ.AI-governance` `main`**

This document is a reference for Rebekah's next integration boundary: the
**Ephor/KAGP connector**. It records what the
`claude/responsive-ui-layout-jodwxf` branch actually delivered into the
[`tabenius/BAZ.AI-governance`](https://github.com/tabenius/BAZ.AI-governance)
repository, so Rebekah can target real endpoints, real evidence shapes, and a
real correlation envelope instead of a hypothetical one.

Ephor is the Konsonans AI Governance Platform (KAGP) runtime published as a
Cloudflare Worker. The branch added (1) a working TypeScript CF Worker with a
hash-chained audit trail, policy evaluation, human-in-the-loop holds, and
evidence-pack export, and (2) a WeftMark integration roadmap section on the
public marketing site.

## Source of truth

- Repository: `tabenius/BAZ.AI-governance`
- Branch merged: `claude/responsive-ui-layout-jodwxf`
- Squash-merged to `main` as `30d0bf3` ("Claude/responsive UI layout jodwxf
  (#5)"), later fast-forwarded into the branch tip `f139f9d`
- Worker code under `worker/`, deployment under `.github/workflows/deploy-worker.yml`

The branch is 13 commits spanning the Worker scaffold, CI, and marketing
content. All of it now lives on `main`.

## What was delivered

| Area | Deliverable | Commits |
| --- | --- | --- |
| Governance Worker | TypeScript Cloudflare Worker `ephor-governance` | `8ed7f4c`, `7a49403`, `9f8efd0`, `64869ea`, `95e63e5`, `22c207f`, `e0a4005`, `cc46e44`, `e35d6f8`, `c9bd79b`, `4dc2493` |
| Policy runtime | WASM Component port of the `governance-node` crate (wasm32-wasip2) | `64869ea`, `95e63e5`, `22c207f` |
| Tests | Vitest suite, 37 tests / 4 files | `e0a4005` |
| Data | D1 seed data + `db:reset` scripts + smoke verification | `cc46e44` |
| Deployment | `deploy-worker` GitHub Actions workflow | `4dc2493` |
| Marketing | WeftMark integration roadmap page and CF pipeline diagram | `4ae3211`, `58747e5` |

### The Worker (`worker/`)

The Worker is a single Cloudflare Worker named `ephor-governance`. It exposes
one HTTP surface and maintains a tamper-evident audit chain backed by a
Durable Object.

HTTP surface (from `worker/src/index.ts`):

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/` | service identity, version, and full route list |
| `POST` | `/capture` | intercept an agent action; evaluate policy; start a HITL hold if required |
| `POST` | `/capture/:id/finalize` | record outcome and append the entry to the audit chain |
| `GET` | `/audit` | query audit entries (optional `from`/`to`/`limit`) |
| `GET` | `/audit/:id` | fetch a single entry |
| `GET` | `/audit/chain/verify` | verify hash-chain integrity between two entries |
| `POST` | `/oversight/request-approval` | create an explicit Art 14 HITL hold |
| `POST` | `/oversight/decision` | submit a reviewer decision (bearer-auth required) |
| `GET` | `/oversight/pending` | list pending holds (optional `reviewer` filter) |
| `GET` | `/oversight/:holdId` | fetch a single hold |
| `GET` | `/policy/rules` | list policy rules |
| `POST` | `/policy/rules/:id/enable` / `:/disable` | toggle a rule (bearer-auth; invalidates KV cache) |
| `POST` | `/export/evidence-pack` | bundle audit entries, policy snapshot, HITL decisions, and chain proof; upload to R2 |

### Cloudflare bindings (`worker/wrangler.toml`)

| Binding | Service | Role |
| --- | --- | --- |
| `ephor_audit_db` | D1 (`ephor-audit-db`) | SQLite-at-the-edge: policy rules, agent classes, audit mirror, HITL holds |
| `POLICY_CACHE` | Workers KV | 60-second TTL hot cache for policy evaluation results |
| `AUDIT_CHAIN` | Durable Object (`AuditChain`) | stateful single-writer actor; serialises all appends and owns the canonical hash chain |
| `HITL_QUEUE` | Queues (`ephor-hitl-queue`) | async capture fan-out; DLQ `ephor-hitl-dlq` |
| `EVIDENCE_PACKS` | R2 | content-addressed (SHA-256) evidence-pack storage; immutable once written |
| `* * * * *` cron | every 15 minutes | expires stale HITL holds with the safe-default (deny) transition |
| `AUTH_TOKEN`, `RESEND_API_KEY` | secrets | bearer auth for review/export endpoints; reviewer notifications |

### Audit chain (`worker/src/chain.ts`)

The `AuditChain` Durable Object is the canonical, single-writer record. Each
append computes

```text
sha256(prevHash : entry.id : agentClass : action : occurredAt)
```

and stores `{ id, hash, prevHash, data, appendedAt }`. `GET /audit/chain/verify`
walks a segment from `fromId` to `toId` re-hashing each entry and detecting
tamper. D1 keeps a queryable mirror; the DO is the integrity authority.

### Policy evaluation (`worker/src/policy.ts`)

The hot path checks Workers KV (60s TTL); on miss it loads enabled rules from
D1, evaluates, and writes back. Rule toggling invalidates the cache. The
in-repo JS engine is a placeholder — the real evaluation is the
`governance-node` Rust crate compiled to a WASM Component (`wasm32-wasip2`)
and transpiled to JS with `jco` (`npm run build:wasm`). Agent risk is mapped by
`agent_class` (e.g. `payment-*` → `high`).

### Human-in-the-loop (`worker/src/hitl.ts`)

SLA-bound holds: create → reviewer decision (`approved`/`denied`) → or
automatic `timeout` (safe-default deny) when `created_at + sla_ms` passes,
enforced by the 15-minute cron. Illegal transitions are unrepresentable — only
a `pending` hold can be decided.

### Evidence packs (`worker/src/export.ts`)

`POST /export/evidence-pack` bundles audit entries, a policy snapshot, HITL
decisions, and the chain's last hash + entry count into a `v1.0` JSON pack,
then uploads it to R2 under its SHA-256 digest. The key is the integrity
proof; entries reference only the key.

### Tests and data

- `worker/src/__tests__/*.test.ts` — Vitest, 37 tests across `chain`,
  `export`, `hitl`, and `policy`, using an in-memory DO storage mock so chain
  math is actually exercised.
- `worker/schema.sql` — `policy_rules`, `agent_classes`, `audit_entries`,
  `hitl_holds` with indexes.
- `worker/seed.sql` — three policy rules (rate limit, data exfiltration, auth
  escalation) and six agent classes with SLA defaults.
- `npm run db:reset` = schema init + seed.
- `.github/workflows/deploy-worker.yml` — deploys on push to `main`
  (`worker/**`) or `workflow_dispatch`, using `CLOUDFLARE_API_TOKEN` and
  `CLOUDFLARE_ACCOUNT_ID` secrets.

## Marketing

The branch also added a `/weftmark` route to the marketing site
(`marketing/worker.js`):

- WeftMark + Ephor synergy story and five integration surfaces
  (`4ae3211`);
- a five-step pipeline visual (Capture → Evaluate → Hold → Decide → Chain)
  with infrastructure-agnostic labels (`58747e5`);
- a platform-services grid mapping each architectural role to a Cloudflare
  binding.

## Mapping to the Rebekah correlation spine

The Worker's identifiers line up with the envelope in
[bootstrap-contract.md](bootstrap-contract.md):

| Bootstrap contract | Worker field | Notes |
| --- | --- | --- |
| `ephor_entry_id` | `entry.id` (UUID from `/capture`) | the audit entry id |
| `ephor_chain_hash` | `hash` returned by `/capture/:id/finalize` | the DO chain append hash |
| `opencode_session_id` | `sessionId` on `POST /capture` | currently just stored |

The remaining adapter work for Rebekah/Ephor is to place these worker values
into WeftMark evidence of kind `ephor:governance` (state `passed`) as a
readiness precondition — see the governance-evidence section of the contract.

## Current status

The Worker itself is **implemented and merged**, with local Vitest coverage and
a CI deploy path. It has not yet been exercised end-to-end against a real
agent workload or through the Rebekah image; the WASM policy component is wired
for build but the JS fallback remains the active evaluator until the transpiled
component is committed to a deploy.

The corresponding Rebekah milestone — a working Ephor/KAGP connector inside the
image — remains open. This document is the reference against which that
adapter will be built and smoke-tested.