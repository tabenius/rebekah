# Rebekah build and deployment contract

How to build the image and get it onto a box. This describes the **method**, not
any particular machine: no host paths, no user names, no key fingerprints. Box-
local facts live in the workspace, outside any repository.

For the running service's own environment surface, see `nix/entrypoint.sh`; for
the image contents see `nix/image.nix`; for the general run model see
[`../README.md`](../README.md) and `deploy/podman/README.md`.

## Two images, one flake

| output | contains | who needs it |
|---|---|---|
| `.#image` | WeftMark, Sylvae, Nostoi, OpenCode, Ollama | anyone; builds from public sources only |
| `.#image-ephor` | the above **plus** `governance-http` and `agent-proxy`, and Litestream | governance; Ephor stays off until `REBEKAH_EPHOR_ENABLE=1` |

Ephor is opt-in because its source is private, and Nix fetches every locked input
even when the package is unused. That is why the default `ephor-src` is the
in-repo placeholder `nix/ephor-absent`: the baseline image must build from public
sources so it is reproducible for anyone.

```bash
nix flake check
nix build .#image
docker load < result
```

## Ephor: the private input

`BAZ.AI-Governance` is private, so a default `GITHUB_TOKEN` cannot fetch
`ephor-src` and the build fails with a `404`. Override the input from a local
staged tree at the pinned revision:

```bash
REV="$(cat nix/ephor.rev)"
git -C ../BAZ.AI-Governance archive "$REV" | tar -x -c -C /tmp/ephor-src
nix build .#image-ephor --override-input ephor-src path:/tmp/ephor-src
```

Stage the tree from `git archive`, not a working copy: a `path:` input copies the
directory, so a dirty checkout silently changes what gets built. The archive of a
pinned revision is the reproducible form.

`flake.lock` deliberately does **not** record `ephor-src`, because the override
supplies it per build. `nix/ephor.rev` is the pin instead, and it is a separate
file on purpose: a rev that has to be edited into `flake.nix` to change is a rev
that will drift from the lock.

## Regenerate the lock with the tooling

`flake.lock` is frequently stale relative to `flake.nix`. Never hand-edit it:

```bash
git status --short flake.nix flake.lock   # assume stale until checked
nix flake update <input>
```

The inputs are pinned to exact revisions *in `flake.nix`* (`weftmark-src`,
`sylvae-src`, `nostoi-src`) rather than only in the lock, so a stale lock fails
loudly instead of silently building something else.

## Pin images deliberately

Rebekah is published to `ghcr.io/tabenius/rebekah`. A new `:latest` must never
reach a host unreviewed, so a box pins:

- a **registry digest** (`ghcr.io/tabenius/rebekah@sha256:…`) when the image came
  from a registry; or
- an **image ID** when the image was built locally and loaded, because a locally
  loaded image has no registry digest to pin. This is a real weakness, not a
  convenience: an ID pin records *what* is running but attests to nothing about
  where it came from.

Move the pin on purpose, with the previous value left in a comment so the change
is reviewable after the fact.

## Portability: builds under nix-portable

Where Nix is not installed system-wide, `nix-portable` is vendored under
`src/vendor/nix-portable`. Three consequences:

- **`/nix/store` only exists inside the namespace.** The store's real location is
  `<NP_LOCATION>/.nix-portable/nix/store/`. A path under `/nix/store` will not
  resolve from outside; source it through the wrapper, or read the store directly
  at its physical location.
- **`NP_LOCATION` is the whole relocation story.** The store is one directory, so
  moving it to a larger filesystem is changing that one variable. Point it at a
  big disk before building anything large.
- **A stale build temp directory breaks every later build.** A leftover
  `/tmp/nix-*-0` makes builds fail with `home directory '/homeless-shelter'
  exists`, which reads like a sandbox error and is not. Delete the stale temp
  dirs and retry:

  ```bash
  rm -rf /tmp/nix-*-0
  ```

## Running it

The run model is unchanged from the main README: read-only root, five
capabilities, `no-new-privileges`, tmpfs for `/run/rebekah` and `/tmp`, a volume
for state, a bind mount for the governed repository, and secrets passed as Podman
secrets so they never appear in `podman inspect` or in a unit file.

`deploy/podman/` holds the launcher, the systemd user unit, and the settings file
for running it as an unprivileged host user. Read that directory rather than
inventing a second launcher; a divergent copy of the launcher is how a box ends up
with different privileges than it thinks it has.

## Checks

```bash
bash tests/smoke.sh              # the loaded image, end to end
bash tests/ephor-connector.sh    # connector pass/fail-closed cases
shellcheck --severity=warning nix/*.sh tests/*.sh deploy/podman/rebekah-run
nix flake check --print-build-logs
```

CI additionally builds and loads the image and runs the service smoke test;
`.github/workflows/ci.yml` is the executable version of the sequence above.

## What this repository does not contain

- **Host facts.** Disk layouts, home paths, image IDs and box names are
  box-local and belong in the workspace, not here — this repository mirrors to a
  public upstream.
- **Key material or fingerprints.** The key registry schema is specified in the
  design system; the registry itself is box-local.
- **Fleet conventions.** Coordination, terminal styling and box definitions live
  in their own repositories and are linked from the workspace.