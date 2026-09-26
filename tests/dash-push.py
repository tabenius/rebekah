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


OVERSIGHT_TOKEN = "oversight-token-0123456789"
WRITE_TOKEN = "weftmark-write-0123456789"


class Ephor(BaseHTTPRequestHandler):
    """A tiny governance-http: holds, and reviewer routes behind a bearer token."""
    holds = {}
    calls = []

    def _send(self, status, data):
        out = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):
        rid = self.path.rsplit("/", 1)[-1]
        if self.path.startswith("/oversight/fetch/") and rid in Ephor.holds:
            return self._send(200, {"action": Ephor.holds[rid]})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        Ephor.calls.append((self.path, self.headers.get("Authorization"), body))
        if self.path == "/oversight/list":
            items = [h for h in Ephor.holds.values() if h["status"] == body.get("status")]
            return self._send(200, {"items": items, "total": len(items), "next_cursor": None})
        if self.headers.get("Authorization") != "Bearer " + OVERSIGHT_TOKEN:
            return self._send(401, {"error": "reviewer token required"})
        hold = Ephor.holds.get(body.get("request_id"))
        if hold is None:
            return self._send(400, {"error": "Approval request not found"})
        if self.path == "/oversight/decide":
            hold.update(status=body["decision"], reviewer=body["reviewer"], rationale=body["rationale"])
        elif self.path == "/oversight/defer":
            hold["deadline_ms"] += body["defer_ms"]
        elif self.path == "/oversight/escalate":
            hold["target_queue"] = body["target_queue"]
        self._send(200, {"action": hold})

    def log_message(self, *a):
        pass


