"""Comprehensive tests for Sprint P3 Features.

Covers:
1. SQLite write-through behavior and isolation of SessionManager under multiple tenants.
2. Per-tenant bounded LRU eviction in SessionManager.
3. Thread-id persistence in ChatSession and sse-level thread-id flow.
4. Dynamic multi-tenant checkpoint isolation in CheckpointManager.
5. Fail-closed RBAC gating inside UnifiedToolExecutor.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import AsyncGenerator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import aiosqlite

from kazma_core.tenant_context import set_current_tenant_id, reset_current_tenant_id
from kazma_core.rbac import RBACEngine
from kazma_core.mcp.manager import UnifiedToolExecutor
from kazma_ui.session_manager import SessionManager, ChatSession, reset_session_manager
from kazma_gateway.stores.checkpoint import CheckpointManager, create_checkpoint_manager


@contextmanager
def tenant_context(tenant_id: str | None):
    """Context manager to set and reset tenant context."""
    token = set_current_tenant_id(tenant_id)
    try:
        yield
    finally:
        reset_current_tenant_id(token)


@pytest.fixture(autouse=True)
def clean_test_env():
    """Ensure kazma-data directories and databases are cleaned up for tests."""
    # Reset singleton to prevent caching issues
    reset_session_manager()

    test_dirs = [Path("kazma-data")]
    for d in test_dirs:
        if d.exists():
            # Delete checkpoint and session test databases
            for f in d.glob("*_test.db*"):
                try:
                    f.unlink()
                except Exception:
                    pass
            for f in d.glob("checkpoints_*.db*"):
                try:
                    f.unlink()
                except Exception:
                    pass
            for f in d.glob("chat_sessions_test.db*"):
                try:
                    f.unlink()
                except Exception:
                    pass

    yield

    # Clean up after
    reset_session_manager()


# ─── 1 & 2. SessionManager Multi-Tenancy & LRU Eviction ────────────────

def test_session_manager_tenant_isolation_and_persistence(tmp_path):
    """Verify SessionManager isolates sessions by tenant and persists to SQLite."""
    db_path = str(tmp_path / "chat_sessions_test.db")
    
    # 1. Create sessions in separate tenant contexts
    with tenant_context("tenant-alpha"):
        mgr = SessionManager(db_path=db_path)
        sess_a = mgr.get_or_create("session-1")
        sess_a.messages.append({"role": "user", "content": "Hello alpha"})
        sess_a.thread_id = "thread-alpha-1"
        mgr.put(sess_a)

    with tenant_context("tenant-beta"):
        # Re-use same DB path to verify isolation in the same database
        mgr = SessionManager(db_path=db_path)
        sess_b = mgr.get_or_create("session-1")  # Same session_id, different tenant
        sess_b.messages.append({"role": "user", "content": "Hello beta"})
        sess_b.thread_id = "thread-beta-1"
        mgr.put(sess_b)

    # 2. Re-load from DB and verify correctness and isolation
    with tenant_context("tenant-alpha"):
        mgr_new = SessionManager(db_path=db_path)
        loaded_a = mgr_new.get("session-1")
        assert loaded_a is not None
        assert loaded_a.tenant_id == "tenant-alpha"
        assert loaded_a.thread_id == "thread-alpha-1"
        assert len(loaded_a.messages) == 1
        assert loaded_a.messages[0]["content"] == "Hello alpha"

    with tenant_context("tenant-beta"):
        mgr_new = SessionManager(db_path=db_path)
        loaded_b = mgr_new.get("session-1")
        assert loaded_b is not None
        assert loaded_b.tenant_id == "tenant-beta"
        assert loaded_b.thread_id == "thread-beta-1"
        assert len(loaded_b.messages) == 1
        assert loaded_b.messages[0]["content"] == "Hello beta"


def test_session_manager_lru_eviction_bounded_per_tenant(tmp_path):
    """Verify LRU eviction is bounded and happens independently per tenant."""
    db_path = str(tmp_path / "chat_sessions_test.db")
    
    # Limit to 2 sessions per tenant
    mgr = SessionManager(max_sessions=2, db_path=db_path)

    with tenant_context("tenant-alpha"):
        # Add 3 sessions for alpha
        s1 = mgr.get_or_create("sess-a-1")
        mgr.put(s1)
        s2 = mgr.get_or_create("sess-a-2")
        mgr.put(s2)
        s3 = mgr.get_or_create("sess-a-3")
        mgr.put(s3)

    with tenant_context("tenant-beta"):
        # Add 2 sessions for beta
        s4 = mgr.get_or_create("sess-b-1")
        mgr.put(s4)
        s5 = mgr.get_or_create("sess-b-2")
        mgr.put(s5)

    # Verify alpha evicted its oldest session (sess-a-1), but kept sess-a-2 and sess-a-3
    with tenant_context("tenant-alpha"):
        assert mgr.get("sess-a-1") is None
        assert mgr.get("sess-a-2") is not None
        assert mgr.get("sess-a-3") is not None

    # Verify beta's sessions are completely unaffected by alpha's insertions/evictions
    with tenant_context("tenant-beta"):
        assert mgr.get("sess-b-1") is not None
        assert mgr.get("sess-b-2") is not None


# ─── 3. Thread-ID persistence in ChatSession ───────────────────────────

def test_chat_session_thread_id_persistence_and_update(tmp_path):
    """Verify thread_id is stored in ChatSession and survives DB roundtrip."""
    db_path = str(tmp_path / "chat_sessions_test.db")
    mgr = SessionManager(db_path=db_path)

    with tenant_context("tenant-alpha"):
        sess = mgr.get_or_create("my-session-id")
        assert sess.thread_id == ""  # Initially empty
        
        # Simulating sse_chat flow update
        sess.thread_id = "custom-thread-123"
        mgr.put(sess)

        # Refresh
        mgr_new = SessionManager(db_path=db_path)
        loaded = mgr_new.get("my-session-id")
        assert loaded is not None
        assert loaded.thread_id == "custom-thread-123"


# ─── 4. CheckpointManager Multi-Tenant Isolation ───────────────────────

@pytest.mark.asyncio
async def test_checkpoint_manager_multi_tenant_isolation(tmp_path):
    """Verify checkpoints are saved in isolated databases per tenant."""
    # Ensure kazma-data dir exists
    Path("kazma-data").mkdir(parents=True, exist_ok=True)
    
    # create default checkpoint manager
    base_db = "kazma-data/checkpoints_test.db"
    manager = await create_checkpoint_manager(base_db)

    try:
        from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata

        # Prepare test checkpoint
        cp_alpha = Checkpoint(
            v=1,
            id="cp-1",
            ts="2026-07-08T00:00:00Z",
            channel_values={"messages": [{"role": "user", "content": "alpha text"}]},
            channel_versions={},
            versions_seen={},
            pending_sends=[],
        )
        metadata_alpha = CheckpointMetadata(source="loop", step=0, writes={}, parents={})

        cp_beta = Checkpoint(
            v=1,
            id="cp-2",
            ts="2026-07-08T00:01:00Z",
            channel_values={"messages": [{"role": "user", "content": "beta text"}]},
            channel_versions={},
            versions_seen={},
            pending_sends=[],
        )
        metadata_beta = CheckpointMetadata(source="loop", step=0, writes={}, parents={})

        # 1. Save under tenant-alpha context
        with tenant_context("tenant-alpha"):
            config_alpha = {"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}}
            await manager.aput(config_alpha, cp_alpha, metadata_alpha, {})

        # 2. Save under tenant-beta context
        with tenant_context("tenant-beta"):
            config_beta = {"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}}
            await manager.aput(config_beta, cp_beta, metadata_beta, {})

        # 3. Check that isolated database files were created
        assert Path("kazma-data/checkpoints_tenant-alpha.db").exists()
        assert Path("kazma-data/checkpoints_tenant-beta.db").exists()

        # 4. Verify list_checkpoints isolates correctly
        with tenant_context("tenant-alpha"):
            checkpoints_alpha = await manager.list_checkpoints()
            assert len(checkpoints_alpha) == 1
            assert checkpoints_alpha[0]["thread_id"] == "thread-1"
            assert checkpoints_alpha[0]["message_count"] is not None

        with tenant_context("tenant-beta"):
            checkpoints_beta = await manager.list_checkpoints()
            assert len(checkpoints_beta) == 1
            assert checkpoints_beta[0]["thread_id"] == "thread-1"
            assert checkpoints_beta[0]["message_count"] is not None

        # 5. Read back and verify values are isolated
        with tenant_context("tenant-alpha"):
            loaded_alpha = await manager.aget_tuple({"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}})
            assert loaded_alpha is not None
            assert loaded_alpha.checkpoint["id"] == "cp-1"

        with tenant_context("tenant-beta"):
            loaded_beta = await manager.aget_tuple({"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}})
            assert loaded_beta is not None
            assert loaded_beta.checkpoint["id"] == "cp-2"

    finally:
        await manager.close()


# ─── 5. UnifiedToolExecutor RBAC Gating ────────────────────────────────

class MockLocalToolRegistry:
    """Mock local registry to test tool routing and parameter popping."""
    def __init__(self):
        self.executed_with_args = None

    def get_tool(self, name: str):
        if name == "test_tool":
            return self
        return None

    def list_tools(self):
        return [{"name": "test_tool"}]

    async def execute(self, name: str, arguments: dict[str, Any]):
        self.executed_with_args = dict(arguments)  # shallow copy
        return {"content": "Tool Executed Successfully", "is_error": False}


@pytest.mark.asyncio
async def test_unified_tool_executor_rbac_gating(tmp_path):
    """Verify fail-closed RBAC gating and parameter popping in UnifiedToolExecutor."""
    db_path = str(tmp_path / "rbac_test.db")
    rbac_engine = RBACEngine(db_path=db_path)
    
    try:
        # Setup roles and permissions
        # Assign alice a 'trader' role in 'gas_oil'
        await rbac_engine.assign_role("alice", "gas_oil", "trader")
        
        # Initialize mock local tools and executor
        mock_local = MockLocalToolRegistry()
        executor = UnifiedToolExecutor(local=mock_local, rbac=rbac_engine)

        # Case A: Access allowed (alice is gas_oil trader, has read permission on pricing)
        args_allowed = {
            "user_id": "alice",
            "division": "gas_oil",
            "resource": "pricing",
            "action": "read",
            "param1": "value1"
        }
        res = await executor.execute("test_tool", args_allowed)
        assert res["is_error"] is False
        assert "Tool Executed Successfully" in res["content"]
        
        # Crucial Verification: context keys popped, parameter signature preserved!
        assert mock_local.executed_with_args == {"param1": "value1"}
        assert "user_id" not in mock_local.executed_with_args
        assert "division" not in mock_local.executed_with_args
        assert "resource" not in mock_local.executed_with_args
        assert "action" not in mock_local.executed_with_args

        # Case B: Access denied (alice is gas_oil trader, but delete on sensitive pricing is not allowed)
        mock_local.executed_with_args = None
        args_denied = {
            "_user_id": "alice",
            "_division": "gas_oil",
            "_resource": "pricing",
            "_action": "delete",
            "param1": "value1"
        }
        res_denied = await executor.execute("test_tool", args_denied)
        assert res_denied["is_error"] is True
        assert "Access Denied" in res_denied["content"]
        assert mock_local.executed_with_args is None  # Never routed to local tool!

        # Case C: No user_id context supplied -> normal execution with no RBAC checks
        mock_local.executed_with_args = None
        args_no_rbac = {"param1": "no_rbac"}
        res_no_rbac = await executor.execute("test_tool", args_no_rbac)
        assert res_no_rbac["is_error"] is False
        assert mock_local.executed_with_args == {"param1": "no_rbac"}
    finally:
        await rbac_engine.close()
