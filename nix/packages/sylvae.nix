{ python3Packages, src }:

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
      --replace-fail "import json" "import json\nimport os" \
      --replace-fail 'model: str = "ollama/qwen2.5:14b"' 'model: str | None = None' \
      --replace-fail 'api_base: str = "http://localhost:11434"' 'api_base: str | None = None' \
      --replace-fail 'self.model = model' 'self.model = model or os.environ.get("SYLVAE_OLLAMA_MODEL", "qwen2.5:0.5b")' \
      --replace-fail 'self.api_base = api_base' 'self.api_base = api_base or os.environ.get("OLLAMA_API_BASE", "http://localhost:11434")'
  '';

  build-system = [ python3Packages.hatchling ];
  dependencies = with python3Packages; [ anthropic litellm pyyaml ];

  nativeCheckInputs = [ python3Packages.pytestCheckHook ];

  pythonImportsCheck = [ "sylvae" "sylvae.review" ];

  meta = {
    description = "Portable skill runner across agent backends";
    homepage = "https://github.com/tabenius/sylvae";
    mainProgram = "sylvae";
  };
}
