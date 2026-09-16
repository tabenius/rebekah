{ lib, python3Packages, makeWrapper, git, src }:

python3Packages.buildPythonApplication {
  pname = "weftmark";
  version = "0.0.1";
  pyproject = true;
  inherit src;

  build-system = [ python3Packages.setuptools ];
  dependencies = [ python3Packages.pyyaml ];
  nativeBuildInputs = [ makeWrapper ];
  # textual (>=8,<9) backs WeftMark's optional TUI surface and is available in
  # nixpkgs, so its tests run. The optional MCP surface needs mcp>=2 (it imports
  # mcp.Client); nixpkgs only ships mcp 1.29, which lacks that API. Rebekah does
  # not build or ship the MCP surface (it wraps weftmark-http and the CLI only),
  # so tests/mcp is scoped out rather than run against an incompatible mcp.
  nativeCheckInputs = [
    git
    python3Packages.pytestCheckHook
    python3Packages.textual
  ];
  disabledTestPaths = [ "tests/mcp" ];

  pythonImportsCheck = [ "weftmark" "weftmark.http.server" ];

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
