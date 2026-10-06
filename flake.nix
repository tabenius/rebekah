{
  description = "Rebekah — reproducible runtime for governed agentic work";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    weftmark-src = {
      url = "github:tabenius/WeftMark/8d4e270f3aa18877ba177a19fa5da0fea8266acf";
      flake = false;
    };
    sylvae-src = {
      url = "github:tabenius/sylvae/8b0f950ee2d4ee6f9f24fc58393243c5d4a1b27d";
      flake = false;
    };
    # Pin the public Nostoi source that ships CLI verification for all suite
    # audit formats, including WeftMark JSONL and Ephor SQLite.
    nostoi-src = {
      url = "github:tabenius/nostoi/4e3827a1434cc2bb91f8ef512507f7bc8dfcdbbf";
      flake = false;
    };
    # Ephor is opt-in, and its source is private. Nix fetches every locked
    # input, so the default is an in-repo placeholder and the baseline builds
    # from public sources. The opt-in image overrides it with the pinned rev:
    #   --override-input ephor-src "github:tabenius/BAZ.AI-governance/$(cat nix/ephor.rev)"
    ephor-src = {
      url = "path:./nix/ephor-absent";
      flake = false;
    };
  };

  outputs = { self, nixpkgs, weftmark-src, sylvae-src, nostoi-src, ephor-src }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in {
      packages = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          weftmark = pkgs.callPackage ./nix/packages/weftmark.nix {
            src = weftmark-src;
          };
          sylvae = pkgs.callPackage ./nix/packages/sylvae.nix {
            src = sylvae-src;
            nostoiSrc = nostoi-src;
          };
          nostoi = pkgs.callPackage ./nix/packages/nostoi.nix {
            src = nostoi-src;
          };
          ephor =
            if builtins.pathExists "${ephor-src}/Cargo.lock" then
              pkgs.callPackage ./nix/packages/ephor.nix { src = ephor-src; }
            else
              # Fails when built, not when evaluated, so `nix flake check`
              # (which evaluates every package) passes on the baseline.
              pkgs.runCommand "ephor-opt-in-required" { } ''
                echo "Ephor is opt-in: build with --override-input ephor-src" \
                  "github:tabenius/BAZ.AI-governance/<rev from nix/ephor.rev>" >&2
                exit 1
              '';
        in {
          inherit weftmark sylvae nostoi ephor;
          default = self.packages.${system}.image;
          # The baseline image: no Ephor, public sources only.
          image = pkgs.callPackage ./nix/image.nix {
            inherit weftmark sylvae nostoi;
          };
          # Opt-in: the same image with the Ephor bridge bundled (still off
          # until REBEKAH_EPHOR_ENABLE=1). Needs ephor-src overridden (above).
          image-ephor = pkgs.callPackage ./nix/image.nix {
            inherit weftmark sylvae nostoi ephor;
            inherit (pkgs) litestream;
          };
        });

      checks = forAllSystems (system:
        let pkgs = import nixpkgs { inherit system; };
        in {
          shell = pkgs.runCommand "rebekah-shell-checks" {
            nativeBuildInputs = [ pkgs.shellcheck ];
          } ''
            shellcheck --severity=warning ${./nix/entrypoint.sh} ${./nix/ephor-connector.sh} ${./nix/govern.sh} ${./tests/ephor-connector.sh} ${./tests/smoke.sh} ${./tests/gateway.sh} ${./tests/mcp-bridge.sh} ${./deploy/podman/rebekah-run}
            touch $out
          '';
          gateway = pkgs.runCommand "rebekah-gateway-check" {
            nativeBuildInputs = [ (pkgs.python3.withPackages (ps: [ ps.pyjwt ps.cryptography ])) ];
          } ''
            python3 -m py_compile ${./nix/gateway.py} ${./nix/mcp-bridge.py} ${./tests/gateway-oidc.py} ${./tests/dash-push.py}
            touch $out
          '';
          # No ephor here: `nix flake check` must pass from public sources.
          # CI builds .#ephor and .#image-ephor in a separate, token-gated job.
          inherit (self.packages.${system}) weftmark sylvae nostoi;
        });

      formatter = forAllSystems (system:
        nixpkgs.legacyPackages.${system}.nixfmt-rfc-style);
    };
}
