#!/usr/bin/env python3
"""Exercise the changed gateway/UI in an isolated existing Rebekah runtime image.

No service restart or deployment. The new sources are mounted read-only over the
image copies; this checks the installed interpreter and unprivileged gateway UID.
Set REBEKAH_TEST_IMAGE to a locally available image (default localhost/rebekah:latest).
"""
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("agenda_tests", ROOT / "tests/daily-agenda.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
case = fixture.AgendaTests()
case.setUp()
container = None
name = "rebekah-agenda-check-" + secrets.token_hex(5)
image = os.environ.get("REBEKAH_TEST_IMAGE", "localhost/rebekah:latest")
try:
    # Public artificial fixture only, not the user's private reports.
    case.directory.chmod(0o755)
    for path in case.directory.iterdir():
        path.chmod(0o644)
    args = ["podman", "run", "--detach", "--rm", "--name", name,
        "--user", "10005:10005", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--entrypoint", "/bin/tini",
        "--env", "REBEKAH_AUTH_PASSWORD=0", "--env", "REBEKAH_GATEWAY_TOKEN=agenda-container-fixture",
        "--env", "REBEKAH_GATEWAY_HOST=127.0.0.1", "--env", "REBEKAH_DAILY_AGENDA_DIR=/run/daily-agenda",
        "--mount", f"type=bind,src={ROOT / 'nix/gateway.py'},dst=/usr/local/lib/rebekah/gateway.py,ro",
        "--mount", f"type=bind,src={ROOT / 'nix/ui'},dst=/usr/local/share/rebekah/ui,ro",
        "--mount", f"type=bind,src={case.directory},dst=/run/daily-agenda,ro", image,
        "--", "/usr/local/bin/rebekah-gateway"]
    # Use the container's loopback and exec-side client; expose no listener.
    container = subprocess.check_output(args, text=True).strip()
    print(f"Isolated fixture container {name}: {container[:12]}", flush=True)
    # curl lives in the image; no credentials from a real instance are involved.
    def request(path, token=False, method="GET"):
        command = ["podman", "exec", container, "curl", "--silent", "--show-error", "--max-time", "5", "--request", method]
        if token:
            command += ["--header", "Authorization: Bearer agenda-container-fixture"]
        command += ["--write-out", "\n%{http_code}", "http://127.0.0.1:8080" + path]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            return 0, result.stderr
        body, status = result.stdout.rsplit("\n", 1)
        return int(status), body
    for _ in range(40):
        status, _ = request("/healthz")
        if status == 200:
            break
        time.sleep(0.25)
    else:
        raise RuntimeError(subprocess.check_output(["podman", "logs", container], text=True))
    assert request("/api/v1/daily-agenda")[0] == 401
    status, body = request("/api/v1/daily-agenda", True)
    data = json.loads(body)
    assert status == 200 and data["status"] == "ready", data
    assert data["items"][0]["tasks"][0]["scope"] == "Full recorded scope"
    assert request("/api/v1/daily-agenda", True, "POST")[0] == 405
    status, html = request("/")
    assert status == 200 and 'id="agendaEntry"' in html and 'id="panel-agenda"' in html
    assert request("/ui/snapshot.json")[0] == 404
    print("PASS: installed runtime Python, UID 10005, read-only agenda mount, auth, readonly route, Work card/tab shell, no public artifact route.")
finally:
    if container:
        # Enumerate and verify our exact just-started container before stopping it.
        actual = subprocess.check_output(["podman", "inspect", "--format", "{{.Name}}", container], text=True).strip()
        assert actual == name
        subprocess.run(["podman", "ps", "--filter", "id=" + container, "--format", "{{.ID}} {{.Names}}"], check=True)
        subprocess.run(["podman", "stop", "--time", "5", container], check=True, stdout=subprocess.DEVNULL)
    case.doCleanups()