class WeftMarkControl(Kanban):
    """The kanban mock plus WeftMark's review control route."""
    reviews = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        WeftMarkControl.reviews.append((self.path, self.headers.get("Authorization"),
                                        self.headers.get("Idempotency-Key"), body))
        outcome = "blocked" if body.get("request_changes") else "evidence_incomplete"
        out = json.dumps({"ok": True, "control": {"result": {"decision": {
            "id": body["review_id"], "author_id": body["author_id"], "outcome": outcome}}}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def hold(rid="11111111-2222-4333-8444-555555555555", deadline_ms=None):
    return {"request_id": rid, "session_id": "s", "agent_class": "CodingAgent",
            "action": "repo.push", "arguments": ["branch=main", "force=true"],
            "risk_level": "high", "risk_flags": ["requires_human_approval"],
            "entry_id": "e-1", "status": "pending",
            "deadline_ms": deadline_ms if deadline_ms is not None else int(time.time() * 1000) + 600000}


ephor_srv = serve(Ephor)
EPHOR_PORT = ephor_srv.server_address[1]
kanban_srv = serve(Kanban)
dash_srv = serve(Dash)
KB_PORT = kanban_srv.server_address[1]
DASH_URL = "http://127.0.0.1:%d" % dash_srv.server_address[1]
CLOSED_PORT = free_port()


def config(**extra):
    env = {
        "REBEKAH_AUTH_PASSWORD": "0",
        "REBEKAH_GATEWAY_TOKEN": "internal-token-0123456789",
        "REBEKAH_GATEWAY_EXPOSE": "weftmark",
        "WEFTMARK_PORT": str(KB_PORT),
        "REBEKAH_DASH_URL": DASH_URL,
        "REBEKAH_DASH_PUSH_KEY": KEY,
        # No Ephor unless a test brings one: never reach a real service on 9800.
        "EPHOR_PORT": str(CLOSED_PORT),
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
            "oversight": "rebekah.oversight.v1",
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


class DecisionsTest(unittest.TestCase):
    """Human-in-the-loop decisions from Dash, applied to the local Ephor/WeftMark."""

    def setUp(self):
        Dash.requests.clear()
        Dash.reply.clear()
        Ephor.holds = {}
        Ephor.calls.clear()
        WeftMarkControl.reviews.clear()
        self.wm_srv = serve(WeftMarkControl)
        self.addCleanup(self.wm_srv.shutdown)
        self.clock = Clock()
        self.logs = []
        self.pusher = self.make_pusher(REBEKAH_EPHOR_STATE="enabled")

    def make_pusher(self, **extra):
        cfg = config(EPHOR_PORT=str(EPHOR_PORT), WEFTMARK_PORT=str(self.wm_srv.server_address[1]),
                     EPHOR_OVERSIGHT_TOKEN=OVERSIGHT_TOKEN, REBEKAH_WEFTMARK_WRITE_TOKEN=WRITE_TOKEN,
                     **extra)
        return gw.DashPusher(cfg, gw.Authenticator(cfg), clock=self.clock, log=self.logs.append)

    def give(self, *commands, where=("GET", "/api/connector/pending")):
        Dash.reply[where] = (200, {"commands": list(commands)}, {})

    def results(self):
        return [r["body"]["results"] for r in Dash.requests if r["path"] == "/api/connector/results"]

    def test_pending_holds_are_pushed_as_the_oversight_view(self):
        Ephor.holds = {"h1": hold("h1")}
        self.pusher.step()
        view = Dash.requests[-1]["body"]["views"]["oversight"]
        self.assertFalse(view["stale"])
        self.assertEqual(view["decisions"], {"oversight": True, "review": True})
        item = view["items"][0]
        self.assertEqual((item["request_id"], item["action"], item["risk_level"]), ("h1", "repo.push", "high"))
        self.assertEqual(item["arguments"], ["branch=main", "force=true"])
        self.assertRegex(item["deadline"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_without_an_enabled_ephor_there_is_nothing_to_hold_or_decide(self):
        # Ephor is opt-in: absent, bundled but off, or external, the gateway
        # never calls a local bridge, even one listening on EPHOR_PORT with a
        # reviewer token in the environment.
        for n, state in enumerate(("absent", "disabled", "external", None)):
            with self.subTest(state=state):
                Ephor.holds = {"h1": hold("h1")}
                Ephor.calls.clear()
                pusher = self.make_pusher(REBEKAH_EPHOR_STATE=state)
                self.give({"id": "cmd-100000%d" % n, "kind": "oversight.decide",
                           "requested_by": "ada@example.com",
                           "params": {"request_id": "h1", "decision": "approved", "rationale": "ok"}},
                          where=("POST", "/api/connector/push"))
                pusher.step()
                pushes = [r for r in Dash.requests if r["path"] == "/api/connector/push"]
                view = pushes[-1]["body"]["views"]["oversight"]
                self.assertEqual((view["enabled"], view["stale"], view["items"]), (False, False, []))
                self.assertFalse(view["decisions"]["oversight"])
                self.assertEqual(pushes[-1]["body"]["views"]["system"]["ephor"]["state"], state or "absent")
                self.assertEqual(self.results()[-1][0]["error"], "oversight_not_enabled")
                self.assertEqual(Ephor.calls, [])
                self.assertEqual(Ephor.holds["h1"]["status"], "pending")
                del Dash.reply[("POST", "/api/connector/push")]

    def test_an_approval_is_applied_with_the_reviewer_token_and_reported(self):
        Ephor.holds = {"h1": hold("h1")}
        self.pusher.step()
        self.give({"id": "cmd-0000001", "kind": "oversight.decide", "requested_by": "ada@example.com",
                   "params": {"request_id": "h1", "decision": "approved", "rationale": "Looks right."}})
        self.clock.t += 30
        self.assertEqual(self.pusher.step(), 0, "push the new state at once")
        self.assertEqual(Ephor.holds["h1"]["status"], "approved")
        self.assertEqual(Ephor.holds["h1"]["reviewer"], "ada@example.com")
        decide = [c for c in Ephor.calls if c[0] == "/oversight/decide"]
        self.assertEqual(decide[0][1], "Bearer " + OVERSIGHT_TOKEN)
        self.assertEqual(self.results()[-1], [{"id": "cmd-0000001", "ok": True, "outcome": "approved"}])
        self.pusher.step()
        self.assertEqual(Dash.requests[-1]["path"], "/api/connector/push")
        self.assertEqual(Dash.requests[-1]["body"]["views"]["oversight"]["items"], [], "no longer pending")
        self.assertFalse(any(OVERSIGHT_TOKEN in m for m in self.logs))

    def test_a_command_sent_again_is_reported_not_applied_twice(self):
        Ephor.holds = {"h1": hold("h1")}
        cmd = {"id": "cmd-0000002", "kind": "oversight.decide", "requested_by": "ada@example.com",
               "params": {"request_id": "h1", "decision": "denied", "rationale": "No force pushes."}}
        self.give(cmd, where=("POST", "/api/connector/push"))
        Dash.reply[("POST", "/api/connector/results")] = (500, {}, {})  # the acknowledgement is lost
        self.pusher.step()
        del Dash.reply[("POST", "/api/connector/results")]
        self.pusher.step()
        self.assertEqual(len([c for c in Ephor.calls if c[0] == "/oversight/decide"]), 1)
        self.assertEqual(self.results()[-1][0]["outcome"], "denied")
        self.assertEqual(self.pusher.unreported, {}, "acknowledged")

    def test_a_late_approval_is_refused_but_a_denial_is_not(self):
        Ephor.holds = {"late": hold("late", deadline_ms=1000), "late2": hold("late2", deadline_ms=1000)}
        self.give(
            {"id": "cmd-0000003", "kind": "oversight.decide", "requested_by": "ada@example.com",
             "params": {"request_id": "late", "decision": "approved", "rationale": "ok"}},
            {"id": "cmd-0000004", "kind": "oversight.decide", "requested_by": "ada@example.com",
             "params": {"request_id": "late2", "decision": "denied", "rationale": "too late anyway"}},
            where=("POST", "/api/connector/push"))
        self.pusher.step()
        by_id = {r["id"]: r for r in self.results()[-1]}
        self.assertEqual(by_id["cmd-0000003"], {"id": "cmd-0000003", "ok": False, "error": "deadline_passed"})
        self.assertTrue(by_id["cmd-0000004"]["ok"])
        self.assertEqual(Ephor.holds["late"]["status"], "pending")

    def test_defer_escalate_and_already_decided(self):
        Ephor.holds = {"h1": hold("h1"), "h2": hold("h2"), "h3": dict(hold("h3"), status="denied")}
        before = Ephor.holds["h1"]["deadline_ms"]
        self.give(
            {"id": "cmd-0000005", "kind": "oversight.defer", "requested_by": "ada@example.com",
             "params": {"request_id": "h1", "defer_minutes": 30, "rationale": "Need the owner."}},
            {"id": "cmd-0000006", "kind": "oversight.escalate", "requested_by": "ada@example.com",
             "params": {"request_id": "h2", "target_queue": "security", "rationale": "Risky."}},
            {"id": "cmd-0000007", "kind": "oversight.decide", "requested_by": "ada@example.com",
             "params": {"request_id": "h3", "decision": "approved", "rationale": "ok"}},
            where=("POST", "/api/connector/push"))
        self.pusher.step()
        by_id = {r["id"]: r for r in self.results()[-1]}
        self.assertTrue(by_id["cmd-0000005"]["ok"])
        self.assertEqual(Ephor.holds["h1"]["deadline_ms"], before + 30 * 60000)
        self.assertTrue(by_id["cmd-0000006"]["ok"])
        self.assertEqual(Ephor.holds["h2"]["target_queue"], "security")
        self.assertEqual(by_id["cmd-0000007"], {"id": "cmd-0000007", "ok": False, "error": "not_pending", "outcome": "denied"})

    def test_a_review_is_recorded_in_weftmark_as_the_reviewer(self):
        self.give({"id": "cmd-0000008", "kind": "review.record", "requested_by": "ada@example.com",
                   "params": {"change_set_id": "cs-1", "request_changes": "Add a test."}},
                  where=("POST", "/api/connector/push"))
        self.pusher.step()
        path, auth, key, body = WeftMarkControl.reviews[0]
        self.assertEqual(path, "/v0/control/changes/cs-1/reviews")
        self.assertEqual(auth, "Bearer " + WRITE_TOKEN)
        self.assertEqual(key, "dash-cmd-0000008")
        self.assertEqual(body, {"review_id": "dash-cmd-0000008", "author_id": "ada@example.com",
                                "request_changes": "Add a test."})
        self.assertEqual(self.results()[-1], [{"id": "cmd-0000008", "ok": True, "outcome": "blocked"}])

    def test_malformed_commands_are_refused_without_touching_backends(self):
        self.give(
            {"id": "cmd-0000009", "kind": "shell.exec", "requested_by": "ada@example.com", "params": {}},
            {"id": "cmd-0000010", "kind": "oversight.decide", "requested_by": "not an email",
             "params": {"request_id": "h1", "decision": "approved", "rationale": "x"}},
            {"id": "cmd-0000011", "kind": "oversight.decide", "requested_by": "ada@example.com",
             "params": {"request_id": "../../x", "decision": "approved", "rationale": "x"}},
            {"id": "cmd-0000012", "kind": "oversight.decide", "requested_by": "ada@example.com",
             "params": {"request_id": "h1", "decision": "approved", "rationale": "  "}},
            {"id": "cmd-0000013", "kind": "review.record", "requested_by": "ada@example.com",
             "params": {"change_set_id": "../etc"}},
            {"id": "x", "kind": "oversight.decide"},
            where=("POST", "/api/connector/push"))
        self.pusher.step()
        errors = {r["id"]: r["error"] for r in self.results()[-1]}
        self.assertEqual(errors, {"cmd-0000009": "unknown_kind", "cmd-0000010": "bad_actor",
                                  "cmd-0000011": "bad_request_id", "cmd-0000012": "rationale_required",
                                  "cmd-0000013": "bad_change_set"})
        self.assertEqual([c for c in Ephor.calls if c[0] != "/oversight/list"], [])
        self.assertEqual(WeftMarkControl.reviews, [])

    def test_without_credentials_decisions_are_refused(self):
        cfg = config(EPHOR_PORT=str(EPHOR_PORT))
        pusher = gw.DashPusher(cfg, gw.Authenticator(cfg), clock=self.clock, log=self.logs.append)
        Ephor.holds = {"h1": hold("h1")}
        self.give({"id": "cmd-0000014", "kind": "oversight.decide", "requested_by": "ada@example.com",
                   "params": {"request_id": "h1", "decision": "approved", "rationale": "x"}},
                  {"id": "cmd-0000015", "kind": "review.record", "requested_by": "ada@example.com",
                   "params": {"change_set_id": "cs-1"}},
                  where=("POST", "/api/connector/push"))
        pusher.step()
        errors = {r["id"]: r["error"] for r in self.results()[-1]}
        self.assertEqual(errors, {"cmd-0000014": "oversight_not_enabled", "cmd-0000015": "review_not_enabled"})
        self.assertEqual(Ephor.holds["h1"]["status"], "pending")
        self.assertEqual(Dash.requests[0]["body"]["views"]["oversight"]["decisions"],
                         {"oversight": False, "review": False})


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
