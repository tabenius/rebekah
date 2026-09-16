{ python3Packages, src }:

python3Packages.buildPythonApplication {
  pname = "sylvae";
  version = "0.1.0";
  pyproject = true;
  inherit src;

  build-system = [ python3Packages.hatchling ];
  dependencies = with python3Packages; [ anthropic litellm pyyaml ];

  pythonImportsCheck = [ "sylvae" "sylvae.review" ];
  doCheck = false;

  meta = {
    description = "Portable skill runner across agent backends";
    homepage = "https://github.com/tabenius/sylvae";
    mainProgram = "sylvae";
  };
}
