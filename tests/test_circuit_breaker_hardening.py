"""Tests for hardened tool-loop circuit breaker (typed outcomes + per-round credit)."""

from __future__ import annotations

from typing import Any

import pytest

from kazma_core.agent.graph_builder import tool_worker_node
from kazma_core.agent.state import NodeName, initial_supervisor_state
from kazma_core.agent.tool_loop_breaker import (
    ToolOutcome,
    classify_tool_result,
    update_breaker,
)
from kazma_core.swarm.task import WorkerCapabilities
from kazma_core.swarm.worker import InProcessWorker


class DummyTracer:
    def trace_tool_execution(self, *args, **kwargs):
        pass


class DummyToolExecutor:
    def __init__(self, execute_fn):
        self._execute_fn = execute_fn

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._execute_fn(name, arguments)


# ── Unit: update_breaker ────────────────────────────────────────────────


def test_parallel_hard_errors_credit_one_round_only() -> None:
    """Five hard failures in one batch → consecutive=1, not trip."""
    results = [
        {"content": "Error: connection refused", "is_error": True} for _ in range(5)
    ]
    state, stamped = update_breaker(0, results)
    assert state.consecutive_hard_rounds == 1
    assert state.tripped is False
    assert all(s.get("outcome") == ToolOutcome.HARD.value for s in stamped)


def test_three_hard_rounds_trip() -> None:
    hard = [{"content": "Error: boom", "is_error": True}]
    s1, _ = update_breaker(0, hard)
    s2, _ = update_breaker(s1.consecutive_hard_rounds, hard)
    s3, stamped = update_breaker(s2.consecutive_hard_rounds, hard)
    assert s1.consecutive_hard_rounds == 1
    assert s2.consecutive_hard_rounds == 2
    assert s3.consecutive_hard_rounds == 3
    assert s3.tripped is True
    assert "SYSTEM OVERRIDE" in stamped[0]["content"]


def test_policy_denials_do_not_trip() -> None:
    results = [
        {
            "content": "Access denied - path outside allowed directories: /home/x",
            "is_error": True,
        }
        for _ in range(5)
    ]
    state, stamped = update_breaker(0, results)
    assert all(s.get("outcome") == ToolOutcome.POLICY.value for s in stamped)
    assert state.consecutive_hard_rounds == 0
    assert state.tripped is False


def test_ok_resets_hard_streak() -> None:
    hard = [{"content": "Error: boom", "is_error": True}]
    s1, _ = update_breaker(0, hard)
    s2, _ = update_breaker(s1.consecutive_hard_rounds, hard)
    ok = [{"content": "hello", "is_error": False}]
    s3, _ = update_breaker(s2.consecutive_hard_rounds, ok)
    assert s2.consecutive_hard_rounds == 2
    assert s3.consecutive_hard_rounds == 0
    assert s3.tripped is False


def test_user_deny_is_not_hard() -> None:
    r = {"content": "Tool call denied by user.", "is_error": True}
    assert classify_tool_result(r) == ToolOutcome.USER_DENY
    state, _ = update_breaker(2, [r])
    assert state.consecutive_hard_rounds == 0


def test_empty_result_not_hard() -> None:
    r = {"content": "[]", "is_error": False}
    assert classify_tool_result(r) == ToolOutcome.EMPTY
    state, _ = update_breaker(2, [r])
    assert state.consecutive_hard_rounds == 0


def test_notimplemented_is_hard() -> None:
    r = {
        "name": "browser_navigate",
        "content": "Future exception was never retrieved\nNotImplementedError",
        "is_error": False,
        "duration_ms": 0,
    }
    assert classify_tool_result(r) == ToolOutcome.HARD


def test_browser_zero_ms_noop_is_hard() -> None:
    r = {
        "name": "browser_extract_text",
        "content": "",
        "is_error": False,
        "duration_ms": 0,
    }
    assert classify_tool_result(r) == ToolOutcome.HARD
    s1, _ = update_breaker(0, [r])
    s2, _ = update_breaker(s1.consecutive_hard_rounds, [r])
    s3, _ = update_breaker(s2.consecutive_hard_rounds, [r])
    assert s3.tripped is True


# ── Graph integration ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_graph_circuit_breaker_tripped_bypass() -> None:
    """If circuit_breaker_tripped is True, tool_worker_node must bypass execution."""
    state = initial_supervisor_state()
    state["circuit_breaker_tripped"] = True
    state["tool_calls_pending"] = [
        {"id": "call_1", "name": "web_search", "arguments": {"query": "test1"}},
        {"id": "call_2", "name": "other_tool", "arguments": {}},
    ]

    async def mock_execute(name, args):
        pytest.fail("Tool should NOT be executed when circuit breaker is tripped!")

    result_state = await tool_worker_node(
        state,
        tool_executor=DummyToolExecutor(mock_execute),
        tracer=DummyTracer(),
        hitl_config=None,
    )

    assert result_state["circuit_breaker_tripped"] is True
    assert len(result_state["tool_calls_done"]) == 2
    for tr in result_state["tool_calls_done"]:
        assert "SYSTEM OVERRIDE: Tool blocked" in tr["content"]
        assert tr["is_error"] is True
    assert result_state["next_node"] == NodeName.RESPOND


