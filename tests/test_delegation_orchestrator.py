"""Tests for DelegationOrchestrator — task decomposition and coordination."""
from __future__ import annotations

import pytest
from kazma_core.cost_breaker import CostCircuitBreaker
from kazma_core.delegation.discovery import AgentDiscovery
from kazma_core.delegation.orchestrator import (
    DelegationOrchestrator,
    OrchestrationResult,
    SubTask,
    SubTaskStatus,
)
from kazma_core.delegation.protocol import (
    DelegationProtocol,
    RequestStatus,
)
from kazma_core.hub import AgentInfo as HubInfo
from kazma_core.hub import KazmaHub


@pytest.fixture
def hub():
    return KazmaHub()


@pytest.fixture
def discovery(hub):
    return AgentDiscovery(agent_id="orch-agent", hub=hub)


@pytest.fixture
def protocol():
    return DelegationProtocol(agent_id="orch-agent")


@pytest.fixture
def orchestrator(protocol, discovery):
    return DelegationOrchestrator(protocol=protocol, discovery=discovery)


class TestOrchestratorInit:
    """Test orchestrator initialization."""

    def test_default_init(self, orchestrator):
        assert orchestrator.protocol is not None
        assert orchestrator.discovery is not None
        assert orchestrator.tracer is None
        assert orchestrator.cost_breaker is None

    def test_with_cost_breaker(self, protocol, discovery):
        cb = CostCircuitBreaker(max_cost=1.0)
        orch = DelegationOrchestrator(protocol=protocol, discovery=discovery, cost_breaker=cb)
        assert orch.cost_breaker is cb

    def test_stats_empty(self, orchestrator):
        stats = orchestrator.get_stats()
        assert stats["active_orchestrations"] == 0
        assert stats["total_sub_tasks"] == 0


class TestDecomposeAndDelegate:
    """Test task decomposition and delegation."""

    async def test_basic_delegation(self, orchestrator, hub):
        # Register a capable agent
        await hub.register_agent(HubInfo(agent_id="worker-1", capabilities=["general"]))
        result = await orchestrator.decompose_and_delegate("Analyze data")
        assert isinstance(result, OrchestrationResult)
        assert result.status in (RequestStatus.COMPLETED, RequestStatus.FAILED)
        assert len(result.sub_tasks) > 0

    async def test_circuit_breaker_blocks(self, protocol, discovery):
        cb = CostCircuitBreaker(max_cost=0.01, silence_window_seconds=0)
        cb.record_cost(0.02)
        cb.should_halt()  # Trip it

        orch = DelegationOrchestrator(protocol=protocol, discovery=discovery, cost_breaker=cb)
        result = await orch.decompose_and_delegate("Blocked task")
        assert result.status == RequestStatus.REJECTED
        assert "Circuit breaker" in result.error

    async def test_result_has_cost(self, orchestrator):
        result = await orchestrator.decompose_and_delegate("Task")
        assert result.total_cost >= 0.0

    async def test_result_has_duration(self, orchestrator):
        result = await orchestrator.decompose_and_delegate("Task")
        assert result.duration_seconds >= 0.0

    async def test_result_has_synthesized_output(self, orchestrator):
        result = await orchestrator.decompose_and_delegate("Task")
        assert result.synthesized_output is not None
        assert "sub_task_count" in result.synthesized_output


class TestHandleTimeout:
    """Test timeout handling."""

    async def test_handle_timeout(self, orchestrator):
        # Should not raise
        await orchestrator.handle_timeout("nonexistent-request")


class TestHandleFailure:
    """Test failure handling."""

    async def test_handle_failure(self, orchestrator):
        # Should not raise
        await orchestrator.handle_failure("req-1", "Something went wrong")


class TestGetOrchestration:
    """Test orchestration lookup."""

    async def test_get_existing(self, orchestrator):
        result = await orchestrator.decompose_and_delegate("Task")
        lookup = orchestrator.get_orchestration(result.task_id)
        assert lookup is not None
        assert lookup.task_id == result.task_id

    async def test_get_nonexistent(self, orchestrator):
        lookup = orchestrator.get_orchestration("nonexistent")
        assert lookup is None


class TestSubTask:
    """Test SubTask dataclass."""

    def test_sub_task_defaults(self):
        st = SubTask(task_id="t1", description="Test", required_capabilities=["cap"])
        assert st.status == SubTaskStatus.PENDING
        assert st.assigned_agent == ""
        assert st.max_budget == 0.10
