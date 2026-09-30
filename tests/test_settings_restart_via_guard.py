"""Settings' "Restart server" goes through the guard (2026-09-30).

The route started a detached copy of the server (its own command line) and
hard-exited the running one. Under the guard -- the live install -- that
went behind the guard's back: the guard restarted its own child, found the
copy on its port and killed it as a foreign server, and ``os._exit`` skipped
every shutdown hook. A server the guard started now asks the guard to reload
it (the path ``kazma_guard.py --reload`` takes: a graceful stop, then the
code on disk); only a server with no guard restarts itself.

The guard carrying out the request is proven against a real guard in
``tests/test_guard_integration.py``.
"""

from __future__ import annotations

import json
import time

import pytest

from kazma_core.observability import supervisor_watch


def _state_file(tmp_path, heartbeat_age_s: float | None):
    state = tmp_path / "state.json"
    data = {"guard_pid": 4242}
    if heartbeat_age_s is not None:
        data["heartbeat"] = time.time() - heartbeat_age_s
    state.write_text(json.dumps(data), encoding="utf-8")
    return state


def test_a_live_guard_is_asked(tmp_path, monkeypatch):
    state = _state_file(tmp_path, heartbeat_age_s=5)
    monkeypatch.setenv(supervisor_watch.STATE_ENV, str(state))
    monkeypatch.setenv(supervisor_watch.RELOAD_ENV, str(tmp_path / "guard.reload"))
    before = time.time()
    assert supervisor_watch.request_guard_reload("test") is True
    request = json.loads((tmp_path / "guard.reload").read_text(encoding="utf-8"))
    # The guard acts on a request newer than its running child.
    assert request["ts"] >= before
    assert request["reason"] == "test"


def test_an_older_guard_is_found_beside_its_state_file(tmp_path, monkeypatch):
    """A guard from before it handed the path over keeps the request next to
    its state file (``<install>/.kazma``)."""
    state = _state_file(tmp_path, heartbeat_age_s=5)
    monkeypatch.setenv(supervisor_watch.STATE_ENV, str(state))
    monkeypatch.delenv(supervisor_watch.RELOAD_ENV, raising=False)
    assert supervisor_watch.request_guard_reload("test") is True
    assert (tmp_path / "guard.reload").is_file()


@pytest.mark.parametrize("age", [None, supervisor_watch.RELOAD_FRESH_S + 30])
def test_no_live_guard_is_not_asked(tmp_path, monkeypatch, age):
    """A stale heartbeat may be a dead guard: a request would wait forever."""
    state = _state_file(tmp_path, heartbeat_age_s=age)
    monkeypatch.setenv(supervisor_watch.STATE_ENV, str(state))
    monkeypatch.setenv(supervisor_watch.RELOAD_ENV, str(tmp_path / "guard.reload"))
    assert supervisor_watch.request_guard_reload("test") is False
    assert not (tmp_path / "guard.reload").exists()


def test_a_server_the_guard_did_not_start_is_not_asked(monkeypatch):
    monkeypatch.delenv(supervisor_watch.STATE_ENV, raising=False)
    assert supervisor_watch.request_guard_reload("test") is False


@pytest.fixture
def client(tmp_path):
    from unittest.mock import MagicMock

    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient
    from kazma_core.config_store import ConfigStore
    from kazma_ui.settings import create_settings_router

    cs = ConfigStore(db_path=str(tmp_path / "api.db"))
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "settings.html").write_text("ok")
    app = FastAPI()
    app.include_router(create_settings_router(
        MagicMock(), cs, Jinja2Templates(directory=str(tmp_path / "templates"))))
    return TestClient(app)


def test_under_a_guard_the_route_starts_no_process_and_does_not_exit(client, monkeypatch):
    import subprocess

    asked: list[str] = []
    monkeypatch.setattr(supervisor_watch, "request_guard_reload",
                        lambda reason: asked.append(reason) or True)

    def no_spawn(*_a, **_k):
        raise AssertionError("the route started a copy of the server behind the guard's back")

    monkeypatch.setattr(subprocess, "Popen", no_spawn)
    resp = client.post("/api/settings/system/restart")
    assert resp.status_code == 200
    assert resp.json()["via"] == "guard"
    assert asked == ["Settings: restart server"]


def test_without_a_guard_the_route_restarts_itself(client, monkeypatch):
    """Negative control: no guard, and the old path runs (a spawn)."""
    import subprocess

    monkeypatch.setattr(supervisor_watch, "request_guard_reload", lambda reason: False)
    spawned: list[list[str]] = []

    class _Proc:
        def __init__(self, args, **_kw):
            spawned.append(list(args))

    monkeypatch.setattr(subprocess, "Popen", _Proc)
    # Keep the test process alive: the route schedules its own exit
    # (os._exit on Windows, SIGTERM to itself elsewhere). Disarm all three:
    # an exit that fired would end the whole run with status 0.
    import asyncio.base_events
    import os

    monkeypatch.setattr(asyncio.base_events.BaseEventLoop, "call_later", lambda *a, **k: None)
    exits: list[object] = []
    monkeypatch.setattr(os, "_exit", lambda code: exits.append(code))
    monkeypatch.setattr(os, "kill", lambda *a: exits.append(a))
    resp = client.post("/api/settings/system/restart")
    assert resp.status_code == 200
    assert "via" not in resp.json()
    assert spawned, "without a guard the server restarts itself"
