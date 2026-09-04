"""Tests for Wave 5 audit fixes (Persistence & Queue Scaling).

Covers:
- M8: agent_runner checkpointer cleanup
- M20: autoscaler atomic save_templates
- M19: config_store child-merge vault pointer resolution & recursive structure
- M15: bounded _emit_locks and _approve_locks with LRU eviction
- M9: time_travel global snapshot ceiling
- M25: async commitment operations off the event loop
- M4: task_queue dedicated lease renewal loop
- M2: memory_task_queue composite index and purge_completed_tasks
- M3: swarm task_store materialized sort_at and prune_tasks
- M5: config_store set_if_absent & distributed circuit breaker probe lease
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ----------------------------------------------------------------------
# M8: agent_runner checkpointer pool cleanup
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_m8_agent_runner_close_checkpointer():
    from kazma_core.agent_runner import KazmaAgent

    agent = KazmaAgent()
    agent._checkpointer = MagicMock()
    agent._close_checkpointer = AsyncMock()

    await agent.shutdown()
    agent._close_checkpointer.assert_awaited_once()


# ----------------------------------------------------------------------
# M20: autoscaler atomic save_templates
# ----------------------------------------------------------------------
def test_m20_autoscaler_atomic_save_templates(tmp_path):
    from kazma_core.swarm.autoscaler import AutoScaler

    scaler = AutoScaler(MagicMock())
    target_path = tmp_path / "templates.json"
    scaler._templates_path = str(target_path)
    mock_template = MagicMock()
    mock_template.to_dict.return_value = {"system_prompt": "hello"}
    scaler._templates = {"test_worker": mock_template}

    with patch("os.replace", wraps=os.replace) as mock_replace:
        scaler.save_templates()
        assert mock_replace.called
        assert target_path.exists()
        import json
        data = json.loads(target_path.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["system_prompt"] == "hello"


# ----------------------------------------------------------------------
# M19: config_store vault pointer resolution on child-merge & nested dicts
# ----------------------------------------------------------------------
def test_m19_config_store_vault_resolution(tmp_path):
    from kazma_core.config_store import ConfigStore

    db_path = str(tmp_path / "test_config.db")
    store = ConfigStore(db_path=db_path)

    mock_vault = MagicMock()
    mock_vault.retrieve.return_value = "decrypted_secret"

    with patch("kazma_core.config_store._try_get_vault", return_value=mock_vault):
        # 1. Direct pointer
        assert store._resolve_vault_value("k1", "vault://sec1") == "decrypted_secret"

        # 2. Nested dict
        nested = {"token": "vault://sec2", "sub": {"secret": "vault://sec3"}}
        resolved = store._resolve_vault_value("k2", nested)
        assert resolved["token"] == "decrypted_secret"
        assert resolved["sub"]["secret"] == "decrypted_secret"

        # 3. List
        lst = ["vault://sec4", "plain", 42]
        resolved_lst = store._resolve_vault_value("k3", lst)
        assert resolved_lst[0] == "decrypted_secret"
        assert resolved_lst[1] == "plain"
        assert resolved_lst[2] == 42
    store.close()


# ----------------------------------------------------------------------
# M15: bounded locks in delivery.py and routes_direct/misc.py
# ----------------------------------------------------------------------
def test_m15_turn_broker_bounded_emit_locks():
    from kazma_ui.delivery import TurnBroker

    broker = TurnBroker()
    for i in range(550):
        lock = broker._emit_lock_for(f"thread_{i}")
        assert isinstance(lock, asyncio.Lock)

    # Should have evicted unlocked locks to stay <= 512
    assert len(broker._emit_locks) <= 512


def test_m15_misc_bounded_approve_locks():
    from kazma_ui.routes_direct.misc import _approve_lock_for, _approve_locks

    for i in range(550):
        lock = _approve_lock_for(f"gate_{i}")
        assert isinstance(lock, asyncio.Lock)

    assert len(_approve_locks) <= 512


# ----------------------------------------------------------------------
# M9: time_travel global snapshot ceiling
# ----------------------------------------------------------------------
def test_m9_snapshot_recorder_global_cap(tmp_path):
    from kazma_core.time_travel import SnapshotRecorder

    db_path = str(tmp_path / "snapshots.db")
    rec = SnapshotRecorder(db_path=db_path, max_snapshots=100, max_global_snapshots=5)

    for i in range(10):
        state = {"thread_id": f"t_{i % 3}", "iteration": i, "messages": []}
        rec.capture(state)

    assert len(rec._memory) <= 5


# ----------------------------------------------------------------------
# M25: async commitment store offloading
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_m25_async_commitment_store(tmp_path):
    from kazma_core.safety.commitment.store import (
        Commitment,
        async_create_commitment,
        async_get_commitment,
        async_update_status,
        async_list_by_thread,
        ensure_commitment_schema,
    )

    db_path = str(tmp_path / "commitment.db")
    with patch("kazma_core.safety.commitment.store.memory_ops_db", return_value=db_path):
        ensure_commitment_schema()
        c = Commitment(
            commitment_id="c_test_1",
            thread_id="th_1",
            act="soul_diff",
            status="pending",
        )
        cid = await async_create_commitment(c)
        assert cid == "c_test_1"

        fetched = await async_get_commitment("c_test_1")
        assert fetched is not None
        assert fetched.commitment_id == "c_test_1"

        updated = await async_update_status("c_test_1", "committed")
        assert updated is not None
        assert updated.status == "committed"

        items = await async_list_by_thread("th_1")
        assert len(items) == 1
        assert items[0].status == "committed"


# ----------------------------------------------------------------------
# M4: task_queue dedicated lease renewal loop
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_m4_task_queue_renewal_task():
    from kazma_core.memory.task_queue import _MemoryWorker

    worker = _MemoryWorker()
    with patch.object(worker, "_run", AsyncMock()):
        with patch.object(worker, "_run_lease_renewal", AsyncMock()):
            worker.start()
            assert worker._renewal_task is not None
            assert not worker._renewal_task.done()

            await worker.stop()
            assert worker._renewal_task is None or worker._renewal_task.done()


# ----------------------------------------------------------------------
# M2: memory_task_queue composite index and purge_completed_tasks
# ----------------------------------------------------------------------
def test_m2_task_queue_purge(tmp_path):
    from kazma_core.memory.task_queue import purge_completed_tasks
    from kazma_core.memory.schema_v2 import ensure_ops_schema

    db_path = str(tmp_path / "memory_ops.db")
    with patch("kazma_core.paths.memory_ops_db", return_value=db_path):
        conn = sqlite3.connect(db_path)
        ensure_ops_schema(conn)

        # Check index exists
        indices = [row[1] for row in conn.execute("PRAGMA index_list(memory_task_queue)").fetchall()]
        assert "idx_mem_queue_status_updated" in indices

        # Insert old completed task and recent completed task
        old_time = time.time() - (10 * 86400)
        recent_time = time.time() - 3600

        conn.execute(
            """INSERT INTO memory_task_queue (id, task_type, payload_json, status, updated_at, created_at)
               VALUES ('old_1', 'extract', '{}', 'completed', ?, ?)""",
            (old_time, old_time),
        )
        conn.execute(
            """INSERT INTO memory_task_queue (id, task_type, payload_json, status, updated_at, created_at)
               VALUES ('recent_1', 'extract', '{}', 'completed', ?, ?)""",
            (recent_time, recent_time),
        )
        conn.execute(
            """INSERT INTO memory_task_queue (id, task_type, payload_json, status, updated_at, created_at)
               VALUES ('pending_1', 'extract', '{}', 'pending', ?, ?)""",
            (old_time, old_time),
        )
        conn.commit()
        conn.close()

        purged = purge_completed_tasks(retention_days=7)
        assert purged == 1

        conn = sqlite3.connect(db_path)
        remaining = [r[0] for r in conn.execute("SELECT id FROM memory_task_queue").fetchall()]
        conn.close()
        assert "old_1" not in remaining
        assert "recent_1" in remaining
        assert "pending_1" in remaining


# ----------------------------------------------------------------------
# M3: swarm task_store materialized sort_at and prune_tasks
# ----------------------------------------------------------------------
def test_m3_swarm_task_store_sort_at_and_prune(tmp_path):
    from kazma_core.swarm.task import SwarmTask, TaskStatus, TaskType
    from kazma_core.swarm.task_store import TaskStore
    from datetime import datetime, UTC, timedelta

    db_path = str(tmp_path / "swarm_tasks.db")
    store = TaskStore(db_path=db_path)

    # 1. Verify sort_at column and index exist
    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(swarm_tasks)").fetchall()]
    assert "sort_at" in cols
    indices = [r[1] for r in conn.execute("PRAGMA index_list(swarm_tasks)").fetchall()]
    assert "idx_swarm_tasks_sort_at" in indices
    conn.close()

    # 2. Persist tasks
    t1 = SwarmTask(id="task_1", type=TaskType.CONSULT, prompt="p1")
    t1.status = TaskStatus.COMPLETED
    t1.created_at = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    t1.completed_at = t1.created_at
    store.persist_task(t1)

    t2 = SwarmTask(id="task_2", type=TaskType.CONSULT, prompt="p2")
    t2.status = TaskStatus.COMPLETED
    t2.created_at = datetime.now(UTC).isoformat()
    t2.completed_at = t2.created_at
    store.persist_task(t2)

    # 3. Query list_tasks
    tasks = store.list_tasks()
    assert len(tasks) == 2
    assert tasks[0].id == "task_2"  # most recent first

    # 4. Prune tasks older than 30 days
    pruned = store.prune_tasks(retention_days=30)
    assert pruned == 1

    remaining = store.list_tasks()
    assert len(remaining) == 1
    assert remaining[0].id == "task_2"
    store.close()


# ----------------------------------------------------------------------
# M5: config_store set_if_absent and circuit breaker probe lease
# ----------------------------------------------------------------------
def test_m5_config_store_set_if_absent(tmp_path):
    from kazma_core.config_store import ConfigStore

    db_path = str(tmp_path / "test_cs.db")
    cs = ConfigStore(db_path=db_path)

    key = "probe_lease:worker_a"
    # 1. First set should succeed
    payload1 = {"holder": "pid1", "expires_at": time.time() + 10.0}
    assert cs.set_if_absent(key, payload1) is True

    # 2. Concurrent set with active lease should fail
    payload2 = {"holder": "pid2", "expires_at": time.time() + 10.0}
    assert cs.set_if_absent(key, payload2) is False

    # 3. When expired, set should succeed
    payload3 = {"holder": "pid3", "expires_at": time.time() + 10.0}
    # Artificially expire
    cs.set(key, {"holder": "pid1", "expires_at": time.time() - 1.0})
    assert cs.set_if_absent(key, payload3) is True
    cs.close()


def test_m5_circuit_breaker_probe_lease():
    from kazma_core.swarm.reliability import CircuitBreaker

    cb = CircuitBreaker()
    with patch("kazma_core.swarm.reliability._shared_breakers_enabled", return_value=True):
        with patch("kazma_core.config_store.get_config_store") as mock_get_cs:
            mock_cs = MagicMock()
            mock_cs.set_if_absent.return_value = True
            mock_get_cs.return_value = mock_cs

            acquired = cb._try_acquire_probe_lease("worker_foo")
            assert acquired is True
            assert mock_cs.set_if_absent.called
