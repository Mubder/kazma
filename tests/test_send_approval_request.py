"""send_approval_request is a question to the operator, never an approval card.

Audit 2026-10-26 AUD-060. The tool sent the real HITL card's Approve / Deny /
Approve-for-task buttons and a "Reply: hitl approve <thread>" line, on a
message bound to no interrupt. A prompt-injected turn could post "Approve
reading the weather?", then pause on ``shell_exec``; the operator's typed
reply approved the real ``shell_exec``. An approval card is the gate's own
(§7, §30): the tool now sends a plain question, and no send carries approval
buttons.

(Before 2026-10-26 this file asserted the opposite -- that the card carried
``hitl_approval`` markup and a ``hitl approve <thread>`` line.)
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest


async def _sent(**kw: Any) -> tuple[str, dict[str, Any]]:
    from kazma_skills.native.chat_platform_dispatcher.tools import send_approval_request

    mock = AsyncMock(return_value="sent:telegram:123")
    with (
        patch("kazma_skills.native.chat_platform_dispatcher.tools._core_send_message", mock),
        patch("kazma_core.safety.hitl.get_current_thread_id", return_value="gw-telegram-1"),
    ):
        res = await send_approval_request(
            kw.get("channel", "telegram"),
            kw.get("recipient", "telegram:123"),
            kw.get("title", "Approve reading the weather?"),
            kw.get("actions", ["Approve", "Deny"]),
        )
    mock.assert_awaited_once()
    return res, mock.await_args.kwargs


@pytest.mark.asyncio
async def test_the_message_decides_no_gate() -> None:
    res, kwargs = await _sent()
    assert "Error" not in res
    assert kwargs["backend"] == "telegram"
    assert kwargs["target_id"] == "telegram:123"
    # No approval buttons, and no command that approves the thread's gate.
    assert "hitl_approval" not in kwargs
    assert "reply_markup" not in kwargs and "components" not in kwargs and "blocks" not in kwargs
    text = kwargs["text"]
    assert "hitl approve" not in text.lower() and "hitl deny" not in text.lower()
    assert "gw-telegram-1" not in text
    assert "Approval required" not in text  # the real card's heading
    assert "Approve reading the weather?" in text
    assert "approves nothing" in text
    # The answer it returns does not claim an approval channel either.
    assert "not an approval" in res


@pytest.mark.asyncio
async def test_the_offered_answers_are_listed_as_words() -> None:
    _res, kwargs = await _sent(actions=["Yes", "No", "Later"])
    assert "Yes / No / Later" in kwargs["text"]


@pytest.mark.asyncio
async def test_send_approval_request_uses_delivery_target_when_recipient_empty():
    from kazma_skills.native.chat_platform_dispatcher.tools import send_approval_request

    mock = AsyncMock(return_value="sent:telegram:99")
    with (
        patch(
            "kazma_skills.native.chat_platform_dispatcher.tools._core_send_message",
            mock,
        ),
        patch(
            "kazma_core.tools.send_message.get_current_delivery_target",
            return_value="telegram:99",
        ),
    ):
        res = await send_approval_request("telegram", "", "Need approval", [])

    assert "Error" not in res
    assert mock.await_args.kwargs["target_id"] == "telegram:99"


class _Manager:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send(self, outbound: Any) -> bool:
        self.sent.append(outbound)
        return True


class _Store:
    async def get(self, _key: str) -> None:
        return None


@pytest.mark.parametrize("platform", ["telegram", "discord", "slack"])
def test_the_gateway_send_backend_attaches_no_approval_buttons(platform: str) -> None:
    """Whatever a caller passes, a send is never dressed as a HITL card:
    the backend applied the gate's own markup to any ``hitl_approval=``."""
    from kazma_gateway.agent_handler.graph import make_gateway_send_handler

    manager = _Manager()
    handler = make_gateway_send_handler(manager, _Store())
    answer = asyncio.run(handler(
        f"{platform}:42", "Approve?",
        hitl_approval={"request_id": "gw-thread-1", "title": "Approve?"},
    ))
    assert answer == f"sent:{platform}:42"
    ctx = manager.sent[0].context_metadata
    for key in ("reply_markup", "components", "blocks"):
        assert key not in ctx, f"{platform}: {key} = {ctx.get(key)!r}"


def test_the_declarations_do_not_call_it_an_approval_channel() -> None:
    """The tier comment, the side-effect profile and the store registry said
    it IS the HITL card and that "its answer returns to the turn"."""
    from kazma_core import store_registry

    note = store_registry.TOOL_WRITES["send_approval_request"].note
    assert "not an approval card" in note
    assert "returns to the turn" not in note
