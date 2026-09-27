{ lib, rustPlatform, pkg-config, openssl, src }:

rustPlatform.buildRustPackage {
  pname = "ephor";
  version = "0.1.0";
  inherit src;

  cargoLock.lockFile = "${src}/Cargo.lock";
  # governance-http: the audit chain and the bridge's oversight routes.
  # agent-proxy: the MCP gate in front of the tools OpenCode's agents use,
  # which holds a call until a reviewer decides.
  cargoBuildFlags = [ "-p" "governance-http" "-p" "agent-proxy" ];
  cargoTestFlags = [ "-p" "governance-http" "-p" "agent-proxy" ];

  # agent-proxy's mail notifications link OpenSSL (lettre, native-tls).
  nativeBuildInputs = [ pkg-config ];
  buildInputs = [ openssl ];

  postInstall = ''
    test -x "$out/bin/governance-http"
    test -x "$out/bin/agent-proxy"
  '';

  meta = {
    description = "Ephor/KAGP governance: the HTTP bridge and the MCP agent proxy";
    homepage = "https://github.com/tabenius/BAZ.AI-governance";
    license = lib.licenses.mit;
    mainProgram = "governance-http";
    platforms = lib.platforms.linux;
  };
}
