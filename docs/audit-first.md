# Audit-first actions and chain verification

The gateway writes a bounded `rebekah.decision.requested` record before it
sends an approval, defer, escalation or WeftMark review to its service. The
record keeps the command id, actor and resource identity, and hashes the free
text rationale. It never copies the reviewer credential or raw rationale.
The result is appended with the intent digest. If the result write fails, the
gateway reports the outcome as unknown; the durable intent signals that the
remote action may have happened and must be reconciled.

Intent storage is fail-closed. The 20-command response cap, Dash retry/backoff
handling and bounded 4 KiB intent keep an authenticated command burst from
creating unbounded audit writes. Authentication and upstream request limits
remain necessary at the network boundary.

The image pins and ships the Nostoi verifier. `rebekah doctor` checks each
existing chain it can reach: the gateway and Sylvae `nostoi-v1` JSONL files,
the WeftMark `weftmark-ledger-v1` JSONL file, and (when enabled) Ephor's
`ephor-audit-v1` SQLite store. Missing first-run chains are reported as
pending; an existing invalid chain makes doctor fail. The smoke suite verifies
the WeftMark ledger and, in the Ephor-enabled image, the SQLite audit chain.
