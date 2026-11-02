"""A message a chat platform kept refusing with 429 is not sent (FA-105).

The 2026-11 functional audit: the Discord sender started each send with
``all_sent = True`` and its 429 branch ``continue``d without marking the chunk
unsent, so after the last retry the loop ended and the send answered True.
A reminder Discord never took was acknowledged by the cron outbox (§16E) and
never tried again.

Parity across the three senders, each the real adapter against a platform
that always answers 429: the send answers False. The negative control is the
old Discord loop.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from kazma_gateway.gateway import OutboundMessage


def _outbound(target: str) -> OutboundMessage:
    chat = target.split(":", 1)[1]
    return OutboundMessage(target_id=target, text="your reminder",
                           context_metadata={"chat_id": chat, "channel_id": chat})


def _always_429(platform: str):
    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request.url.path)
        if platform == "telegram":
            return httpx.Response(429, json={"ok": False, "error_code": 429,
                                             "description": "Too Many Requests: retry after 0",
                                             "parameters": {"retry_after": 0}})
        if platform == "slack":
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"ok": False, "error": "ratelimited"})
        return httpx.Response(429, json={"message": "You are being rate limited.", "retry_after": 0})

    return handler, posts


def _send(platform: str, monkeypatch) -> tuple[bool, list[str]]:
    import random

    from kazma_gateway.adapters import discord as discord_mod

    monkeypatch.setattr(random, "uniform", lambda a, b: 0.0)  # no jitter in the test
    monkeypatch.setattr(discord_mod, "_SEND_BASE_DELAY", 0.0, raising=False)

    async def no_sleep(*_a, **_k):
        return None

    handler, posts = _always_429(platform)

    async def run() -> bool:
        if platform == "telegram":
            from kazma_gateway.adapters.telegram import TelegramAdapter

            adapter = TelegramAdapter(token="123:fake", allow_all=True)
            adapter._http = httpx.AsyncClient(base_url="https://api.telegram.org/bot123:fake",
                                              transport=httpx.MockTransport(handler))
            target = "telegram:42"
        elif platform == "slack":
            from kazma_gateway.adapters.slack import SlackAdapter

            adapter = SlackAdapter(bot_token="xoxb-fake", app_token="xapp-fake", allow_all=True)
            adapter._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            target = "slack:C1"
        else:
            from kazma_gateway.adapters.discord import DiscordAdapter

            adapter = DiscordAdapter(token="fake", allow_all=True)
            adapter._http = httpx.AsyncClient(base_url="https://discord.com/api/v10",
                                              transport=httpx.MockTransport(handler))
            target = "discord:99"
        monkeypatch.setattr(asyncio, "sleep", no_sleep)
        try:
            return await asyncio.wait_for(adapter.send(_outbound(target)), 30)
        finally:
            await adapter._http.aclose()

    return asyncio.run(run()), posts


@pytest.mark.parametrize("platform", ["telegram", "slack", "discord"])
def test_a_platform_that_always_answers_429_gets_no_sent(platform, monkeypatch):
    sent, posts = _send(platform, monkeypatch)
    assert sent is False, (platform, posts)
    assert posts, "the platform was asked"


def test_the_old_discord_loop_answered_sent(monkeypatch):
    """Negative control: the loop before FA-105, against the same platform."""
    import inspect
    import textwrap

    from kazma_gateway.adapters import discord as discord_mod

    async def old_loop(self, chunks, channel_id):
        all_sent = True
        for chunk in chunks:
            for attempt in range(discord_mod._SEND_MAX_RETRIES):
                try:
                    resp = await self._http.post(f"/channels/{channel_id}/messages", json={"content": chunk})
                    if resp.status_code == 429:
                        continue
                    resp.raise_for_status()
                    break
                except httpx.HTTPStatusError:
                    all_sent = False
                    break
        return all_sent

    handler, posts = _always_429("discord")

    async def run() -> bool:
        adapter = discord_mod.DiscordAdapter(token="fake", allow_all=True)
        adapter._http = httpx.AsyncClient(base_url="https://discord.com/api/v10",
                                          transport=httpx.MockTransport(handler))
        try:
            return await old_loop(adapter, ["your reminder"], "99")
        finally:
            await adapter._http.aclose()

    assert asyncio.run(run()) is True and len(posts) == discord_mod._SEND_MAX_RETRIES
    # ...and the fixed loop keeps its per-chunk flag.
    assert "chunk_sent = True" in textwrap.dedent(inspect.getsource(discord_mod.DiscordAdapter.send))
