"""send_approval_request must emit native HITL buttons, not ASCII mocks."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_send_approval_request_passes_hitl_approval():
    from kazma_skills.native.chat_platform_dispatcher.tools import send_approval_request

    mock = AsyncMock(return_value="sent:telegram:123")
    with (
        patch(
            "kazma_skills.native.chat_platform_dispatcher.tools._core_send_message",
            mock,
        ),
        patch(
            "kazma_core.safety.hitl.get_current_thread_id",
            return_value="gw-telegram-1",
        ),
    ):
        res = await send_approval_request(
            "telegram",
            "telegram:123",
            "Example permission card from Kazma",
            ["Approve", "Deny"],
        )

    assert "Error" not in res
    mock.assert_awaited_once()
    kwargs = mock.await_args.kwargs
    assert kwargs["backend"] == "telegram"
    assert kwargs["target_id"] == "telegram:123"
    assert kwargs["hitl_approval"]["request_id"] == "gw-telegram-1"
    assert kwargs["hitl_approval"]["title"] == "Example permission card from Kazma"
    text = kwargs["text"]
    assert "KAZMA INTERACTIVE HITL CARD" not in text
    assert "[ APPROVE ]" not in text
    assert "[ DENY ]" not in text
    assert "Approval required" in text
    assert "hitl approve gw-telegram-1" in text


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
            "kazma_core.safety.hitl.get_current_thread_id",
            return_value="gw-t",
        ),
        patch(
            "kazma_core.tools.send_message.get_current_delivery_target",
            return_value="telegram:99",
        ),
    ):
        res = await send_approval_request("telegram", "", "Need approval", [])

    assert "Error" not in res
    assert mock.await_args.kwargs["target_id"] == "telegram:99"


def test_apply_hitl_approval_markup_telegram():
    from kazma_gateway.agent_handler.hitl import apply_hitl_approval_markup

    ctx = apply_hitl_approval_markup(
        {"parse_mode": "HTML"},
        platform="telegram",
        hitl_approval={"request_id": "tid-9", "title": "demo"},
    )
    rows = ctx["reply_markup"]["inline_keyboard"]
    assert rows[0][0]["callback_data"] == "hitl:approve:tid-9"
    assert rows[0][1]["callback_data"] == "hitl:deny:tid-9"
    assert rows[1][0]["callback_data"] == "hitl:approve_task:tid-9"
    assert ctx["parse_mode"] == "HTML"


def test_apply_hitl_approval_markup_noop_without_id():
    from kazma_gateway.agent_handler.hitl import apply_hitl_approval_markup

    assert apply_hitl_approval_markup({}, platform="telegram", hitl_approval=None) == {}
    assert apply_hitl_approval_markup({}, platform="telegram", hitl_approval={}) == {}
