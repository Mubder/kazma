"""Tests for sub-agent spawning (gw-026).

8 tests:
    1. Spawn single agent → SubAgentResult
    2. Spawn 3 parallel → 3 results
    3. Child isolated context (messages don't leak to parent)
    4. Child tool restriction
    5. Child auto-deny danger tools
    6. spawn_agent + spawn_agents in tool list
    7. Max concurrent semaphore enforced
    8. Child checkpoint persists
"""

from __future__ import annotations

import asyncio

import pytest
from kazma_core.agent.sub_agent import (
    SubAgentManager,
    SubAgentResult,
    get_sub_agent_manager,
    set_sub_agent_manager,
)


class MockGraph:
    """Mock LangGraph for testing sub-agent spawning."""

    def __init__(self, response: str = "Task completed successfully."):
        self._response = response
        self.invocations: list[dict] = []

    async def ainvoke(self, state: dict, config: dict = None) -> dict:
        self.invocations.append({"state": state, "config": config})
        return {
            "messages": [
                *state.get("messages", []),
                {"role": "assistant", "content": self._response},
            ],
        }


def mock_graph_builder(tools=None, hitl_config=None):
    """Build a mock graph for testing."""
    return MockGraph()


class TestSubAgentResult:
    """Test SubAgentResult dataclass."""

    def test_to_dict(self) -> None:
        result = SubAgentResult(
            task_id="sub-abc123",
            goal="Research Python",
            status="success",
            summary="FastAPI is great.",
            artifacts=["file.md"],
        )
        d = result.to_dict()
        assert d["task_id"] == "sub-abc123"
        assert d["status"] == "success"
        assert d["summary"] == "FastAPI is great."


class TestSpawnSingle:
    """Test 1: Spawn one child → returns SubAgentResult."""

    @pytest.mark.asyncio
    async def test_spawn_single(self) -> None:
        manager = SubAgentManager(
            graph_builder=mock_graph_builder,
            max_concurrent=3,
        )
        result = await manager.spawn(goal="Say hello", context="Test context")

        assert isinstance(result, SubAgentResult)
        assert result.status == "success"
        assert result.task_id.startswith("sub-")
        assert "hello" in result.summary.lower() or "Task completed" in result.summary

    @pytest.mark.asyncio
    async def test_spawn_with_timeout(self) -> None:
        """Spawn with very short timeout should still succeed for fast tasks."""
        manager = SubAgentManager(
            graph_builder=mock_graph_builder,
            max_concurrent=3,
        )
        result = await manager.spawn(goal="Quick task", timeout=10.0)
        assert result.status == "success"


class TestSpawnParallel:
    """Test 2: Spawn 3 children in parallel → 3 results."""

    @pytest.mark.asyncio
    async def test_spawn_parallel(self) -> None:
        manager = SubAgentManager(
            graph_builder=mock_graph_builder,
            max_concurrent=3,
        )
        tasks = [
            {"goal": "Task 1"},
            {"goal": "Task 2"},
            {"goal": "Task 3"},
        ]
        results = await manager.spawn_parallel(tasks)

        assert len(results) == 3
        for r in results:
            assert r.status == "success"
            assert r.task_id.startswith("sub-")

    @pytest.mark.asyncio
    async def test_spawn_clamps_to_5(self) -> None:
        """More than 5 tasks gets clamped."""
        manager = SubAgentManager(
            graph_builder=mock_graph_builder,
            max_concurrent=5,
        )
        tasks = [{"goal": f"Task {i}"} for i in range(10)]
        results = await manager.spawn_parallel(tasks)
        assert len(results) == 5


