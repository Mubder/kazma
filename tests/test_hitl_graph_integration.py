"""HITL graph integration tests — verify interrupt() + resume end-to-end.

These tests build a minimal LangGraph wrapping the real tool_worker_node
with a real AsyncSqliteSaver checkpointer, feed it a danger tool call,
and verify:
    1. The graph pauses at interrupt() for danger tools
    2. Resume with approved=True executes the tool
    3. Resume with approved=False returns a denied result
    4. Safe tools never trigger interrupt()
    5. aget_state() surfaces the interrupt payload (for /api/pending-approvals)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, TypedDict

import aiosqlite
import pytest
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

# ── Minimal state schema matching SupervisorState subset ────────────────


class HitlState(TypedDict, total=False):
    """Minimal state for the HITL test graph."""
    messages: list[dict[str, Any]]
    tool_calls_pending: list[dict[str, Any]]
    tool_calls_done: list[dict[str, Any]]
    tool_results: dict[str, Any]
    next_node: str


# ── Mock tool executor ──────────────────────────────────────────────────


class MockToolExecutor:
    """Mock that records calls and returns canned results."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    async def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        # Strip the private _hitl_approved flag
        args.pop("_hitl_approved", None)
        self.executed.append((name, args))
        return {"content": f"Executed {name} successfully", "is_error": False}


class MockTracer:
    """No-op tracer."""
    def trace_tool_execution(self, **kwargs: Any) -> None:
        pass


# ── Graph factory ───────────────────────────────────────────────────────


async def _build_test_graph(
    hitl_config: dict[str, Any] | None,
    tmp_path: Path,
) -> Any:
    """Build a minimal graph that wraps tool_worker_node with a checkpointer."""
    from kazma_core.agent.graph_builder import tool_worker_node

    executor = MockToolExecutor()
    tracer = MockTracer()

    async def worker(state: HitlState) -> dict[str, Any]:
        return await tool_worker_node(
            state, tool_executor=executor, tracer=tracer, hitl_config=hitl_config
        )

    builder = StateGraph(HitlState)
    builder.add_node("worker", worker)
    builder.set_entry_point("worker")
    # After the worker, route to END (the real graph loops to supervisor,
    # but for HITL testing we just need the worker node).
    builder.add_edge("worker", END)

    db_path = str(tmp_path / "hitl_test_checkpoints.db")
    conn = await aiosqlite.connect(db_path)
    await conn.execute("PRAGMA journal_mode=WAL")
    checkpointer = AsyncSqliteSaver(conn)
    await checkpointer.setup()

    graph = builder.compile(checkpointer=checkpointer)
    return graph, executor, conn


# ══════════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════════


