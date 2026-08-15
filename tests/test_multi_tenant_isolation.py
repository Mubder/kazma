"""Tests for Multi-Tenant State Isolation in Session and Memory stores.

Verifies:
1. SQLiteSessionStore strictly isolates session CRUD operations by tenant_id.
2. (removed — V1 SQLiteMemoryBackend retired; V2 isolation in test_memory_v2_tenant_routes.py)
   search, and vector search by tenant_id.
3. Zero leakage between distinct tenants under all retrieval pathways.
"""

from __future__ import annotations

import pytest
from typing import Any

from kazma_gateway.stores.sqlite import SQLiteSessionStore


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


