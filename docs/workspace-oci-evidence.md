# Rebekah workspace-to-OCI evidence contract (draft v0.1)

Status: experimental, not a claim of deployed integration. Tracks #41 and #42.

## Boundary and ownership
- Codeg owns interactive conversations and approval UX; it is optional.
- Rebekah allocates runtime backends and controls ingress; Codeg receives a narrowly scoped endpoint, never host KVM or Docker sockets.
- An isolated guest owns its checkout and ephemeral worktree; no host workspace bind mount for untrusted tasks.
- WeftMark records task outcomes and publishes content-addressed artifacts.
- Pieces consumes packages/previews; Mouseion indexes evidence relationships and trust decisions.
- DetCordon-derived policy can authorize sandbox requests and ingest audit events; its existing WAF is not a sandbox.
- Firecracker requires KVM and a separate guest network/egress policy. A Git worktree is not a security boundary.

## Minimal handoff record
Use JSON Schema in `docs/contracts/workspace-handoff.schema.json`. A producer MUST record immutable commit and artifact digests. A task session ID is an opaque reference, not proof of identity. Evidence MUST distinguish observation from assertion.

## OCI publication
1. Publish payload (WASM component, WIT package, document, source bundle, or data) as an OCI 1.1 artifact with a documented media type and immutable manifest digest.
2. Publish provenance as an in-toto statement (SLSA predicate when appropriate) with the payload manifest digest as subject.
3. Publish tests/evidence as separate OCI artifacts referring to the payload using OCI subject/referrers. Preserve raw evidence digests.
4. Sign attestations using a configured offline-capable signing identity; verify issuer/key, signature, statement subject and predicate policy independently.
5. Export an OCI image layout with all referenced manifests/blobs/referrers for offline transfer; test import without network.
6. For WASM components and WIT packages, prefer existing Bytecode Alliance wkg conventions and registered media types over new custom types.

## Security gates
- Never execute artifacts during ingestion, indexing or preview.
- Fail closed on unknown issuer, missing required evidence, digest mismatch or policy failure.
- A signature authenticates a statement, not its factual truth.
- Guest secrets must be task-scoped and excluded from evidence bundles.
- No guest network path to host management APIs, registry credentials or unrestricted Ollama administration.
- Snapshot restore only from trusted clean bases; never reuse untrusted guest memory across tasks.
- Deny cross-task filesystem access; test symlink, Git hook, network, and resource-exhaustion attacks.

## Acceptance tests (not yet executed)
1. Offline Codeg -> OpenCode -> Ollama session completes in a sandbox and returns a reviewed Git commit.
2. WeftMark produces handoff record with immutable OCI digest.
3. Pieces pulls and previews package without executing it.
4. Mouseion discovers referrers and queries provenance/evidence by subject digest.
5. WASM component + WIT package round-trip through OCI using wkg.
6. Tampered blob, forged signature, missing attestation and unauthorized egress are rejected.
7. Android reconnect does not grant broader authority or lose pending approvals.

## Open design decisions
- Pick an OCI registry implementation with verified referrers behavior and offline replication.
- Choose a trust root and key rotation model for disconnected installations.
- Decide whether Firecracker lifecycle uses microvm.nix directly or a dedicated controller.
- Confirm Pieces and Mouseion repositories and existing APIs before writing adapters.
