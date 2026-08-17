"""Unit tests for swarm.task_control cancel/retry helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from kazma_core.swarm.task import SwarmTask, TaskType
from kazma_core.swarm.task_control import build_retry_task, cancel_active_task


def _task(tid: str = "t1") -> SwarmTask:
    return SwarmTask(
        id=tid,
        type=TaskType.DISPATCH,
        prompt="do work",
        workers=["w1"],
        metadata={"k": 1},
    )


def test_cancel_not_active():
    assert (
        cancel_active_task(
            task_id="missing",
            active_tasks={},
            task_handles={},
            finalize=MagicMock(),
        )
        is False
    )


def test_cancel_active_live_handle_defers_finalize():
    """With a live asyncio handle, only cancel() fires — finalize is DEFERRED.

    Audit H7: dispatch's CancelledError path finalizes once; calling finalize
    here too double-finalized the task.
    """
    t = _task("a")
    handle = MagicMock()
    handle.done.return_value = False
    finalize = MagicMock()
    ok = cancel_active_task(
        task_id="a",
        active_tasks={"a": t},
        task_handles={"a": handle},
        finalize=finalize,
    )
    assert ok is True
    handle.cancel.assert_called_once()
    finalize.assert_not_called()  # deferred to the CancelledError path


def test_cancel_active_no_handle_finalizes_immediately():
    t = _task("b")
    finalize = MagicMock()
    ok = cancel_active_task(
        task_id="b",
        active_tasks={"b": t},
        task_handles={},
        finalize=finalize,
    )
    assert ok is True
    finalize.assert_called_once()
    assert finalize.call_args.kwargs["status"] == "cancelled"


def test_build_retry_from_history():
    original = _task("old")
    new = build_retry_task(
        task_id="old",
        history={"old": original},
        active_tasks={},
        task_store=None,
    )
    assert new is not None
    assert new.id != "old"
    assert new.prompt == "do work"
    assert new.metadata.get("retry_of") == "old"
    assert new.metadata.get("k") == 1


def test_build_retry_missing():
    assert (
        build_retry_task(
            task_id="x",
            history={},
            active_tasks={},
            task_store=None,
        )
        is None
    )
