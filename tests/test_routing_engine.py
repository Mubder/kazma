"""Tests for the Unified Routing Engine.

Verifies:
1. UnifiedRouter initialization.
2. Keyword matching fallback mechanism.
3. Dialect/Language detection routing.
4. Semantic fallback integration.
"""

from __future__ import annotations

import pytest
from typing import Any

from kazma_core.swarm.task import SwarmTask
from kazma_core.routing_engine import UnifiedRouter, NoCapableWorkersError
from kazma_core.swarm.task import WorkerCapabilities

from tests._no_models import finish_turn_memory, keep_models_out


@pytest.fixture(autouse=True)
def _keywords_and_dialects_only(monkeypatch):
    """These tests are the keyword and dialect strategies: no embedder (the
    real one is a 2 GB model; semantic routing has its own tests,
    tests/test_swarm_semantic_routing.py)."""
    import kazma_core.swarm.semantic_router as semantic_router

    monkeypatch.setattr(semantic_router, "_kazma_embedder", lambda: None)


@pytest.fixture(autouse=True)
def _no_models(monkeypatch):
    """No real embedder or model behind this file's memory and swarm paths
    (tests/_no_models.py; tests/_egress_guard.py refuses the real ones)."""
    keep_models_out(monkeypatch, llm=False)
    yield
    finish_turn_memory()  # a turn's memory write ends inside its test


@pytest.mark.anyio
async def test_unified_router_keyword_fallback() -> None:
    """Test unified router keyword-overlap routing."""
    router = UnifiedRouter()

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

    routed = await router.route(task, workers)
    assert "PythonExpert" in routed
    assert "Writer" not in routed


@pytest.mark.anyio
async def test_unified_router_dialect_kuwaiti() -> None:
    """Test UnifiedRouter prioritizing Kuwaiti/Gulf workers."""
    router = UnifiedRouter()

    # Create task with Kuwaiti dialect keywords (e.g. 'شلونك', 'شنو')
    task = SwarmTask(
        id="task_kuwaiti",
        prompt="شلونك يا خوي؟ شنو تبي نسوي اليوم؟",
        workers=["auto"],
    )

    workers = [
        {"name": "KuwaitiSpecialist", "capabilities": WorkerCapabilities(expertise=["Kuwaiti dialect", "Gulf culture"], role="Kuwait Advisor")},
        {"name": "StandardTranslator", "capabilities": WorkerCapabilities(expertise=["Standard Arabic", "MSA"], role="Translator")},
    ]

    routed = await router.route(task, workers)
    assert len(routed) > 0
    assert routed[0] == "KuwaitiSpecialist"


@pytest.mark.anyio
async def test_unified_router_dialect_msa() -> None:
    """Test UnifiedRouter prioritizing MSA/Standard Arabic workers."""
    router = UnifiedRouter()

    task = SwarmTask(
        id="task_msa",
        prompt="كيف حالك يا أخي العزيز؟ ماذا تريد أن نفعل اليوم؟",
        workers=["auto"],
    )

    workers = [
        {"name": "KuwaitiSpecialist", "capabilities": WorkerCapabilities(expertise=["Kuwaiti dialect", "Gulf culture"], role="Kuwait Advisor")},
        {"name": "StandardTranslator", "capabilities": WorkerCapabilities(expertise=["Standard Arabic", "MSA"], role="Translator")},
    ]

    routed = await router.route(task, workers)
    assert len(routed) > 0
    assert routed[0] == "StandardTranslator"


@pytest.mark.anyio
async def test_unified_router_no_capable_workers() -> None:
    """Test UnifiedRouter raising NoCapableWorkersError when no matches found."""
    router = UnifiedRouter()
    
    task = SwarmTask(
        id="task_strict",
        prompt="Do some astrophysics quantum computing",
        workers=["auto"],
    )
    workers = [
        {"name": "ChefBot", "capabilities": WorkerCapabilities(expertise=["cooking", "recipes"], role="Chef")},
    ]

    try:
        routed = await router.route(task, workers)
    except NoCapableWorkersError:
        pass
    else:
        pytest.fail("Should have raised NoCapableWorkersError")


@pytest.mark.anyio
async def test_unified_router_routing_diagnostics() -> None:
    """Test UnifiedRouter populating routing_diagnostics in task metadata."""
    router = UnifiedRouter()

    task = SwarmTask(
        id="task_diag",
        prompt="شلونك يا خوي؟ شنو تبي نسوي اليوم؟", # Kuwaiti dialect
        workers=["auto"],
    )

    workers = [
        {"name": "KuwaitiSpecialist", "capabilities": WorkerCapabilities(expertise=["Kuwaiti dialect", "Gulf culture"], role="Kuwait Advisor")},
        {"name": "PythonExpert", "capabilities": WorkerCapabilities(expertise=["python", "json"], role="Developer")},
    ]

    routed = await router.route(task, workers)
    assert len(routed) > 0
    assert "routing_diagnostics" in task.metadata
    
    diag = task.metadata["routing_diagnostics"]
    assert diag["dialect_detected"] == "kw"
    assert diag["dialect_confidence"] > 0.3
    assert "KuwaitiSpecialist" in diag["scores"]
    assert "PythonExpert" in diag["scores"]
    assert diag["scores"]["KuwaitiSpecialist"]["dialect_boost"] > 0
    assert diag["scores"]["PythonExpert"]["dialect_boost"] == 0

