# Scope: WeftMark changes to make Change Set → OpenCode/Sylvae links real

Rebekah's Change Set detail (`GET /api/v1/change-sets/{id}`, added in the human
-interface first slice) already correlates the WeftMark identifiers WeftMark
surfaces today — git branch/head, evidence counts, review, handoff, claims, and
the linked tasks (`change_set_ids` + `task_change_set_links`). It also declares
two **link slots** that currently read *"not linked yet"*:

- `related.opencode` — the OpenCode session that worked the change set;
- `related.sylvae` — the Sylvae run(s) that produced its evidence.

This document scopes the **WeftMark-side changes** needed to populate those
slots with real, resolvable identifiers, and the small Rebekah-side follow-up
that consumes them. It is a design/scoping record, not an implemented change.

References are to `tabenius/WeftMark` at the rev Rebekah pins today
(`48bdc25`); paths may have moved on newer branches (see *Prior art* below).

## Why this is small: the data already exists

The linkage seam is **already in WeftMark's domain model** — it is only dropped
on the read path.

- `src/weftmark/domain/evidence.py` — `Evidence` carries
  `producer: EvidenceProducer(kind: ProducerKind, id: str)` and
  `artifacts: tuple[ArtifactReference(uri, digest), ...]`, plus `kind`,
  `command`, and `state`. `ProducerKind` includes `WORKER`. **The producer id
  and artifact uri are the natural place a Sylvae run or OpenCode session
  identifies itself.**
- `src/weftmark/application/status.py` — `StatusService.summarize()` fetches the
  full evidence records (`self._workflow.list_evidence()`) but projects them to
  **counts only** (`evidence_count`, `current_evidence_count`, …) on
  `ChangeSetStatus`. The producer/artifact/kind are discarded here.
- `src/weftmark/application/kanban_projection.py` — the board projection faithfully
  re-emits those counts and nothing more.

The v0 projection contract (`docs/contracts/kanban-projection-v0.md`) already
anticipates this exact slice. Its **Deliberate omissions** section lists, as a
separate future integration slice:

> V0 does not yet expose: **worker/agent runtime identity**; terminal endpoints;
> diff endpoints; …

and its **Versioning** rule makes the addition cheap:

> V0 consumers must ignore unknown object fields and unknown attention-flag
> strings. … Existing fields and known values must not silently change meaning.

So new *additive optional* fields need **no schema bump** — `weftmark.kanban
-projection.v0` stays valid, and Rebekah already ignores unknown keys.

## The one genuinely new thing: a producer-id convention

WeftMark stores `producer.id` verbatim; it has no notion of "OpenCode" or
"Sylvae". For the slots to resolve, the tools that **record evidence** must
stamp a namespaced, resolvable identity. Proposed convention:

| Producer | `producer` | and/or artifact |
| --- | --- | --- |
| Sylvae skill run | `EvidenceProducer(WORKER, "sylvae:run/<run_id>")` | `ArtifactReference("sylvae://run/<run_id>")` |
| OpenCode session | `EvidenceProducer(WORKER, "opencode:session/<session_id>")` | — |

