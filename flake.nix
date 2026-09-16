{
  description = "Rebekah — reproducible runtime for governed agentic work";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in {
      packages = forAllSystems (system:
        let pkgs = import nixpkgs { inherit system; };
        in {
          default = self.packages.${system}.image;
          image = pkgs.callPackage ./nix/image.nix { };
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
        });

      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt-rfc-style);
    };
}
