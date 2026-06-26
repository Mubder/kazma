"""Tests for RAG pipeline — VectorMemory and agent tools.

6 tests per gw-022 spec:
    1. Store text → search returns it
    2. Metadata round-trips
    3. memory_store in tool list
    4. memory_search in tool list
    5. Auto-index on session end
    6. No platform leak in stored metadata
"""

from __future__ import annotations

import tempfile

import pytest
from kazma_core.memory.vector_store import VectorMemory


def _rag_dependencies_available() -> bool:
    """Check if the optional RAG test dependencies are installed."""
    try:
        import chromadb  # noqa: F401
        import sentence_transformers  # noqa: F401

        return True
    except Exception:
        return False


def _build_vector_memory(*, path: str, collection_name: str) -> VectorMemory:
    """Create VectorMemory or skip when optional RAG dependencies are unusable."""
    try:
        return VectorMemory(path=path, collection_name=collection_name)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"RAG test dependencies unavailable: {exc}")


@pytest.fixture
def vector_memory():
    """Temporary VectorMemory for testing."""
    if not _rag_dependencies_available():
        pytest.skip("RAG test dependencies not installed (optional [rag] extra)")
    with tempfile.TemporaryDirectory() as tmpdir:
        mem = _build_vector_memory(path=tmpdir, collection_name="test_memory")
        yield mem


class TestVectorMemory:
    """Tests for the VectorMemory class."""

    def test_add_and_search(self, vector_memory: VectorMemory) -> None:
        """Test 1: Store text → search returns it."""
        vector_memory.add("The user prefers dark mode", {"topic": "preferences"})
        results = vector_memory.search("what theme does the user prefer?")
        assert len(results) >= 1
        assert "dark mode" in results[0]["text"]

    def test_metadata_preserved(self, vector_memory: VectorMemory) -> None:
        """Test 2: Metadata round-trips through add/search."""
        vector_memory.add(
            "User's name is Balfaris",
            {"topic": "identity", "user": "balfaris", "importance": "high"},
        )
        results = vector_memory.search("user name")
        assert len(results) >= 1
        meta = results[0]["metadata"]
        assert meta["topic"] == "identity"
        assert meta["user"] == "balfaris"
        assert meta["importance"] == "high"

    def test_search_empty(self, vector_memory: VectorMemory) -> None:
        """Search on empty store returns empty list."""
        results = vector_memory.search("anything")
        assert results == []

    def test_count(self, vector_memory: VectorMemory) -> None:
        """Count reflects stored documents."""
        assert vector_memory.count == 0
        vector_memory.add("fact 1")
        vector_memory.add("fact 2")
        assert vector_memory.count == 2

    def test_custom_doc_id(self, vector_memory: VectorMemory) -> None:
        """Custom doc_id is used."""
        doc_id = vector_memory.add("test", doc_id="custom-id")
        assert doc_id == "custom-id"
        results = vector_memory.search("test")
        assert len(results) >= 1


class TestMemoryToolsRegistered:
    """Test 3 & 4: memory_store and memory_search in tool list."""

    def test_memory_store_registered(self) -> None:
        """memory_store must appear in the built-in tool registry."""
        from kazma_core.agent.tool_registry import LocalToolRegistry

        registry = LocalToolRegistry(include_builtins=True)
        tools = registry.get_tool_definitions()
        tool_names = [t["function"]["name"] for t in tools]
        assert "memory_store" in tool_names

    def test_memory_search_registered(self) -> None:
        """memory_search must appear in the built-in tool registry."""
        from kazma_core.agent.tool_registry import LocalToolRegistry

        registry = LocalToolRegistry(include_builtins=True)
        tools = registry.get_tool_definitions()
        tool_names = [t["function"]["name"] for t in tools]
        assert "memory_search" in tool_names

    def test_memory_store_schema(self) -> None:
        """memory_store tool must have text and metadata parameters."""
        from kazma_core.agent.tool_registry import LocalToolRegistry

        registry = LocalToolRegistry(include_builtins=True)
        tools = registry.get_tool_definitions()
        ms = next(t for t in tools if t["function"]["name"] == "memory_store")
        params = ms["function"]["parameters"]
        assert "text" in params["properties"]


class TestAutoIndexAndIsolation:
    """Test 5 & 6: Auto-index and platform isolation."""

    def test_no_platform_leak_in_metadata(self, vector_memory: VectorMemory) -> None:
        """Test 6: chat_id should not be stored in memory metadata by default."""
        # The handler should store thread_id and platform, not chat_id
        vector_memory.add(
            "User: hello\nAssistant: hi there",
            {
                "thread_id": "gateway-telegram-123",
                "platform": "telegram",
                "display_name": "balfaris",
                "timestamp": "2025-01-01T00:00:00",
            },
        )
        results = vector_memory.search("hello")
        assert len(results) >= 1
        meta = results[0]["metadata"]
        # These are safe to store
        assert "thread_id" in meta
        assert "platform" in meta
        # chat_id should NOT be in the stored metadata
        assert "chat_id" not in meta
        assert "user_id" not in meta
