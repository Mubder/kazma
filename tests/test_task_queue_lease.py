"""Lease-heartbeat tests for the durable memory task queue.

Covers the double-execution durability fix (2026-08-26 audit):
  - a claimed task whose lease is renewed past the 300s stuck threshold is
    NOT re-claimed
  - a claimed task WITHOUT renewal IS re-claimed (attempts incremented,
    token rotated)
  - a stale handler's ack is rejected once the task was reclaimed
  - a stuck task that exhausted max_attempts dead-letters (termination)
"""

from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from kazma_core.memory import task_queue
from kazma_core.memory.task_queue import _MemoryWorker, enqueue_task, reset_worker


@pytest.fixture()
def ops_db(tmp_path, monkeypatch):
    """Isolated memory_ops.db via env override; restores handler registry."""
    db = str(tmp_path / "memory_ops.db")
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", db)

    from kazma_core.memory.schema_v2 import ensure_ops_schema

    conn = sqlite3.connect(db)
    try:
        ensure_ops_schema(conn)
    finally:
        conn.close()

    saved_handlers = dict(task_queue._HANDLERS)
    yield db
    task_queue._HANDLERS.clear()
    task_queue._HANDLERS.update(saved_handlers)
    reset_worker()


def _row(db: str, task_id: str) -> dict:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT * FROM memory_task_queue WHERE id=?", (task_id,)
        )
        return dict(cur.fetchone())
    finally:
        conn.close()


def _force_updated_at(db: str, task_id: str, ts: float) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE memory_task_queue SET updated_at=? WHERE id=?",
            (ts, task_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_renewed_lease_is_not_reclaimed(ops_db):
    """A >300s handler whose lease is renewed must not be double-executed."""
    tid = enqueue_task("macro_sleep", {})
    assert tid is not None
    worker = _MemoryWorker()
    claimed = worker._claim_batch()
    assert len(claimed) == 1
    token = claimed[0]["lease_token"]
    assert token

    # Handler legitimately still running past the stuck threshold.
    _force_updated_at(ops_db, tid, time.time() - 400.0)
    # Renewal pass (as driven by the _run loop every 60s) refreshes it.
    worker._leases[tid] = token
    worker._renew_leases()

    assert worker._claim_batch() == []


def test_unrenewed_lease_is_reclaimed_with_rotated_token(ops_db):
    """Without renewal the stuck row IS reclaimed, attempts incremented."""
    tid = enqueue_task("macro_sleep", {})
    worker = _MemoryWorker()
    first = worker._claim_batch()[0]

    _force_updated_at(ops_db, tid, time.time() - 400.0)
    second = worker._claim_batch()
    assert len(second) == 1
    assert second[0]["lease_token"] != first["lease_token"]

    row = _row(ops_db, tid)
    assert row["status"] == "processing"
    assert row["attempts"] == 1  # reclaim incremented attempts

    # Renewal with the STALE token is a no-op (row holds the new token).
    stale_ts = time.time() - 400.0
    _force_updated_at(ops_db, tid, stale_ts)
    worker._leases[tid] = first["lease_token"]
    worker._renew_leases()
    assert abs(_row(ops_db, tid)["updated_at"] - stale_ts) < 1e-6


def test_stale_handler_ack_is_rejected(ops_db):
    """After a reclaim, the old handler's ack must not finalize the row."""
    tid = enqueue_task("macro_sleep", {})
    worker = _MemoryWorker()
    first = worker._claim_batch()[0]

    _force_updated_at(ops_db, tid, time.time() - 400.0)
    second = worker._claim_batch()[0]
    assert second["lease_token"] != first["lease_token"]

    # Stale handler (old token) finishes successfully — rejected.
    worker._ack(first, success=True, error=None)
    row = _row(ops_db, tid)
    assert row["status"] == "processing"
    assert row["lease_token"] == second["lease_token"]

    # The current lease's ack finalizes and clears the token.
    worker._ack(second, success=True, error=None)
    row = _row(ops_db, tid)
    assert row["status"] == "completed"
    assert row["lease_token"] is None


def test_stuck_task_dead_letters_at_max_attempts(ops_db):
    """A crash-looping handler that never acks must terminate."""
    conn = sqlite3.connect(ops_db)
    try:
        conn.execute(
            """INSERT INTO memory_task_queue
               (id, task_type, payload_json, status, attempts, max_attempts,
                created_at, updated_at, lease_token)
               VALUES ('t_dead', 'macro_sleep', '{}', 'processing', 2, 3, ?, ?, 'old')""",
            (time.time() - 400.0, time.time() - 400.0),
        )
        conn.commit()
    finally:
        conn.close()

    worker = _MemoryWorker()
    # No pending rows: the claim itself must COMMIT the dead-letter work.
    assert worker._claim_batch() == []
    row = _row(ops_db, "t_dead")
    assert row["status"] == "failed"
    assert row["attempts"] == 3
    assert "max attempts" in (row["error_log"] or "")


async def test_process_registers_and_clears_lease(ops_db):
    """The handler executor registers the in-flight lease for renewal."""
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(payload: dict) -> bool:
        entered.set()
        await release.wait()
        return True

    task_queue.register_handler("lease_probe", handler)
    tid = enqueue_task("lease_probe", {})
    worker = _MemoryWorker()
    claimed = worker._claim_batch()
    assert len(claimed) == 1

    sem = asyncio.Semaphore(1)
    proc = asyncio.create_task(worker._process(sem, claimed[0]))
    await asyncio.wait_for(entered.wait(), timeout=5)
    assert worker._leases.get(tid) == claimed[0]["lease_token"]

    release.set()
    await asyncio.wait_for(proc, timeout=5)
    assert tid not in worker._leases
    assert _row(ops_db, tid)["status"] == "completed"


async def test_worker_loop_renews_lease_of_running_handler(ops_db, monkeypatch):
    """End-to-end: the _run poll loop keeps a >300s handler's lease fresh."""
    monkeypatch.setattr(task_queue, "_LEASE_RENEW_INTERVAL_SEC", 0.0)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow(payload: dict) -> bool:
        entered.set()
        await release.wait()
        return True

    task_queue.register_handler("slow_probe", slow)
    tid = enqueue_task("slow_probe", {})

    worker = _MemoryWorker(poll_interval=0.05)
    worker.start()
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        # Simulate the lease aging past the stuck threshold mid-handler.
        _force_updated_at(ops_db, tid, time.time() - 400.0)
        await asyncio.sleep(0.3)  # a few poll ticks renew the lease
        row = _row(ops_db, tid)
        assert row["status"] == "processing"
        assert row["updated_at"] > time.time() - 300
        # A competing claimer must not double-claim the running task.
        assert _MemoryWorker()._claim_batch() == []
    finally:
        release.set()
        await worker.stop()

    assert _row(ops_db, tid)["status"] == "completed"
