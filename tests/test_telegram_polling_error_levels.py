"""Telegram's own outage or rate limit is a WARNING; a refusal of the bot an ERROR.

The live log held "[telegram] HTTP 502 polling error: Bad Gateway" and "HTTP
429 ... retry after" at ERROR (2026-11-08/09), and the daily digest lists
ERROR lines as needing attention: Telegram's own hiccups, which pass by
themselves, read as Kazma's (both statuses fail on the old line, which
logged every status at ERROR). The polling loop below runs against a stand-in
Telegram (httpx MockTransport: nothing leaves the machine) that answers one
getUpdates with the status under test, then the stop.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from kazma_gateway.adapters import telegram as telegram_module
from kazma_gateway.adapters.telegram import TelegramAdapter

_BODIES = {
    429: {"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 1",
          "parameters": {"retry_after": 1}},
    502: {"ok": False, "error_code": 502, "description": "Bad Gateway"},
    401: {"ok": False, "error_code": 401, "description": "Unauthorized"},
}


def _poll_once(monkeypatch, status: int, caplog) -> list[logging.LogRecord]:
    stop = asyncio.Event()

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"username": "kazma_test_bot", "id": 1}})
        if path.endswith("/getUpdates"):
            stop.set()
            return httpx.Response(status, content=json.dumps(_BODIES[status]).encode(),
                                  headers={"content-type": "application/json"})
        return httpx.Response(200, json={"ok": True, "result": True})

    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs.pop("verify", None)
        return real_client(*args, transport=httpx.MockTransport(answer), **kwargs)

    monkeypatch.setattr(telegram_module.httpx, "AsyncClient", client)
    adapter = TelegramAdapter(token="123:fake", allow_all=True)
    with caplog.at_level(logging.INFO, logger=telegram_module.logger.name):
        asyncio.run(asyncio.wait_for(adapter.listen(asyncio.Queue(), stop), timeout=30))
    return [r for r in caplog.records if "polling error" in r.getMessage()]


@pytest.mark.parametrize("status", [429, 502])
def test_telegram_s_own_trouble_is_a_warning(monkeypatch, caplog, status):
    lines = _poll_once(monkeypatch, status, caplog)
    assert [(r.levelno, r.getMessage().split(":")[0]) for r in lines] == [
        (logging.WARNING, f"[telegram] HTTP {status} polling error")]


def test_a_refusal_of_the_bot_stays_an_error(monkeypatch, caplog):
    lines = _poll_once(monkeypatch, 401, caplog)
    assert [r.levelno for r in lines] == [logging.ERROR]

