"""Tests for the HITL (Human-in-the-Loop) approval UI backend.

Covers:
  1. _extract_interrupt_info — extracting tool name/args from PregelTask
  2. _get_pending_approvals — scanning checkpointed threads for interrupts
  3. GET /api/pending-approvals endpoint — HTTP-level behavior
"""

from __future__ import annotations

from typing import Any

import pytest
from kazma_ui.hitl_approval import _extract_interrupt_info, _get_pending_approvals

# ══════════════════════════════════════════════════════════════════════════
# Helpers: mock PregelTask, StateSnapshot, graph, checkpointer
# ══════════════════════════════════════════════════════════════════════════


class MockInterrupt:
    """Simulates a langgraph Interrupt with a value payload."""

    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value


class MockTask:
    """Simulates a langgraph PregelTask with interrupts."""

    def __init__(self, interrupts: list[Any] | None = None) -> None:
        self.interrupts = interrupts or []


class MockStateSnapshot:
    """Simulates a langgraph StateSnapshot."""

    def __init__(
        self,
        next_nodes: tuple[str, ...] = (),
        tasks: list[MockTask] | None = None,
    ) -> None:
        self.next = next_nodes
        self.tasks = tasks or []


class MockCheckpointer:
    """Simulates an AsyncSqliteSaver-backed checkpointer with a conn."""

    def __init__(self, thread_ids: list[str]) -> None:
        # Build a fake conn that returns distinct thread_ids
        self.conn = MockConn(thread_ids)


class MockConn:
    """Fake aiosqlite connection returning distinct thread_ids."""

    def __init__(self, thread_ids: list[str]) -> None:
        self._thread_ids = thread_ids

    async def execute(self, query: str, params: tuple = ()) -> MockCursor:
        return MockCursor(self._thread_ids)


class MockCursor:
    def __init__(self, thread_ids: list[str]) -> None:
        self._rows = [(tid,) for tid in thread_ids]

    async def fetchall(self) -> list[tuple]:
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


class MockGraph:
    """Simulates a compiled LangGraph with aget_state()."""

    def __init__(self, states: dict[str, MockStateSnapshot]) -> None:
        self._states = states

    async def aget_state(self, config: dict[str, Any]) -> MockStateSnapshot | None:
        thread_id = config.get("configurable", {}).get("thread_id", "")
        return self._states.get(thread_id)


# ══════════════════════════════════════════════════════════════════════════
# 1. _extract_interrupt_info
# ══════════════════════════════════════════════════════════════════════════


class TestExtractInterruptInfo:
    """Test extraction of tool name and arguments from PregelTask interrupts."""

    def test_extracts_hitl_approval_payload(self) -> None:
        """A task with a hitl_approval interrupt should yield tool_name and arguments."""
        task = MockTask(interrupts=[
            MockInterrupt({
                "type": "hitl_approval",
                "tool": "file_write",
                "args": {"path": "/tmp/test.txt", "content": "hello"},
                "message": "Agent wants to run: file_write(...)",
            })
        ])
        result = _extract_interrupt_info(task)
        assert result is not None
        assert result["tool_name"] == "file_write"
        assert result["arguments"]["path"] == "/tmp/test.txt"
        assert "file_write" in result["message"]

    def test_extracts_without_type_tag(self) -> None:
        """Interrupt payload without type but with tool/args keys is still recognised."""
        task = MockTask(interrupts=[
            MockInterrupt({"tool": "shell_exec", "args": {"command": "ls"}})
        ])
        result = _extract_interrupt_info(task)
        assert result is not None
        assert result["tool_name"] == "shell_exec"

    def test_returns_none_for_no_interrupts(self) -> None:
        """A task with no interrupts should return None."""
        task = MockTask(interrupts=[])
        assert _extract_interrupt_info(task) is None

    def test_returns_none_for_unrelated_interrupt(self) -> None:
        """A task with a non-HITL interrupt should return None."""
        task = MockTask(interrupts=[
            MockInterrupt({"type": "some_other_type", "data": 123})
        ])
        assert _extract_interrupt_info(task) is None


# ══════════════════════════════════════════════════════════════════════════
# 2. _get_pending_approvals
# ══════════════════════════════════════════════════════════════════════════


