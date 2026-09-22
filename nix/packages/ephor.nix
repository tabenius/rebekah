{ lib, rustPlatform, src }:

rustPlatform.buildRustPackage {
  pname = "ephor-governance-http";
  version = "0.1.0";
  inherit src;

  cargoLock.lockFile = "${src}/Cargo.lock";
  cargoBuildFlags = [ "-p" "governance-http" ];
  cargoTestFlags = [ "-p" "governance-http" ];

  postInstall = ''
    test -x "$out/bin/governance-http"
  '';

  meta = {
    description = "Ephor/KAGP governance HTTP bridge";
    homepage = "https://github.com/tabenius/BAZ.AI-governance";
    license = lib.licenses.mit;
    mainProgram = "governance-http";
    platforms = lib.platforms.linux;
  };
}
