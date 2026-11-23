"""An install that does not enforce Spaces keeps its press gate exactly
(audit 2026-09-16 F-8, AGENTS §42 and §64; S3-06 must not move it).

S3-06 lets an approval or run card's press go on to the card's own rule
once Spaces is enforced (``allowlists.press_needs_admin``). Before Spaces
nothing changes: a press that answers an approval (``hitl``), a swarm bus
card (``swarm``) or a run incident (``run``) still needs a gateway admin,
on every chat app, and a non-admin's press changes nothing -- no turn is
queued, no swarm request is decided, the card keeps its buttons -- and is
told it is not authorized.

Telegram, Discord and WhatsApp are driven through their press handlers;
Slack's press gate lives in its Socket Mode reader, held by the source gate
``test_every_adapter_s_press_gate_asks_whose_rule_it_is``
(``tests/test_chat_commands_act_as_the_account.py``) and by
:func:`test_the_rule_before_spaces_is_the_admin_s_for_every_kind`.

Negative control: the rule of an install that enforces Spaces applied to
one that does not -- a non-admin's approval press reaches the handler.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kazma_core.spaces import scope

from kazma_gateway import allowlists

KINDS = {
    "hitl": "hitl:approve:req_1",
    "run": "run:cancel:inc_1",
    "swarm": "swarm_approve_task_1",
}


@pytest.fixture(autouse=True)
def not_enforced(monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    # The admin is someone else; the open switch lets the presser chat.
    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "telegram:1,discord:1,whatsapp:1")


def test_the_rule_before_spaces_is_the_admin_s_for_every_kind() -> None:
    for kind in KINDS:
        assert allowlists.press_needs_admin(kind), kind
        assert not allowlists.press_keeps_card(kind, direct=False), kind


# ── Telegram ────────────────────────────────────────────────────────────


def _telegram_press(data: str) -> tuple[list[Any], list[str], list[str], Any]:
    from kazma_gateway.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter(token="fake", allow_all=True)
    answers: list[str] = []
    posted: list[str] = []

    async def answer(_cb_id: str, text: str = "") -> None:
        answers.append(text or "")

    class _Http:
        async def post(self, path, json=None):
            posted.append(path)
            return MagicMock(status_code=200)

    adapter._answer_callback_query = answer
    adapter._http = _Http()
    adapter._ensure_http = lambda: adapter._http
    queue: asyncio.Queue = asyncio.Queue()
    adapter._queue = queue

    async def run() -> list[Any]:
        with patch("kazma_core.swarm.shared_approvals.decide_from_button", return_value="task_1") as decided:
            await adapter._handle_callback_query({
                "id": "cb_1", "data": data,
                "message": {"chat": {"id": -100, "type": "supergroup"}, "message_id": 2},
                "from": {"id": 424242},
            })
            for _ in range(20):
                await asyncio.sleep(0.01)
            return [queue.get_nowait() for _ in range(queue.qsize())], decided

    queued, decided = asyncio.run(run())
    return queued, answers, posted, decided


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_telegram_non_admin_press_changes_nothing(kind: str) -> None:
    queued, answers, posted, decided = _telegram_press(KINDS[kind])
    assert queued == [], f"a non-admin {kind} press must not reach the handler"
    decided.assert_not_called()
    assert "/editMessageReplyMarkup" not in posted, "the card keeps its buttons"
    assert any("requires admin" in a for a in answers), f"refused by the admin gate: {answers}"


# ── Discord ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_discord_non_admin_press_changes_nothing(kind: str) -> None:
    from kazma_gateway.adapters.discord import DiscordAdapter

    adapter = DiscordAdapter(token="fake", allow_all=True)
    acks: list[dict] = []

    async def post(url, json=None, **_kw):
        acks.append(json or {})
        return MagicMock(status_code=200)

    adapter._http = MagicMock()
    adapter._http.post = AsyncMock(side_effect=post)
    queue: asyncio.Queue = asyncio.Queue()
    adapter._queue = queue

    async def run() -> int:
        with patch("kazma_gateway.adapters.discord_callbacks.route_swarm_bus", return_value="task_1") as routed:
            await adapter._handle_interaction({
                "id": "ia_1", "token": "tok_1", "channel_id": "900", "guild_id": "77",
                "data": {"custom_id": KINDS[kind]}, "message": {"content": "Approve?"},
                "member": {"user": {"id": "424242", "username": "someone"}},
            })
            routed.assert_not_called()
            return queue.qsize()

    assert asyncio.run(run()) == 0, f"a non-admin {kind} press must not reach the handler"
    assert acks and "Admin privilege required" in (acks[0].get("data") or {}).get("content", ""), acks


# ── WhatsApp ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_whatsapp_non_admin_press_changes_nothing(kind: str) -> None:
    from kazma_core.config_store import get_config_store

    from tests.test_whatsapp import STRANGER, FakeMeta, _payload, _Rig, _until

    get_config_store().set("connectors.whatsapp.allowed_users", "", category="connectors")
    meta = FakeMeta()

    async def run() -> list[Any]:
        with patch("kazma_core.swarm.shared_approvals.decide_from_button", return_value="task_1") as decided:
            async with _Rig(meta, allowed=[], allow_all=True) as rig:
                await rig.post(_payload({"from": STRANGER, "id": "w1", "timestamp": "1", "type": "interactive",
                                         "interactive": {"type": "button_reply",
                                                         "button_reply": {"id": KINDS[kind], "title": "Yes"}}}))
                await _until(lambda: any("answering an approval" in t for t in meta.texts()))
                decided.assert_not_called()
                return rig.taken()

    assert asyncio.run(run()) == [], f"a non-admin {kind} press must not reach the handler"


# ── negative control ────────────────────────────────────────────────────


def test_negative_control_the_enforced_rule_without_spaces(monkeypatch) -> None:
    """Planted: the rule of an install that enforces Spaces, applied to one
    that does not. A non-admin's approval press reaches the handler: the
    check above fails on its own assertion."""
    monkeypatch.setattr(allowlists, "press_needs_admin", lambda kind: kind not in ("hitl", "run"))
    with pytest.raises(AssertionError, match="must not reach the handler"):
        test_telegram_non_admin_press_changes_nothing("hitl")
