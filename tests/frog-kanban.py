#!/usr/bin/env python3
"""Frog source validation, source freshness, auth and read-only gates."""
import copy
import http.client
import importlib.util
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gateway", ROOT / "nix/gateway.py")
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)


class FrogTests(unittest.TestCase):
    def setUp(self):
        self.cfg = gw.Config({"REBEKAH_GATEWAY_EXPOSE": "frog", "FROG_HOST": "localhost",
                              "REBEKAH_GATEWAY_TOKEN": "test-token", "REBEKAH_AUTH_PASSWORD": "0"})
        self.observed = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
        self.data = {"schema": "frog.kanban-projection.v0", "source": "frog", "read_only": True,
            "observed_at": self.observed, "revision": 12,
            "lanes": [{"id": lane, "title": lane} for lane in ["idea", "blocked", "in_progress", "done"]],
            "cards": [{"id": "one", "title": "<script>title</script>", "priority": "p0", "status": "review", "lane": "in_progress",
                "repository": "frog", "assignee": "agent", "ready": False, "unmet_dependencies": [], "blocks": [],
                "status_observed_at": self.observed, "updated_at": self.observed,
                "secret": "must-not-export", "repo_path": "/private/workspace"}]}

    def view(self, data=None):
        with patch.object(gw, "internal_json", return_value=(200, self.data if data is None else data)):
            return gw.v1_frog_kanban(self.cfg)

    def test_preserves_raw_status_priority_and_original_source_capture(self):
        view = self.view()
        self.assertEqual(view["status"], "ready"); self.assertTrue(view["stale"])
        self.assertEqual(view["source_observed_at"], self.observed)
        self.assertNotEqual(view["observed_at"], self.observed)
        self.assertEqual(view["items"][0]["status"], "review")
        self.assertEqual(view["items"][0]["priority"], "p0")
        self.assertNotIn("secret", view["items"][0]); self.assertNotIn("repo_path", view["items"][0])

    def test_invalid_schema_duplicates_links_and_future_capture_fail_closed(self):
        for mutate in [lambda d:d.update(schema="wrong"), lambda d:d.update(read_only=False),
                lambda d:d["cards"].append(copy.deepcopy(d["cards"][0])),
                lambda d:d["cards"][0].update(blocks="not-array"),
                lambda d:d.update(observed_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()),
                lambda d:d.update(cards=d["cards"]*1001)]:
            data=copy.deepcopy(self.data); mutate(data)
            self.assertEqual(self.view(data)["status"], "unavailable")
            self.assertEqual(self.view(data)["items"], [])

    def test_default_disabled_and_loopback_only(self):
        self.assertEqual(gw.v1_frog_kanban(gw.Config({}))["status"], "disabled")
        self.assertEqual(self.cfg.backends["frog"][0], "127.0.0.1")
        cfg=gw.Config({"REBEKAH_GATEWAY_EXPOSE":"frog", "FROG_HOST":"192.0.2.1"})
        self.assertTrue(any("Frog" in problem for problem in cfg.validate()))

    def test_empty_snapshot_is_ready_with_capture_and_gateway_times(self):
        data = copy.deepcopy(self.data)
        data["cards"] = []
        result = self.view(data)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["source_observed_at"], self.observed)
        self.assertTrue(result["observed_at"])

    def test_system_lists_frog_with_bounded_read_route(self):
        system = gw.v1_system(self.cfg, gw.Authenticator(self.cfg))
        self.assertEqual(system["backends"]["frog"]["route"], "/frog/api/v1/kanban")
        self.assertEqual(system["urls"]["backends"]["frog"], "/frog/api/v1/kanban")

    def test_pusher_reports_disabled_source_instead_of_omitting_view(self):
        cfg = gw.Config({"REBEKAH_AUTH_PASSWORD": "0"})
        pusher = gw.DashPusher(cfg, gw.Authenticator(cfg))
        views = pusher.views()
        self.assertEqual(views["frog-kanban"]["status"], "disabled")

    def test_pusher_includes_empty_snapshot(self):
        data = copy.deepcopy(self.data)
        data["cards"] = []
        with patch.object(gw, "internal_json", return_value=(200, data)):
            view = gw.DashPusher(self.cfg, gw.Authenticator(self.cfg)).views()["frog-kanban"]
        self.assertEqual(view["status"], "ready")
        self.assertEqual(view["items"], [])
        self.assertEqual(view["source_observed_at"], self.observed)

    def test_auth_and_read_only_http(self):
        server=ThreadingHTTPServer(("127.0.0.1",0),gw.make_handler(self.cfg,gw.Authenticator(self.cfg)))
        thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            with patch.object(gw,"internal_json",return_value=(200,self.data)):
                for path in ["/api/v1/frog-kanban","/frog/api/v1/kanban"]:
                    for token,method,expected in [(None,"GET",401),("test-token","GET",200),("test-token","POST",405),("test-token","DELETE",405)]:
                        conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=5)
                        conn.request(method,path,headers={"Authorization":"Bearer "+token} if token else {})
                        response=conn.getresponse();self.assertEqual(response.status,expected);response.read();conn.close()
        finally:
            server.shutdown();thread.join(5);server.server_close()


if __name__ == '__main__':
    unittest.main()
