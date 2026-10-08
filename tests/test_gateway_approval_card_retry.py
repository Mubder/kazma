"""A fresh operator request must not inherit a previous turn's card mute."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kazma_gateway.gateway import IncomingMessage


class TurnEntryReached(BaseException):
    """Stop at fresh-turn construction without running tools or a model."""


@pytest.mark.parametrize("platform", ["telegram", "discord", "slack"])
@pytest.mark.parametrize("text", ["Write the approved fixture", "/long on Write the approved fixture"])
@pytest.mark.parametrize("negative_control", [False, True])
async def test_new_request_lifts_card_mute_before_turn_entry(
    monkeypatch, platform, text, negative_control,
):
    from kazma_gateway.agent_handler import graph, hitl
    from kazma_gateway.agent_handler.store import _InMemoryStore

    tid = f"fresh-card-{platform}"
    other = "unrelated-card-thread"
    args = {"path": "fixture.txt", "content": "approved"}
    monkeypatch.setattr(hitl, "_recent_cards", {})
    monkeypatch.setattr("kazma_core.sessions.directory.find_mouth_thread", lambda *a, **k: None)
    assert hitl.approval_card_suppressed(tid, "file_write", args) is None
    assert hitl.approval_card_suppressed(other, "file_write", args) is None
    assert hitl.approval_card_suppressed(tid, "file_write", args)

    async def stop_at_turn(msg, store):
        assert msg.context_metadata["thread_id"] == tid
        raise TurnEntryReached()

    monkeypatch.setattr(graph, "_build_initial_state", stop_at_turn)
    if negative_control:
        monkeypatch.setattr(graph, "clear_approval_throttle", lambda thread: None)
    manager = SimpleNamespace(send=AsyncMock(), adapters=[])
    handler = graph.create_graph_handler(graph=object(), manager=manager, store=_InMemoryStore())
    msg = IncomingMessage(platform, f"{platform}:42", text,
                          context_metadata={"thread_id": tid, "chat_id": 42})
    with pytest.raises(TurnEntryReached):
        await handler(msg)
    assert bool(hitl.approval_card_suppressed(tid, "file_write", args)) == negative_control
    # The next duplicate within this new turn remains muted; other threads
    # retain their own histories. No approval or tool execution was granted.
    assert hitl.approval_card_suppressed(tid, "file_write", args)
    assert hitl.approval_card_suppressed(other, "file_write", args)
    manager.send.assert_not_awaited()


@pytest.mark.parametrize("platform", ["telegram", "discord", "slack"])
async def test_approval_resume_does_not_lift_card_mute(monkeypatch, platform):
    from kazma_gateway.agent_handler import graph, hitl
    from kazma_gateway.agent_handler.store import _InMemoryStore

    tid = f"resume-card-{platform}"
    args = {"path": "fixture.txt", "content": "approved"}
    monkeypatch.setattr(hitl, "_recent_cards", {})
    monkeypatch.setattr("kazma_core.sessions.directory.find_mouth_thread", lambda *a, **k: None)
    assert hitl.approval_card_suppressed(tid, "file_write", args) is None
    resume = AsyncMock(return_value=True)
    monkeypatch.setattr(graph, "_handle_hitl_resume", resume)
    build = AsyncMock()
    monkeypatch.setattr(graph, "_build_initial_state", build)
    handler = graph.create_graph_handler(
        graph=object(), manager=SimpleNamespace(send=AsyncMock(), adapters=[]), store=_InMemoryStore(),
    )
    await handler(IncomingMessage(platform, f"{platform}:42", "/hitl approve",
                                  context_metadata={"thread_id": tid, "chat_id": 42}))
    resume.assert_awaited_once()
    build.assert_not_awaited()
    assert hitl.approval_card_suppressed(tid, "file_write", args)
