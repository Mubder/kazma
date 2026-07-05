"""Tests for Multi-Tenant State Isolation in Session and Memory stores.

Verifies:
1. SQLiteSessionStore strictly isolates session CRUD operations by tenant_id.
2. SQLiteMemoryBackend strictly isolates memory document indexing, FTS5 BM25 keyword
   search, and vector search by tenant_id.
3. Zero leakage between distinct tenants under all retrieval pathways.
"""

from __future__ import annotations

import pytest
from typing import Any

from kazma_gateway.stores.sqlite import SQLiteSessionStore
from kazma_memory.search_backend import SQLiteMemoryBackend


@pytest.mark.anyio
async def test_session_store_multi_tenant_isolation() -> None:
    """Test SQLiteSessionStore segregating session context per tenant_id."""
    # Use in-memory SQLite database for test isolation
    store = SQLiteSessionStore(":memory:")

    # 1. Put sessions for different tenants
    await store.put("thread_1", {"platform": "telegram", "username": "alice", "data": "T1_data"}, tenant_id="tenant_1")
    await store.put("thread_2", {"platform": "discord", "username": "bob", "data": "T2_data"}, tenant_id="tenant_2")
    # A global/un-scoped session
    await store.put("thread_global", {"platform": "web", "username": "charlie", "data": "global_data"}, tenant_id=None)

    # 2. Verify get() is tenant-scoped
    # Tenant 1 should get thread_1, and can optionally access global if fallback is intended,
    # but cannot see tenant_2's private threads.
    t1_retrieved_1 = await store.get("thread_1", tenant_id="tenant_1")
    assert t1_retrieved_1.get("data") == "T1_data"

    t1_retrieved_2 = await store.get("thread_2", tenant_id="tenant_1")
    assert t1_retrieved_2 == {}  # Empty/Not found due to tenant isolation

    t2_retrieved_2 = await store.get("thread_2", tenant_id="tenant_2")
    assert t2_retrieved_2.get("data") == "T2_data"

    # Global session should be retrievable when tenant_id matches, or if tenant_id is None
    global_retrieved_t1 = await store.get("thread_global", tenant_id="tenant_1")
    assert global_retrieved_t1.get("data") == "global_data"  # fallback allowed

    global_retrieved_none = await store.get("thread_global", tenant_id=None)
    assert global_retrieved_none.get("data") == "global_data"

    # 3. Verify list_active() filtering
    active_t1 = await store.list_active(tenant_id="tenant_1")
    active_t2 = await store.list_active(tenant_id="tenant_2")
    active_all = await store.list_active(tenant_id=None)

    t1_threads = {item["thread_id"] for item in active_t1}
    t2_threads = {item["thread_id"] for item in active_t2}
    all_threads = {item["thread_id"] for item in active_all}

    assert "thread_1" in t1_threads
    assert "thread_2" not in t1_threads

    assert "thread_2" in t2_threads
    assert "thread_1" not in t2_threads

    assert "thread_global" in all_threads

    # 4. Verify delete() is tenant-scoped
    # Attempting to delete thread_2 as tenant_1 should not delete it
    await store.delete("thread_2", tenant_id="tenant_1")
    t2_still_exists = await store.get("thread_2", tenant_id="tenant_2")
    assert t2_still_exists.get("data") == "T2_data"

    # Correct deletion
    await store.delete("thread_2", tenant_id="tenant_2")
    t2_deleted = await store.get("thread_2", tenant_id="tenant_2")
    assert t2_deleted == {}

    await store.close()


@pytest.mark.anyio
async def test_memory_store_multi_tenant_isolation() -> None:
    """Test SQLiteMemoryBackend segregating memory documents per tenant_id."""
    # Use in-memory database for testing
    backend = SQLiteMemoryBackend(":memory:")

    # 1. Index memories for different tenants
    await backend.index(
        {"id": "doc_1", "content": "The top-secret code is blue-velvet", "metadata": {"category": "secrets"}},
        tenant_id="tenant_1",
    )
    await backend.index(
        {"id": "doc_2", "content": "The top-secret code is red-velvet", "metadata": {"category": "secrets"}},
        tenant_id="tenant_2",
    )
    # A memory with tenant_id embedded in metadata or general structure
    await backend.index(
        {"id": "doc_3", "content": "Standard operating procedures for AI", "metadata": {"tenant_id": "tenant_1"}},
    )

    # 2. Perform FTS5 searches and verify strict isolation
    # Tenant 1 searches for "top-secret"
    results_t1 = await backend.search("top-secret", tenant_id="tenant_1")
    assert len(results_t1) > 0
    # Should find doc_1 (same tenant) but NOT doc_2 (different tenant)
    found_ids_t1 = {r["id"] for r in results_t1}
    assert "doc_1" in found_ids_t1
    assert "doc_2" not in found_ids_t1

    # Tenant 2 searches for "top-secret"
    results_t2 = await backend.search("top-secret", tenant_id="tenant_2")
    assert len(results_t2) > 0
    found_ids_t2 = {r["id"] for r in results_t2}
    assert "doc_2" in found_ids_t2
    assert "doc_1" not in found_ids_t2

    # Check search with embedded tenant_id in metadata (doc_3 was registered under tenant_1)
    results_t1_ai = await backend.search("procedures", tenant_id="tenant_1")
    assert any(r["id"] == "doc_3" for r in results_t1_ai)

    results_t2_ai = await backend.search("procedures", tenant_id="tenant_2")
    assert not any(r["id"] == "doc_3" for r in results_t2_ai)

    # 3. Perform vector search and verify isolation if vector search is mocked or checked
    # Since we can pass a dummy embedding bytes to _vector_search, let's verify direct query isolation too
    # Write some dummy bytes representing mock embedding
    dummy_emb = b"\x00" * 6144  # 1536 float32 dimensions * 4 bytes
    await backend.index(
        {"id": "vec_doc_1", "content": "Vector content tenant 1", "embedding": dummy_emb},
        tenant_id="tenant_1",
    )
    await backend.index(
        {"id": "vec_doc_2", "content": "Vector content tenant 2", "embedding": dummy_emb},
        tenant_id="tenant_2",
    )

    # Search with semantic_search enabled and embedding provided
    vec_results_t1 = await backend.search(
        "Vector",
        semantic_search=True,
        embedding=dummy_emb,
        tenant_id="tenant_1",
    )
    vec_ids_t1 = {r["id"] for r in vec_results_t1}
    # Should only find vec_doc_1 or doc_1, never vec_doc_2 (tenant 2)
    assert "vec_doc_2" not in vec_ids_t1

    await backend.close()