This is a change in the **evidence-recording path** of the producing tools
(Sylvae's `weftmark evidence` integration; the OpenCode-driven worker wrapper),
plus a documented convention — **not** a WeftMark core schema change. WeftMark
keeps treating the id as an opaque string.

## Changes, smallest first

### 1. Surface evidence producers/artifacts on the read model (WeftMark)

`status.py` — add an additive field to `ChangeSetStatus`, e.g.
`evidence_entries: tuple[EvidenceRef, ...]`, where `EvidenceRef` distils each
matching evidence to `{id, kind, state, producer_kind, producer_id,
artifacts:[{uri, digest}]}`. Populate it from the `matching_evidence` already
gathered in `summarize()`. Counts stay exactly as they are.

Keep it authoritative-but-thin: surface the producer/artifact **facts**;
do not have WeftMark classify "opencode" vs "sylvae" — that naming is the
producing tools' convention and the consumer's to interpret.

### 2. Expose it — prefer a detail endpoint over fattening the card

Two options; recommend **(b)**.

- **(a) Additive card field.** Add `runtime` / `links` to each change-set card in
  `kanban_projection.py` (`KanbanCardProjection` + `kanban_projection_to_payload`).
  v0-safe, but it pushes per-run detail onto every board card, which the
  interface plan explicitly warns against (§ "Board behavior": *"Avoid
  compressing every service identifier into the card face"*).
- **(b) Dedicated detail read path (recommended).** Add
  `GET /v0/change-sets/{id}` to `src/weftmark/http/server.py` returning a
  `weftmark.change-set-detail.v0` payload: the change set's facts **plus** the
  evidence entries from (1) with producers/artifacts. Keeps the board projection
  lean and matches the plan's detail-dialog model. Rebekah's gateway then
  fetches this for `/api/v1/change-sets/{id}` instead of re-deriving from the
  board. Add a matching contract doc under `docs/contracts/`.

Either way this is additive and read-only; it must obey the same authority rule
(derive from `StatusService`; never refresh Git or mutate the ledger).

### 3. (Optional, larger) Link a session during active work, before evidence

The evidence-based OpenCode link is now shipped (see the OpenCode bridge under
§4). What remains deferred is linkage *before* any evidence exists: to show the
OpenCode session while work is still *in progress*, add an optional `session_ref`
to the claim / native work-binding record (the same records behind
`task_change_set_links`, which already carry `claim_id`), populated when an agent
claims a change set, and surface it through status → detail. This is a
write-path + domain addition in WeftMark — still a later slice; the
evidence-producer path covers the common "what ran against this change set"
question first.

### 4. Rebekah-side follow-up (small; this repo) — **implemented**

`nix/gateway.py::_serve_v1_changeset` now fetches WeftMark's Change Set detail
route (`/v0/kanban/changes/{id}`), reads its `evidence_refs`, and resolves the
`related.opencode` / `related.sylvae` slots by scanning each ref's `producer.id`
and `artifacts` for the `sylvae:` / `opencode:` prefixes: a match sets
`linked: true` with the namespaced ref(s); otherwise the slot stays an honest
"not linked yet". The console renders resolved refs with a "linked" chip. This
is live against a mock in `tests/gateway.sh` today and **degrades gracefully**
against the current WeftMark pin (whose detail route has no `evidence_refs`
yet) — it will light up once the pin advances past
[tabenius/WeftMark#39](https://github.com/tabenius/WeftMark/pull/39) and a
producer stamps a namespaced id (see
[tabenius/sylvae#1](https://github.com/tabenius/sylvae/pull/1)). Sylvae now
supports a coordinator-preallocated `--run-id`; the coordinator gives the same
identity to WeftMark as `--producer-id sylvae:run/<id>` and runs the real
`sylvae run ... --run-id <id>` command through `weftmark evidence run`. This
closes the bridge without importing mutable results, guessing joins, or making
Rebekah a second evidence authority.

That coordinator is `nix/sylvae-evidence.sh` (`rebekah-sylvae-evidence`):
given `REBEKAH_CHANGE_SET_ID`, a skill path and input, it preallocates the
run id, records `weftmark --producer-id sylvae:run/<id> evidence run <cs>
--command sylvae run <skill> --run-id <id> ...`, and emits a
`cc.ragbaz.rebekah.sylvae-evidence.v0` summary. WeftMark runs the command and
binds its pass/fail; the gateway then resolves the `related.sylvae` link from
the stored producer id.

The symmetric OpenCode coordinator is `nix/opencode-evidence.sh`
(`rebekah-opencode-evidence`). It differs in one principled way: OpenCode mints
its own session id when a session starts, so the coordinator *receives* it in
`REBEKAH_OPENCODE_SESSION_ID` (validated to `[A-Za-z0-9._-]`, failing closed on a
malformed value — the same discipline the Ephor connector applies to its
`entry_id`) rather than preallocating one. Given a change set, that session id,
and a verification command (argv or `REBEKAH_OPENCODE_COMMAND`), it records
`weftmark --producer-id opencode:session/<id> evidence run <cs> --command <verify
…>` and emits a `cc.ragbaz.rebekah.opencode-evidence.v0` summary. WeftMark runs
the command and binds its pass/fail; the gateway resolves the `related.opencode`
link from the stored producer id. It never reaches into OpenCode's HTTP surface
or imports mutable session state — the session id is the only join key.
`tests/opencode-evidence.sh` guards the fail-closed cases and the attribution
against a stubbed `weftmark`.

## Status: landed

Both halves of the runtime-links thread are merged and live:

- **Write side (attribution):** WeftMark's global `--producer-id` /
  `--producer-kind` ([tabenius/WeftMark#39](https://github.com/tabenius/WeftMark/pull/39), merged).
- **Read side (surface):** `evidence_refs` on the Change Set detail route
  (same PR).
- **Producer identity:** Sylvae's `sylvae:run/<id>` runtime ref
  ([tabenius/sylvae#1](https://github.com/tabenius/sylvae/pull/1), merged).
- **Consumer:** the gateway's `related.opencode` / `related.sylvae` resolution
  and the Sylvae bridge ([tabenius/rebekah#22](https://github.com/tabenius/rebekah/pull/22), merged).
- **OpenCode bridge:** `rebekah-opencode-evidence`, this slice — closes the
  `related.opencode` slot symmetrically.

The only remaining, explicitly-deferred piece is §3 (linking a session *before*
evidence exists, via a claim-time `session_ref` in WeftMark's domain).

## Prior art to reconcile

Several WeftMark branches suggest runtime-identity work is already underway and
should be checked before implementing, to avoid divergent designs:

- `agent/kanban-runtime-port`
- `agent/runtime-provider-handoff`
- `agent/acp-runtime-adapter`

If any already introduces a worker/agent-identity field or a change-set detail
endpoint, adopt its shape rather than inventing a parallel one; this scope's
value is then mostly the producer-id convention (the new thing) and the Rebekah
consumer follow-up.

## Compatibility & invariants

- **Additive only** in v0: new optional fields, unknown-key tolerance already
  required of consumers; a new `weftmark.change-set-detail.v0` for the detail
  endpoint. No existing field changes meaning.
- **Authority unchanged:** WeftMark stays the coordination authority; the
  projection/detail remains a read-only derivation of `StatusService`. Rebekah
  and the board stay replaceable projections, never a second source of truth.
- **No fabricated joins:** Rebekah continues to show "not linked yet" rather
  than guessing a session/run from a branch name; a link appears only when a
  real producer/artifact id exists.