class TestHitlGraphInterrupt:
    """Verify the graph interrupt() mechanism works end-to-end."""

    @pytest.mark.asyncio
    async def test_danger_tool_triggers_interrupt(self, tmp_path: Path) -> None:
        """A danger tool (shell_exec) pauses the graph at interrupt()."""
        hitl_config = {
            "enabled": True,
            "require_approval_for": {"shell_exec", "file_write", "file_delete"},
        }
        graph, executor, conn = await _build_test_graph(hitl_config, tmp_path)

        config = {"configurable": {"thread_id": "test-interrupt-1"}}
        initial_state: HitlState = {
            "messages": [{"role": "user", "content": "run a command"}],
            "tool_calls_pending": [
                {"id": "tc1", "name": "shell_exec", "arguments": {"command": "echo hi"}}
            ],
        }

        try:
            # This should pause at interrupt() and return a partial state.
            result = await graph.ainvoke(initial_state, config)
            # When interrupted, the return value contains the interrupt payload.
            # The tool should NOT have been executed.
            assert len(executor.executed) == 0, "Danger tool ran before approval!"

            # Verify the graph state shows a pending interrupt.
            snapshot = await graph.aget_state(config)
            assert snapshot.next is not None, "Graph should be paused (next non-empty)"

            # Find the interrupt payload in the tasks.
            found_interrupt = False
            for task in snapshot.tasks:
                for intr in task.interrupts:
                    payload = intr.value
                    if isinstance(payload, dict) and payload.get("type") == "hitl_approval":
                        found_interrupt = True
                        assert payload["tool"] == "shell_exec"
                        assert payload["args"]["command"] == "echo hi"
            assert found_interrupt, "No hitl_approval interrupt found in paused state"
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_approve_resumes_and_executes(self, tmp_path: Path) -> None:
        """Resume with approved=True → tool executes."""
        hitl_config = {
            "enabled": True,
            "require_approval_for": {"shell_exec"},
        }
        graph, executor, conn = await _build_test_graph(hitl_config, tmp_path)

        config = {"configurable": {"thread_id": "test-approve-1"}}
        initial_state: HitlState = {
            "messages": [{"role": "user", "content": "run a command"}],
            "tool_calls_pending": [
                {"id": "tc1", "name": "shell_exec", "arguments": {"command": "ls"}}
            ],
        }

        try:
            # First invoke pauses at interrupt.
            await graph.ainvoke(initial_state, config)
            assert len(executor.executed) == 0

            # Resume with approval.
            await graph.ainvoke(
                Command(resume={"approved": True, "reason": "user approved"}),
                config,
            )

            # The tool should now have been executed.
            assert len(executor.executed) == 1
            assert executor.executed[0][0] == "shell_exec"

            # Graph should be complete (no pending interrupts).
            snapshot = await graph.aget_state(config)
            assert not snapshot.next, "Graph should be complete after resume"
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_deny_does_not_execute(self, tmp_path: Path) -> None:
        """Resume with approved=False → tool NOT executed, denied result returned."""
        hitl_config = {
            "enabled": True,
            "require_approval_for": {"file_write"},
        }
        graph, executor, conn = await _build_test_graph(hitl_config, tmp_path)

        config = {"configurable": {"thread_id": "test-deny-1"}}
        initial_state: HitlState = {
            "messages": [{"role": "user", "content": "write a file"}],
            "tool_calls_pending": [
                {"id": "tc1", "name": "file_write", "arguments": {"path": "/tmp/x", "content": "data"}}
            ],
        }

        try:
            # Pause at interrupt.
            await graph.ainvoke(initial_state, config)
            assert len(executor.executed) == 0

            # Resume with denial.
            result = await graph.ainvoke(
                Command(resume={"approved": False, "reason": "user denied"}),
                config,
            )

            # Tool should NOT have been executed.
            assert len(executor.executed) == 0, "Denied tool should not execute"

            # The state should contain a denied tool result.
            done = result.get("tool_calls_done", [])
            assert len(done) == 1
            assert done[0]["is_error"] is True
            assert "denied" in done[0]["content"].lower()
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_safe_tool_never_interrupts(self, tmp_path: Path) -> None:
        """A safe tool (file_read) runs immediately without interrupt."""
        hitl_config = {
            "enabled": True,
            "require_approval_for": {"shell_exec"},
        }
        graph, executor, conn = await _build_test_graph(hitl_config, tmp_path)

        config = {"configurable": {"thread_id": "test-safe-1"}}
        initial_state: HitlState = {
            "messages": [{"role": "user", "content": "read a file"}],
            "tool_calls_pending": [
                {"id": "tc1", "name": "file_read", "arguments": {"path": "/tmp/test"}}
            ],
        }

        try:
            result = await graph.ainvoke(initial_state, config)

            # Safe tool should execute immediately (no interrupt).
            assert len(executor.executed) == 1
            assert executor.executed[0][0] == "file_read"

            # Graph should be complete.
            snapshot = await graph.aget_state(config)
            assert not snapshot.next
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_no_hitl_config_allows_all(self, tmp_path: Path) -> None:
        """With hitl_config=None, danger tools run without interrupt."""
        graph, executor, conn = await _build_test_graph(None, tmp_path)

        config = {"configurable": {"thread_id": "test-no-hitl-1"}}
        initial_state: HitlState = {
            "messages": [{"role": "user", "content": "run a command"}],
            "tool_calls_pending": [
                {"id": "tc1", "name": "shell_exec", "arguments": {"command": "rm -rf /"}}
            ],
        }

        try:
            await graph.ainvoke(initial_state, config)
            # Without hitl_config, the danger tool runs immediately.
            assert len(executor.executed) == 1
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_interrupt_survives_new_connection(self, tmp_path: Path) -> None:
        """Paused graph state persists in the checkpointer (survives restart)."""
        hitl_config = {
            "enabled": True,
            "require_approval_for": {"shell_exec"},
        }

        db_path = str(tmp_path / "persist_test.db")

        # Phase 1: build graph, invoke, pause at interrupt, close connection.
        from kazma_core.agent.graph_builder import tool_worker_node

        executor1 = MockToolExecutor()
        conn1 = await aiosqlite.connect(db_path)
        await conn1.execute("PRAGMA journal_mode=WAL")
        checkpointer1 = AsyncSqliteSaver(conn1)
        await checkpointer1.setup()

        async def worker1(state: HitlState) -> dict[str, Any]:
            return await tool_worker_node(
                state, tool_executor=executor1, tracer=MockTracer(), hitl_config=hitl_config
            )

        builder1 = StateGraph(HitlState)
        builder1.add_node("worker", worker1)
        builder1.set_entry_point("worker")
        builder1.add_edge("worker", END)
        graph1 = builder1.compile(checkpointer=checkpointer1)

        config = {"configurable": {"thread_id": "persist-1"}}
        await graph1.ainvoke(
            {"messages": [{"role": "user", "content": "test"}],
             "tool_calls_pending": [{"id": "tc1", "name": "shell_exec", "arguments": {}}]},
            config,
        )
        await conn1.close()

        # Phase 2: reconnect to the same DB, resume the paused graph.
        executor2 = MockToolExecutor()
        conn2 = await aiosqlite.connect(db_path)
        checkpointer2 = AsyncSqliteSaver(conn2)
        await checkpointer2.setup()

        async def worker2(state: HitlState) -> dict[str, Any]:
            return await tool_worker_node(
                state, tool_executor=executor2, tracer=MockTracer(), hitl_config=hitl_config
            )

        builder2 = StateGraph(HitlState)
        builder2.add_node("worker", worker2)
        builder2.set_entry_point("worker")
        builder2.add_edge("worker", END)
        graph2 = builder2.compile(checkpointer=checkpointer2)

        # The paused state should still be there.
        snapshot = await graph2.aget_state(config)
        assert snapshot.next is not None, "Paused state should persist across connections"

        # Resume with approval.
        await graph2.ainvoke(Command(resume={"approved": True}), config)
        assert len(executor2.executed) == 1, "Tool should execute after resume"
        await conn2.close()
