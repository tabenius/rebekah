# Logging and idle polling

Gateway diagnostic events use one line with `service`, `level`, and `event`
fields. Additional values are JSON-escaped so newlines cannot forge records.
HTTP 4xx responses are warnings; 5xx responses are errors. Protocol diagnostics
never echo raw request lines, query parameters, headers, or bodies. Successful
requests do not introduce a new gateway access-log stream. Backend probe records
remain available to existing health metrics and observers.

The Dash pusher logs connectivity transitions, not every successful poll. Its
WeftMark Kanban projection is cached for at most 30 seconds (or the configured
Dash poll interval, when shorter). Explicit refresh and decision-triggered
refresh bypass that cache. Unavailable reads are retried rather than retained
as a successful cached projection. Oversight reads remain on every watch,
including the five-second Ephor watch; holds and decision deadlines do not use
the Kanban cache. This reduces idle read/access-log volume, not health-probe
frequency or oversight responsiveness.

For one container-to-journal delivery path, see
[`deploy/podman/README.md`](../deploy/podman/README.md#one-journal-delivery-path).
Driver changes require explicit activation and a container restart. Match the
chosen stream identifier in downstream readers before switching.

Checks:

```sh
python3 tests/log-hardening.py
python3 tests/dash-push.py
```
