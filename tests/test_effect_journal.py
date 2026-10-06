"""Effects survive the result/checkpoint crash window without being replayed."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
from unittest.mock import AsyncMock, Mock

import pytest
from kazma_core.agent.effect_journal import EffectUncertain, _EffectJournal, execute_effect
from kazma_core.agent.state import NodeName, initial_supervisor_state


@pytest.fixture(autouse=True)
def _isolated_effects(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "data"))


def _state():
    return {"thread_id": "effect-thread", "tenant_id": "owner", "created_at": "turn-1", "iteration": 2}


def _call():
    return {"id": "call-1", "name": "file_append", "arguments": {"path": "example.txt", "content": "one"}}


@pytest.mark.asyncio
async def test_completed_result_is_reused_without_dispatch():
    executor = Mock(execute=AsyncMock(return_value={"content": "written", "is_error": False}))
    call = _call()
    first = await execute_effect(executor, _state(), call, call["arguments"])
    second = await execute_effect(executor, _state(), call, call["arguments"])
    assert first == second == {"content": "written", "is_error": False}
    assert executor.execute.await_count == 1


@pytest.mark.asyncio
async def test_same_identity_cannot_change_arguments():
    executor = Mock(execute=AsyncMock(return_value={"content": "written"}))
    call = _call()
    await execute_effect(executor, _state(), call, call["arguments"])
    with pytest.raises(EffectUncertain, match="different request"):
        await execute_effect(executor, _state(), call, {**call["arguments"], "content": "two"})
    assert executor.execute.await_count == 1


@pytest.mark.asyncio
async def test_new_turn_is_a_distinct_action_even_if_provider_reuses_id():
    executor = Mock(execute=AsyncMock(return_value={"content": "written"}))
    call = _call()
    await execute_effect(executor, _state(), call, call["arguments"])
    await execute_effect(executor, {**_state(), "created_at": "turn-2"}, call, call["arguments"])
    assert executor.execute.await_count == 2


@pytest.mark.asyncio
async def test_read_tools_are_not_cached_or_journaled():
    from kazma_core.paths import data_dir

    executor = Mock(execute=AsyncMock(side_effect=[{"content": "before"}, {"content": "after"}]))
    call = {**_call(), "name": "file_read"}
    assert (await execute_effect(executor, _state(), call, call["arguments"]))["content"] == "before"
    assert (await execute_effect(executor, _state(), call, call["arguments"]))["content"] == "after"
    assert not (data_dir() / "tool_effects.db").exists()


@pytest.mark.asyncio
async def test_concurrent_replay_has_one_dispatch():
    entered, release = asyncio.Event(), asyncio.Event()

    async def run(*args):
        entered.set()
        await release.wait()
        return {"content": "written"}

    executor = Mock(execute=AsyncMock(side_effect=run))
    call = _call()
    first = asyncio.create_task(execute_effect(executor, _state(), call, call["arguments"]))
    await asyncio.wait_for(entered.wait(), timeout=5)
    try:
        with pytest.raises(EffectUncertain, match="effects are unknown"):
            await execute_effect(executor, _state(), call, call["arguments"])
    finally:
        release.set()
        await first
    assert executor.execute.await_count == 1


@pytest.mark.asyncio
async def test_reservation_failure_prevents_dispatch(monkeypatch):
    monkeypatch.setattr(_EffectJournal, "begin", Mock(side_effect=sqlite3.OperationalError("locked")))
    executor = Mock(execute=AsyncMock())
    call = _call()
    with pytest.raises(EffectUncertain, match="dispatch was withheld"):
        await execute_effect(executor, _state(), call, call["arguments"])
    executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_result_commit_failure_never_reexecutes(monkeypatch):
    executor = Mock(execute=AsyncMock(return_value={"content": "written"}))
    call = _call()
    monkeypatch.setattr(_EffectJournal, "finish", Mock(side_effect=sqlite3.OperationalError("locked")))
    with pytest.raises(EffectUncertain, match="not committed"):
        await execute_effect(executor, _state(), call, call["arguments"])
    with pytest.raises(EffectUncertain, match="effects are unknown"):
        await execute_effect(executor, _state(), call, call["arguments"])
    assert executor.execute.await_count == 1


@pytest.mark.asyncio
async def test_dispatch_exception_keeps_unknown_effect_held():
    executor = Mock(execute=AsyncMock(side_effect=RuntimeError("connection lost after sending")))
    call = _call()
    with pytest.raises(EffectUncertain, match="effects are unknown"):
        await execute_effect(executor, _state(), call, call["arguments"])
    with pytest.raises(EffectUncertain, match="effects are unknown"):
        await execute_effect(executor, _state(), call, call["arguments"])
    assert executor.execute.await_count == 1


@pytest.mark.asyncio
async def test_real_registry_does_not_retry_mutator_or_hide_its_failure(monkeypatch):
    from kazma_core import retry
    from kazma_core.agent.tool_registry import LocalToolRegistry, _graph_hitl_gate_ctx

    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    monkeypatch.setattr(retry, "load_retry_config", lambda: {
        "max_attempts": 3, "min_wait": 0, "max_wait": 0,
    })
    registry = LocalToolRegistry(include_builtins=False)
    calls = []

    @registry.register(description="Fixture mutator", category="test")
    async def fixture_write():
        calls.append("sent")
        raise TimeoutError("lost acknowledgement after sending")

    token = _graph_hitl_gate_ctx.set(True)
    call = {"id": "write-1", "name": "fixture_write", "arguments": {}}
    try:
        with pytest.raises(EffectUncertain, match="effects are unknown"):
            await execute_effect(registry, _state(), call, {})
        with pytest.raises(EffectUncertain, match="recorded tool failure"):
            await execute_effect(registry, _state(), call, {})
    finally:
        _graph_hitl_gate_ctx.reset(token)
    assert calls == ["sent"]


@pytest.mark.asyncio
async def test_real_registry_pre_invocation_argument_error_can_be_corrected(monkeypatch):
    from kazma_core.agent.tool_registry import LocalToolRegistry, _graph_hitl_gate_ctx

    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    registry = LocalToolRegistry(include_builtins=False)
    calls = []

    @registry.register(description="Fixture mutator", category="test")
    async def fixture_write(value: str):
        calls.append(value)
        return "written"

    token = _graph_hitl_gate_ctx.set(True)
    try:
        call = {"id": "write-1", "name": "fixture_write", "arguments": {}}
        result = await execute_effect(registry, _state(), call, {})
        assert result["is_error"] and not result.get("effect_uncertain")
        assert calls == []
        call = {"id": "write-2", "name": "fixture_write", "arguments": {"value": "one"}}
        result = await execute_effect(registry, _state(), call, call["arguments"])
        assert not result["is_error"]
    finally:
        _graph_hitl_gate_ctx.reset(token)
    assert calls == ["one"]


@pytest.mark.asyncio
@pytest.mark.parametrize("leaf", ["write_file", "read_file"])
async def test_unified_mcp_failure_holds_mutators_but_reads_remain_correctable(monkeypatch, leaf):
    from kazma_core.agent.tool_registry import _hitl_approved_ctx
    from kazma_core.mcp import manager as mcp_module

    monkeypatch.setattr(mcp_module, "set_active_mcp_manager", lambda manager: None)
    manager = Mock()
    manager.is_mcp_tool.return_value = True
    manager.get_server_for_tool.return_value = "fixture"
    manager.get_server_trust.return_value = "untrusted"
    manager.execute_mcp_tool = AsyncMock(return_value={"content": "server failed", "is_error": True})
    executor = mcp_module.UnifiedToolExecutor(mcp=manager, rbac=Mock())
    call = {"id": "mcp-1", "name": f"mcp__fixture__{leaf}", "arguments": {}}
    token = _hitl_approved_ctx.set(True)
    try:
        if leaf == "write_file":
            with pytest.raises(EffectUncertain, match="effects are unknown"):
                await execute_effect(executor, _state(), call, {})
            with pytest.raises(EffectUncertain, match="recorded tool failure"):
                await execute_effect(executor, _state(), call, {})
        else:
            result = await execute_effect(executor, _state(), call, {})
            assert result["is_error"] and not result.get("effect_uncertain")
    finally:
        _hitl_approved_ctx.reset(token)
    manager.execute_mcp_tool.assert_awaited_once()


@pytest.mark.asyncio
async def test_changed_workspace_does_not_reuse_another_roots_receipt(monkeypatch, tmp_path):
    from kazma_core.workspace import binding

    monkeypatch.setattr(binding, "resolve_active_root", lambda: tmp_path / "one")
    executor = Mock(execute=AsyncMock(return_value={"content": "written"}))
    call = _call()
    await execute_effect(executor, _state(), call, call["arguments"])
    monkeypatch.setattr(binding, "resolve_active_root", lambda: tmp_path / "two")
    with pytest.raises(EffectUncertain, match="different request"):
        await execute_effect(executor, _state(), call, call["arguments"])
    assert executor.execute.await_count == 1


@pytest.mark.parametrize("completed", [False, True])
def test_real_process_death_preserves_receipt_and_never_repeats(tmp_path, completed):
    db, marker = tmp_path / "receipts.db", tmp_path / "effect.txt"
    script = """
