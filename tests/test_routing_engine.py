"""Tests for the Polymorphic Routing Engine.

Verifies:
1. BaseRouter interface and subclassing.
2. CapabilityRouterWrapper keyword-overlap and graceful error handling.
3. DialectRouterWrapper detecting dialects and prioritizing specialists.
4. SemanticRouterWrapper handling fallback and routing gracefully.
5. RoutingEngine fallback cascading and cooperative chaining.
"""

from __future__ import annotations

import pytest
from typing import Any

from kazma_core.swarm.task import SwarmTask
from kazma_core.routing_engine import (
    BaseRouter,
    CapabilityRouterWrapper,
    DialectRouterWrapper,
    SemanticRouterWrapper,
    RoutingEngine,
)


class DummyRouter(BaseRouter):
    """Simple test router that returns a fixed list of workers."""

    def __init__(self, workers: list[str]) -> None:
        self.workers = workers

    async def route(
        self,
        task: SwarmTask,
        available_workers: list[dict[str, Any]],
    ) -> list[str]:
        return self.workers


class FailingRouter(BaseRouter):
    """Test router that raises an exception."""

    async def route(
        self,
        task: SwarmTask,
        available_workers: list[dict[str, Any]],
    ) -> list[str]:
        raise ValueError("Simulated router failure")


@pytest.mark.anyio
async def test_capability_router_wrapper() -> None:
    """Test keyword-overlap routing via CapabilityRouterWrapper."""
    from kazma_core.swarm.task import WorkerCapabilities

    # Create a wrapper. It uses the legacy CapabilityRouter by default.
    wrapper = CapabilityRouterWrapper()

    task = SwarmTask(
        id="task_coding",
        prompt="Write a python function to parse a JSON string",
        workers=["auto"],
    )

    workers = [
        {
            "name": "PythonExpert",
            "capabilities": WorkerCapabilities(expertise=["python", "json"], role="Developer"),
        },
        {
            "name": "Writer",
            "capabilities": WorkerCapabilities(expertise=["copywriting", "editing"], role="Author"),
        },
    ]

    routed = await wrapper.route(task, workers)
    assert "PythonExpert" in routed
    assert "Writer" not in routed


@pytest.mark.anyio
async def test_dialect_router_wrapper_kuwaiti() -> None:
    """Test DialectRouterWrapper prioritizing Kuwaiti/Gulf workers."""
    wrapper = DialectRouterWrapper()

    # Create task with Kuwaiti dialect keywords (e.g. 'شلونك', 'شنو')
    task = SwarmTask(
        id="task_kuwaiti",
        prompt="شلونك يا خوي؟ شنو تبي نسوي اليوم؟",
    )

    workers = [
        {"name": "KuwaitiSpecialist", "expertise": ["Kuwaiti dialect", "Gulf culture"], "role": "Kuwait Advisor"},
        {"name": "StandardTranslator", "expertise": ["Standard Arabic", "MSA"], "role": "Translator"},
    ]

    routed = await wrapper.route(task, workers)
    assert len(routed) > 0
    assert routed[0] == "KuwaitiSpecialist"


@pytest.mark.anyio
async def test_dialect_router_wrapper_msa() -> None:
    """Test DialectRouterWrapper prioritizing MSA/Standard Arabic workers."""
    wrapper = DialectRouterWrapper()

    task = SwarmTask(
        id="task_msa",
        prompt="كيف حالك يا أخي العزيز؟ ماذا تريد أن نفعل اليوم؟",
    )

    workers = [
        {"name": "KuwaitiSpecialist", "expertise": ["Kuwaiti dialect", "Gulf culture"], "role": "Kuwait Advisor"},
        {"name": "StandardTranslator", "expertise": ["Standard Arabic", "MSA"], "role": "Translator"},
    ]

    routed = await wrapper.route(task, workers)
    assert len(routed) > 0
    assert routed[0] == "StandardTranslator"


@pytest.mark.anyio
async def test_semantic_router_wrapper_graceful_fallback() -> None:
    """Test SemanticRouterWrapper fallback when sentence-transformers or embeddings are unavailable."""
    wrapper = SemanticRouterWrapper()
    
    # Even if ChromaDB is not available or initialized, the wrapper must not crash.
    # It should return an empty list or fall back gracefully.
    task = SwarmTask(
        id="task_sem",
        prompt="Analyze this financial report for risk assessment",
    )
    workers = [
        {"name": "FinanceBot", "expertise": ["finance", "risk"], "role": "Analyst"},
    ]

    routed = await wrapper.route(task, workers)
    assert isinstance(routed, list)


@pytest.mark.anyio
async def test_routing_engine_fallback_mode() -> None:
    """Test RoutingEngine cascading in fallback mode."""
    # Sequence of routers: Failing -> Empty/No-match -> Success
    r1 = FailingRouter()
    r2 = DummyRouter([])
    r3 = DummyRouter(["Expert1"])
    r4 = DummyRouter(["Expert2"])

    engine = RoutingEngine(routers=[r1, r2, r3, r4], mode="fallback")

    task = SwarmTask(id="task1", prompt="test prompt")
    workers = [{"name": "Expert1"}, {"name": "Expert2"}]

    routed = await engine.route(task, workers)
    # Falling back cascades past Failing and Empty, stopping at first success (r3)
    assert routed == ["Expert1"]


@pytest.mark.anyio
async def test_routing_engine_chain_mode() -> None:
    """Test RoutingEngine cooperative narrowing in chain mode."""
    # First router returns 3 options
    r1 = DummyRouter(["Expert1", "Expert2", "Generalist"])
    # Second router filters to 2 options (intersection happens)
    r2 = DummyRouter(["Expert2", "Generalist", "Other"])

    engine = RoutingEngine(routers=[r1, r2], mode="chain")

    task = SwarmTask(id="task2", prompt="test prompt")
    workers = [
        {"name": "Expert1"},
        {"name": "Expert2"},
        {"name": "Generalist"},
        {"name": "Other"},
    ]

    routed = await engine.route(task, workers)
    # The chain should narrow down to the intersection of r1 and r2 matches
    assert set(routed) == {"Expert2", "Generalist"}


@pytest.mark.anyio
async def test_routing_engine_all_failed_fallback() -> None:
    """Test RoutingEngine returning all available workers if all strategies fail."""
    r1 = FailingRouter()
    r2 = DummyRouter([])

    engine = RoutingEngine(routers=[r1, r2], mode="fallback")

    task = SwarmTask(id="task3", prompt="test prompt")
    workers = [{"name": "WorkerA"}, {"name": "WorkerB"}]

    routed = await engine.route(task, workers)
    # Ultimate fallback safety: returns all workers
    assert set(routed) == {"WorkerA", "WorkerB"}
