"""The TUI writes a setting itself only when no server answers at all (§47).

Audit 2026-10-26 AUD-035. ``SettingsPanel._persist_setting`` fell back to a
local ``get_config_store().set(...)`` on every exception but an answered
refusal: a 401/403 (a mismatched ``KAZMA_SECRET``), a 5xx or a slow server
all wrote straight into the shared store, past the server's admin check, its
change listeners and its cache. ``local_api`` now names "nothing listens" --
``LocalApiUnreachable`` -- and only that writes locally.

Requests go through the real ``request_json`` over ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from kazma_core.runtime import local_api


def _serve(monkeypatch, handler) -> None:
    monkeypatch.setattr(local_api, "candidate_api_bases", lambda: ["http://127.0.0.1:9", "http://127.0.0.1:8"])
    real = httpx.Client
    real_async = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    def async_client(*args, **kwargs):
        kwargs.pop("verify", None)
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setattr(httpx, "AsyncClient", async_client)


def _status(code: int):
    return lambda req: httpx.Response(code, json={"error": "nope"})


def _raises(exc_type):
    def handler(req):
        raise exc_type("down", request=req)
    return handler


_ANSWERED = {
    "401 (mismatched secret)": _status(401),
    "403": _status(403),
    "500": _status(500),
    "503": _status(503),
    "slow server": _raises(httpx.ReadTimeout),
}
_NOTHING = {
    "nothing listening": _raises(httpx.ConnectError),
    "connect timed out": _raises(httpx.ConnectTimeout),
}


@pytest.mark.parametrize("name", sorted(_ANSWERED))
def test_an_answering_server_is_not_unreachable(monkeypatch, name) -> None:
    _serve(monkeypatch, _ANSWERED[name])
    with pytest.raises(RuntimeError) as caught:
        local_api.request_json("PUT", "/api/settings/single", payload={"key": "x"})
    assert not isinstance(caught.value, local_api.LocalApiUnreachable), name
    with pytest.raises(RuntimeError) as caught_async:
        asyncio.run(local_api.request_json_async("GET", "/api/settings"))
    assert not isinstance(caught_async.value, local_api.LocalApiUnreachable), name


@pytest.mark.parametrize("name", sorted(_NOTHING))
def test_nothing_listening_is_unreachable(monkeypatch, name) -> None:
    _serve(monkeypatch, _NOTHING[name])
    with pytest.raises(local_api.LocalApiUnreachable):
        local_api.request_json("PUT", "/api/settings/single", payload={"key": "x"})
    with pytest.raises(local_api.LocalApiUnreachable):
        asyncio.run(local_api.request_json_async("GET", "/api/settings"))


class _Recorder:
    def __init__(self, writes: list) -> None:
        self.writes = writes

    def set(self, key, value, category="general") -> None:
        self.writes.append((key, value, category))


@pytest.fixture
def panel():
    from kazma_tui.settings_panel import SettingsPanel

    return SettingsPanel()


@pytest.mark.parametrize("name", sorted(_ANSWERED))
def test_the_tui_never_writes_past_a_server_that_answered(monkeypatch, panel, name) -> None:
    _serve(monkeypatch, _ANSWERED[name])
    writes: list = []
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: _Recorder(writes))
    with pytest.raises(RuntimeError):
        panel._persist_setting("safety.hitl.enabled", False, "safety")
    assert writes == [], f"{name}: written locally behind the server"


def test_with_nothing_listening_the_tui_writes_locally(monkeypatch, panel) -> None:
    """Control: no server at all is still the local write (§47)."""
    _serve(monkeypatch, _NOTHING["nothing listening"])
    writes: list = []
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: _Recorder(writes))
    assert panel._persist_setting("swarm.task_retention_days", "14", "swarm") == "local"
    assert writes == [("swarm.task_retention_days", 14, "swarm")]
