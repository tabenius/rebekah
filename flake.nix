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
  };

  outputs = { self, nixpkgs, weftmark-src, sylvae-src }:
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
        in {
          inherit weftmark sylvae;
          default = self.packages.${system}.image;
          image = pkgs.callPackage ./nix/image.nix {
            inherit weftmark sylvae;
          };
        });

      checks = forAllSystems (system:
        let pkgs = import nixpkgs { inherit system; };
        in {
          shell = pkgs.runCommand "rebekah-shell-checks" {
            nativeBuildInputs = [ pkgs.shellcheck ];
          } ''
            shellcheck ${./nix/entrypoint.sh} ${./tests/smoke.sh}
            touch $out
          '';
          inherit (self.packages.${system}) weftmark sylvae;
        });

      formatter = forAllSystems (system:
        nixpkgs.legacyPackages.${system}.nixfmt-rfc-style);
    };
}
