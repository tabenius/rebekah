{ python3Packages, src, callPackage, nostoiSrc ? null }:

let
  # `sylvae mcp` uses the MCP SDK 2.x API (MCPServer), though Sylvae's
  # pyproject still says mcp>=1.0; nixpkgs ships 1.x. See mcp2.nix.
  mcp = callPackage ./mcp2.nix { inherit python3Packages; };
in

python3Packages.buildPythonApplication {
  pname = "sylvae";
  version = "0.1.0";
  pyproject = true;
  inherit src;

  # The pinned Sylvae release predates runtime-configurable Ollama defaults.
  # Carry the tiny compatibility patch here until the next upstream pin: it
  # keeps Sylvae aligned with Rebekah/v-BAZ's cached model without hardcoding a
  # second, usually unavailable 14B model.
  postPatch = ''
    substituteInPlace src/sylvae/backends/ollama_backend.py \
      --replace-fail "import json" "import json
import os" \
      --replace-fail 'model: str = "ollama/qwen2.5:14b"' 'model: str | None = None' \
      --replace-fail 'api_base: str = "http://localhost:11434"' 'api_base: str | None = None' \
      --replace-fail 'self.model = model' 'self.model = model or os.environ.get("SYLVAE_OLLAMA_MODEL", "ollama/qwen2.5:14b")' \
      --replace-fail 'self.api_base = api_base' 'self.api_base = api_base or os.environ.get("OLLAMA_API_BASE", "http://localhost:11434")'
    ${if nostoiSrc == null then "" else ''
      cp ${nostoiSrc}/contrib/python/nostoi.py src/sylvae/nostoi_reference.py
    ''}
  '';

  build-system = [ python3Packages.hatchling ];
  # mcp backs `sylvae mcp`, which Ephor's agent-proxy puts in front of OpenCode.
  dependencies = (with python3Packages; [ anthropic litellm pyyaml ]) ++ [ mcp ];

  nativeCheckInputs = [ python3Packages.pytestCheckHook ];
  # This test starts `sys.executable hostile_server.py` through the MCP stdio
  # client, which passes the child only an allow-list of variables, not
  # PYTHONPATH: inside the Nix build the bare interpreter then cannot import
  # mcp. The installed `sylvae` is wrapped, so the runtime is unaffected; the
  # sibling transport test still runs the real server over stdio.
  disabledTests = [ "test_stray_stdout_writes_do_not_corrupt_the_wire" ];

  pythonImportsCheck = [ "sylvae" "sylvae.review" "sylvae.mcp.server" ];

  meta = {
    description = "Portable skill runner across agent backends";
    homepage = "https://github.com/tabenius/sylvae";
    mainProgram = "sylvae";
  };
}
