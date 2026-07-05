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
