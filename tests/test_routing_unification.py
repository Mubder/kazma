"""Tests for the WorkerRegistry + UnifiedRouter integration."""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from kazma_core.swarm.registry import WorkerRegistry, WorkerEntry

from tests._no_models import finish_turn_memory, keep_models_out


@pytest.fixture(autouse=True)
def _no_models(monkeypatch):
    """No real embedder or model behind this file's memory and swarm paths
    (tests/_no_models.py; tests/_egress_guard.py refuses the real ones)."""
    keep_models_out(monkeypatch, llm=False)
    yield
    finish_turn_memory()  # a turn's memory write ends inside its test


def test_find_best_unified_router_integration(tmp_path) -> None:
    """Test find_best utilizing UnifiedRouter and fallbacks."""
    registry_file = tmp_path / "test_registry.json"
    registry = WorkerRegistry(path=registry_file)

    # 1. Register a specialist and a generalist
    py_specialist = WorkerEntry(
        name="PythonExpert",
        expertise=["python", "coding"],
        roles=["developer"],
        system_prompt="You are a python expert."
    )
    generalist = WorkerEntry(
        name="GeneralHelper",
        expertise=["general"],
        roles=["assistant"],
        system_prompt="I can help with general tasks."
    )
    registry.register(py_specialist)
    registry.register(generalist)

    # 2. Query for a python task
    best_workers = registry.find_best("Write a python class")
    assert len(best_workers) > 0
    assert best_workers[0].name == "PythonExpert"

    # 3. Query for an unknown task (should fallback to generalist)
    best_workers_fallback = registry.find_best("Do some quantum astrophysics")
    assert len(best_workers_fallback) > 0
    assert best_workers_fallback[0].name == "GeneralHelper"
