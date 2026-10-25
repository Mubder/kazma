"""A run card keeps its buttons after Diagnose (live test, 2026-10-25).

The owner pressed "Ask Kazma to diagnose" on a Telegram incident card and Retry
and Dismiss went with it: the adapters took every button off a run card on any
press. Diagnose decides nothing -- the incident stays open -- so the card stays
answerable; Retry, Dismiss, Cancel and Close still clear it.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from kazma_gateway.adapters.discord import DiscordAdapter
from kazma_gateway.adapters.platform_callbacks import parse_callback_data
from kazma_gateway.adapters.telegram import TelegramAdapter
from kazma_gateway.run_cards import press_keeps_buttons


@pytest.fixture(autouse=True)
def _admin(monkeypatch):
    """The presser is the gateway admin (the card's buttons are admin-only)."""
    import kazma_gateway.allowlists as allowlists

    monkeypatch.setattr(allowlists, "is_gateway_admin", lambda *_a, **_k: True)


def _telegram_press(action: str) -> tuple[list[str], list[str]]:
    adapter = TelegramAdapter(token="t", allow_all=False, allowed_users=[42])
    posted: list[str] = []

    async def post(url, json=None, **_):
        posted.append(url)
        return MagicMock(status_code=200)

    http = MagicMock()
    http.post = AsyncMock(side_effect=post)
    adapter._ensure_http = lambda: http  # type: ignore[method-assign]
    adapter._spawn = lambda coro: coro.close()  # type: ignore[method-assign]
    queued: list[str] = []
    adapter._queue = MagicMock()
    adapter._queue.put_nowait = lambda msg: queued.append(msg.text)
    asyncio.run(adapter._handle_callback_query({
        "id": "cb1", "data": f"run:{action}:inc_51a752e2ab3c", "from": {"id": 42},
        "message": {"chat": {"id": 1804015016}, "message_id": 7},
    }))
    return posted, queued


def _discord_press(action: str) -> list[dict]:
    adapter = DiscordAdapter(token="fake", allow_all=True)
    acks: list[dict] = []

    async def post(url, json=None, **_):
        acks.append(json)
        return MagicMock(status_code=200)

    adapter._http = MagicMock()
    adapter._http.post = AsyncMock(side_effect=post)
    adapter._queue = MagicMock()
    asyncio.run(adapter._handle_interaction({
        "id": "ia_1", "token": "tok_1", "data": {"custom_id": f"run:{action}:inc_51a752e2ab3c"},
        "message": {"content": "❌ Swarm task failed"}, "user": {"id": "user_1"},
    }))
    return acks


def test_diagnose_leaves_the_telegram_card_its_buttons():
    posted, queued = _telegram_press("diagnose")
    assert queued == ["/runs act inc_51a752e2ab3c diagnose"]
    assert "/editMessageReplyMarkup" not in posted


@pytest.mark.parametrize("action", ["retry", "dismiss", "cancel", "close", "retry_checked"])
def test_a_decision_still_clears_the_telegram_card(action):
    posted, queued = _telegram_press(action)
    assert queued == [f"/runs act inc_51a752e2ab3c {action}"]
    assert "/editMessageReplyMarkup" in posted


def test_diagnose_leaves_the_discord_card_its_buttons():
    assert _discord_press("diagnose") == [{"type": 6}]


def test_a_decision_still_clears_the_discord_card():
    assert _discord_press("retry")[-1]["data"]["components"] == []


def test_only_a_run_cards_diagnose_keeps_the_card():
    assert press_keeps_buttons(parse_callback_data("run:diagnose:inc_1"))
    assert not press_keeps_buttons(parse_callback_data("run:retry:inc_1"))
    assert not press_keeps_buttons(parse_callback_data("hitl:approve:thread-1"))


def test_the_old_rule_cleared_the_card_on_diagnose(monkeypatch):
    """Negative control: every press clearing the card, as on 2026-10-25."""
    import kazma_gateway.run_cards as run_cards

    monkeypatch.setattr(run_cards, "press_keeps_buttons", lambda _action: False)
    posted, _queued = _telegram_press("diagnose")
    assert "/editMessageReplyMarkup" in posted
