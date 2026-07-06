"""Tests for thread-safe context-based tenant isolation in VectorMemory."""

from __future__ import annotations

import tempfile
import pytest

from kazma_core.memory.vector_store import VectorMemory
from kazma_core.tenant_context import set_current_tenant_id, reset_current_tenant_id


def _rag_dependencies_available() -> bool:
    try:
        import chromadb
        import sentence_transformers
        return True
    except ImportError:
        return False


@pytest.fixture
def temp_vector_memory():
    if not _rag_dependencies_available():
        pytest.skip("RAG dependencies not available for VectorMemory tenant isolation test")
    with tempfile.TemporaryDirectory() as tmpdir:
        yield VectorMemory(path=tmpdir, collection_name="test_tenant_isolation")


def test_tenant_isolation_stored_and_retrieved(temp_vector_memory):
    """Verify that storing under tenant context limits search results to that context."""
    # 1. Store fact under Tenant A
    tok1 = set_current_tenant_id("tenant-a")
    try:
        temp_vector_memory.add("Balfaris prefers dark mode.", {"topic": "preferences"})
    finally:
        reset_current_tenant_id(tok1)

    # 2. Store fact under Tenant B
    tok2 = set_current_tenant_id("tenant-b")
    try:
        temp_vector_memory.add("Hermes prefers light mode.", {"topic": "preferences"})
    finally:
        reset_current_tenant_id(tok2)

    # 3. Search under Tenant A - should only see Balfaris' fact
    tok3 = set_current_tenant_id("tenant-a")
    try:
        results_a = temp_vector_memory.search("preferences")
        assert len(results_a) == 1
        assert "Balfaris" in results_a[0]["text"]
        assert "Hermes" not in results_a[0]["text"]
    finally:
        reset_current_tenant_id(tok3)

    # 4. Search under Tenant B - should only see Hermes' fact
    tok4 = set_current_tenant_id("tenant-b")
    try:
        results_b = temp_vector_memory.search("preferences")
        assert len(results_b) == 1
        assert "Hermes" in results_b[0]["text"]
        assert "Balfaris" not in results_b[0]["text"]
    finally:
        reset_current_tenant_id(tok4)

    # 5. Search without tenant context - should not match any filtered records since filter where is none or empty
    # ChromaDB queries without a filter do not implicitly restrict results by tenant_id
    results_none = temp_vector_memory.search("preferences")
    # All stored documents are retrieved (count is 2)
    assert len(results_none) == 2
