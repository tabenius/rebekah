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

    def test_governed_change_sets_are_projected_bounded_and_validated(self):
        self.snapshot["projects"][0]["governed"] = {"available": True, "reason": "", "change_sets": [{
            "id": "agenda-outage", "goal": "Offline tolerance", "state": "active", "readiness": "unreviewed",
            "branch": "main", "head": "c" * 40, "observed_at": self.observed.isoformat(),
            "evidence_current": 2, "evidence_failed": 1, "evidence_obsolete": 0,
            "dirty_paths": 3, "scope_collisions": 1}]}
        self.write_edition()
        view = gw.v1_daily_agenda(self.cfg)
        governed = view["items"][0]["governed"]
        self.assertTrue(governed["available"])
        change = governed["change_sets"][0]
        self.assertEqual((change["evidence_current"], change["evidence_failed"]), (2, 1))
        self.assertEqual(change["scope_collisions"], 1)
        # A malformed governed record must fail the edition, not read as "none".
        for mutation in (
            lambda g: g["change_sets"][0].update(id="../escape"),
            lambda g: g["change_sets"][0].update(evidence_current=-1),
            lambda g: g["change_sets"][0].update(evidence_current=True),
            lambda g: g["change_sets"][0].update(observed_at="not-a-time"),
            lambda g: g.update(available="yes"),
            lambda g: g.update(available=False, reason=""),
        ):
            self.snapshot["projects"][0]["governed"] = json.loads(json.dumps(self.snapshot["projects"][0]["governed"]))
            mutation(self.snapshot["projects"][0]["governed"])
            self.write_edition()
            self.assertEqual(gw.v1_daily_agenda(self.cfg)["status"], "unavailable")

    def test_a_repository_without_a_ledger_is_not_a_collection_failure(self):
        view = gw.v1_daily_agenda(self.cfg)
        self.assertEqual(view["status"], "ready")
        self.assertIsNone(view["items"][0]["governed"])
        self.assertEqual(view["collection_error_count"], 0)
        self.snapshot["projects"][0]["governed"] = {"available": False, "reason": "no local ledger", "change_sets": []}
        self.write_edition()
        view = gw.v1_daily_agenda(self.cfg)
        self.assertEqual(view["status"], "ready")
        self.assertFalse(view["items"][0]["governed"]["available"])
        self.assertEqual(view["items"][0]["governed"]["change_sets"], [])

    def test_an_unknown_generation_contract_is_its_own_state(self):
        self.snapshot["generator_contract"] = "ragbaz.daily-agenda.generator.v9"
        self.write_edition()
        view = gw.v1_daily_agenda(self.cfg)
        self.assertEqual(view["status"], "contract-mismatch")
        self.assertEqual(view["generator_contract"], "ragbaz.daily-agenda.generator.v9")
        self.assertIn("ragbaz.daily-agenda.generator.v1", view["supported_contracts"])
        self.assertEqual(view["items"], [])
        # A damaged edition is still a different fault from an unreadable contract.
        self.snapshot["generator_contract"] = "ragbaz.daily-agenda.generator.v1"
        self.write_edition()
        (self.directory / "snapshot.json").write_text("{")
        self.assertEqual(gw.v1_daily_agenda(self.cfg)["status"], "unavailable")

    def test_an_edition_without_a_contract_is_still_served(self):
        self.snapshot.pop("generator_contract", None)
        self.write_edition()
        view = gw.v1_daily_agenda(self.cfg)
        self.assertEqual(view["status"], "ready")
        self.assertEqual(view["generator_contract"], None)

    def test_the_generator_revision_is_validated_and_projected(self):
        self.snapshot["generator"] = {"revision": "a" * 40, "dirty": True}
        self.write_edition()
        view = gw.v1_daily_agenda(self.cfg)
        self.assertEqual(view["status"], "ready")
        self.assertEqual(view["generator"], {"revision": "a" * 40, "dirty": True})
        for mutation in (
            lambda g: g.update(revision="not-a-sha"),
            lambda g: g.update(revision="a" * 39),
            lambda g: g.update(dirty="yes"),
            lambda g: g.update(extra="ignored-or-not"),
        ):
            self.snapshot["generator"] = {"revision": "a" * 40, "dirty": True}
            mutation(self.snapshot["generator"])
            self.write_edition()
            self.assertEqual(gw.v1_daily_agenda(self.cfg)["status"], "unavailable")

    def test_model_provenance_is_bounded_and_does_not_expose_endpoint(self):
        self.snapshot["generation"] = {"provider": "ollama-local", "model": "qwen2.5:3b",
            "model_digest": "sha256:" + "a" * 64, "network": "offline", "status": "ready",
            "summary": "Inspect the native records <script>untrusted text</script>",
            "reason": "Local data only", "endpoint": "http://localhost/private"}
        self.write_edition()
        view = gw.v1_daily_agenda(self.cfg)
        self.assertEqual(view["generation"]["model"], "qwen2.5:3b")
        self.assertEqual(view["generation"]["network"], "offline")
        self.assertNotIn("endpoint", view["generation"])
        self.snapshot["generation"]["provider"] = "unknown"
        self.write_edition()
        self.assertEqual(gw.v1_daily_agenda(self.cfg)["status"], "unavailable")

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
        # The fixture image declares no agenda contract, so the launcher's
        # pre-start check is opted out of here; it has its own tests below.
        result = subprocess.run(["bash", str(ROOT / "deploy/podman/rebekah-run")], capture_output=True, text=True,
            env={**os.environ, "PATH": str(self.directory) + ":" + os.environ["PATH"],
                 "REBEKAH_SKIP_CONTRACT_CHECK": "1",
                 "REBEKAH_IMAGE": "sha256:fixture", "REBEKAH_WORKSPACE": str(ROOT),
                 "REBEKAH_ENV_FILE": str(self.directory / "none"), "REBEKAH_DAILY_AGENDA_HOST_DIR": str(self.directory)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"type=bind,src={self.directory},dst=/run/daily-agenda,ro", result.stdout)
        self.assertIn("REBEKAH_DAILY_AGENDA_DIR=/run/daily-agenda", result.stdout)

    def _launch(self, **extra):
        """Run the launcher with a stub podman that reports a fixture image."""
        stub = self.directory / "podman"
        # deploy-check inspects with `run --rm --entrypoint cat <image> <path>`;
        # the launcher runs `run --rm --name <name> ...`. Only the former is
        # made to fail, so the test exercises the check rather than the launch.
        stub.write_text(
            '#!/bin/sh\n'
            'for arg in "$@"; do [ "$arg" = --entrypoint ] && exit "${STUB_IMAGE_READ:-0}"; done\n'
            'if [ "$1" = run ]; then printf "LAUNCHED\\n"; fi\n'
            'exit 0\n')
        stub.chmod(0o755)
        checker = ROOT / "tests/deploy-check.py"
        self.assertTrue(checker.is_file(), "deploy-check.py must ship with the repository")
        return subprocess.run(["bash", str(ROOT / "deploy/podman/rebekah-run")], capture_output=True, text=True,
            env={**os.environ, "PATH": str(self.directory) + ":" + os.environ["PATH"],
                 "REBEKAH_IMAGE": "sha256:fixture", "REBEKAH_WORKSPACE": str(ROOT),
                 "REBEKAH_DAILY_AGENDA_HOST_DIR": str(self.directory), **extra})

    def test_launcher_refuses_an_image_whose_contract_cannot_be_read(self):
        # deploy-check exits 2 when it cannot inspect the image at all.
        result = self._launch(STUB_IMAGE_READ="2")
        self.assertEqual(result.returncode, 0, "an uninspectable image must not block a launch")
        self.assertIn("LAUNCHED", result.stdout)
        self.assertIn("could not inspect", result.stderr)

    def test_launcher_runs_the_contract_check_only_when_an_agenda_is_mounted(self):
        stub = self.directory / "podman"
        stub.write_text('#!/bin/sh\nif [ "$1" = run ]; then printf "LAUNCHED\\n"; fi\nexit 0\n')
        stub.chmod(0o755)
        no_agenda = subprocess.run(["bash", str(ROOT / "deploy/podman/rebekah-run")], capture_output=True, text=True,
            env={**os.environ, "PATH": str(self.directory) + ":" + os.environ["PATH"],
                 "REBEKAH_IMAGE": "sha256:" + "0" * 64, "REBEKAH_WORKSPACE": str(ROOT)})
        self.assertEqual(no_agenda.returncode, 0, no_agenda.stderr)
        self.assertNotIn("contract", no_agenda.stderr.lower())


if __name__ == "__main__":
    unittest.main()
