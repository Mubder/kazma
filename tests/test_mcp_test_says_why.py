"""An MCP Test says why it failed, and pages nobody (FA-245, FA-256).

The 2026-11 functional audit, on live: a missing command, a process that
exits, a closed SSE URL and a closed streamable-HTTP URL each answered the MCP
page's Test with ``internal_error (ref: ...)`` -- the real causes were safe
and actionable, and logged at ERROR with a traceback. And a Test of an
UNSAVED HTTP server raised the install-wide alert "1 MCP server(s)
unavailable -- their tools are gone until fixed" to the owner's ops channel:
the Test shared the live connector's failure handling.

Both Test routes now go through one probe: an expected failure is the
tester's answer (``validation_error``, redacted, a WARNING without a
traceback), and the HTTP probe runs the manager ``quiet``. Every route under
``/api/mcp/test*`` is called with each failing config; no ops alert. The
negative control is the manager without ``quiet``.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from typing import Any

import pytest

FAILING = {
    "no-binary": ({"transport": "stdio", "command": ["audit-fa245-no-such-binary"]}, "Command not found"),
    "exits": ({"transport": "stdio", "command": [sys.executable, "-c", "import sys; sys.exit(3)"]},
              "closed stdout"),
    "sse-closed": ({"transport": "sse", "url": "http://127.0.0.1:9/sse"}, "SSE request failed"),
    "http-closed": ({"transport": "streamable_http", "url": "http://127.0.0.1:9/mcp"}, "MCP connection failed"),
}


@pytest.fixture()
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient

    from kazma_core.observability import ops_alerts
    from kazma_ui.mcp_ui import create_mcp_router

    alerts: list[str] = []
    monkeypatch.setattr(ops_alerts, "alert", lambda key, *a, **k: alerts.append(key) or True)
    saved: dict[str, Any] = {}
    agent = SimpleNamespace(get_mcp_servers_config=lambda: list(saved.values()))
    app = FastAPI()
    app.include_router(create_mcp_router(agent, Jinja2Templates(directory=".")))
    return SimpleNamespace(http=TestClient(app), alerts=alerts, saved=saved)


def _answers(client, case: str) -> list[dict[str, Any]]:
    cfg, _ = FAILING[case]
    name = f"audit-fa245-{case}"
    config = {"name": name, **cfg}
    client.saved[name] = config
    unsaved = client.http.post("/api/mcp/test-config", json=config).json()
    stored = client.http.post(f"/api/mcp/servers/{name}/test").json()
    return [unsaved, stored]


@pytest.mark.parametrize("case", sorted(FAILING))
def test_each_failure_says_its_own_cause(client, case):
    _cfg, cause = FAILING[case]
    for answer in _answers(client, case):
        assert answer["success"] is False
        assert not str(answer["error"]).startswith("internal_error"), answer
        assert cause in str(answer["error"]), (case, answer)


def test_no_test_route_pages_anyone(client):
    for case in FAILING:
        _answers(client, case)
    assert client.alerts == []


def test_every_test_route_is_covered(client):
    """Every route under /api/mcp/test* is one the cases above call."""
    from tests.test_api_route_callers import _all_routes

    paths = {getattr(r, "path", "") for r in _all_routes(client.http.app.routes)}
    assert {p for p in paths if p.startswith("/api/mcp/test") or p.endswith("/test")
            and p.startswith("/api/mcp/")} == {"/api/mcp/test-config", "/api/mcp/servers/{name}/test"}


def test_the_live_connect_still_alerts(monkeypatch):
    """Negative control: the manager without ``quiet`` -- the path the Test
    used -- raises the install-wide alert for the same unsaved server."""
    from kazma_core.mcp.manager import AsyncMCPManager
    from kazma_core.observability import ops_alerts

    alerts: list[str] = []
    monkeypatch.setattr(ops_alerts, "alert", lambda key, *a, **k: alerts.append(key) or True)

    async def run():
        manager = AsyncMCPManager()
        try:
            await manager.connect_from_config([{"name": "audit-fa256", **FAILING["http-closed"][0]}])
        finally:
            await manager.shutdown()

    asyncio.run(run())
    assert alerts == ["mcp.servers_unavailable"]
