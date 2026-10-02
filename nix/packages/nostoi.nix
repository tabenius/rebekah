{ lib, rustPlatform, src }:

rustPlatform.buildRustPackage {
  pname = "nostoi";
  version = "0.1.0";
  inherit src;
  cargoLock.lockFile = "${src}/Cargo.lock";
  cargoBuildFlags = [ "-p" "nostoi" ];
  cargoTestFlags = [ "-p" "nostoi" ];

  postInstall = ''
    test -x "$out/bin/nostoi"
  '';

  meta = {
    description = "Tamper-evident audit chain verifier and writer";
    homepage = "https://github.com/tabenius/nostoi";
    license = lib.licenses.mit;
    mainProgram = "nostoi";
    platforms = lib.platforms.linux;
  };
}
