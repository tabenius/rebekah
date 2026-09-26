#!/usr/bin/env python3
"""rebekah-mcp-bridge: reach a service's stdio MCP server across a UID boundary.

Ephor's agent-proxy gates the MCP tools OpenCode's agents use. It holds the
reviewer token, so it runs as its own UID (10007), apart from anything an agent
can drive. The tools themselves must run as the service they belong to
(WeftMark's as 10004, Sylvae's as 10003), with that service's state. The proxy
cannot switch UID to start them, so the supervisor runs this bridge twice:

  serve SOCKET ALLOW_UID -- COMMAND...   (as the service's UID)
      Listen on a Unix socket. For each connection from ALLOW_UID (checked with
      SO_PEERCRED, whatever the socket's mode), start COMMAND and join the
      connection to its stdin/stdout. Anyone else is refused, OpenCode's agents
      included, so the tools stay reachable only through the proxy.

  connect SOCKET                         (the proxy's upstream program)
      Join this process's stdin/stdout to SOCKET.

Bytes pass through untouched (newline-delimited JSON-RPC either way). Stdlib only.
"""

import os
import socket
import struct
import subprocess
import sys
import threading

MAX_SESSIONS = 8
CHUNK = 65536


def log(message):
    sys.stderr.write("rebekah-mcp-bridge: %s\n" % message)
    sys.stderr.flush()


def pump(read, write, done):
    """Copy until EOF or error, then run done() once."""
    try:
        while True:
            data = read(CHUNK)
            if not data:
                break
            write(data)
    except OSError:
        pass
    finally:
        done()


def peer_uid(conn):
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", creds)
    return uid


def session(conn, command, slots):
    try:
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    except OSError as error:
        log("cannot start %s: %s" % (command[0], error))
        conn.close()
        slots.release()
        return

    def to_child(data):
        child.stdin.write(data)
        child.stdin.flush()

    def close_stdin():
        try:
            child.stdin.close()
        except OSError:
            pass

    def close_conn():
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    inbound = threading.Thread(target=pump, args=(conn.recv, to_child, close_stdin), daemon=True)
    inbound.start()
    pump(child.stdout.read1, conn.sendall, close_conn)
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
    conn.close()
    slots.release()


def serve(path, allow_uid, command):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    # Reachability is decided by the peer check below, not the mode: the
    # proxy's UID cannot be given group access without a shared group.
    os.chmod(path, 0o666)
    server.listen(MAX_SESSIONS)
    slots = threading.BoundedSemaphore(MAX_SESSIONS)
    log("serving %s on %s for uid %d" % (command[0], path, allow_uid))
    while True:
        conn, _ = server.accept()
        try:
            uid = peer_uid(conn)
        except OSError:
            conn.close()
            continue
        if uid != allow_uid:
            log("refused a connection from uid %d" % uid)
            conn.close()
            continue
        if not slots.acquire(blocking=False):
            log("refused a connection: %d sessions already open" % MAX_SESSIONS)
            conn.close()
            continue
        threading.Thread(target=session, args=(conn, command, slots), daemon=True).start()


def connect(path):
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.connect(path)
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    def to_stdout(data):
        stdout.write(data)
        stdout.flush()

    def half_close():
        try:
            conn.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    threading.Thread(target=pump, args=(stdin.read1, conn.sendall, half_close), daemon=True).start()
    pump(conn.recv, to_stdout, lambda: None)
    # The service side is gone; the stdin thread may still be blocked in a
    # read, which a normal interpreter exit would wait on.
    stdout.flush()
    os._exit(0)


def main(argv):
    if len(argv) >= 5 and argv[0] == "serve" and argv[3] == "--":
        try:
            allow_uid = int(argv[2])
        except ValueError:
            allow_uid = -1
        if allow_uid < 0:
            log("ALLOW_UID must be a uid")
            return 64
        serve(argv[1], allow_uid, argv[4:])
        return 0
    if len(argv) == 2 and argv[0] == "connect":
        return connect(argv[1])
    log("usage: serve SOCKET ALLOW_UID -- COMMAND... | connect SOCKET")
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