class TestGetPendingApprovals:
    """Test scanning checkpointed threads for interrupted HITL approvals."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_threads(self) -> None:
        """No threads in DB → empty list."""
        graph = MockGraph({})
        checkpointer = MockCheckpointer(thread_ids=[])
        result = await _get_pending_approvals(graph, checkpointer)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_interrupts(self) -> None:
        """Threads with completed state (no pending next) → empty list."""
        graph = MockGraph({
            "thread-1": MockStateSnapshot(next_nodes=(), tasks=[]),
        })
        checkpointer = MockCheckpointer(thread_ids=["thread-1"])
        result = await _get_pending_approvals(graph, checkpointer)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_pending_for_interrupted_thread(self) -> None:
        """A thread with next_node and an interrupt task → approval entry."""
        graph = MockGraph({
            "thread-1": MockStateSnapshot(
                next_nodes=("tool_worker",),
                tasks=[MockTask(interrupts=[
                    MockInterrupt({
                        "type": "hitl_approval",
                        "tool": "shell_exec",
                        "args": {"command": "rm -rf /"},
                        "message": "Dangerous command",
                    })
                ])],
            ),
        })
        checkpointer = MockCheckpointer(thread_ids=["thread-1"])
        result = await _get_pending_approvals(graph, checkpointer)
        assert len(result) == 1
        assert result[0]["thread_id"] == "thread-1"
        assert result[0]["tool_name"] == "shell_exec"
        assert result[0]["arguments"]["command"] == "rm -rf /"

    @pytest.mark.asyncio
    async def test_filters_completed_threads_with_tasks(self) -> None:
        """A thread with interrupt tasks but empty next_nodes is NOT pending."""
        graph = MockGraph({
            "thread-1": MockStateSnapshot(
                next_nodes=(),  # not interrupted — already resumed
                tasks=[MockTask(interrupts=[
                    MockInterrupt({"type": "hitl_approval", "tool": "file_write"})
                ])],
            ),
        })
        checkpointer = MockCheckpointer(thread_ids=["thread-1"])
        result = await _get_pending_approvals(graph, checkpointer)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_multiple_pending_threads(self) -> None:
        """Multiple interrupted threads all show up."""
        graph = MockGraph({
            "thread-1": MockStateSnapshot(
                next_nodes=("tool_worker",),
                tasks=[MockTask(interrupts=[
                    MockInterrupt({"type": "hitl_approval", "tool": "file_delete", "args": {}})
                ])],
            ),
            "thread-2": MockStateSnapshot(
                next_nodes=("tool_worker",),
                tasks=[MockTask(interrupts=[
                    MockInterrupt({"type": "hitl_approval", "tool": "shell_exec", "args": {}})
                ])],
            ),
            "thread-3": MockStateSnapshot(next_nodes=(), tasks=[]),  # not pending
        })
        checkpointer = MockCheckpointer(thread_ids=["thread-1", "thread-2", "thread-3"])
        result = await _get_pending_approvals(graph, checkpointer)
        tool_names = {r["tool_name"] for r in result}
        assert tool_names == {"file_delete", "shell_exec"}
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_returns_empty_when_graph_is_none(self) -> None:
        """Graph None → empty list (not crash)."""
        checkpointer = MockCheckpointer(thread_ids=["thread-1"])
        result = await _get_pending_approvals(None, checkpointer)
        assert result == []

    @pytest.mark.asyncio
    async def test_handles_state_lookup_failure_gracefully(self) -> None:
        """If aget_state raises, that thread is skipped but others still returned."""
        class FailingGraph(MockGraph):
            async def aget_state(self, config):
                tid = config["configurable"]["thread_id"]
                if tid == "bad-thread":
                    raise RuntimeError("DB error")
                return super().aget_state.__wrapped_get(self, config) if False else self._states.get(tid)

        graph = MockGraph({
            "good-thread": MockStateSnapshot(
                next_nodes=("tool_worker",),
                tasks=[MockTask(interrupts=[
                    MockInterrupt({"type": "hitl_approval", "tool": "file_write", "args": {}})
                ])],
            ),
        })
        # Override aget_state to raise for bad-thread
        original_aget = graph.aget_state

        async def failing_aget(config):
            tid = config["configurable"]["thread_id"]
            if tid == "bad-thread":
                raise RuntimeError("DB error")
            return await original_aget(config)

        graph.aget_state = failing_aget

        checkpointer = MockCheckpointer(thread_ids=["bad-thread", "good-thread"])
        result = await _get_pending_approvals(graph, checkpointer)
        assert len(result) == 1
        assert result[0]["thread_id"] == "good-thread"


# ══════════════════════════════════════════════════════════════════════════
# 3. HTTP endpoint via FastAPI TestClient
# ══════════════════════════════════════════════════════════════════════════


class TestPendingApprovalsEndpoint:
    """Test the GET /api/pending-approvals endpoint through FastAPI."""

    def test_endpoint_returns_empty_when_no_checkpointer(self) -> None:
        """When the graph/checkpointer are None, return 200 with empty pending."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from kazma_ui.hitl_approval import create_hitl_approval_router

        app = FastAPI()
        app.include_router(create_hitl_approval_router(graph=None, checkpointer=None))

        with TestClient(app) as client:
            resp = client.get("/api/pending-approvals")
            assert resp.status_code == 200
            data = resp.json()
            assert data["pending"] == []
            assert data["count"] == 0

    def test_endpoint_returns_pending_list(self) -> None:
        """With interrupted threads, endpoint returns them as JSON."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from kazma_ui.hitl_approval import create_hitl_approval_router

        graph = MockGraph({
            "t-1": MockStateSnapshot(
                next_nodes=("tool_worker",),
                tasks=[MockTask(interrupts=[
                    MockInterrupt({
                        "type": "hitl_approval",
                        "tool": "file_write",
                        "args": {"path": "/x"},
                        "message": "write file",
                    })
                ])],
            ),
        })
        checkpointer = MockCheckpointer(thread_ids=["t-1"])

        app = FastAPI()
        app.include_router(create_hitl_approval_router(graph=graph, checkpointer=checkpointer))

        with TestClient(app) as client:
            resp = client.get("/api/pending-approvals")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1
            assert data["pending"][0]["thread_id"] == "t-1"
            assert data["pending"][0]["tool_name"] == "file_write"
            assert data["pending"][0]["arguments"]["path"] == "/x"