import os, sys
from pathlib import Path
from kazma_core.agent.effect_journal import _EffectJournal
journal = _EffectJournal(sys.argv[1])
journal.begin('effect', 'request', 'thread', 'file_append')
Path(sys.argv[2]).write_text('one', encoding='utf-8')
if sys.argv[3] == 'True':
    journal.finish('effect', {'content': 'written'})
os._exit(75)
"""
    result = subprocess.run([sys.executable, "-c", script, str(db), str(marker), str(completed)],
                            env=os.environ.copy(), capture_output=True, timeout=30)
    assert result.returncode == 75, result.stderr.decode(errors="replace")
    journal = _EffectJournal(db)
    if completed:
        assert journal.begin("effect", "request", "thread", "file_append") == {"content": "written"}
    else:
        with pytest.raises(EffectUncertain, match="effects are unknown"):
            journal.begin("effect", "request", "thread", "file_append")
    assert marker.read_text() == "one"


@pytest.mark.asyncio
async def test_worker_ends_uncertain_turn_and_respond_does_not_synthesize(monkeypatch):
    from kazma_core.agent.graph_respond import respond_node
    from kazma_core.agent.graph_tool_worker import tool_worker_node

    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    monkeypatch.setattr(_EffectJournal, "finish", Mock(side_effect=sqlite3.OperationalError("locked")))
    state = initial_supervisor_state(thread_id="held-effect")
    state["messages"] = [{"role": "user", "content": "remember this fixture"}]
    state["tool_calls_pending"] = [{"id": "remember-1", "name": "memory_store",
                                    "arguments": {"subject": "fixture", "predicate": "is", "object": "test"}}]
    executor = Mock(execute=AsyncMock(return_value={"content": "stored"}))
    out = await tool_worker_node(state, tool_executor=executor, tracer=Mock(), hitl_config=None)
    assert out["turn_failed"] is True
    assert out["next_node"] == NodeName.RESPOND
    assert "reconciliation" in out["error_message"]
    llm = Mock(chat=AsyncMock(), chat_stream=AsyncMock())
    response = await respond_node({**state, **out}, llm=llm)
    llm.chat.assert_not_called()
    llm.chat_stream.assert_not_called()
    assert response["messages"][-1]["content"] == out["error_message"]
    assert "must not be repeated" in response["messages"][-1]["content"]


def test_receipts_require_full_durability(tmp_path):
    from contextlib import closing

    with closing(_EffectJournal(tmp_path / "effects.db")._connect()) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2


@pytest.mark.asyncio
async def test_cancelled_dispatch_holds_receipt():
    entered = asyncio.Event()

    async def run(*args):
        entered.set()
        await asyncio.Event().wait()

    executor = Mock(execute=AsyncMock(side_effect=run))
    call = _call()
    task = asyncio.create_task(execute_effect(executor, _state(), call, call["arguments"]))
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(EffectUncertain, match="effects are unknown"):
        await execute_effect(executor, _state(), call, call["arguments"])
    assert executor.execute.await_count == 1


def test_inspection_excludes_results_and_preserves_state(tmp_path):
    journal = _EffectJournal(tmp_path / "effects.db")
    assert journal.inspect("thread") == []
    assert not journal.path.exists()
    journal.begin("one", "request", "thread", "file_append")
    journal.finish("one", {"content": "private tool result"})
    journal.begin("two", "request", "other-thread", "file_append")
    rows = journal.inspect("thread")
    assert len(rows) == 1
    assert rows[0]["state"] == "completed"
    assert "private tool result" not in str(rows)
    assert journal.begin("one", "request", "thread", "file_append") == {"content": "private tool result"}


@pytest.mark.asyncio
async def test_mutator_timeout_surfaces_unknown_effect_and_prevents_replay(monkeypatch):
    from kazma_core.agent import graph_tool_worker
    from kazma_core.agent.graph_respond import respond_node

    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    monkeypatch.setattr(graph_tool_worker, "_resolve_tool_timeout", lambda: 1)
    state = initial_supervisor_state(thread_id="timed-out-effect")
    state["messages"] = [{"role": "user", "content": "remember this fixture"}]
    call = {"id": "remember-1", "name": "memory_store", "arguments": {"subject": "fixture"}}
    state["tool_calls_pending"] = [call]

    async def stalled(*args):
        await asyncio.Event().wait()

    executor = Mock(execute=AsyncMock(side_effect=stalled))
    out = await graph_tool_worker.tool_worker_node(
        state, tool_executor=executor, tracer=Mock(), hitl_config=None,
    )
    assert out["turn_failed"] and out["next_node"] == NodeName.RESPOND
    llm = Mock(chat=AsyncMock())
    final = await respond_node({**state, **out}, llm=llm)
    assert "effects are unknown" in final["messages"][-1]["content"]
    assert "must not be repeated" in final["messages"][-1]["content"]
    llm.chat.assert_not_called()
    with pytest.raises(EffectUncertain, match="effects are unknown"):
        await execute_effect(executor, state, call, call["arguments"])
    assert executor.execute.await_count == 1
