"""Legacy crash recovery cannot silently repeat committed tool effects."""
from __future__ import annotations

from unittest.mock import Mock

from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.task import SwarmTask, TaskStatus
from kazma_core.swarm.task_store import TaskStore


def test_legacy_pending_recovery_is_finalized_without_worker_dispatch(tmp_path, monkeypatch):
    store = TaskStore(db_path=str(tmp_path / "swarm_tasks.db"))
    recovered = SwarmTask(prompt="effect may have committed", metadata={"recovery_count": 1})
    queued = SwarmTask(prompt="ordinary pending work")
    paused = SwarmTask(prompt="approval", status=TaskStatus.PAUSED)
    for task in (recovered, queued, paused):
        store.persist_task(task)
    engine = SwarmEngine(task_store=store)
    dispatch = Mock(side_effect=AssertionError("uncertain effects must not be replayed"))
    monkeypatch.setattr(engine, "dispatch", dispatch)
    try:
        assert engine.redispatch_recovered_tasks() == 0
        saved = store.get_task(recovered.id)
        assert saved.status == TaskStatus.FAILED
        assert saved.result.metadata["execution_outcome"] == "unknown"
        assert "automatic replay refused" in saved.result.error
        assert store.get_task(queued.id).status == TaskStatus.PENDING
        assert store.get_task(paused.id).status == TaskStatus.PAUSED
        dispatch.assert_not_called()
    finally:
        store.close()
