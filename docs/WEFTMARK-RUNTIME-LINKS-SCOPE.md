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

Evidence-based linkage only appears once a run has recorded evidence. To show
the OpenCode session while work is *in progress*, add an optional
`session_ref` to the claim / native work-binding record (the same records behind
`task_change_set_links`, which already carry `claim_id`), populated when an agent
claims a change set, and surface it through status → detail. This is a
write-path + domain addition — defer to a second slice; the evidence-producer
path above covers the common "what ran against this change set" question first.

### 4. Rebekah-side follow-up (small; this repo)

`nix/gateway.py::_serve_v1_changeset` already emits the `related.opencode` /
`related.sylvae` slots. Once WeftMark exposes producers/artifacts (via 2b,
preferably), parse the namespaced ids (`sylvae:`, `opencode:` prefixes on
`producer_id` / artifact `uri`) and set `linked: true` with the extracted id and
a deep link; leave the honest "not linked yet" when no evidence carries them.
The console (`nix/ui/index.html`) already renders whatever the slots contain, so
no UI change is required. Add a gateway test with an evidence-bearing mock.

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
