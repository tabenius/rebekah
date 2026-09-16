<p align="center">
  <a href="https://github.com/tabenius/WeftMark">
    <img src="https://raw.githubusercontent.com/tabenius/WeftMark/main/assets/weftmark.svg" alt="WeftMark logo" width="240">
  </a>
</p>

<h1 align="center">Rebekah</h1>

<p align="center">
  A reproducible, self-hosted environment for governed agentic software work.
</p>

> [!IMPORTANT]
> Rebekah is in its bootstrap phase. The architecture below is an implementation
> direction, not a production-readiness claim.

## Purpose

Rebekah composes a Docker-compatible container built with **Nix/NixOS tooling**
that brings together local inference, agent execution, engineering provenance,
and governance evidence.

The first integration target is deliberately small:

- **OpenCode server** — provider-neutral interactive agent sessions.
- **Ollama server** — local model inference.
- **[WeftMark](https://github.com/tabenius/WeftMark)** — Change Sets, semantic
  scopes, Git lineage, evidence, handoff, review, and merge/release readiness.
- **[Sylvae](https://github.com/tabenius/sylvae)** — portable `SKILL.md`
  execution with durable run evidence.
- **Ephor/KAGP connector** — policy evaluation, risk classification, human
  oversight, and tamper-evident governance records.

Rebekah owns reproducible composition, isolation, service wiring, lifecycle,
and end-to-end verification. It does not replace the domains of the components
it runs or create another source of truth.

## System boundary

| Component | Authoritative responsibility |
| --- | --- |
| OpenCode | Interactive agent sessions and workspace access |
| Ollama | Local model inference |
| Sylvae | Skill execution and run evidence |
| WeftMark | Engineering provenance, review, evidence policy, and readiness |
| Ephor/KAGP | Governance policy, risk, oversight, and audit-chain records |
| Rebekah | Packaging, isolation, wiring, lifecycle, and integration verification |

**Ephor** is the **Konsonans AI Governance Platform (KAGP)**. Its repository is
[`tabenius/BAZ.AI-governance`](https://github.com/tabenius/BAZ.AI-governance).

## Correlation spine

A governed unit of work must remain traceable across all participating services:

```text
WeftMark Change Set ID
        │
        ├── OpenCode session ID
        ├── Sylvae run_id
        └── Ephor entry_id
                └── chain_hash
```

The WeftMark Change Set is the workflow subject. Provider-specific identifiers
remain correlated attributes rather than competing identities.

Ephor governance decisions should enter WeftMark as typed
`ephor:governance` evidence. Ephor supplies the policy decision and
tamper-evident chain reference; WeftMark remains responsible for deciding
whether a Change Set is `READY`.

See the [bootstrap integration contract](docs/bootstrap-contract.md) for the
initial correlation envelope, deployment boundary, evidence shape, and
acceptance test.

## Initial deployment shape

```text
Rebekah · Nix-built container
├── supervised services
│   ├── Ollama
│   ├── OpenCode
│   ├── Sylvae
│   └── WeftMark
├── Ephor/KAGP connector
├── shared correlation envelope
├── isolated persistent state
└── end-to-end smoke test
```

Internal services should bind only to loopback or a private container network.
Remote access belongs behind an authenticated TLS proxy or secure tunnel.
Secrets must be injected at runtime and must not enter the image or Nix store.

## First milestone

The bootstrap milestone is one reproducible test flow:

```text
Change Set
  → correlated OpenCode/Sylvae execution
  → Ephor policy record and chain hash
  → typed WeftMark evidence
  → readiness decision
```

The test must also prove that missing, unavailable, or failed governance cannot
silently become approved evidence.

## Build the bootstrap image

Nix with flakes enabled is required.

```bash
nix flake check
nix build .#image
docker load < result
docker run --rm rebekah:bootstrap doctor
```

Run the container smoke test after loading the image:

```bash
tests/smoke.sh
```

The image currently validates its filesystem, service-state boundaries, and
Change Set correlation input. It reports a component binary as `pending` when
that component has not yet been packaged; pending never means healthy or
approved.

## Repository layout

- `flake.nix` defines supported systems, the image package, and shell checks.
- `nix/image.nix` defines the OCI image, service identities, state volumes,
  environment, and image metadata.
- `nix/entrypoint.sh` provides the bootstrap doctor and placeholder supervisor.
- `tests/smoke.sh` exercises the image through Docker or another configured
  container runtime.
- `docs/bootstrap-contract.md` defines integration semantics and acceptance
  criteria.

## Status

**Executable bootstrap.** The Nix-built image definition, separated service
identities/state directories, doctor command, and container smoke harness now
exist. Full packaging and supervision of OpenCode, Ollama, Sylvae, and WeftMark
is the next implementation slice.
