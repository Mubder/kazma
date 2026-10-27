"""``kazma gateway`` commands send Kazma's credential and report what the
server answered (audit 2026-10-26 AUD-073).

They printed "Gateway started." / "Adapters refreshed." whatever the HTTP
status -- a 401 from a server with sign-in on, a 500 -- exited 0, and sent no
credential at all, so on a signed-in server none of them could ever work.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

BASE = "http://127.0.0.1:9090"


def _serve(monkeypatch: pytest.MonkeyPatch, handler: Any) -> list[httpx.Request]:
    """Route the CLI's HTTP client to *handler*; return the requests seen."""
    import kazma_cli.gateway as gw

    seen: list[httpx.Request] = []
    real = httpx.AsyncClient

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    class _Client(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a: Any, **kw: Any) -> None:
            kw["transport"] = httpx.MockTransport(record)
            super().__init__(*a, **kw)

    monkeypatch.setattr(gw.httpx, "AsyncClient", _Client)
    return seen


@pytest.mark.parametrize("cmd", ["cmd_start", "cmd_stop", "cmd_refresh", "cmd_status", "cmd_restart"])
def test_a_refusal_is_a_failure(monkeypatch: pytest.MonkeyPatch, cmd: str, capsys) -> None:
    import kazma_cli.gateway as gw

    _serve(monkeypatch, lambda r: httpx.Response(401, json={"detail": "Sign in required"}))
    rc = asyncio.run(getattr(gw, cmd)(BASE))
    out = capsys.readouterr().out
    assert rc == 1
    assert "401" in out and "Sign in required" in out
    assert "started." not in out and "stopped." not in out and "refreshed." not in out


def test_a_server_error_is_a_failure(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import kazma_cli.gateway as gw

    _serve(monkeypatch, lambda r: httpx.Response(500, json={"error": "boom"}))
    assert asyncio.run(gw.cmd_refresh(BASE)) == 1
    assert "Adapters refreshed." not in capsys.readouterr().out


def test_an_error_answered_with_200_is_a_failure(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import kazma_cli.gateway as gw

    # The refresh route answers 200 {"status": "error"} with no gateway.
    _serve(monkeypatch, lambda r: httpx.Response(200, json={"status": "error", "message": "Gateway not initialized"}))
    assert asyncio.run(gw.cmd_refresh(BASE)) == 1
    assert "Adapters refreshed." not in capsys.readouterr().out


def test_the_credential_is_sent_and_success_is_reported(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import kazma_cli.gateway as gw

    monkeypatch.setenv("KAZMA_SECRET", "demo-cli-secret")
    seen = _serve(monkeypatch, lambda r: httpx.Response(200, json={"status": "started"}))
    assert asyncio.run(gw.cmd_start(BASE)) == 0
    assert "Gateway started." in capsys.readouterr().out
    assert seen and seen[0].headers.get("X-Kazma-Secret") == "demo-cli-secret"


def test_negative_control_the_old_request_reads_any_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The old ``_request`` returned the body whatever the status: the 401
    became a dict the commands read as an answer."""

    async def old_request(client: httpx.AsyncClient, method: str, path: str) -> dict[str, Any]:
        response = await client.request(method, path)
        try:
            return response.json()
        except ValueError:
            return {"status": "error", "message": response.text}

    async def run() -> dict[str, Any]:
        transport = httpx.MockTransport(lambda r: httpx.Response(401, json={"detail": "Sign in required"}))
        async with httpx.AsyncClient(base_url=BASE, transport=transport) as client:
            return await old_request(client, "POST", "/api/gateway/refresh-adapters")

    body = asyncio.run(run())
    assert body == {"detail": "Sign in required"}  # no error raised: read as success
