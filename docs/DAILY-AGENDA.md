# Private Daily Agenda

Work now has a **Daily Agenda** card opening an agenda tab in both the local
Rebekah console and a connected RAGBAZ Dash instance. It displays the generated
top-ten Git overview, Frog roadmap scopes/owners/dependencies, editorial execution
slices and expandable concept reminders with one-sentence hover/focus previews.

## Configure the source

Generation belongs on the workspace host. The gateway reads a selected artifact
directory; it does not execute Git, Frog or an arbitrary generator on an HTTP
request. Produce `snapshot.json`, `editorial.json` and `manifest.json` with the
Daily Agenda ceremony helper, then mount that **directory**, read-only, so later
atomic file replacements become visible. Keep it outside the public UI directory.

For `deploy/podman/rebekah-run`, set the host launcher setting:

```sh
REBEKAH_DAILY_AGENDA_HOST_DIR=/srv/ragbaz/reports/daily-agenda
```

The launcher mounts it at `/run/daily-agenda:ro` and sets
`REBEKAH_DAILY_AGENDA_DIR=/run/daily-agenda` in the container. With another launcher,
mount a chosen absolute directory read-only and set `REBEKAH_DAILY_AGENDA_DIR` to
its **container-side** path. With a host gateway, set it directly to the artifact
directory. The setting is optional and empty by default.

Grant the gateway's UID 10005 read/traverse access to that selected directory and
read access to its files. With rootless Podman, use its user namespace to address
the container UID; follow the existing ACL pattern in `deploy/podman/README.md`.
Give newly generated files the same access, using a suitable default ACL. Do not
widen other services' state-directory permissions to make this work.

## What “private” means

- The console shell contains only the generic card/tab. Agenda data requires the
  gateway's existing bearer, password-session or OIDC authentication.
- Dash requires an authenticated account with the instance's `view` capability;
  unauthenticated callers and non-members cannot read its agenda. All authorized
  viewers of that instance can read it: select an instance whose audience matches
  the report, rather than attaching personal reports to a broadly shared instance.
- The connector pulls a fixed API view or receives it with the existing push key.
  Dash receives a bounded data projection, not the original HTML or local file
  paths. Neither application injects generated HTML. Concept source links, when
  provided, must be credential-free HTTPS; workstation `file:///` links are omitted.
- No static artifact download route is added. Snapshot responses use `no-store`.

## Contract and freshness

`GET`/`HEAD /api/v1/daily-agenda` returns `rebekah.daily-agenda.v1`, with
`source: rebekah-gateway`. `observed_at` is the **gateway read time**;
`source_observed_at` remains the original **Git/Frog observation time** and
`generated_at` is the document rendering time. Regenerating layout or refreshing
the connector must not rejuvenate old source evidence. Evidence older than 24
hours is labeled stale; source failures have an explicit count.

States: `ready`, `not-configured`, `unavailable`. An unavailable/malformed or
mixed generation returns no project data. The gateway verifies snapshot/editorial
bytes against the manifest, checks schema/time-window consistency, rejects
symlinks/FIFOs/devices and caps files before parsing. Digests detect mixed or
modified output; they are not signatures or proof that source claims are true.

The projection contains at most ten projects, 40 related tasks per project, ten
editorial steps per task and eight concepts per project. Native task scope is
capped at 12,000 characters with an explicit truncation flag. Total projection
size is capped at 384 KiB, within the connector's bounded request/response budget.
If the aggregate push envelope would exceed Dash's 512 KiB cap, the optional
agenda is sent as unavailable so it cannot displace the existing Work/Review views.
Task descriptions may contain native source references; dedicated absolute
artifact/repository paths and dirty-path names are not projected.

**Reload latest / Refresh** rereads the latest generated edition. A push-only
Dash instance uses the existing audited refresh flag and next outbound push.
It does not create a new agenda edition. Selection is retained across refresh;
source records and ownership must be rechecked before continuing assigned work.

## Dash rollout and checks

Apply Dash migration `0008_daily_agenda.sql` before deploying the updated Worker;
it widens the snapshot view constraint while preserving existing snapshots.
The view is rendered with React alongside Work/Review/System using the existing
connector and instance membership checks. Older gateways can still serve the
other views; selecting Agenda on one reports unavailable.

```sh
python3 tests/daily-agenda.py
python3 tests/dash-push.py
python3 tests/runtime-status.py
shellcheck --severity=warning deploy/podman/rebekah-run
REBEKAH_TEST_IMAGE=localhost/rebekah:latest python3 tests/daily-agenda-container.py
```

The last check uses an isolated existing runtime image with changed gateway/UI
sources mounted read-only. It verifies the installed interpreter and gateway UID,
not a rebuilt image or a production deployment. The normal image build/smoke
requirement still applies before publishing an image.
