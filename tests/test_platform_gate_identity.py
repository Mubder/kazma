"""A platform approval belongs to the interrupt that rendered its card."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def test_long_discord_callbacks_preserve_gate_identity() -> None:
    from kazma_gateway.adapters.platform_callbacks import parse_callback_data
    from kazma_gateway.adapters.platform_keyboards import (
        discord_approval_components,
        discord_semantic_components,
    )

    request = "thread-" + "x" * 90 + "~gate-123"
    rows = discord_approval_components(request)
    buttons = [button for row in rows for button in row["components"]]
    assert len(buttons) == 3
    for button in buttons:
        assert len(button["custom_id"].encode()) <= 100
        assert parse_callback_data(button["custom_id"]).text.split()[2] == request
    semantic = discord_semantic_components(request, [{"id": "confirm", "label": "Confirm"}])
    assert parse_callback_data(semantic[0]["components"][0]["custom_id"]).text == f"/hitl opt {request} confirm"


@pytest.mark.asyncio
@pytest.mark.parametrize("gate_token", ["thread~old-gate", "thread"])
async def test_stale_or_unbound_button_cannot_decide_current_gate(gate_token, monkeypatch) -> None:
    import kazma_ui.hitl_decision as decisions
    from kazma_gateway.agent_handler.hitl import _handle_hitl_resume
    from kazma_gateway.gateway import IncomingMessage

    recorder = AsyncMock()
    monkeypatch.setattr(decisions, "record_gate_decision", recorder)
    interrupt = SimpleNamespace(id="new-gate", value={"type": "hitl_approval", "tool": "file_write"})
    graph = SimpleNamespace(
        aget_state=AsyncMock(return_value=SimpleNamespace(next=("tool_worker",), tasks=[SimpleNamespace(interrupts=[interrupt])])),
        ainvoke=AsyncMock(),
    )
    store = SimpleNamespace(get=AsyncMock(return_value={"sender_id": "user"}))
    manager = SimpleNamespace(send=AsyncMock())
    from kazma_core.sessions.directory import record_thread_owner
    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "telegram:user")
    record_thread_owner("thread", "telegram:user")
    message = IncomingMessage("telegram", "user", f"/hitl approve_task {gate_token}",
                              context_metadata={"chat_id": 123, "callback_query_id": "button"})
    assert await _handle_hitl_resume(message, graph, {}, "thread", store, manager)
    recorder.assert_not_awaited()
    graph.ainvoke.assert_not_awaited()
    assert "stale" in manager.send.call_args.args[0].text
