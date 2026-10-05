"""Package facades preserve identity while workflow imports remain independent."""
from __future__ import annotations

import importlib
import subprocess
import sys

import pytest


@pytest.mark.parametrize("name", ["kazma_core", "kazma_core.swarm"])
def test_public_exports_keep_their_defining_identity(name):
    package = importlib.import_module(name)
    assert set(package.__all__) == set(package._EXPORTS)
    for symbol in package.__all__:
        module_name = package._EXPORTS[symbol]
        if name == "kazma_core":
            module_name = "kazma_core." + module_name
        assert getattr(package, symbol) is getattr(importlib.import_module(module_name), symbol)
        assert symbol in dir(package)
    with pytest.raises(AttributeError):
        getattr(package, "not_a_public_symbol")


def test_workflow_leaf_does_not_import_runtime_services():
    source = """
import sys
import kazma_core.swarm.durable_temporal
for name in ('kazma_core.llm_provider', 'kazma_core.swarm.engine', 'kazma_core.agent_runner'):
    assert name not in sys.modules, name
"""
    result = subprocess.run([sys.executable, "-c", source], text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_facade_does_not_capture_patched_accessors(monkeypatch):
    import kazma_core
    from kazma_core import service_container
    marker = object()
    monkeypatch.setattr(service_container, "get_container", lambda: marker)
    assert kazma_core.get_container() is marker
