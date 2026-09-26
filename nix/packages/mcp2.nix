# The MCP Python SDK 2.x, which WeftMark's MCP server needs (it imports
# mcp.Client); nixpkgs ships 1.x. Built from pinned PyPI wheels, with the two
# 2.x-only dependencies (mcp-types, and httpx2 over httpcore2) alongside. The
# rest of the dependency tree comes from nixpkgs.
{ python3Packages }:

let
  inherit (python3Packages) buildPythonPackage fetchPypi;
  wheel = pname: version: hash: fetchPypi {
    inherit pname version hash;
    format = "wheel";
    dist = "py3";
    python = "py3";
  };

  httpcore2 = buildPythonPackage rec {
    pname = "httpcore2";
    version = "2.13.1";
    format = "wheel";
    src = wheel pname version "sha256-4eBdTyX319SWv7lnSPb0tnZXsD2gabOmjDYGnz23PQo=";
    dependencies = with python3Packages; [ h11 truststore ];
    pythonImportsCheck = [ "httpcore2" ];
  };

  httpx2 = buildPythonPackage rec {
    pname = "httpx2";
    version = "2.13.1";
    format = "wheel";
    src = wheel pname version "sha256-bf9Q+rwnDuX9JdhF0LB47SBWRXl0TW2WKFCXWZbS+aQ=";
    dependencies = with python3Packages; [ anyio httpcore2 idna truststore ];
    pythonImportsCheck = [ "httpx2" ];
  };

  mcp-types = buildPythonPackage rec {
    pname = "mcp_types";
    version = "2.2.0";
    format = "wheel";
    src = wheel pname version "sha256-6kdrc+6GcJq1q8lFI4XtNswFkH5YI1ViLilFlcmgTxM=";
    dependencies = with python3Packages; [ pydantic typing-extensions ];
    pythonImportsCheck = [ "mcp_types" ];
  };
in
buildPythonPackage rec {
  pname = "mcp";
  version = "2.2.0";
  format = "wheel";
  src = wheel pname version "sha256-vemCWJRzoGCuFF40BumlMz/lOMlyKbqEH1p/kr4AT4E=";
  dependencies = with python3Packages; [
    anyio httpx2 jsonschema mcp-types opentelemetry-api pydantic pyjwt
    python-multipart sse-starlette starlette typing-extensions
    typing-inspection uvicorn
  ] ++ pyjwt.optional-dependencies.crypto;
  pythonImportsCheck = [ "mcp" "mcp.server" ];
}
