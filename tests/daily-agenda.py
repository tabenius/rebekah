#!/usr/bin/env python3
"""Private agenda integration: bounded artifacts, original freshness and auth."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gateway", ROOT / "nix/gateway.py")
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)


class AgendaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.observed = datetime.now(timezone.utc) - timedelta(hours=1)
        self.snapshot = {"schema": "ragbaz.daily-agenda.v1", "observed_at": self.observed.isoformat(),
            "window_start": (self.observed - timedelta(days=7)).isoformat(), "window_end": self.observed.isoformat(),
            "workspace": "/private/workspace", "ranking": "commits, including merges", "errors": [],
            "projects": [{"name": "frog", "path": "/private/workspace/frog", "head": "a" * 40,
                          "last_commit_at": self.observed.isoformat(), "commit_count": 1,
                          "commits": [{"subject": "Fix relocation", "revision": "a" * 40}],
                          "dirty": [{"path": "private-file", "status": " M"}]}],
            "tasks": [{"slug": "relocate", "repo_path": "/private/workspace/frog", "title": "Move safely",
                       "workflow_status": "in_progress", "priority": "p1", "assigned_agent": "owner",
                       "updated_at": self.observed.isoformat(), "what_text": "Full recorded scope",
                       "why": "Keep locks meaningful", "dependencies": []}],
            "task_statuses": {"relocate": "in_progress"}, "schedule": {"tasks": [], "skipped": [{"slug": "relocate", "reason": "owned by owner"}]}}
        self.editorial = {"schema": "ragbaz.daily-agenda-editorial.v1", "projects": {
            "frog": {"preferred": "relocate", "next": "Coordinate with owner", "concepts": ["vfs"]}},
            "task_steps": {"relocate": ["Inspect existing work"]}, "concepts": {"vfs": {
                "title": "VFS", "brief": "Resolve logical locations", "full": "A full concept explanation",
                "source": "file:///private/workspace/source.md"}}}
        self.write_edition()
        self.cfg = gw.Config({"REBEKAH_DAILY_AGENDA_DIR": str(self.directory),
                              "REBEKAH_GATEWAY_TOKEN": "agenda-test", "REBEKAH_AUTH_PASSWORD": "0"})

    def write_edition(self):
        files = {}
        for name, data in (("snapshot.json", self.snapshot), ("editorial.json", self.editorial)):
            raw = json.dumps(data).encode()
            (self.directory / name).write_bytes(raw)
            files[name] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        (self.directory / "manifest.json").write_text(json.dumps({"schema": "ragbaz.daily-agenda-manifest.v1",
            "id": "agenda-fixture", "observed_at": self.snapshot["observed_at"],
            "generated_at": datetime.now(timezone.utc).isoformat(), "files": files}))

    def test_projects_native_tasks_and_editorial_without_host_paths_or_html(self):
        view = gw.v1_daily_agenda(self.cfg)
        self.assertEqual(view["status"], "ready")
        self.assertEqual(view["source_observed_at"], self.observed.isoformat())
        p = view["items"][0]
        self.assertEqual((p["commit_count"], p["local_path_count"]), (1, 1))
        self.assertEqual(p["tasks"][0]["scope"], "Full recorded scope")
        self.assertEqual(p["tasks"][0]["steps"], ["Inspect existing work"])
        self.assertIn("owned by owner", p["tasks"][0]["scheduler_note"])
        self.assertIsNone(p["concepts"][0]["source_url"])
        self.assertNotIn("/private/workspace", json.dumps(view))
        self.assertNotIn("private-file", json.dumps(view))

    def test_rerender_does_not_rejuvenate_source_evidence(self):
        old = self.observed - timedelta(days=2)
        self.snapshot.update(observed_at=old.isoformat(), window_end=old.isoformat(),
                             window_start=(old - timedelta(days=7)).isoformat())
        self.write_edition()
        view = gw.v1_daily_agenda(self.cfg)
        self.assertEqual(view["status"], "ready")
        self.assertTrue(view["stale"])
        self.assertEqual(view["source_observed_at"], old.isoformat())
        self.assertGreater(view["generated_at"], view["source_observed_at"])

    def test_failed_or_mixed_generation_never_exposes_partial_data(self):
        (self.directory / "snapshot.json").write_text("{}")
        view = gw.v1_daily_agenda(self.cfg)
        self.assertEqual(view["status"], "unavailable")
        self.assertEqual(view["items"], [])
        self.assertNotIn(str(self.directory), json.dumps(view))

    def test_symlink_fifo_oversize_and_future_are_refused(self):
        path = self.directory / "snapshot.json"
        for kind in ("symlink", "fifo", "oversize", "nested", "future"):
            with self.subTest(kind=kind):
                path.unlink()
                if kind == "symlink":
                    path.symlink_to(self.directory / "editorial.json")
                elif kind == "fifo":
                    os.mkfifo(path)
                elif kind == "oversize":
                    path.write_bytes(b" " * (gw.AGENDA_MAX_FILE + 1))
                elif kind == "nested":
                    raw = b"[" * 1200 + b"0" + b"]" * 1200
                    path.write_bytes(raw)
                    manifest_path = self.directory / "manifest.json"
                    manifest = json.loads(manifest_path.read_text())
                    manifest["files"]["snapshot.json"] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                    manifest_path.write_text(json.dumps(manifest))
                else:
                    self.snapshot["observed_at"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
                    self.snapshot["window_end"] = self.snapshot["observed_at"]
                    self.write_edition()
                self.assertEqual(gw.v1_daily_agenda(self.cfg)["status"], "unavailable")
                if kind != "future":
                    path.unlink()
                    self.write_edition()

    def test_absent_configuration_and_missing_directory_are_distinct(self):
        self.assertEqual(gw.v1_daily_agenda(gw.Config({}))["status"], "not-configured")
        cfg = gw.Config({"REBEKAH_DAILY_AGENDA_DIR": str(self.directory / "missing")})
        self.assertEqual(gw.v1_daily_agenda(cfg)["status"], "unavailable")
        self.assertIn("absolute", " ".join(gw.Config({"REBEKAH_DAILY_AGENDA_DIR": "relative"}).validate()))

    def test_large_optional_agenda_cannot_displace_other_push_views(self):
        agenda = gw.v1_daily_agenda(self.cfg)
        agenda["items"][0]["tasks"][0]["scope"] = "s" * 350000
        cfg = gw.Config({"REBEKAH_DASH_URL": "http://127.0.0.1", "REBEKAH_DASH_PUSH_KEY": "fixture"})
        system = {"schema": "rebekah.system.v1", "padding": "x" * 200000}
        with patch.object(gw, "fetch_kanban", return_value=None), patch.object(gw, "v1_system", return_value=system), patch.object(gw, "v1_daily_agenda", return_value=agenda):
            views = gw.DashPusher(cfg, None).views()
        self.assertEqual(views["system"], system)
        self.assertEqual(views["daily-agenda"]["status"], "unavailable")
        self.assertLess(len(json.dumps({"schema": gw.DASH_ENVELOPE, "views": views}).encode()), gw.DASH_MAX_PUSH)

    def test_route_requires_auth_and_is_read_only(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), gw.make_handler(self.cfg, gw.Authenticator(self.cfg)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/api/v1/daily-agenda"
            with self.assertRaises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(url)
            self.assertEqual(denied.exception.code, 401)
            denied.exception.close()
            request = urllib.request.Request(url, headers={"Authorization": "Bearer agenda-test"})
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(json.load(response)["status"], "ready")
            request = urllib.request.Request(url, method="POST", headers={"Authorization": "Bearer agenda-test"})
            with self.assertRaises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(request)
            self.assertEqual(denied.exception.code, 405)
            denied.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_launcher_mounts_directory_read_only(self):
        fake = self.directory / "podman"
        fake.write_text('#!/bin/sh\nif [ "$1" = run ]; then printf "%s\\n" "$@"; fi\n')
        fake.chmod(0o755)
        result = subprocess.run(["bash", str(ROOT / "deploy/podman/rebekah-run")], capture_output=True, text=True,
            env={**os.environ, "PATH": str(self.directory) + ":" + os.environ["PATH"],
                 "REBEKAH_IMAGE": "sha256:fixture", "REBEKAH_WORKSPACE": str(ROOT),
                 "REBEKAH_ENV_FILE": str(self.directory / "none"), "REBEKAH_DAILY_AGENDA_HOST_DIR": str(self.directory)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"type=bind,src={self.directory},dst=/run/daily-agenda,ro", result.stdout)
        self.assertIn("REBEKAH_DAILY_AGENDA_DIR=/run/daily-agenda", result.stdout)


if __name__ == "__main__":
    unittest.main()
