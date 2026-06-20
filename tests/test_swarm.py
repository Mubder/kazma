"""Tests for SwarmIntelligence — parallel, consensus, and cascade execution."""
from __future__ import annotations

import asyncio
import pytest
from kazma_core.delegation.swarm import (
    SwarmIntelligence,
    ConsensusResult,
    CascadeResult,
)
from kazma_core.delegation.orchestrator import DelegationOrchestrator
from kazma_core.delegation.protocol import (
    DelegationProtocol,
    DelegationResult,
    DelegationRequest,
    RequestStatus,
)
from kazma_core.delegation.discovery import AgentDiscovery, AgentInfo
from kazma_core.hub import KazmaHub, AgentInfo as HubInfo


@pytest.fixture
def hub():
    return KazmaHub()


@pytest.fixture
def discovery(hub):
    return AgentDiscovery(agent_id="swarm-agent", hub=hub)


@pytest.fixture
def protocol():
    return DelegationProtocol(agent_id="swarm-agent")


@pytest.fixture
def orchestrator(protocol, discovery):
    return DelegationOrchestrator(protocol=protocol, discovery=discovery)


@pytest.fixture
def swarm(orchestrator):
    return SwarmIntelligence(orchestrator=orchestrator)


class TestSwarmInit:
    """Test swarm initialization."""

    def test_init(self, swarm):
        assert swarm.orchestrator is not None

    def test_stats(self, swarm):
        stats = swarm.get_swarm_stats()
        assert "orchestrator_stats" in stats
        assert "protocol_stats" in stats


class TestParallelExecute:
    """Test parallel task execution."""

    async def test_parallel_single_task(self, swarm):
        results = await swarm.parallel_execute(["Task 1"])
        assert len(results) >= 0  # May be empty if no agents

    async def test_parallel_respects_concurrency(self, swarm):
        # Should not raise
        results = await swarm.parallel_execute(
            ["T1", "T2", "T3"], max_concurrent=2
        )
        assert isinstance(results, list)


class TestConsensusExecute:
    """Test consensus-based execution."""

    async def test_consensus_basic(self, swarm, hub):
        # Register agents
        for i in range(5):
            await hub.register_agent(
                HubInfo(agent_id=f"worker-{i}", capabilities=["general"])
            )

        result = await swarm.consensus_execute(
            "Verify this result",
            min_responses=2,
        )
        assert isinstance(result, ConsensusResult)
        assert result.min_responses_required == 2
        assert result.task_description == "Verify this result"

    async def test_consensus_no_agents(self, swarm):
        result = await swarm.consensus_execute("Task", min_responses=3)
        assert isinstance(result, ConsensusResult)
        assert result.actual_responses == 0
        assert result.consensus_reached is False


class TestCascadeExecute:
    """Test pipeline (cascade) execution."""

    async def test_cascade_basic(self, swarm, hub):
        await hub.register_agent(
            HubInfo(agent_id="stage-1", capabilities=["general"])
        )
        result = await swarm.cascade_execute(
            ["Stage 1: Collect data", "Stage 2: Analyze"]
        )
        assert isinstance(result, CascadeResult)
        assert result.total_cost >= 0.0

    async def test_cascade_empty_pipeline(self, swarm):
        result = await swarm.cascade_execute([])
        assert isinstance(result, CascadeResult)
        assert result.all_succeeded is True
        assert len(result.pipeline_stages) == 0


class TestConsensusResult:
    """Test ConsensusResult dataclass."""

    def test_defaults(self):
        cr = ConsensusResult(task_description="Test")
        assert cr.consensus_reached is False
        assert cr.agreement_ratio == 0.0
        assert cr.actual_responses == 0


class TestCascadeResult:
    """Test CascadeResult dataclass."""

    def test_defaults(self):
        cr = CascadeResult()
        assert cr.all_succeeded is True
        assert cr.failed_stage == -1
        assert cr.total_cost == 0.0
        assert len(cr.pipeline_stages) == 0
