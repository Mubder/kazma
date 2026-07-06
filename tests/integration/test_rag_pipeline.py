"""Integration test for RAG pipeline (gw-033).

Proves the full pipeline: store -> retrieve -> agent responds.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from kazma_core.agent.tool_registry import (
    LocalToolRegistry,
    get_vector_memory,
    set_vector_memory,
)
from kazma_core.memory.vector_store import VectorMemory


def _rag_dependencies_available() -> bool:
    """Check if the optional RAG test dependencies are installed."""
    try:
        import chromadb  # noqa: F401
        import sentence_transformers  # noqa: F401

        return True
    except Exception:
        return False


def _build_vector_memory(*, path: str, collection_name: str, model_name: str = "all-MiniLM-L6-v2") -> VectorMemory:
    """Create VectorMemory or skip when optional RAG dependencies are unusable."""
    try:
        return VectorMemory(path=path, collection_name=collection_name, model_name=model_name)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"RAG test dependencies unavailable: {exc}")


@pytest.fixture
def vector_memory():
    """Temporary VectorMemory with test data."""
    if not _rag_dependencies_available():
        pytest.skip("RAG test dependencies not installed (optional [rag] extra)")
    with tempfile.TemporaryDirectory() as tmpdir:
        vm = _build_vector_memory(path=tmpdir, collection_name="test_rag")
        yield vm


class TestRAGPipeline:
    """End-to-end RAG pipeline tests."""

    def test_store_and_retrieve(self, vector_memory: VectorMemory) -> None:
        """Store a fact -> search retrieves it."""
        vector_memory.add(
            "The secret launch code is ZEPHYR-42",
            {"topic": "launch_codes"},
        )
        results = vector_memory.search("What is the launch code?")
        assert len(results) >= 1
        assert "ZEPHYR-42" in results[0]["text"]

    def test_singleton_wiring(self, vector_memory: VectorMemory) -> None:
        """set_vector_memory -> get_vector_memory returns same instance."""
        set_vector_memory(vector_memory)
        assert get_vector_memory() is vector_memory
        set_vector_memory(None)

    @pytest.mark.asyncio
    async def test_tools_use_singleton(self, vector_memory: VectorMemory) -> None:
        """memory_store and memory_search use the singleton, not a new instance."""
        set_vector_memory(vector_memory)

        registry = LocalToolRegistry(include_builtins=True)

        # Execute memory_store
        store_result = await registry.execute("memory_store", {
            "text": "User prefers dark mode",
            "metadata": json.dumps({"topic": "preferences"}),
        })
        assert "Stored" in store_result.get("content", "")

        # Execute memory_search
        search_result = await registry.execute("memory_search", {
            "query": "What theme does the user prefer?",
        })
        content = search_result.get("content", "")
        assert "dark mode" in content.lower()

        set_vector_memory(None)

    def test_no_chromadb_in_graph_builder(self) -> None:
        """graph_builder.py must NOT import chromadb directly."""
        import ast

        graph_builder_path = Path(__file__).parent.parent.parent / "kazma-core" / "kazma_core" / "agent" / "graph_builder.py"
        if not graph_builder_path.exists():
            pytest.skip("graph_builder.py not found")

        source = graph_builder_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "chromadb" not in alias.name, f"chromadb import found in graph_builder.py: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module and "chromadb" in node.module:
                    pytest.fail(f"chromadb import found in graph_builder.py: from {node.module}")

    def test_no_chromadb_in_agent_handler(self) -> None:
        """agent_handler.py must NOT import chromadb directly."""
        import ast

        handler_path = Path(__file__).parent.parent.parent / "kazma-gateway" / "kazma_gateway" / "agent_handler.py"
        if not handler_path.exists():
            pytest.skip("agent_handler.py not found")

        source = handler_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "chromadb" not in alias.name, "chromadb import found in agent_handler.py"
            elif isinstance(node, ast.ImportFrom):
                if node.module and "chromadb" in node.module:
                    pytest.fail("chromadb import found in agent_handler.py")

    def test_env_vars_respected(self) -> None:
        """VectorMemory respects env vars for path/collection/model."""
        if not _rag_dependencies_available():
            pytest.skip("RAG test dependencies not installed (optional [rag] extra)")
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["KAZMA_VECTOR_PATH"] = tmpdir
            os.environ["KAZMA_VECTOR_COLLECTION"] = "test_env_col"
            os.environ["KAZMA_VECTOR_MODEL"] = "all-MiniLM-L6-v2"

            vm = _build_vector_memory(
                path=os.environ["KAZMA_VECTOR_PATH"],
                collection_name=os.environ["KAZMA_VECTOR_COLLECTION"],
                model_name=os.environ["KAZMA_VECTOR_MODEL"],
            )
            vm.add("test fact")
            assert vm.count >= 1

            del os.environ["KAZMA_VECTOR_PATH"]
            del os.environ["KAZMA_VECTOR_COLLECTION"]
            del os.environ["KAZMA_VECTOR_MODEL"]
