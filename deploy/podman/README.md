# Rebekah under rootless Podman

Run Rebekah as an unprivileged host user, with systemd keeping it up. Rootless
Podman adds a user namespace to the least-privilege run in the main README:
container root is that user on the host, and the service UIDs map to its
subordinate UIDs.

## Host (once, as root)

```bash
apt install -y podman uidmap slirp4netns acl
loginctl enable-linger <user>           # keep the user's services up without a login
grep <user> /etc/subuid /etc/subgid     # needs a range of at least 65536
```

## User setup

```bash
install -m 0755 rebekah-run ~/bin/rebekah-run
install -m 0755 rebekah-ab ~/bin/rebekah-ab
install -Dm 0644 rebekah.service ~/.config/systemd/user/rebekah.service
```

**Settings**, in `~/.config/rebekah/rebekah.env` (`0600`):

```bash
REBEKAH_NAME=rebekah
REBEKAH_IMAGE=ghcr.io/tabenius/rebekah@sha256:…   # pin a digest; see "Updating"
# REBEKAH_WORKSPACE=/home/<user>/workspace
# REBEKAH_MEMORY=16g
```

Container settings that are not secrets (for example `REBEKAH_CHANGE_SET_ID`,
`REBEKAH_DASH_URL`) go in `~/.config/rebekah/container.env`.

### One journal delivery path

The default `REBEKAH_LOG_DRIVER=journald` preserves Podman's own container log.
When running attached under systemd this can duplicate stdout/stderr in the
journal. To use only systemd's attached stream, set `REBEKAH_LOG_DRIVER=none`
in the **host** settings and set a stable `SyslogIdentifier` in a service
drop-in. Adapt edge selection, health observers, and dashboards to that
identifier before activating it. `podman logs` is then unavailable; use
`journalctl --user -u <unit>`. A container restart is required to switch drivers.
When systemd supplies `INVOCATION_ID`, the launcher uses host `systemd-cat` to
connect stdout at info priority and stderr at error priority, both identified
by the container name. Application-declared levels and HTTP status can still
override those transport defaults downstream. Outside systemd, attached output
stays on the caller's terminal.

**Secrets** are Podman secrets. `rebekah-run` passes each one that exists as an
environment variable inside the container only, so it does not show in
`podman inspect` or in any file the unit reads:

```bash
# The Dash push key, issued once in RAGBAZ Dash (instance → Push):
printf '%s' 'rbkp_…' | podman secret create rebekah-dash-push-key -
# Optional: a known admin password / gateway token instead of generated ones.
openssl rand -base64 24 | tr -d '\n' | podman secret create rebekah-admin-password -
```

To replace a secret: `podman secret rm <name>`, create it again, restart.

**Workspace.** Rebekah governs a Git repository with at least one commit.
OpenCode (UID 10002) and WeftMark (UID 10004) run with their groups cleared, so
give exactly those two access with ACLs rather than opening the directory to
every service UID. `podman unshare` makes the container's UIDs addressable:

```bash
git init ~/workspace && git -C ~/workspace commit --allow-empty -m init
chmod -R go-rwx ~/workspace
podman unshare setfacl -R -m u:10002:rwX,u:10004:rwX,m::rwX ~/workspace
podman unshare setfacl -R -d -m u:10002:rwX,u:10004:rwX,m::rwX ~/workspace
```

## Run

```bash
systemctl --user daemon-reload
systemctl --user enable --now rebekah.service
podman ps                                  # (healthy) after a few seconds
journalctl --user -u rebekah.service -f
```

First sign-in: unless `rebekah-admin-password` is set, the gateway writes a
generated password to its state volume. Read it once, then delete it:

```bash
podman exec rebekah sh -c 'cat /var/lib/rebekah/gateway/initial-admin-password && rm /var/lib/rebekah/gateway/initial-admin-password'
```

## Updating

Pin the image by digest and move the pin on purpose, so a new `:latest` never
reaches the host unreviewed:

```bash
podman pull ghcr.io/tabenius/rebekah:latest
podman image inspect ghcr.io/tabenius/rebekah:latest --format '{{index .RepoDigests 0}}'
# activate the digest shown above, then verify before promotion:
rebekah-ab activate ghcr.io/tabenius/rebekah@sha256:<digest>
```

State lives in the `rebekah-state` volume and survives restarts and updates.

### Verified activation and rollback

`rebekah-ab` runs as the same user that owns the service and Podman store. It
requires host Bash, Python 3, `flock`, and standard Unix tools. The candidate must
already be loaded/pulled and pinned by digest or full image ID; tags are refused.
It reads `REBEKAH_NAME` and `REBEKAH_DAILY_AGENDA_HOST_DIR` from the host
EnvironmentFile without executing it. Override the file with `REBEKAH_ENV_FILE`
or the unit with `REBEKAH_UNIT` (default: `<REBEKAH_NAME>.service`).

```bash
rebekah-ab status
rebekah-ab verify
rebekah-ab --dry-run activate sha256:<full-image-id>
rebekah-ab activate sha256:<full-image-id>
rebekah-ab rollback
rebekah-ab recover
```

Activation preflights the candidate and rollback image before switching. For a
configured agenda, the edition contract must match an exact gateway declaration;
a missing or malformed manifest refuses deployment. After restart, the running
image ID must match the requested pin, service health must pass, the gateway must
answer, and a configured agenda must serve `ready`. `not-configured` is a failure
when an agenda was requested. Initial B is seeded only from a verified running
image; activation without a verified baseline is refused.

After success, `last-good-image` becomes A; `previous-good-image` retains the
former B. To return to that older baseline after a later regression, pass its pin
to `activate`. Keep those images in the local store; do not prune them. Failed
activation restores B and verifies it again. Exit codes: `0` success, `1` candidate
failed but rollback verified, `2` refused deployment, `3` failed rollback/recovery.

Mutations take an exclusive lock, back up the environment file, and replace pins
atomically. Dry-run never writes state or restarts the service (preflight may run
an isolated, temporary container without network or mounted state). A pending
rollback record survives an interrupted deployment; `recover` restores it and
only clears the record after successful verification. An incomplete transaction
blocks another activation. INT/TERM and unexpected script errors attempt recovery
immediately; power loss or SIGKILL requires `recover` after startup. There is no
background post-promotion monitor.

**This rolls back the image, not persistent data.** Upstream services can change
their databases even without a Rebekah migration marker. For a state-changing
upgrade, arrange application-consistent backups and establish backward
compatibility before activation. The script does not snapshot or restore the
state volume, and must not rewind an audit chain automatically.

## Private Daily Agenda

Set `REBEKAH_DAILY_AGENDA_HOST_DIR` in the host launcher settings to a generated
agenda directory. The launcher mounts the directory read-only at `/run/daily-agenda`
and sets the gateway's `REBEKAH_DAILY_AGENDA_DIR` automatically. Grant UID 10005
read/traverse access to the directory and generated files, including replacements
through a suitable default ACL. The agenda is readable by authenticated gateway
users and authorized viewers of the connected Dash instance, so select an instance
with the intended private audience. Contract and generation/freshness details:
[`docs/DAILY-AGENDA.md`](../../docs/DAILY-AGENDA.md).
