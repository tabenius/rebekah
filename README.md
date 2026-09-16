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
> Rebekah is at the beginning of its design and implementation. The architecture
> below describes the initial direction, not a production-ready release.

## Purpose

Rebekah will compose a Docker container based on **NixOS** that brings together
local model serving, agent execution, and durable evidence about software work.

The first integration target is deliberately small:

- **OpenCode server** — the provider-neutral agent interface and remote session
  surface.
- **Ollama server** — local model serving.
- **[WeftMark](https://github.com/tabenius/WeftMark)** — the vendor-neutral
  control plane for scope, Git lineage, evidence, handoff, review, and
  merge/release readiness.
- **[Sylvae](https://github.com/tabenius/sylvae)** — execution of portable
  `SKILL.md` workflows across supported agent runtimes, with durable evidence.

Rebekah is intended to provide the reproducible runtime in which these
components can work together. It will not replace their individual domains or
make a Kanban interface, agent runtime, or model provider the source of truth.

## Related governance project

**Ephor** is the **Konsonans AI Governance Platform (KAGP)**. Its repository is
[`tabenius/BAZ.AI-governance`](https://github.com/tabenius/BAZ.AI-governance).

Rebekah is the dedicated composition and runtime repository. Governance policy,
runtime orchestration, execution evidence, and change provenance should remain
separable even when they are deployed together.

## Initial architecture

```text
Rebekah (NixOS-based container)
├── OpenCode server
├── Ollama server
├── WeftMark
└── Sylvae
```

The first milestone is a reproducible development container in which all four
components can start, discover their required local services, and expose only
explicitly configured interfaces.

## Status

**Bootstrap phase.** The repository currently records the initial product
boundary. Container definitions, pinned dependencies, service configuration,
and verification will follow.
