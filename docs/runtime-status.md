# Host runtime inventory in the local console and Dash

The gateway's `/api/v1/system` includes an optional `runtime` projection from
Minotaur's `ragbaz.runtime-status.v1` snapshot. Configure
`REBEKAH_RUNTIME_STATUS=/run/host-status/status.json` (or `RAGBAZ_RUNTIME_STATUS`)
and mount the producer's output **directory** read-only at `/run/host-status`.
The producer atomically replaces the file, so a single-file bind mount would
retain an old inode. No Podman/Docker socket is exposed to the gateway.
The rootless launcher accepts `REBEKAH_RUNTIME_STATUS_DIR` and sets the container
paths for the gateway and terminal surfaces. Its directory must already exist,
be traversable/readable by the service UIDs, and contain `status.json` from the
producer; only the producer should have write access.

The existing local System page displays host components, running/stopped
containers, health, URLs, freshness, operator control instructions and human
workflow metadata. The existing Dash pusher carries the same `v1_system`
payload. These are live projections, not a static web release or delivery
acknowledgement. Page reads are not audit events. Gateway mutation intent/result
logging remains its own Nostoi workflow; snapshot publication intent is logged
by the host adapter and labeled separately.

Missing configuration means host state is unknown, not that Minotaur is absent.
The supervised Ephor state still comes from Rebekah itself and remains
absent/disabled/external/enabled independently of host observations. Native
WeftMark CLI review is usable without Ephor; governed holds still require Ephor
and the authenticated Dash→gateway decision path. URLs under the authenticated
gateway are relative to its origin; optional host URLs are operator-declared.

Sylvae now offers authenticated `/healthz` rather than treating any HTML root
response as health. Probes without gateway credentials report `auth-required`,
not ready. Run health probes in the correct host/container namespace and keep
backend exposure allow-listed.
Machine-readable methods and their semantics are announced in `docs/health.json`
and the owning WeftMark/Sylvae/Nostoi/Minotaur health contract files.

No image pins or running deployment are moved by these source changes. A built
image must include the updated Sylvae/WeftMark sources before their endpoints or
CLI wrappers are available there. Validate with the deployment contract's real
container smoke test before moving a deployment pin.
