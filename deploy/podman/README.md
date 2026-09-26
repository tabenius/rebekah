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
# put that digest in REBEKAH_IMAGE, then:
systemctl --user restart rebekah.service
```

State lives in the `rebekah-state` volume and survives restarts and updates.
