"""A task write the store refuses is said where it happens (roadmap theme 1).

``TaskStore.persist_task`` answers False when its transition table refuses a
write: the row is finished, or a newer run owns it. Widening
``tests/test_failure_results_are_used.py`` to the swarm store found three
callers that dropped that answer:

- the Research page's Archive answered ``{"ok": true}`` over a flag that was
  never recorded;
- a resumed pipeline's step journal was taken for written;
- a pipeline paused without an engine (the checkpoint manager's own store
  write) was taken for on record, so a restart would not offer it.

Each now says so. The old shapes, kept here, are the negative controls.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

from kazma_core.swarm.task import SwarmTask, TaskStatus


class _Refusing:
    """A store whose transition table refuses every write."""

    def __init__(self) -> None:
        self.writes = 0

    def persist_task(self, task) -> bool:
        self.writes += 1
        return False


def _task(**kw) -> SwarmTask:
    return SwarmTask(prompt="p", workers=["w"], **kw)


# ── the Research page's Archive ──────────────────────────────────────────


def test_archive_over_a_refused_write_is_409(monkeypatch):
    from kazma_ui.research_panel import routes

    store = _Refusing()
    monkeypatch.setattr(routes, "_find_task", lambda task_id: (store, _task()))
    resp = routes._set_archived("t-1", True)
    assert resp.status_code == 409 and store.writes == 1
    assert b'"code":"not_recorded"' in resp.body


def test_negative_control_the_old_archive_said_ok():
    def old_set_archived(store, task):  # the route before theme 1
        task.metadata["archived"] = True
        store.persist_task(task)
        return {"ok": True}

    assert old_set_archived(_Refusing(), _task()) == {"ok": True}


# ── a resumed pipeline's step journal ────────────────────────────────────


def test_a_refused_step_journal_is_a_warning_and_counted(monkeypatch, caplog):
    from kazma_core.swarm import engine as engine_mod

    failures: list[str] = []
    monkeypatch.setattr(engine_mod, "record_swarm_failure", failures.append)
    fake = SimpleNamespace(_task_store=_Refusing())
    with caplog.at_level(logging.WARNING, logger="kazma_core.swarm.engine"):
        asyncio.run(engine_mod.SwarmEngine._journal_step(fake, _task(), 2, "begun"))
    assert failures == ["step_journal"]
    assert any("refused the journal of step 2" in r.getMessage() for r in caplog.records)


# ── a pipeline paused without an engine ──────────────────────────────────


def test_a_refused_paused_state_is_a_warning_and_counted(monkeypatch, caplog):
    from kazma_core.swarm import checkpoint_manager as cm
    from kazma_core.swarm.patterns import PatternExecution

    failures: list[str] = []
    monkeypatch.setattr(cm, "record_swarm_failure", failures.append)
    monkeypatch.setattr(cm, "_gate_register_pipeline", lambda *a, **k: None, raising=False)
    store = _Refusing()
    mgr = cm.CheckpointManager(checkpoint_handler=MagicMock(), task_store=store)
    task = _task(status=TaskStatus.RUNNING)
    result = PatternExecution(worker_results=[], status="paused",
                              metadata={"checkpoint": {"step": 1, "worker": "w", "task_id": task.id}})
    with caplog.at_level(logging.WARNING, logger="kazma_core.swarm.checkpoint_manager"):
        mgr.handle_pipeline_checkpoint(task=task, pattern_result=result, started=0.0)
    assert store.writes == 1
    assert "task_write" in failures
    assert any("refused the paused state" in r.getMessage() for r in caplog.records)
