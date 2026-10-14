# Frog task board

Frog is an additive read-only live task source, separate from WeftMark evidence
and review. Start `frog --db /workspace/AGENTS.db web serve` in the gateway's
network namespace and read its printed port. Set that port in `FROG_PORT`, and
add `frog` to `REBEKAH_GATEWAY_EXPOSE`. `FROG_HOST=localhost` is normalized to
127.0.0.1; non-loopback sources fail configuration validation. Frog's implicit
port can fall back when busy: do not assume 8788 without checking.

Authenticated `GET /api/v1/frog-kanban` returns `rebekah.frog-kanban.v1`:
original `source_observed_at`, independent gateway `observed_at`, revision,
four lanes, and sanitized task `items`. Missing, malformed, oversized,
duplicate-ID and future sources fail closed as unavailable. The built-in
**Frog tasks** tab uses existing gateway login, priority chips, search and
clickable blocker/reverse-dependency links. Logout discards private cards.
The `/frog` prefix permits only the bounded read API; writes are refused.

The optional Dash pusher sends the same view through existing TLS connectors;
it includes explicit disabled/unavailable states even when Frog is not enabled.
A valid zero-task snapshot is ready and carries both source capture and gateway
observation timestamps; it is not an absent push or a connection error.

The push envelope also includes `schedule.next_push_at` (UTC unix seconds): the
producer's next heartbeat deadline. Scheduling is separate from source capture
and receipt times. Changes and requested refreshes may push earlier; network
failures can delay delivery. The monotonic scheduler wakes by this deadline even
when it falls before the next ordinary poll. Metadata never authorizes a write.
mirrors still cannot apply real-instance commands. Deploy the updated Dash
consumer before activating Frog pushes. Oversized optional data degrades to
unavailable instead of blocking existing Work/Review/System views.

Frog is not automatically packaged/started in the OCI image. Run it alongside
the gateway with read-only database access in the same network namespace.
The authenticated gateway remains the remote entry point; OpenCode exposure
and WeftMark evidence/approval semantics are unchanged.
