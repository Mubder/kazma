#!/usr/bin/env python3
"""A controllable stand-in for the Kazma server, for testing the supervisor.

Validating a supervisor against the real agent is slow and dangerous: a
cold boot is 60-212 seconds, and every experiment runs against something
holding real credentials. Every defect found in the guard on 2026-08-28 was
found that way, and each one cost the operator downtime.

This process speaks the same health contract as Kazma -- ``/health/ready``
and ``/health/live`` with ``build.started_at`` -- and can be told to fail in
each of the specific ways that broke the guard in production:

    FAKE_BOOT_DELAY_S     seconds before the port is bound (slow cold start)
    FAKE_EXIT_AFTER_S     exit abruptly this long after becoming ready
    FAKE_EXIT_CODE        exit code to use
    FAKE_UNHEALTHY_AFTER_S  keep serving, but report degraded (the "alive but
                          wedged" case no OS supervisor catches)
    FAKE_HANG_AFTER_S     stop answering entirely without exiting
    FAKE_NEVER_READY      bind nothing at all; run forever
    FAKE_PORT             port to bind (default 9099)
    FAKE_MARKER           file to append one line to per generation, so a
                          test can count how many times it was restarted

Nothing here imports Kazma. The point is to exercise the supervisor, not
the agent.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STARTED_AT = time.time()

BOOT_DELAY_S = float(os.environ.get("FAKE_BOOT_DELAY_S", "0"))
EXIT_AFTER_S = float(os.environ.get("FAKE_EXIT_AFTER_S", "0"))
EXIT_CODE = int(os.environ.get("FAKE_EXIT_CODE", "1"))
UNHEALTHY_AFTER_S = float(os.environ.get("FAKE_UNHEALTHY_AFTER_S", "0"))
HANG_AFTER_S = float(os.environ.get("FAKE_HANG_AFTER_S", "0"))
NEVER_READY = os.environ.get("FAKE_NEVER_READY", "").lower() in ("1", "true", "yes")
PORT = int(os.environ.get("FAKE_PORT", "9099"))
MARKER = os.environ.get("FAKE_MARKER", "")

_state = {"hung": False, "unhealthy": False}


def _note(event: str) -> None:
    if not MARKER:
        return
    try:
        with open(MARKER, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "event": event, "pid": os.getpid(),
                "started_at": STARTED_AT, "ts": time.time(),
            }) + "\n")
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):  # keep test output readable
        pass

    def do_GET(self):  # noqa: N802 - stdlib naming
        if _state["hung"]:
            # Accept the connection and never answer: the shape of a wedged
            # event loop, which a plain "is the port open?" check passes.
            time.sleep(3600)
            return

        if self.path.startswith("/health/live"):
            body = {
                "status": "alive",
                "build": {"started_at": STARTED_AT, "commit": "fake"},
            }
        elif self.path.startswith("/health/ready"):
            if _state["unhealthy"]:
                body = {
                    "status": "degraded",
                    "checks": {"database": {"status": "failed"}},
                }
            else:
                body = {
                    "status": "ready",
                    "checks": {"database": {"status": "ok"}},
                }
        else:
            self.send_response(404)
            self.end_headers()
            return

        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _timer(delay: float, fn) -> None:
    if delay <= 0:
        return
    t = threading.Thread(target=lambda: (time.sleep(delay), fn()), daemon=True)
    t.start()


def main() -> int:
    _note("spawned")

    if NEVER_READY:
        # Started, alive, binds nothing. The guard must detect this by
        # timeout rather than by the process exiting.
        while True:
            time.sleep(1)

    if BOOT_DELAY_S:
        time.sleep(BOOT_DELAY_S)

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    _note("ready")

    def _die():
        _note("exiting")
        os._exit(EXIT_CODE)

    def _hang():
        _note("hanging")
        _state["hung"] = True

    def _degrade():
        _note("degraded")
        _state["unhealthy"] = True

    _timer(EXIT_AFTER_S, _die)
    _timer(HANG_AFTER_S, _hang)
    _timer(UNHEALTHY_AFTER_S, _degrade)

    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