@pytest.mark.anyio
async def test_graph_parallel_hard_errors_do_not_instant_trip() -> None:
    """One batch of 3 hard errors credits +1 only — does not trip."""
    state = initial_supervisor_state()
    state["consecutive_tool_failures"] = 0
    state["tool_calls_pending"] = [
        {"id": "call_1", "name": "t1", "arguments": {}},
        {"id": "call_2", "name": "t2", "arguments": {}},
        {"id": "call_3", "name": "t3", "arguments": {}},
    ]

    async def mock_execute(name, args):
        return {"content": "Error: connection refused", "is_error": True}

    result_state = await tool_worker_node(
        state,
        tool_executor=DummyToolExecutor(mock_execute),
        tracer=DummyTracer(),
        hitl_config=None,
    )

    assert result_state["circuit_breaker_tripped"] is False
    assert result_state["consecutive_tool_failures"] == 1
    assert result_state["next_node"] == NodeName.SUPERVISOR


@pytest.mark.anyio
async def test_graph_trips_after_three_hard_rounds() -> None:
    """Starting at consecutive=2, one more hard round trips."""
    state = initial_supervisor_state()
    state["consecutive_tool_failures"] = 2
    state["tool_calls_pending"] = [
        {"id": "call_1", "name": "t1", "arguments": {}},
    ]

    async def mock_execute(name, args):
        return {"content": "Error: connection refused", "is_error": True}

    result_state = await tool_worker_node(
        state,
        tool_executor=DummyToolExecutor(mock_execute),
        tracer=DummyTracer(),
        hitl_config=None,
    )

    assert result_state["circuit_breaker_tripped"] is True
    assert result_state["consecutive_tool_failures"] == 3
    assert result_state["next_node"] == NodeName.RESPOND
    assert "SYSTEM OVERRIDE" in result_state["tool_calls_done"][0]["content"]


@pytest.mark.anyio
async def test_graph_mcp_policy_denials_do_not_trip() -> None:
    """MCP path-jail style isError must not trip the breaker."""
    state = initial_supervisor_state()
    state["consecutive_tool_failures"] = 0
    state["tool_calls_pending"] = [
        {"id": f"c{i}", "name": "mcp__filesystem__directory_tree", "arguments": {}}
        for i in range(5)
    ]

    async def mock_execute(name, args):
        return {
            "content": "Error: Access denied - path outside allowed directories: /repo",
            "is_error": True,
        }

    result_state = await tool_worker_node(
        state,
        tool_executor=DummyToolExecutor(mock_execute),
        tracer=DummyTracer(),
        hitl_config=None,
    )

    assert result_state["circuit_breaker_tripped"] is False
    assert result_state["consecutive_tool_failures"] == 0
    assert result_state["next_node"] == NodeName.SUPERVISOR


# ── Swarm integration ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_swarm_worker_circuit_breaker_hard_rounds() -> None:
    """InProcessWorker trips only after 3 hard rounds, not empty results."""
    worker = InProcessWorker(
        name="TestCircuitBreakerWorker",
        model="mock-model",
        capabilities=WorkerCapabilities(tools=["web_search"]),
    )

    class MockProvider:
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, tools=None, model=None):
            class MockResponse:
                def __init__(self, tool_calls, content=None):
                    self.content = content
                    self.tool_calls = tool_calls
                    self.usage = {"prompt_tokens": 1, "completion_tokens": 1}
                    self.cost_usd = 0.0

            class MockToolCall:
                def __init__(self, id, name):
                    self.id = id
                    self.name = name
                    self.arguments = {"query": "test"}

            self.calls += 1
            if self.calls <= 3:
                return MockResponse([MockToolCall(f"c{self.calls}", "web_search")])
            return MockResponse([], content="Done after circuit breaker.")

    import kazma_core.model_registry as mr

    class MockRegistry:
        def get_client(self, *args, **kwargs):
            return MockProvider()

        def get_client_by_provider(self, *args, **kwargs):
            return MockProvider()

        def get_model(self, *args, **kwargs):
            return MockProvider()

    old_registry = mr.get_model_registry
    mr.get_model_registry = lambda: MockRegistry()

    class MockToolRegistry:
        def get_tool_definitions(self):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                        },
                    },
                }
            ]

        async def execute(self, name, args):
            return {"content": "Error: upstream down", "is_error": True}

    import kazma_core.agent.tool_registry as tr

    old_tr_getter = tr.get_tool_registry
    tr.get_tool_registry = lambda: MockToolRegistry()

    try:
        result = await worker.dispatch("Find something online.")
        assert result["status"] == "success"
        assert "Done after circuit breaker" in result["output"]
    finally:
        mr.get_model_registry = old_registry
        tr.get_tool_registry = old_tr_getter
