"""HITL/callback empty-allowlist fail-closed (audit H-3)."""

from __future__ import annotations

import pytest

from kazma_gateway.adapters.discord import DiscordAdapter
from kazma_gateway.adapters.slack import SlackAdapter
from kazma_gateway.adapters.telegram import TelegramAdapter


def test_telegram_empty_allowlist_rejects_callbacks() -> None:
    closed = TelegramAdapter(token="t", allow_all=False, allowed_users=[])
    assert closed.actor_allowed(99) is False
    open_all = TelegramAdapter(token="t", allow_all=True, allowed_users=[])
    assert open_all.actor_allowed(99) is True
    listed = TelegramAdapter(token="t", allow_all=False, allowed_users=[1])
    assert listed.actor_allowed(1) is True
    assert listed.actor_allowed(2) is False


def test_discord_empty_allowlist_rejects_interactions() -> None:
    closed = DiscordAdapter(token="t", allow_all=False, allowed_users=[])
    assert closed.actor_allowed("99") is False
    open_all = DiscordAdapter(token="t", allow_all=True, allowed_users=[])
    assert open_all.actor_allowed("99") is True
    listed = DiscordAdapter(token="t", allow_all=False, allowed_users=["1"])
    assert listed.actor_allowed("1") is True
    assert listed.actor_allowed("2") is False


def test_slack_empty_allowlist_rejects_block_actions() -> None:
    closed = SlackAdapter(bot_token="xoxb-t", allow_all=False, allowed_users=[])
    assert closed.actor_allowed("U99") is False
    open_all = SlackAdapter(bot_token="xoxb-t", allow_all=True, allowed_users=[])
    assert open_all.actor_allowed("U99") is True
    listed = SlackAdapter(bot_token="xoxb-t", allow_all=False, allowed_users=["U1"])
    assert listed.actor_allowed("U1") is True
    assert listed.actor_allowed("U2") is False


@pytest.mark.asyncio
async def test_telegram_callback_handler_acks_and_ignores_when_closed() -> None:
    """Negative control: empty allowlist must not process hitl:approve."""
    adapter = TelegramAdapter(token="t", allow_all=False, allowed_users=[])
    spawned: list[str] = []

    def _spawn(coro: object) -> None:
        if hasattr(coro, "close"):
            coro.close()  # type: ignore[union-attr]
        spawned.append("acked")

    adapter._spawn = _spawn  # type: ignore[method-assign]
    adapter._queue = None

    await adapter._handle_callback_query(
        {
            "id": "cb1",
            "data": "hitl:approve:thread-xyz",
            "from": {"id": 42},
            "message": {"chat": {"id": 1}, "message_id": 2},
        }
    )
    assert spawned, "must ack the spinner"
    assert adapter._queue is None
