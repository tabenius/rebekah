{ lib, python3Packages, makeWrapper, git, src }:

python3Packages.buildPythonApplication {
  pname = "weftmark";
  version = "0.0.1";
  pyproject = true;
  inherit src;

  build-system = [ python3Packages.setuptools ];
  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [ git python3Packages.pytestCheckHook ];

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
