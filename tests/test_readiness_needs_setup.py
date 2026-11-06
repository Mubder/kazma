"""Kazma waiting for its owner is up, said so, and never restarted for it.

On the drill VM (2026-11-06) a fresh install had no provider key. The server
was up and its pages served, but ``/health/ready`` answered 503
(``llm_provider`` is required), the guard counted that as "never became
healthy" after its 900 s start budget and restarted it, every 15 minutes, and
its log said only ``not_ready``: the provider check is "degraded", and the
guard named only "failed" checks. ``--reload`` waited out the whole budget.

Readiness now marks a required check that only the owner can fix
(``needs_setup``), and says when every failing required check is one. The
guard reads it: the server is up, no restart, said once (``guard
integration`` holds the guard's side with a real guard and a stand-in
server). These tests build the body the real readiness code builds and hand
it to the guard's own reader, over HTTP.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from kazma_ui import health

_GUARD = Path(__file__).resolve().parents[1] / "scripts" / "service" / "kazma_guard.py"
_CHECKS = ("check_config_store", "check_database", "check_swarm_engine",
           "check_model_registry", "check_agent_runner", "check_mcp",
           "check_cron", "check_schedulers")


class _Registry:
    def __init__(self, profile: dict) -> None:
        self.profile = profile

    def get_active_profile(self) -> dict:
        return self.profile


@pytest.fixture
def ready_except_the_provider(monkeypatch):
    """Every check ok but the provider's, which is the REAL check reading a
    stand-in registry."""
    for name in _CHECKS:
        component = name.removeprefix("check_")
        monkeypatch.setattr(health, name, lambda c=component: {"status": "ok", "component": c})

    def profile(**fields):
        monkeypatch.setattr("kazma_core.model_registry.get_model_registry",
                            lambda: _Registry({"model": "gpt-4o-mini",
                                               "base_url": "https://api.openai.com/v1", **fields}))
    return profile


def _readiness() -> tuple[int, dict]:
    resp = asyncio.run(health._readiness())
    return resp.status_code, json.loads(resp.body)


def test_no_provider_key_is_setup_for_the_owner(ready_except_the_provider):
    ready_except_the_provider(api_key="")
    code, body = _readiness()
    assert code == 503 and body["status"] == "not_ready", "chat cannot run without a model"
    assert body["unavailable_capabilities"] == ["llm_provider"]
    assert body["needs_setup"] == ["llm_provider"]
    assert "Settings -> Providers" in body["setup"]["llm_provider"]
    assert body["checks"]["llm_provider"]["error"] == "no API key for a remote provider"


def test_setup_is_not_said_over_a_real_failure(ready_except_the_provider, monkeypatch):
    """Negative control: a database down too is not the owner's to set up."""
    ready_except_the_provider(api_key="")
    monkeypatch.setattr(health, "check_database",
                        lambda: {"status": "failed", "component": "database", "error": "timed out"})
    code, body = _readiness()
    assert code == 503 and "needs_setup" not in body


def test_with_a_key_it_is_ready(ready_except_the_provider):
    ready_except_the_provider(api_key="sk-demo-placeholder")
    code, body = _readiness()
    assert code == 200 and "needs_setup" not in body


# ── the guard reads it ────────────────────────────────────────────────


@pytest.fixture(scope="module")
def guard():
    spec = importlib.util.spec_from_file_location("kazma_guard_needs_setup_test", _GUARD)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def _serve(code: int, body: dict):
    raw = json.dumps(body).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 -- the stdlib's name
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def test_the_guard_reads_the_real_body(guard, ready_except_the_provider):
    ready_except_the_provider(api_key="")
    code, body = _readiness()
    httpd = _serve(code, body)
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/health/ready"
        result = guard.probe(url, 5.0)
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert result.answered and not result[0]
    assert result.needs_setup == ("llm_provider",)
    assert "llm_provider: no API key for a remote provider" in result[1], result[1]
    assert "llm_provider" in result.checks
    # Negative control: the reading of before named only "failed" checks.
    old = [k for k, v in body["checks"].items() if v.get("status") == "failed"]
    assert old == [], "the provider check is degraded: the old reading said a bare not_ready"


def test_status_says_up_and_needs_setup(guard, monkeypatch, capsys):
    """``--status`` called every unhealthy answer "not answering"."""
    result = guard.ProbeResult(False, "llm_provider: no API key for a remote provider",
                               needs_setup=("llm_provider",))
    monkeypatch.setattr(guard, "probe", lambda url, timeout: result)
    monkeypatch.setattr(guard, "_port_holder_pid", lambda port: None)
    monkeypatch.setattr(guard, "read_pause", lambda: None)
    started = time.monotonic()
    guard._cmd_status()
    out = capsys.readouterr().out
    assert "server      : up, needs setup in Settings -> Providers" in out, out
    assert time.monotonic() - started < 30
