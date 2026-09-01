"""TaskStore persists workspace_id (audit H-11)."""

from __future__ import annotations

from kazma_core.swarm.task import SwarmTask, TaskStatus, TaskType
from kazma_core.swarm.task_store import TaskStore


def test_workspace_id_survives_save_load(tmp_path) -> None:
    store = TaskStore(db_path=str(tmp_path / "swarm_tasks.db"))
    task = SwarmTask(
        prompt="do the thing",
        type=TaskType.PIPELINE,
        workers=["alpha"],
        status=TaskStatus.PAUSED,
        workspace_id="ws-shipx",
        metadata={"hitl_checkpoints": [1]},
    )
    store.persist_task(task)
    loaded = store.get_task(task.id)
    assert loaded is not None
    assert loaded.workspace_id == "ws-shipx"
    store.close()
