"""A deploy waits for idle: ``/health/activity`` and ``kazma_guard --reload --when-idle``.

A reload mid-turn drops the reply in flight, and on 2026-09-24 a restart
during a save failure discarded a finished answer that existed only in
memory. Deploys are now also run by an agent while the operator is away
(2026-09-26), so "is anyone mid-turn?" had to become a question the guard can
ask instead of a judgement someone makes from the log. The server answers it
for callers on its own machine only: behind the tunnel, the caller is a
visitor, and whether someone is mid-turn is not a visitor's business.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi import FastAPI
from kazma_ui import auth, health
from kazma_ui.active_turns import (
    register_turn,
    reset_active_turns,
    running_turn_count,
    unregister_turn,
)
from kazma_ui.proxy_headers import ForwardedHeadersMiddleware
from starlette.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
LOCAL = ("127.0.0.1", 40555)  # the guard, on the same host
TUNNEL = ("127.0.0.1", 40123)  # cloudflared, on the same host
FORWARDED = {"X-Forwarded-For": "46.186.228.227", "X-Forwarded-Proto": "https"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in ("KAZMA_LOOPBACK_AUTOLOGIN", "KAZMA_TRUST_LAN", "KAZMA_DEMO_MODE", "KAZMA_PRODUCTION"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KAZMA_TRUSTED_PROXIES", "127.0.0.1")
    auth.reset_proxy_detection()
    reset_active_turns()
    yield
    reset_active_turns()
    auth.reset_proxy_detection()


class _Task:
    def __init__(self, done: bool) -> None:
        self._done = done

    def done(self) -> bool:
        return self._done


def _client(peer):
    app = FastAPI()
    app.include_router(health.router)
    return TestClient(ForwardedHeadersMiddleware(app), client=peer)


def _no_pending(monkeypatch, count: int = 0):
    import kazma_core.safety.hitl_gates as gates

    async def _pending(tenant_id=None):
        return [object()] * count

    monkeypatch.setattr(gates, "pending_gates_async", _pending)


def test_running_turns_are_counted_on_every_transport():
    register_turn("sse-thread", _Task(done=False))
    register_turn("gateway-thread", _Task(done=False))
    register_turn("finished-thread", _Task(done=True))
    assert running_turn_count() == 2
    unregister_turn("sse-thread")
    assert running_turn_count() == 1


def test_a_local_caller_gets_the_counts(monkeypatch):
    _no_pending(monkeypatch, count=2)
    register_turn("t1", _Task(done=False))
    body = _client(LOCAL).get("/health/activity").json()
    assert body == {"active_turns": 1, "pending_approvals": 2, "idle": False}
    unregister_turn("t1")
    assert _client(LOCAL).get("/health/activity").json()["idle"] is True


def test_the_tunnel_does_not_get_them(monkeypatch):
    _no_pending(monkeypatch)
    resp = _client(TUNNEL).get("/health/activity", headers=FORWARDED)
    assert resp.status_code == 403
    assert "active_turns" not in resp.text


def test_a_remote_peer_does_not_get_them(monkeypatch):
    _no_pending(monkeypatch)
    resp = _client(("192.168.1.20", 50000)).get("/health/activity")
    assert resp.status_code == 403


def test_negative_control_the_counts_are_really_hidden(monkeypatch):
    """The 403 is the check, not an accident: the same app answers the
    local peer, so a refusal above cannot be a broken route."""
    _no_pending(monkeypatch)
    assert _client(LOCAL).get("/health/activity").status_code == 200


# ── the guard ────────────────────────────────────────────────────────────


def _guard():
    spec = importlib.util.spec_from_file_location(
        "kazma_guard_idle_test", REPO / "scripts" / "service" / "kazma_guard.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Log:
    def __init__(self) -> None:
        self.events: list[str] = []

    def __call__(self, level, event, **fields):
        self.events.append(event)


def test_the_guard_waits_for_two_quiet_answers(monkeypatch):
    guard = _guard()
    answers = iter([{"active_turns": 1}, {"active_turns": 0}, {"active_turns": 1},
                    {"active_turns": 0}, {"active_turns": 0}])
    monkeypatch.setattr(guard, "_activity", lambda url: next(answers))
    log = _Log()
    assert guard._wait_until_idle("http://127.0.0.1:9090/health/ready", 60, log, poll_s=0) is True
    assert "reload.waiting_for_idle" in log.events


def test_the_guard_gives_up_when_still_busy(monkeypatch):
    guard = _guard()
    monkeypatch.setattr(guard, "_activity", lambda url: {"active_turns": 2})
    log = _Log()
    assert guard._wait_until_idle("http://127.0.0.1:9090/health/ready", 0, log, poll_s=0) is False
    assert "reload.busy_gave_up" in log.events


def test_a_build_without_the_route_is_reloaded_and_says_so(monkeypatch):
    guard = _guard()
    monkeypatch.setattr(guard, "_activity", lambda url: None)
    log = _Log()
    assert guard._wait_until_idle("http://127.0.0.1:9090/health/ready", 60, log, poll_s=0) is True
    assert log.events == ["reload.idle_unknown"]


def test_a_busy_reload_does_not_touch_the_server(monkeypatch):
    """--when-idle that gives up returns before the reload flag or any kill."""
    guard = _guard()
    touched: list[str] = []
    monkeypatch.setattr(guard, "_wait_until_idle", lambda *a, **k: False)
    monkeypatch.setattr(guard, "request_reload", lambda: touched.append("flag"))
    monkeypatch.setattr(guard, "_stop_recorded_child", lambda log, **kw: touched.append("kill"))
    monkeypatch.setattr(guard, "GuardLog", lambda path: _Log())
    assert guard._cmd_reload(when_idle=True, idle_timeout_s=1) == 3
    assert touched == []


def test_the_activity_url_is_derived_from_the_health_url(monkeypatch):
    guard = _guard()
    seen: list[str] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"active_turns": 0}'

    def _open(url, timeout=5):
        seen.append(url)
        return _Resp()

    monkeypatch.setattr(guard.urllib.request, "urlopen", _open)
    assert guard._activity("http://127.0.0.1:9090/health/ready") == {"active_turns": 0}
    assert seen == ["http://127.0.0.1:9090/health/activity"]
