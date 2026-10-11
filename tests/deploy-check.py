#!/usr/bin/env python3
"""Check that an image or a running instance can read the edition the host generates.

A gateway older than the generator rejects a new edition and reports the whole
view unavailable, which is indistinguishable from a damaged artifact. That is
how a stale image survived a full test run: the tests exercised the source, not
what was running. This compares the two before it costs anyone a day.

  --image NAME   check an image instead of a running container (CI, pre-activate)
  REBEKAH_NAME   the running container to check (default: rebekah)

Reports, exits non-zero on a mismatch:

  contract   the gateway does not understand the generated contract
  revision   the gateway differs from the source gateway in the repository
  service    the container is not running or not healthy

Read-only: it inspects, it never rebuilds, restarts or deploys.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "nix/gateway.py"
CONTAINER = os.environ.get("REBEKAH_NAME", "rebekah")
RUNTIME = os.environ.get("CONTAINER_RUNTIME", "podman")
# Bound every probe: this runs before a deployment, so it must not hang.
PROBE_TIMEOUT = 20


def run(argv, **kwargs):
    return subprocess.run(argv, capture_output=True, text=True, timeout=PROBE_TIMEOUT, **kwargs)


def source_contract():
    """The contract the source gateway accepts, read from the code itself."""
    text = GATEWAY.read_text(encoding="utf-8")
    marker = "AGENDA_CONTRACTS = {"
    start = text.find(marker)
    if start < 0:
        raise SystemExit("source gateway has no AGENDA_CONTRACTS declaration")
    body = text[start + len(marker):text.find("}", start)]
    return {value.strip().strip('"\'') for value in body.split(",") if value.strip()}


def source_digest():
    return hashlib.sha256(GATEWAY.read_bytes()).hexdigest()


def container_file(path, image):
    """Read a file out of the image or the running container, executing nothing.

    Returns None when the file is genuinely unreadable. A runtime that cannot
    start the image at all also yields None, so an unreadable image is never
    mistaken for one that simply omits a field.
    """
    if image:
        argv = [RUNTIME, "run", "--rm", "--entrypoint", "cat", image, path]
    else:
        argv = [RUNTIME, "exec", CONTAINER, "cat", path]
    result = run(argv)
    if result.returncode:
        return None
    # A runtime error can exit zero with an empty body; require real content.
    return result.stdout if result.stdout.strip() else None


def container_contracts(image):
    """Contracts the gateway declares.

    Returns (readable, contracts). readable is False when the gateway file
    could not be read at all; contracts is None when it was read but predates
    the constant, meaning it cannot read a contract-named edition.
    """
    text = container_file("/usr/local/lib/rebekah/gateway.py", image)
    if text is None:
        return False, None
    marker = "AGENDA_CONTRACTS = {"
    start = text.find(marker)
    if start < 0:
        return True, None
    body = text[start + len(marker):text.find("}", start)]
    return True, {value.strip().strip('"\'') for value in body.split(",") if value.strip()}


def container_running_contract():
    """The contract the edition on disk declares, if it declares one."""
    view = os.environ.get("REBEKAH_DAILY_AGENDA_HOST_DIR") or "/run/daily-agenda"
    host_dir = os.environ.get("REBEKAH_DAILY_AGENDA_DIR", "")
    directory = Path(host_dir) if host_dir else Path(view)
    manifest = directory / "manifest.json"
    if not manifest.is_file():
        return None, None
    edition = json.loads(manifest.read_text(encoding="utf-8"))
    return edition.get("generator_contract"), edition.get("generator")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", help="check this image instead of a running container")
    image = parser.parse_args().image

    problems = []
    subject = f"image '{image}'" if image else f"container '{CONTAINER}'"
    print(f"source gateway contracts: {', '.join(sorted(source_contract()))}")
    print(f"source gateway sha256:   {source_digest()[:16]}")

    if not image:
        health = run([RUNTIME, "exec", CONTAINER, "rebekah-health"])
        if health.returncode:
            print(f"FAIL service: container '{CONTAINER}' is not healthy")
            print(health.stdout.strip() or health.stderr.strip())
            return 1
        print(f"container '{CONTAINER}': healthy")

    readable, running = container_contracts(image)
    if not readable:
        # Exit 2, distinct from a mismatch: the image could not be inspected at
        # all. An unreadable image will not start regardless, and a launcher
        # reading this must not mistake a runtime hiccup for a bad contract.
        print(f"INCONCLUSIVE: cannot read the gateway in {subject}")
        return 2
    if running:
        print(f"{'image' if image else 'running'} gateway contracts: {', '.join(sorted(running))}")
    else:
        print(f"{'image' if image else 'running'} gateway contracts: declares none (predates the contract check)")

    if running != source_contract():
        problems.append(
            f"the {subject} accepts {sorted(running) if running else 'nothing'} "
            f"but the source accepts {sorted(source_contract())}")
    running_digest = hashlib.sha256(
        (container_file("/usr/local/lib/rebekah/gateway.py", image) or "").encode()).hexdigest()
    if running_digest != source_digest():
        print(f"NOTE the gateway in {subject} differs from {GATEWAY} (expected when the")
        print("     image predates an uncommitted or unpushed change)")

    contract, generator = container_running_contract()
    if contract and running is not None and contract not in running:
        problems.append(f"the generated edition uses contract '{contract}', "
                        f"which the {subject} does not accept")
    if contract:
        # The snapshot carries the revision object; the manifest's own
        # `generator` key is a legacy path string, so it is not read here.
        edition_dir = Path(os.environ.get("REBEKAH_DAILY_AGENDA_DIR") or os.environ.get("REBEKAH_DAILY_AGENDA_HOST_DIR") or "/run/daily-agenda")
        snapshot = edition_dir / "snapshot.json"
        recorded = json.loads(snapshot.read_text(encoding="utf-8")).get("generator") if snapshot.is_file() else None
        revision = recorded.get("revision") if isinstance(recorded, dict) else None
        print(f"generated edition contract: {contract} (generator {str(revision)[:12] or 'unrecorded'})")

    if problems:
        for problem in problems:
            print(f"FAIL {problem}")
        print("\nRebuild and activate the image before deploying, or the agenda will read unavailable.")
        return 1
    print(f"\nOK: {subject} can read the edition this host generates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())