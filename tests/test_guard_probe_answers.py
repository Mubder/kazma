"""What the guard's probe makes of each answer (2026-09-30).

``probe()`` tells three failures apart, over real HTTP:

* no answer (a refused connection, a timeout): the server is wedged or gone
  -- three in a row restart it;
* an answer that says "not ready" (a 503 naming the database): a dependency
  outage -- ridden out, because a restart cannot bring the database back;
* an answer that says only a restart clears it (``restart_required``: the
  settings store fell back to memory at a boot while the database was away,
  and stays there for the life of the process) -- restarted like no answer.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_GUARD = Path(__file__).resolve().parents[1] / "scripts" / "service" / "kazma_guard.py"


@pytest.fixture(scope="module")
def guard():
    spec = importlib.util.spec_from_file_location("kazma_guard_probe_test", _GUARD)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


_BODIES = {
    "/ready": (200, {"status": "ready", "checks": {"database": {"status": "ok"}}}),
    "/db-down": (503, {"status": "not_ready",
                       "checks": {"database": {"status": "failed", "error": "timed out (3s)"}}}),
    "/volatile": (503, {"status": "not_ready", "restart_required": True,
                        "checks": {"config_store": {"status": "failed", "error": "VOLATILE",
                                                    "restart_required": True}}}),
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 -- the stdlib's name
        code, body = _BODIES[self.path]
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):
        pass


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_a_ready_server(guard, server):
    result = guard.probe(server + "/ready", 5.0)
    assert result[0] is True and result.answered


def test_a_database_outage_is_an_answer_that_may_heal(guard, server):
    ok, detail = result = guard.probe(server + "/db-down", 5.0)
    assert ok is False and "database" in detail
    assert result.answered is True and result.restart_required is False


def test_a_volatile_settings_store_says_only_a_restart_clears_it(guard, server):
    ok, _detail = result = guard.probe(server + "/volatile", 5.0)
    assert ok is False
    assert result.answered is True and result.restart_required is True


def test_a_closed_port_is_no_answer(guard):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    result = guard.probe(f"http://127.0.0.1:{port}/health/ready", 2.0)
    assert result[0] is False and result.answered is False
