"""Unit tests for swarm.task_lifecycle history helpers."""

from __future__ import annotations

import threading

from kazma_core.swarm.task import SwarmTask, TaskStatus, TaskType
from kazma_core.swarm.task_lifecycle import get_task, record_task, update_task


def _task(tid: str = "t1") -> SwarmTask:
    return SwarmTask(
        id=tid,
        type=TaskType.DISPATCH,
        prompt="do thing",
        workers=["w1"],
    )


def test_record_and_get():
    history: dict = {}
    lock = threading.Lock()
    t = _task("a")
    record_task(history, lock, t, max_history=10)
    got = get_task(history, lock, "a")
    assert got is not None
    assert got.id == "a"
    assert got.prompt == "do thing"


def test_lru_trim():
    history: dict = {}
    lock = threading.Lock()
    for i in range(5):
        record_task(history, lock, _task(f"t{i}"), max_history=3)
    assert len(history) == 3
    # First keys dropped (dict preserves insertion order)
    assert "t0" not in history
    assert "t1" not in history
    assert "t4" in history


def test_update_task():
    history: dict = {}
    lock = threading.Lock()
    record_task(history, lock, _task("x"), max_history=10)

    def mut(task: SwarmTask) -> None:
        task.status = TaskStatus.FAILED

    updated = update_task(history, lock, "x", mut, max_history=10)
    assert updated is not None
    assert updated.status == TaskStatus.FAILED
    assert update_task(history, lock, "missing", mut) is None
