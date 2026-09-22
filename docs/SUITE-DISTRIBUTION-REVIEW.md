# Suite distribution review

**Review date:** 2026-09-22  
**Decision:** **NO-GO for a single, reproducible suite release; GO for the existing v-BAZ installer alpha as an explicitly unvalidated preview.**

This review treats the following repositories as one intended product suite:

| Layer | Repository | Reviewed revision | Responsibility |
|---|---|---:|---|
| Host installer | [tabenius/v-BAZ](https://github.com/tabenius/v-BAZ) | `d65650a3559031e957049644ed3d14cb4698c786` | Windows-driven Alpine/KVM/Kata/ZFS host installation |
| Composition | [tabenius/rebekah](https://github.com/tabenius/rebekah) | `a9c9e01f5f355cc863889bb18a31859b797fe4df` | Reproducible image, service isolation, gateway, console, lifecycle |
| Skill execution | [tabenius/sylvae](https://github.com/tabenius/sylvae) | `55f9491c394a54112d2ad39a8720c0b85f015d6c` | Portable skill execution and durable run evidence |
| Engineering coordination | [tabenius/WeftMark](https://github.com/tabenius/WeftMark) | `48bdc25accaf6e68914db6d4efabc8d23b60320f` | Change Sets, scopes, evidence, review, readiness |
| Governance | [tabenius/BAZ.AI-Governance](https://github.com/tabenius/BAZ.AI-Governance) (Ephor/KAGP) | `5b05af53d695f183e8a7954032a85cc8beeb113b` | Policy, human oversight, audit chain, governance evidence |

The repository formerly described as the Konsonans AI Governance Platform is referred to here as **Ephor**, while retaining **KAGP** where it is the current protocol, binary, environment-variable, or schema name.

## Executive assessment

The architectural division of authority is coherent:

- v-BAZ owns host installation and the destructive disk boundary.
- Rebekah owns composition, isolation, service lifecycle, authentication, and the user-facing gateway.
- Sylvae owns skill execution evidence.
- WeftMark owns engineering readiness.
- Ephor owns governance decisions and tamper-evident governance records.
- OpenCode and Ollama remain external runtime products packaged by Rebekah rather than new authorities created by this suite.

The implemented correlation path is also sound: WeftMark Change Set ID → OpenCode session ID / Sylvae run ID / Ephor entry ID and chain hash. Ephor evidence is attached as WeftMark `security:governance` evidence, so Ephor does not become a second readiness authority.

The suite is **not yet one releasable unit**, however. Today v-BAZ consumes the moving tag `ghcr.io/tabenius/rebekah:latest`; Rebekah's committed `flake.lock` does not contain its declared `ephor-src` input; the private Ephor source changes independently; and there is no suite manifest that binds the v-BAZ release, Rebekah image digest, dependency commits, model artifact, licenses, and checksums.

## Current evidence

| Repository | Current evidence | Interpretation |
|---|---|---|
| v-BAZ | `wiring` passed for released commit `d65650a`: [run 35709749325](https://github.com/tabenius/v-BAZ/actions/runs/35709749325) | Static wiring and safety assertions pass. This is not a QEMU/OVMF or hardware install. |
| Rebekah | CI passed: [run 35710314895](https://github.com/tabenius/rebekah/actions/runs/35710314895); image publish passed: [run 35710314826](https://github.com/tabenius/rebekah/actions/runs/35710314826) | The composed image and its smoke path passed at that revision. |
| Sylvae | CI passed at reviewed head: [run 33196179647](https://github.com/tabenius/sylvae/actions/runs/33196179647) | The standalone Python package and backend/security tests pass. |
| WeftMark | CI for reviewed head was cancelled: [run 35159504654](https://github.com/tabenius/WeftMark/actions/runs/35159504654) | Rebekah tests its packaged subset, but the full upstream head is not independently green in the available evidence. |
| Ephor | CI for current head was still running; Worker and marketing deploys failed: [CI](https://github.com/tabenius/BAZ.AI-Governance/actions/runs/35758308643), [Worker](https://github.com/tabenius/BAZ.AI-Governance/actions/runs/35758308577), [marketing](https://github.com/tabenius/BAZ.AI-Governance/actions/runs/35758308557) | The current private head cannot yet be called suite-release-ready. The deploy failures are separate from the last known successful core CI and need concrete triage before inclusion. |

The v-BAZ alpha release is published at [v0.1.0-alpha.1](https://github.com/tabenius/v-BAZ/releases/tag/v0.1.0-alpha.1), tagged at `d65650a`. GitHub provides its source ZIP. It accurately says QEMU/OVMF and real-hardware installation remain unperformed.

## Distribution blockers

### P0 — Rebekah's Ephor source is not committed to the lock graph

`flake.nix` declares `ephor-src = github:tabenius/BAZ.AI-governance`, but the reviewed `flake.lock` root contains only `nixpkgs`, `sylvae-src`, and `weftmark-src`; there is no `ephor-src` node or root input.

This allows a writable build to update the lock implicitly and resolve whatever Ephor head exists at build time. That contradicts the README's claim that Ephor is pinned and prevents reconstruction of the exact published image from the committed lockfile.

**Minimum correction:** run `nix flake update ephor-src`, commit the resulting `flake.lock`, and make CI reject a dirty lockfile after evaluation (for example, copy the checkout read-only or assert `git diff --exit-code -- flake.lock` after `nix flake check`).

### P0 — No immutable suite bill of materials

v-BAZ defaults to `ghcr.io/tabenius/rebekah:latest`. Its alpha tag therefore does not identify the Rebekah image that will be installed tomorrow, nor the dependency revisions inside it. The offline tarball path is safer operationally but still lacks a suite-level manifest binding it to the installer release.

**Minimum correction:** create one machine-readable suite manifest in Rebekah containing:

- suite version;
- v-BAZ tag and commit;
- Rebekah commit and image digest;
- WeftMark, Sylvae, and Ephor commits;
- nixpkgs commit;
- default Ollama model name and offline artifact checksum;
- all downloadable artifact SHA-256 values;
- target architecture.

Then configure the matching v-BAZ release to use a Rebekah version tag or, preferably, an OCI digest. Keep `:latest` only as an explicit development channel.

### P0 — Sylvae has no declared distribution license

The reviewed Sylvae tree has no `LICENSE` file, and its `pyproject.toml` has no license declaration. Rebekah packages Sylvae into a distributable OCI image. Without an explicit license, third parties do not have a clear grant to redistribute it.

**Minimum correction:** add the intended license file and package metadata to Sylvae, and include it in Rebekah's third-party notices/SBOM. Do not infer a license from the other repositories.

### P0 — Ephor is private but required to build Rebekah

Rebekah CI requires `EPHOR_READ_TOKEN`; the default GitHub token cannot fetch the private Ephor repository. That is workable for an internal build, but a public Rebekah source release is not independently reproducible by recipients. It also means the suite's redistribution rights and source-availability promise must be explicit.

**Minimum correction:** choose and document one supported distribution model:

1. publish Ephor source and pin it;
2. keep it private and distribute a licensed, checksummed Ephor binary/OCI artifact consumed by Rebekah; or
3. publish a Rebekah edition without Ephor and clearly label governance as unavailable rather than silently approved.

The current fail-closed behavior should remain.

### P1 — Upstream and composed test scopes are not equivalent

Rebekah deliberately disables WeftMark's MCP tests because nixpkgs provides MCP 1.29 while WeftMark requires `mcp>=2,<3`. Rebekah ships the CLI and HTTP surface, so this is not a defect in the current runtime path, but it means “WeftMark included” does not mean every WeftMark interface is shipped and tested.

The reviewed WeftMark head's own CI was cancelled. Rebekah's green CI validates the pinned composition subset, not the entire upstream repository.

**Minimum correction:** state the shipped surface in the suite manifest (`cli,http,tui?`; no MCP) and require one successful full WeftMark CI run at the exact pinned commit before a suite release.

### P1 — Sylvae compatibility is carried as a downstream source patch

Rebekah patches Sylvae's Ollama model and API base defaults during the Nix build. The patch is tied to exact source strings and will fail or drift when Sylvae changes.

**Minimum correction:** move the already-designed environment configuration upstream into Sylvae, update the pin, then remove the downstream patch. Until then, keep the exact current Sylvae pin and treat any pin update as an integration change requiring Rebekah CI.

### P1 — Ephor current-head deployment evidence is red/incomplete

At review time, Ephor's newest CI had not completed and both cloud deployment workflows had failed. Rebekah's earlier successful image build does not validate the newer Ephor head, especially while the lockfile omits the Ephor pin.

**Minimum correction:** record the exact Ephor revision intended for the suite, require its core CI to finish successfully, and either repair the two deploy workflows or explicitly exclude those cloud surfaces from the offline suite release.

### P1 — No aggregate release gate

Each repository has useful tests, but no single workflow consumes the suite manifest and proves:

1. all pinned sources/artifacts are retrievable;
2. Rebekah builds without modifying the lockfile;
3. the OCI image digest matches the manifest;
4. Rebekah's five services become healthy;
5. a Sylvae run is correlated to a WeftMark Change Set;
6. Ephor denial/hold/approval affects WeftMark readiness correctly;
7. the v-BAZ offline bundle contains the exact image/model artifacts and checksums.

**Minimum correction:** add one Rebekah “suite release gate” workflow. Reuse the existing tests; do not duplicate component test suites.

## Hardware and QEMU boundary

The following work is still genuinely unverified and should remain clearly marked rather than converted into more static checks:

| Test | Requires | What it proves |
|---|---|---|
| QEMU/OVMF destructive-install smoke | Windows VM, UEFI/GPT virtual disk, nested virtualization where needed | rEFInd chain, BCD behavior, apkovl discovery, Alpine first boot, target partition selection, installed reboot |
| Offline QEMU/OVMF smoke | Same plus prepared offline bundle | ESP discovery, image tar import, cached model extraction, first useful off-grid session |
| Physical x86_64 UEFI smoke | Sacrificial/fully backed-up machine | Firmware-specific BCD entry visibility/order, Secure Boot/MOK behavior, storage-controller and Wi-Fi variance |
| Kata/Firecracker runtime smoke on installed host | Working KVM host | containerd devmapper snapshotter, Kata Firecracker launch, persistent ZFS-backed Rebekah state |

These are not prerequisites for preserving the current alpha label, because the release notes already disclose them. They **are** prerequisites for calling the suite installable or promoting it beyond alpha.

## Packaging model

Rebekah should be the suite's release coordinator; v-BAZ should remain the host installer and consumer.

A minimal release set is:

```text
rebekah-suite-<version>/
├── suite-manifest.json
├── SHA256SUMS
├── rebekah-image.tar.gz
├── ollama-model.tar.gz
├── licenses/
│   ├── rebekah
│   ├── v-BAZ
│   ├── weftmark
│   ├── sylvae
│   └── ephor
└── v-baz/
    ├── source.zip
    └── offline-bundle/
```

The manifest, not filenames or `:latest`, is the authority. The online path may pull the same digest; the offline path imports the same image bytes. This preserves the existing online/off-grid design without inventing a second package manager.

## Minimal merge checklist

Only the following changes are necessary before creating a unified suite alpha:

- [ ] Commit `ephor-src` into Rebekah's `flake.lock` and make lock drift fail CI.
- [ ] Add an explicit Sylvae license and package license metadata.
- [ ] Decide/document the private-Ephor distribution model.
- [ ] Add `suite-manifest.json` with exact commits, OCI digest, model identity, architecture, and checksums.
- [ ] Pin v-BAZ's release channel to the manifest's Rebekah tag/digest instead of `:latest`.
- [ ] Obtain a successful full WeftMark CI run at its pinned commit.
- [ ] Obtain successful Ephor core CI at its pinned commit; repair or exclude the failed cloud deploy surfaces.
- [ ] Add one aggregate Rebekah suite release gate reusing existing component tests.
- [ ] Build and attach the combined offline bundle plus `SHA256SUMS`.

Not repeated here as work items because they are already implemented and tested in the composed runtime:

- Rebekah gateway token/OIDC authentication and fail-closed routing;
- built-in same-origin web console;
- per-service UIDs and loopback binding;
- OpenCode password boundary;
- WeftMark `security:governance` evidence attachment;
- Ephor capture/finalize correlation;
- online image pull with offline ESP fallback;
- v-BAZ GPT-GUID safety checks and dry-run path.

## Promotion rule

Keep **alpha** until the immutable suite manifest and legal/reproducibility blockers are resolved. Promote beyond alpha only after both QEMU/OVMF install paths (online and offline) pass. Mark physical-hardware support by tested hardware profile rather than claiming universal UEFI compatibility after one machine.
