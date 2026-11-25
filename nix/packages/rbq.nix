{ lib, rustPlatform, stdenv, src }:

rustPlatform.buildRustPackage {
  pname = "rbq";
  version = "0.1.4";
  inherit src;
  cargoHash = "sha256-CZZluzawPbgVEoRKPXyPYlIx38GtlgbbO1Y+Q5ewwhs=";
  cargoBuildFlags = [ "-p" "rebsync-cli" "--bin" "rbq" ];
  # Pairing requires an explicit credential backend and a Worker test fixture;
  # the owned source suite and image integration tests exercise that boundary.
  doCheck = false;
  doInstallCheck = true;
  installPhase = ''
    runHook preInstall
    install -Dm0555 target/${stdenv.hostPlatform.rust.rustcTarget}/release/rbq $out/bin/rbq
    runHook postInstall
  '';
  installCheckPhase = ''
    $out/bin/rbq dash connector --help | grep -q -- --instance
  '';
  meta = {
    description = "RBQ object storage and explicit passkey-approved Rebekah connector pairing";
    license = lib.licenses.gpl3Plus;
    mainProgram = "rbq";
  };
}
