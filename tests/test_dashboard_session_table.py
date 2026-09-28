"""The Dashboard's session table shows each chat as it is (2026-09-28).

On the live install every row read "unknown / anonymous / 0 messages /
created -". The table enriched checkpoint threads from the gateway's
five-minute session cache, counted messages inside the Postgres checkpoint
row (the Postgres saver keeps them in ``checkpoint_blobs``), and read the
time from checkpoint metadata LangGraph never writes. A row now takes the
chat's title, platform and message count from the chat store, and the
number of saved steps and the last activity from the checkpoints.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime

import pytest
from starlette.requests import Request

from kazma_ui import dashboard


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/api/sessions", "headers": []})


def _checkpoint(checkpoint_id: str, messages: list[dict]) -> dict:
    from langgraph.checkpoint.base import Checkpoint

    return Checkpoint(
        v=1,
        id=checkpoint_id,
        ts="2026-07-08T00:00:00+00:00",
        channel_values={"messages": messages},
        channel_versions={},
        versions_seen={},
        pending_sends=[],
    )


@pytest.mark.asyncio
async def test_list_checkpoints_counts_steps_and_dates_the_newest(tmp_path) -> None:
    from kazma_gateway.stores.checkpoint import create_checkpoint_manager
    from langgraph.checkpoint.base import CheckpointMetadata
    from langgraph.checkpoint.base.id import uuid6

    manager = await create_checkpoint_manager(str(tmp_path / "checkpoints.db"))
    try:
        meta = CheckpointMetadata(source="loop", step=0, writes={}, parents={})
        config = {"configurable": {"thread_id": "chat-a", "checkpoint_ns": ""}}
        for n in range(3):
            config = await manager.aput(
                config, _checkpoint(str(uuid6()), [{"role": "user", "content": "q"}] * (n + 1)), meta, {}
            )
        other = {"configurable": {"thread_id": "chat-b", "checkpoint_ns": ""}}
        await manager.aput(other, _checkpoint(str(uuid6()), []), meta, {})

        rows = await manager.list_checkpoints()
    finally:
        await manager.close()

    by_thread = {r["thread_id"]: r for r in rows}
    assert [r["thread_id"] for r in rows] == ["chat-b", "chat-a"], "newest activity first"
    assert by_thread["chat-a"]["steps"] == 3
    assert by_thread["chat-a"]["message_count"] == 3, "the newest checkpoint's messages"
    assert by_thread["chat-b"]["steps"] == 1
    when = datetime.fromisoformat(by_thread["chat-a"]["last_activity"])
    assert abs(when.timestamp() - time.time()) < 120, "read from the uuid6 id: saved just now"


def test_a_checkpoint_without_messages_reports_none_not_zero() -> None:
    """The Postgres shape: messages live in checkpoint_blobs, not the row."""
    from kazma_gateway.stores.checkpoint import CheckpointManager

    row = CheckpointManager._thread_row(
        "t", "not-a-uuid6", 4, {"ts": "2026-09-01T10:00:00+00:00", "channel_values": {}}
    )
    assert row["message_count"] is None
    assert row["steps"] == 4
    assert row["last_activity"] == "2026-09-01T10:00:00+00:00", "the checkpoint's own ts"


class _Checkpoints:
    async def list_checkpoints(self, limit: int = 50) -> list[dict]:
        return [
            {"thread_id": "t-chat", "checkpoint_id": "c1", "steps": 7,
             "last_activity": "2026-09-28T05:00:00+00:00", "message_count": None},
            {"thread_id": "t-orphan", "checkpoint_id": "c2", "steps": 2,
             "last_activity": "2026-09-27T05:00:00+00:00", "message_count": None},
        ]


@pytest.mark.asyncio
async def test_rows_take_the_chat_title_platform_and_count(monkeypatch) -> None:
    seen_threads: list[str] = []

    def chats() -> dict:
        seen_threads.append(threading.current_thread().name)
        return {"t-chat": {"session_id": "s-1", "title": "Plan the trip", "platform": "web",
                           "message_count": 12, "archived": True, "thread_id": "t-chat"}}

    monkeypatch.setattr(dashboard, "_chats_by_thread", chats)
    dashboard.set_dashboard_context(checkpoint_manager=_Checkpoints(), session_store=None)
    try:
        response = await dashboard.list_sessions(_request())
    finally:
        dashboard.set_dashboard_context(checkpoint_manager=None)
    body = json.loads(response.body)
    chat, orphan = body["sessions"]
    assert chat == {
        "thread_id": "t-chat", "checkpoint_id": "c1", "steps": 7,
        "last_activity": "2026-09-28T05:00:00+00:00", "session_id": "s-1",
        "title": "Plan the trip", "platform": "web", "message_count": 12, "archived": True,
    }
    assert orphan["session_id"] == "" and orphan["title"] == "" and orphan["message_count"] is None
    assert seen_threads and seen_threads[0] != threading.main_thread().name, (
        "the chat store is read off the event loop"
    )

