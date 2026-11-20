"""A swarm task's result follows the chat that started it (the memory
framework audit's F10, its first half; part C2).

A worker's result was stored as a memory under ``swarm:<worker>`` with no
chat, so a chat set to "Don't remember this chat" that started a swarm task
left "Task: ... Result: ..." as a recallable memory, and forgetting the chat
did not reach it. Now the dispatch hands the task's conversation
(``source_thread_id``) to ``swarm_bridge.store_swarm_result``: the result
names its chat (``metadata.chat``), a chat kept out of memory stores nothing,
and forgetting the chat takes it (``forget._chat_notes``). A task no chat
started is the worker's alone, as before.

Negative controls: the dispatch without the chat stores the result of a
kept-out chat, and forgetting the chat leaves it.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from tests._no_models import finish_turn_memory, keep_models_out

CHAT = "thread-kept-out"


@pytest.fixture(autouse=True)
def _memory(tmp_path, monkeypatch):
    keep_models_out(monkeypatch, llm=False)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("kazma_core.memory.forget.chat_keys", lambda key: [key] if key else [])
    from kazma_core.memory import dual_write

    dual_write._reset_mirror()
    yield
    dual_write._reset_mirror()
    finish_turn_memory()


def _results() -> list[tuple[str, str, dict]]:
    from kazma_core.paths import primary_memory_db

    conn = sqlite3.connect(primary_memory_db())
    try:
        return [(r[0], r[1], json.loads(r[2])) for r in conn.execute(
            "SELECT id, tier, metadata_json FROM episodes WHERE session_id LIKE 'swarm:%'")]
    finally:
        conn.close()


async def _dispatch_result(chat: str | None, output: str = "The branch opens at 8:00.") -> None:
    """What the dispatch does with a finished task (``worker_dispatch``):
    the chat read off the task's context, as ``_do_dispatch`` reads it."""
    from kazma_core.swarm import worker_dispatch

    context = SimpleNamespace(task_id="task-1", metadata={"source_thread_id": chat} if chat else {})
    await worker_dispatch._index_worker_l4_memory(
        worker_name="researcher", prompt="Find the Gulf Bank branch hours",
        output=output, task_id="task-1",
        chat=worker_dispatch._source_chat(context), tenant_id="default")


def _keep_out(chat: str) -> None:
    from kazma_core.memory.forget import set_chat_remembered

    assert set_chat_remembered(chat, False, tenant_id="default")["ok"] is not False


@pytest.mark.asyncio
async def test_the_result_names_its_chat():
    await _dispatch_result(CHAT)
    [(_eid, tier, meta)] = _results()
    assert meta["chat"] == CHAT and meta["source"] == "swarm_result" and tier == "episodic"


@pytest.mark.asyncio
async def test_a_chat_kept_out_of_memory_keeps_its_tasks_results_out():
    _keep_out(CHAT)
    await _dispatch_result(CHAT)
    assert _results() == []


@pytest.mark.asyncio
async def test_control_without_the_chat_the_result_of_a_kept_out_chat_is_stored():
    _keep_out(CHAT)
    await _dispatch_result(None)
    assert len(_results()) == 1


@pytest.mark.asyncio
async def test_forgetting_the_chat_takes_its_tasks_results():
    from kazma_core.memory.forget import FORGOTTEN_TIER, forget_chat

    await _dispatch_result(CHAT)
    await _dispatch_result("another-chat", "The branch closes at 14:00.")
    forget_chat(CHAT, tenant_id="default")
    tiers = {meta.get("chat"): tier for _eid, tier, meta in _results()}
    assert tiers == {CHAT: FORGOTTEN_TIER, "another-chat": "episodic"}


@pytest.mark.asyncio
async def test_control_a_forget_that_does_not_look_for_results_leaves_them(monkeypatch):
    from kazma_core.memory import forget

    real = forget._chat_notes

    def notes_only(conn, *, tenant_id, keys, turn=None):
        return [eid for eid in real(conn, tenant_id=tenant_id, keys=keys, turn=turn)
                if conn.execute("SELECT session_id FROM episodes WHERE id = ?", (eid,)).fetchone()[0]
                == forget._NOTES_SESSION]

    monkeypatch.setattr(forget, "_chat_notes", notes_only)
    await _dispatch_result(CHAT)
    forget.forget_chat(CHAT, tenant_id="default")
    assert [tier for _eid, tier, _meta in _results()] == ["episodic"]


@pytest.mark.asyncio
async def test_a_turn_forgotten_alone_leaves_the_chats_results():
    """A result names its chat and no turn: forgetting one turn of the chat
    is not forgetting the task the chat started."""
    import sqlite3 as _sqlite

    from kazma_core.memory import forget
    from kazma_core.paths import primary_memory_db

    await _dispatch_result(CHAT)
    conn = _sqlite.connect(primary_memory_db())
    try:
        assert forget._chat_notes(conn, tenant_id="default", keys=[CHAT], turn=3) == []
        assert len(forget._chat_notes(conn, tenant_id="default", keys=[CHAT])) == 1
    finally:
        conn.close()


def test_a_task_from_no_chat_has_no_chat():
    from kazma_core.swarm.worker_dispatch import _source_chat

    assert _source_chat(SimpleNamespace(metadata={})) == ""
    assert _source_chat(SimpleNamespace(metadata=None)) == ""
    assert _source_chat(SimpleNamespace(metadata={"source_thread_id": 7})) == ""
    assert _source_chat(SimpleNamespace(metadata={"source_thread_id": "t-9"})) == "t-9"
