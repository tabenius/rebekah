# Ephor is not part of this build

Ephor (the governance engine, KAGP protocol) is an opt-in integration. The
flake's `ephor-src` input points here by default so that the baseline image
builds from public sources only.

To build the opt-in image, override the input with the pinned revision (read
access to the private repository required):

```bash
nix build .#image-ephor \
  --override-input ephor-src "github:tabenius/BAZ.AI-governance/$(cat nix/ephor.rev)"
```
