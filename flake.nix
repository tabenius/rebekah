{
  description = "Rebekah — reproducible runtime for governed agentic work";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    weftmark-src = {
      url = "github:tabenius/WeftMark/claude/pensive-pasteur-6cah6b";
      flake = false;
    };
    sylvae-src = {
      url = "github:tabenius/sylvae/claude/pensive-pasteur-6cah6b";
      flake = false;
    };
    ephor-src = {
      url = "github:tabenius/BAZ.AI-governance";
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
          ephor = pkgs.callPackage ./nix/packages/ephor.nix {
            src = ephor-src;
          };
        in {
          inherit weftmark sylvae ephor;
          default = self.packages.${system}.image;
          image = pkgs.callPackage ./nix/image.nix {
            inherit weftmark sylvae ephor;
          };
        });

      checks = forAllSystems (system:
        let pkgs = import nixpkgs { inherit system; };
        in {
          shell = pkgs.runCommand "rebekah-shell-checks" {
            nativeBuildInputs = [ pkgs.shellcheck ];
          } ''
            shellcheck --severity=warning ${./nix/entrypoint.sh} ${./nix/ephor-connector.sh} ${./nix/govern.sh} ${./tests/ephor-connector.sh} ${./tests/smoke.sh} ${./tests/gateway.sh}
            touch $out
          '';
          gateway = pkgs.runCommand "rebekah-gateway-check" {
            nativeBuildInputs = [ (pkgs.python3.withPackages (ps: [ ps.pyjwt ps.cryptography ])) ];
          } ''
            python3 -m py_compile ${./nix/gateway.py} ${./tests/gateway-oidc.py}
            touch $out
          '';
          inherit (self.packages.${system}) weftmark sylvae ephor;
        });

      formatter = forAllSystems (system:
        nixpkgs.legacyPackages.${system}.nixfmt-rfc-style);
    };
}
