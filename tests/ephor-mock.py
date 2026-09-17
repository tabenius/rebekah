#!/usr/bin/env python3
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

mode = sys.argv[1]
port_file = sys.argv[2]
entry_id = "entry-test-0001"
chain_hash = "a" * 64

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return self.reply(400, {"error": "invalid json"})

        if self.path == "/capture":
            if mode == "http-error":
                return self.reply(503, {"error": "unavailable"})
            if mode == "malformed":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"not-json")
                return
            if mode == "bad-entry-id":
                return self.reply(200, {"entry_id": "../../evil", "accepted": True})
            if mode == "deny":
                return self.reply(200, {"entry_id": entry_id, "accepted": False})
            if mode == "hold":
                return self.reply(200, {"entryId": entry_id, "status": "hold", "allowed": False})
            required = {"session_id", "agent_class", "action", "arguments", "caller_stack"}
            if not required.issubset(body):
                return self.reply(400, {"error": "missing fields"})
            return self.reply(200, {"entry_id": entry_id, "hash": chain_hash, "accepted": True})

        if self.path.startswith("/capture/") and self.path.endswith("/finalize"):
            if mode == "invalid-hash":
                return self.reply(200, {"hash": "bogus-not-a-chain-hash", "accepted": True})
            return self.reply(200, {"hash": chain_hash, "accepted": True})

        if self.path == "/finalize":
            if body.get("entry_id") != entry_id or body.get("outcome") != "success":
                return self.reply(400, {"error": "invalid finalization"})
            if mode == "invalid-hash":
                return self.reply(200, {"hash": "bogus-not-a-chain-hash", "accepted": True})
            return self.reply(200, {"hash": chain_hash, "accepted": True})

        self.reply(404, {"error": "not found"})

server = ThreadingHTTPServer((__import__("os").environ.get("EPHOR_MOCK_HOST", "127.0.0.1"), 0), Handler)
with open(port_file, "w", encoding="utf-8") as f:
    f.write(str(server.server_port))
server.serve_forever()
