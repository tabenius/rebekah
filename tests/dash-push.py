#!/usr/bin/env python3
"""rebekah-gateway -> RAGBAZ Dash push client test (no Docker, no Nix).

Runs the gateway's DashPusher against a mock WeftMark and a mock Dash on
loopback, with a fake clock, plus one end-to-end run of the real gateway
process. Standard library only.
"""

import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
GATEWAY = os.path.join(HERE, "..", "nix", "gateway.py")
spec = importlib.util.spec_from_file_location("gateway", GATEWAY)
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)

KEY = "rbkp_0123456789ab_" + "A" * 43


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


KANBAN = {"cards": [
    {"kind": "change_set", "id": "cs-1", "title": "Add gateway", "lane": "review",
     "lifecycle_state": "review", "readiness": "unreviewed",
     "evidence": {"total": 3, "current": 2}, "attention": ["dirty_worktree"]},
], "plan_cards": []}


class Kanban(BaseHTTPRequestHandler):
    data = KANBAN

    def do_GET(self):
        body = json.dumps(Kanban.data).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class Dash(BaseHTTPRequestHandler):
    """Records requests; answers with Dash.reply[(method, path)] or 200 {}."""
    requests = []
    reply = {}

    def _do(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        Dash.requests.append({
            "method": self.command, "path": self.path,
            "auth": self.headers.get("Authorization"),
            "body": json.loads(body) if body else None,
        })
        status, data, headers = Dash.reply.get((self.command, self.path), (200, {}, {}))
        out = json.dumps(data).encode()
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = _do
    do_POST = _do

    def log_message(self, *a):
        pass


kanban_srv = serve(Kanban)
dash_srv = serve(Dash)
KB_PORT = kanban_srv.server_address[1]
DASH_URL = "http://127.0.0.1:%d" % dash_srv.server_address[1]


def config(**extra):
    env = {
        "REBEKAH_AUTH_PASSWORD": "0",
        "REBEKAH_GATEWAY_TOKEN": "internal-token-0123456789",
        "REBEKAH_GATEWAY_EXPOSE": "weftmark",
        "WEFTMARK_PORT": str(KB_PORT),
        "REBEKAH_DASH_URL": DASH_URL,
        "REBEKAH_DASH_PUSH_KEY": KEY,
    }
    env.update(extra)
    return gw.Config({k: v for k, v in env.items() if v is not None})


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class ConfigTest(unittest.TestCase):
    def test_pushing_is_optional(self):
        cfg = config(REBEKAH_DASH_URL=None, REBEKAH_DASH_PUSH_KEY=None)
        self.assertFalse(cfg.dash_enabled)
        self.assertEqual(cfg.validate(), [])

    def test_fails_closed_on_bad_settings(self):
        cases = {
            "only the url": dict(REBEKAH_DASH_PUSH_KEY=None),
            "only the key": dict(REBEKAH_DASH_URL=None),
            "plain http off loopback": dict(REBEKAH_DASH_URL="http://dash.example.com"),
            "credentials in url": dict(REBEKAH_DASH_URL="https://u:p@dash.example.com"),
            "query in url": dict(REBEKAH_DASH_URL="https://dash.example.com/?x=1"),
            "not a push key": dict(REBEKAH_DASH_PUSH_KEY="hunter2"),
            "poll too fast": dict(REBEKAH_DASH_POLL_INTERVAL="2"),
            "heartbeat below poll": dict(REBEKAH_DASH_POLL_INTERVAL="60", REBEKAH_DASH_PUSH_INTERVAL="30"),
            "not a number": dict(REBEKAH_DASH_POLL_INTERVAL="soon"),
        }
        for name, extra in cases.items():
            with self.subTest(name):
                self.assertNotEqual(config(**extra).validate(), [])
        self.assertEqual(config(REBEKAH_DASH_URL="https://dash.ragbaz.cc").validate(), [])
        self.assertEqual(config().validate(), [])


class PusherTest(unittest.TestCase):
    def setUp(self):
        Dash.requests.clear()
        Dash.reply.clear()
        Kanban.data = json.loads(json.dumps(KANBAN))
        self.clock = Clock()
        self.logs = []
        cfg = config()
        self.pusher = gw.DashPusher(cfg, gw.Authenticator(cfg), clock=self.clock, log=self.logs.append)

    def test_pushes_the_gateway_views(self):
        self.assertEqual(self.pusher.step(), 30)
        req = Dash.requests[-1]
        self.assertEqual((req["method"], req["path"]), ("POST", "/api/connector/push"))
        self.assertEqual(req["auth"], "Bearer " + KEY)
        body = req["body"]
        self.assertEqual(body["schema"], "rebekah.dash-push.v1")
        self.assertEqual({k: v["schema"] for k, v in body["views"].items()}, {
            "system": "rebekah.system.v1",
            "attention": "rebekah.attention.v1",
            "change-sets": "rebekah.change-set-list.v1",
        })
        self.assertEqual(body["views"]["change-sets"]["items"][0]["id"], "cs-1")
        self.assertEqual(body["views"]["attention"]["items"][0]["reason"], "dirty_worktree")
        for view in body["views"].values():
            self.assertRegex(view["observed_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_unchanged_views_only_poll_until_the_heartbeat(self):
        self.pusher.step()
        self.clock.t += 30
        self.pusher.step()
        self.assertEqual([(r["method"], r["path"]) for r in Dash.requests][-1], ("GET", "/api/connector/pending"))
        self.clock.t += 300
        self.pusher.step()
        self.assertEqual(Dash.requests[-1]["path"], "/api/connector/push", "heartbeat push")

    def test_a_change_is_pushed_at_once(self):
        self.pusher.step()
        Kanban.data["cards"][0]["lane"] = "done"
        self.clock.t += 30
        self.pusher.step()
        self.assertEqual(Dash.requests[-1]["path"], "/api/connector/push")
        self.assertEqual(Dash.requests[-1]["body"]["views"]["change-sets"]["items"][0]["lane"], "done")

    def test_a_refresh_request_triggers_a_push(self):
        self.pusher.step()
        Dash.reply[("GET", "/api/connector/pending")] = (200, {"refresh_requested": True}, {})
        self.clock.t += 30
        self.assertEqual(self.pusher.step(), 0, "push right away")
        self.pusher.step()
        self.assertEqual(Dash.requests[-1]["path"], "/api/connector/push")

    def test_weftmark_down_is_pushed_as_stale(self):
        cfg = config(WEFTMARK_PORT=str(free_port()))
        pusher = gw.DashPusher(cfg, gw.Authenticator(cfg), clock=self.clock, log=self.logs.append)
        pusher.step()
        views = Dash.requests[-1]["body"]["views"]
        self.assertTrue(views["attention"]["stale"])
        self.assertTrue(views["change-sets"]["stale"])

    def test_a_rejected_key_backs_off_and_says_so_once(self):
        Dash.reply[("POST", "/api/connector/push")] = (401, {"error": "invalid_credential"}, {})
        self.assertEqual(self.pusher.step(), gw.DASH_MAX_BACKOFF)
        self.assertEqual(self.pusher.step(), gw.DASH_MAX_BACKOFF)
        self.assertEqual(len([m for m in self.logs if "rejected the push key" in m]), 1)
        self.assertFalse(any(KEY in m or KEY[18:] in m for m in self.logs), "the key is never logged")

    def test_rate_limits_and_failures(self):
        Dash.reply[("POST", "/api/connector/push")] = (429, {"retry_after": 7}, {"Retry-After": "7"})
        self.assertEqual(self.pusher.step(), 7)
        Dash.reply[("POST", "/api/connector/push")] = (500, {"error": "internal_error"}, {})
        self.assertEqual(self.pusher.step(), 60)
        self.assertEqual(self.pusher.step(), 120, "exponential backoff")
        Dash.reply[("POST", "/api/connector/push")] = (302, {}, {"Location": "https://evil.example/"})
        self.assertEqual(self.pusher.step(), 240, "redirects are failures, never followed")
        self.assertTrue(all(r["path"].startswith("/api/connector/") for r in Dash.requests))
        del Dash.reply[("POST", "/api/connector/push")]
        self.assertEqual(self.pusher.step(), 30, "recovers")
        self.assertEqual(self.pusher.failures, 0)

    def test_unreachable_dash_backs_off(self):
        cfg = config(REBEKAH_DASH_URL="http://127.0.0.1:%d" % free_port())
        pusher = gw.DashPusher(cfg, gw.Authenticator(cfg), clock=self.clock, log=self.logs.append)
        self.assertEqual(pusher.step(), 60)
        self.assertLessEqual(max(pusher.step() for _ in range(12)), gw.DASH_MAX_BACKOFF)


class GatewayProcessTest(unittest.TestCase):
    def run_gateway(self, **extra):
        port = free_port()
        env = dict(os.environ)
        env.update({
            "REBEKAH_GATEWAY_PORT": str(port),
            "REBEKAH_AUTH_PASSWORD": "0",
            "REBEKAH_GATEWAY_TOKEN": "internal-token-0123456789",
            "REBEKAH_GATEWAY_EXPOSE": "weftmark",
            "WEFTMARK_PORT": str(KB_PORT),
            "REBEKAH_GATEWAY_UI": "0",
            "REBEKAH_DASH_URL": DASH_URL,
            "REBEKAH_DASH_PUSH_KEY": KEY,
        })
        env.update(extra)
        return subprocess.Popen([sys.executable, GATEWAY], env=env, stderr=subprocess.PIPE, text=True), port

    def test_the_gateway_pushes_on_start(self):
        Dash.requests.clear()
        Dash.reply.clear()
        proc, port = self.run_gateway()
        try:
            deadline = time.time() + 10
            while time.time() < deadline and not any(r["path"] == "/api/connector/push" for r in Dash.requests):
                time.sleep(0.1)
            self.assertTrue(any(r["path"] == "/api/connector/push" for r in Dash.requests), "no push within 10 s")
            # Pushing opens no new listener: the gateway still serves as usual.
            with urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port, timeout=2) as resp:
                self.assertEqual(resp.status, 200)
        finally:
            proc.terminate()
            _, err = proc.communicate(timeout=5)
        self.assertIn("pushing to %s" % DASH_URL, err)
        self.assertNotIn(KEY[18:], err, "the key is never logged")

    def test_the_gateway_refuses_a_bad_push_config(self):
        proc, _ = self.run_gateway(REBEKAH_DASH_URL="http://dash.example.com")
        _, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("REBEKAH_DASH_URL must be https://", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
