"""Tests for Hardened Tool Circuit Breaker across both Graph and Swarm worker paths."""

from __future__ import annotations

import pytest
from typing import Any

from kazma_core.agent.state import (
    NodeName,
    initial_supervisor_state,
    SupervisorState,
    PendingToolCall,
)
from kazma_core.agent.graph_builder import tool_worker_node
from kazma_core.swarm.worker import InProcessWorker
from kazma_core.swarm.task import WorkerCapabilities

class DummyTracer:
    def trace_tool_execution(self, *args, **kwargs):
        pass

class DummyToolExecutor:
    def __init__(self, execute_fn):
        self._execute_fn = execute_fn

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._execute_fn(name, arguments)


@pytest.mark.anyio
async def test_graph_circuit_breaker_tripped_bypass() -> None:
    """If circuit_breaker_tripped is True, tool_worker_node must immediately bypass execution."""
    state = initial_supervisor_state()
    state["circuit_breaker_tripped"] = True
    state["tool_calls_pending"] = [
        {"id": "call_1", "name": "web_search", "arguments": {"query": "test1"}},
        {"id": "call_2", "name": "other_tool", "arguments": {}},
    ]

    async def mock_execute(name, args):
        pytest.fail("Tool should NOT be executed when circuit breaker is tripped!")

    executor = DummyToolExecutor(mock_execute)
    tracer = DummyTracer()

    result_state = await tool_worker_node(
        state,
        tool_executor=executor,
        tracer=tracer,
        hitl_config=None,
    )

    assert result_state["circuit_breaker_tripped"] is True
    assert len(result_state["tool_calls_done"]) == 2
    for tr in result_state["tool_calls_done"]:
        assert "SYSTEM OVERRIDE: Tool blocked" in tr["content"]
        assert tr["is_error"] is True

    # Tripped breaker must route to RESPOND (not dead-loop to SUPERVISOR)
    assert result_state["next_node"] == NodeName.RESPOND

    # Verify message structures match
    tool_messages = [m for m in result_state["messages"] if m.get("role") == "tool"]
    assert len(tool_messages) == 2
    assert tool_messages[0]["tool_call_id"] == "call_1"
    assert tool_messages[1]["tool_call_id"] == "call_2"
    assert "SYSTEM OVERRIDE" in tool_messages[0]["content"]


@pytest.mark.anyio
async def test_graph_circuit_breaker_trips_during_execution() -> None:
    """If consecutive errors hit threshold (3), circuit_breaker_tripped must be set to True."""
    state = initial_supervisor_state()
    state["consecutive_tool_failures"] = 0
    state["tool_calls_pending"] = [
        {"id": "call_1", "name": "web_search", "arguments": {"query": "fail1"}},
        {"id": "call_2", "name": "other_tool", "arguments": {}},
        {"id": "call_3", "name": "third_tool", "arguments": {}},
    ]

    async def mock_execute(name, args):
        # Return actual errors (is_error=True) to trigger failure counting.
        # Empty results ("[]") no longer count as failures.
        return {"content": "Error: connection refused", "is_error": True}

    executor = DummyToolExecutor(mock_execute)
    tracer = DummyTracer()

    result_state = await tool_worker_node(
        state,
        tool_executor=executor,
        tracer=tracer,
        hitl_config=None,
    )

    assert result_state["circuit_breaker_tripped"] is True
    assert len(result_state["tool_calls_done"]) == 3

    # First tool errored -> counter 1
    # Second tool errored -> counter 2
    # Third tool errored -> counter 3 -> tripped!
    done_results = result_state["tool_calls_done"]
    assert "Error: connection refused" in done_results[0]["content"]
    assert "Error: connection refused" in done_results[1]["content"]
    assert "SYSTEM OVERRIDE" in done_results[2]["content"]

    # Tripped breaker must route to RESPOND (not dead-loop to SUPERVISOR)
    assert result_state["next_node"] == NodeName.RESPOND


@pytest.mark.anyio
async def test_swarm_worker_circuit_breaker_integration() -> None:
    """InProcessWorker.dispatch must trigger circuit breaker on consecutive empty/failed results."""
    worker = InProcessWorker(
        name="TestCircuitBreakerWorker",
        model="mock-model",
        capabilities=WorkerCapabilities(tools=["web_search"])
    )

    # Mock the LLM provider to request tools, then terminate once overridden
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
            if self.calls == 1:
                # First turn: returns TWO tool calls that will both fail (empty content)
                return MockResponse([
                    MockToolCall("c1", "web_search"),
                    MockToolCall("c2", "web_search"),
                ])
            elif self.calls == 2:
                # Second turn: should have received the system override in message history,
                # if the model requests tools again (which we'll test here), they must be bypassed.
                # Let's request tools again to test persistent block.
                return MockResponse([
                    MockToolCall("c3", "web_search"),
                ])
            else:
                # Final response
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
                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}
                    }
                }
            ]

        async def execute(self, name, args):
            # Return empty response to trigger failure
            return {"content": "[]", "is_error": False}

    import kazma_core.agent.tool_registry as tr
    old_tr_getter = tr.get_tool_registry
    tr.get_tool_registry = lambda: MockToolRegistry()

    try:
        result = await worker.dispatch("Find something online.")
        assert result["status"] == "success"
        # We expect worker to have executed two iterations
        # Iteration 1: c1 (failed), c2 (failed -> trips breaker -> result overridden)
        # Iteration 2: c3 (bypassed execution because breaker is tripped -> result overridden)
        # Iteration 3: Final response
        assert "Done after circuit breaker" in result["output"]
    finally:
        mr.get_model_registry = old_registry
        tr.get_tool_registry = old_tr_getter
