{
  description = "Rebekah — reproducible runtime for governed agentic work";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    weftmark-src = {
      url = "github:tabenius/WeftMark";
      flake = false;
    };
    sylvae-src = {
      url = "github:tabenius/sylvae/master";
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

  outputs = { self, nixpkgs, weftmark-src, sylvae-src, ephor-src }:
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
          };
          ephor =
            if builtins.pathExists "${ephor-src}/Cargo.lock" then
              pkgs.callPackage ./nix/packages/ephor.nix { src = ephor-src; }
            else
              throw ("Ephor is opt-in: build with --override-input ephor-src "
                + "github:tabenius/BAZ.AI-governance/<rev from nix/ephor.rev>");
        in {
          inherit weftmark sylvae ephor;
          default = self.packages.${system}.image;
          # The baseline image: no Ephor, public sources only.
          image = pkgs.callPackage ./nix/image.nix {
            inherit weftmark sylvae;
          };
          # Opt-in: the same image with the Ephor bridge bundled (still off
          # until REBEKAH_EPHOR_ENABLE=1). Needs ephor-src overridden (above).
          image-ephor = pkgs.callPackage ./nix/image.nix {
            inherit weftmark sylvae ephor;
          };
        });

      checks = forAllSystems (system:
        let pkgs = import nixpkgs { inherit system; };
        in {
          shell = pkgs.runCommand "rebekah-shell-checks" {
            nativeBuildInputs = [ pkgs.shellcheck ];
          } ''
            shellcheck --severity=warning ${./nix/entrypoint.sh} ${./nix/ephor-connector.sh} ${./nix/govern.sh} ${./tests/ephor-connector.sh} ${./tests/smoke.sh} ${./tests/gateway.sh} ${./deploy/podman/rebekah-run}
            touch $out
          '';
          gateway = pkgs.runCommand "rebekah-gateway-check" {
            nativeBuildInputs = [ (pkgs.python3.withPackages (ps: [ ps.pyjwt ps.cryptography ])) ];
          } ''
            python3 -m py_compile ${./nix/gateway.py} ${./tests/gateway-oidc.py} ${./tests/dash-push.py}
            touch $out
          '';
          # No ephor here: `nix flake check` must pass from public sources.
          # CI builds .#ephor and .#image-ephor in a separate, token-gated job.
          inherit (self.packages.${system}) weftmark sylvae;
        });

      formatter = forAllSystems (system:
        nixpkgs.legacyPackages.${system}.nixfmt-rfc-style);
    };
}
