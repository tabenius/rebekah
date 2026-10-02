#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import os
import subprocess
from datetime import datetime, timedelta, timezone

spec = importlib.util.spec_from_file_location("gateway", Path(__file__).resolve().parents[1] / "nix/gateway.py")
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


class RuntimeTests(unittest.TestCase):
    def test_baseline_has_native_review_and_explicit_unknown_host(self):
        cfg = gateway.Config({"REBEKAH_GATEWAY_TOKEN": "test"})
        auth = type("Auth", (), {"password_active": False})()
        report = gateway.v1_system(cfg, auth)
        self.assertTrue(report["workflows"]["native_review"])
        self.assertFalse(report["workflows"]["hitl"]["governed_holds"])
        self.assertEqual(report["runtime"]["status"], "not-configured")
        self.assertEqual(report["ephor"]["state"], "absent")
        self.assertFalse(report["workflows"]["web"]["reads_logged"])

    def test_stale_snapshot_is_carried_to_system_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({"schema": "ragbaz.runtime-status.v1", "components": {},
                "observed_at": (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat(),
                "summary": ["minotaur: running-but-not-enabled"]}))
            cfg = gateway.Config({"REBEKAH_RUNTIME_STATUS": str(path), "REBEKAH_EPHOR_STATE": "disabled"})
            report = gateway.v1_system(cfg, type("Auth", (), {"password_active": False})())
            self.assertEqual(report["runtime"]["status"], "stale")
            self.assertIn("running-but-not-enabled", report["runtime"]["summary"][0])
            path.write_text("{}")
            self.assertEqual(gateway.runtime_snapshot(str(path))["status"], "unavailable")

    def test_launcher_mounts_status_directory_read_only_without_engine_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "podman"
            fake.write_text('#!/bin/sh\nif [ "$1" = run ]; then printf "%s\\n" "$@"; fi\n')
            fake.chmod(0o755)
            status = root / "status"
            status.mkdir()
            repo = Path(__file__).resolve().parents[1]
            result = subprocess.run(["bash", str(repo / "deploy/podman/rebekah-run")],
                capture_output=True, text=True, check=False,
                env={**os.environ, "PATH": str(root) + ":" + os.environ["PATH"],
                     "REBEKAH_IMAGE": "sha256:test-fixture", "REBEKAH_WORKSPACE": str(repo),
                     "REBEKAH_RUNTIME_STATUS_DIR": str(status), "REBEKAH_ENV_FILE": str(root / "none")})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"type=bind,src={status},dst=/run/host-status,ro", result.stdout)
            self.assertIn("RAGBAZ_RUNTIME_STATUS=/run/host-status/status.json", result.stdout)
            self.assertNotIn("podman.sock", result.stdout)


if __name__ == "__main__":
    unittest.main()
