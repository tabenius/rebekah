{ lib, python3Packages, makeWrapper, git, src, callPackage }:

let
  # WeftMark's MCP server (weftmark-mcp) needs the MCP SDK 2.x; see mcp2.nix.
  mcp = callPackage ./mcp2.nix { inherit python3Packages; };
in

python3Packages.buildPythonApplication {
  pname = "weftmark";
  version = "0.0.1";
  pyproject = true;
  inherit src;

  build-system = [ python3Packages.setuptools ];
  dependencies = [ python3Packages.pyyaml mcp ];
  nativeBuildInputs = [ makeWrapper ];
  # textual (>=8,<9) backs WeftMark's optional TUI surface and is available in
  # nixpkgs, so its tests run. The MCP surface (weftmark-mcp) is shipped for
  # Ephor's agent-proxy to put in front of OpenCode, so tests/mcp runs too.
  nativeCheckInputs = [
    git
    python3Packages.pytestCheckHook
    python3Packages.textual
  ];

  pythonImportsCheck = [ "weftmark" "weftmark.http.server" "weftmark.mcp.server" ];

  postInstall = ''
    makeWrapper ${python3Packages.python.interpreter} $out/bin/weftmark-http \
      --prefix PYTHONPATH : "$out/${python3Packages.python.sitePackages}" \
      --add-flags "-m weftmark.http.server"
  '';

  meta = {
    description = "Coordination, provenance, evidence, and review for multi-agent work";
    homepage = "https://github.com/tabenius/WeftMark";
    license = lib.licenses.asl20;
    mainProgram = "weftmark";
  };
}