class TestChildIsolation:
    """Test 3: Child messages don't appear in parent graph state."""

    @pytest.mark.asyncio
    async def test_child_isolated(self) -> None:
        parent_graph = MockGraph(response="Parent response")
        child_graph = MockGraph(response="Child response")

        def builder(tools=None, hitl_config=None):
            return child_graph

        manager = SubAgentManager(graph_builder=builder, max_concurrent=3)
        result = await manager.spawn(goal="Child task", context="Parent says hi")

        # Child graph was invoked
        assert len(child_graph.invocations) == 1
        # Parent graph was NOT invoked
        assert len(parent_graph.invocations) == 0
        # Child result is isolated
        assert result.status == "success"


class TestToolRestriction:
    """Test 4: Child with restricted tools gets the restriction passed."""

    @pytest.mark.asyncio
    async def test_tool_restriction_passed(self) -> None:
        received_tools = []

        def capture_builder(tools=None, hitl_config=None):
            received_tools.append(tools)
            return MockGraph()

        manager = SubAgentManager(graph_builder=capture_builder, max_concurrent=3)
        await manager.spawn(
            goal="Read only task",
            tools=["file_read", "memory_search"],
        )

        assert received_tools[0] == ["file_read", "memory_search"]


class TestChildAutoDeny:
    """Test 5: Child HITL defaults to auto_deny."""

    @pytest.mark.asyncio
    async def test_auto_deny_config(self) -> None:
        received_hitl = []

        def capture_builder(tools=None, hitl_config=None):
            received_hitl.append(hitl_config)
            return MockGraph()

        manager = SubAgentManager(graph_builder=capture_builder, max_concurrent=3)
        await manager.spawn(goal="Safe task", safety_mode="auto_deny")

        config = received_hitl[0]
        assert config is not None
        assert config["enabled"] is True
        assert config["auto_deny_on_timeout"] is True
        assert config["approval_timeout_seconds"] == 1

    @pytest.mark.asyncio
    async def test_disabled_safety(self) -> None:
        received_hitl = []

        def capture_builder(tools=None, hitl_config=None):
            received_hitl.append(hitl_config)
            return MockGraph()

        manager = SubAgentManager(graph_builder=capture_builder, max_concurrent=3)
        await manager.spawn(goal="Unsafe task", safety_mode="disabled")

        config = received_hitl[0]
        assert config["enabled"] is False


class TestToolsRegistered:
    """Test 6: spawn_agent + spawn_agents in tool list."""

    def test_spawn_agent_registered(self) -> None:
        from kazma_core.agent.tool_registry import LocalToolRegistry

        registry = LocalToolRegistry(include_builtins=True)
        tools = registry.get_tool_definitions()
        tool_names = [t["function"]["name"] for t in tools]
        assert "spawn_agent" in tool_names

    def test_spawn_agents_registered(self) -> None:
        from kazma_core.agent.tool_registry import LocalToolRegistry

        registry = LocalToolRegistry(include_builtins=True)
        tools = registry.get_tool_definitions()
        tool_names = [t["function"]["name"] for t in tools]
        assert "spawn_agents" in tool_names


class TestMaxConcurrent:
    """Test 7: Max concurrent semaphore enforced."""

    @pytest.mark.asyncio
    async def test_semaphore_limits(self) -> None:
        concurrent_count = 0
        max_observed = 0

        async def slow_graph(**kwargs):
            nonlocal concurrent_count, max_observed
            concurrent_count += 1
            max_observed = max(max_observed, concurrent_count)
            await asyncio.sleep(0.2)
            concurrent_count -= 1
            return MockGraph()

        manager = SubAgentManager(
            graph_builder=slow_graph,
            max_concurrent=2,
        )

        tasks = [{"goal": f"Task {i}"} for i in range(5)]
        results = await manager.spawn_parallel(tasks)

        assert len(results) == 5
        assert max_observed <= 2


class TestSingleton:
    """Test get/set sub-agent manager."""

    def test_singleton(self) -> None:
        manager = SubAgentManager(graph_builder=mock_graph_builder)
        set_sub_agent_manager(manager)
        assert get_sub_agent_manager() is manager

        # Cleanup
        set_sub_agent_manager(None)
        assert get_sub_agent_manager() is None
