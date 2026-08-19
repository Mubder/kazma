"""Regression tests for the deep-structure audit fixes (2026-08-19).

Guards the behaviors introduced by docs/audits/AUDIT_DEEP_STRUCTURE_2026-08-19.md:
- BROADCAST dispatch finalizes on cancel/timeout instead of leaking as RUNNING.
- reap_stale_tasks does not kill paused HITL checkpoints that have a
  checkpoint_timeout (their lifecycle belongs to the auto-reject).
- reject_checkpoint does not overwrite/persist a second terminal record when
  the task was already terminally finalized.
- Slack chunk_message yields NO chunks for empty text (attachments still go).
- WebDAV TLS verification defaults to ON with an explicit config opt-out.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from kazma_core.swarm import SwarmConfig, SwarmTask, TaskType, WorkerConfig
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.task import TaskStatus
from kazma_core.swarm.task_lifecycle import record_task as _hist_record_task


@pytest.fixture
def empty_config() -> SwarmConfig:
    return SwarmConfig(enabled=True, workers=[])


# ── Fix #4: broadcast cancellation finalizes ────────────────────────────


@pytest.mark.asyncio
async def test_broadcast_cancel_finalizes_as_cancelled(empty_config):
    engine = SwarmEngine(empty_config)
    engine.add_worker(WorkerConfig(name="alpha", type="in_process"))

    release = asyncio.Event()

    async def slow_dispatch(task: str, context: str = "") -> dict[str, str | None]:
        await release.wait()
        return {"worker": "alpha", "status": "success", "output": "late", "error": None}

    engine.get_worker("alpha").dispatch = slow_dispatch  # type: ignore[assignment,union-attr]

    task = SwarmTask(prompt="Broadcast", type=TaskType.BROADCAST)
    outer = asyncio.create_task(engine.dispatch(task))
    await asyncio.sleep(0.05)  # let the broadcast enter the worker await
    outer.cancel()
    release.set()

    try:
        result = await outer
    except asyncio.CancelledError:
        pytest.fail("BROADCAST cancellation leaked out of dispatch() unfinalized")
        return

    assert result.status == "cancelled"
    assert task.id not in engine._active_tasks
    assert task.status == TaskStatus.CANCELLED


# ── Fix #5a: reap skips paused checkpoints with a configured timeout ────


def _stale_paused_task(*, checkpoint_timeout: float | None) -> SwarmTask:
    task = SwarmTask(
        prompt="Paused pipeline",
        type=TaskType.PIPELINE,
        workers=["alpha"],
        timeout=1.0,
    )
    task.status = TaskStatus.PAUSED
    task.started_at = "2020-01-01T00:00:00+00:00"  # clearly stale
    if checkpoint_timeout is not None:
        task.metadata["checkpoint_timeout"] = checkpoint_timeout
    return task


def test_reap_skips_paused_checkpoint_with_timeout(empty_config):
    engine = SwarmEngine(empty_config)
    task = _stale_paused_task(checkpoint_timeout=300.0)
    engine._active_tasks[task.id] = task

    reaped = engine.reap_stale_tasks()

    assert reaped == 0
    assert task.id in engine._active_tasks
    assert task.status == TaskStatus.PAUSED


def test_reap_still_sweeps_paused_checkpoint_without_timeout(empty_config):
    """No checkpoint_timeout configured → the watchdog stays the safety net."""
    engine = SwarmEngine(empty_config)
    task = _stale_paused_task(checkpoint_timeout=None)
    engine._active_tasks[task.id] = task

    reaped = engine.reap_stale_tasks()

    assert reaped == 1
    assert task.id not in engine._active_tasks


# ── Fix #5b: reject does not double-persist a terminal record ───────────


@pytest.mark.asyncio
async def test_reject_checkpoint_skips_duplicate_persist_when_terminal(empty_config):
    from kazma_core.swarm.checkpoint import HITLCheckpoint

    engine = SwarmEngine(empty_config)
    task = SwarmTask(prompt="P", type=TaskType.PIPELINE, workers=["alpha"])
    task.status = TaskStatus.PAUSED
    engine._checkpoint_handler.store_paused_pipeline(
        task=task,
        checkpoint=HITLCheckpoint(
            task_id=task.id, step=1, worker="alpha", output_preview="x"
        ),
        worker_results=[],
        blackboard_data={},
    )
    # Simulate an earlier terminal finalize (reap/cancel) that already popped
    # the task from _active_tasks and recorded a terminal history entry.
    engine._active_tasks.pop(task.id, None)
    task.status = TaskStatus.TIMEOUT
    task.result = MagicMock(name="earlier-terminal-result")
    _hist_record_task(engine._task_history, engine._task_lock, task)

    store = MagicMock()
    engine._task_store = store

    result = await engine.reject_checkpoint(task.id, reason="too late")

    assert result is not None  # handler still resolves the reject
    assert store.persist_task.call_count == 0  # no second terminal persist
    assert engine._task_history[task.id].status == TaskStatus.TIMEOUT


# ── Fix #3: Slack empty text yields no chunks ───────────────────────────


def test_slack_chunk_message_empty_text_yields_no_chunks():
    from kazma_gateway.adapters.slack_send import chunk_message

    assert chunk_message("") == []
    assert chunk_message(None) == []  # type: ignore[arg-type]
    assert chunk_message("hello") == ["hello"]


# ── Fix #7: WebDAV TLS verification defaults to ON ──────────────────────


def test_webdav_tls_verify_defaults_on():
    from kazma_core.backup import cloud_sync as cs

    original = cs._read_config
    try:
        cs._read_config = lambda key, default="": default  # type: ignore[assignment]
        assert cs._webdav_tls_verify() is True

        cs._read_config = lambda key, default="": "false"  # type: ignore[assignment]
        assert cs._webdav_tls_verify() is False

        cs._read_config = lambda key, default="": "1"  # type: ignore[assignment]
        assert cs._webdav_tls_verify() is True
    finally:
        cs._read_config = original  # type: ignore[assignment]


# ── Patch 2 — finding #2: catalog activation never crashes on integrity
#    check errors (vr unbound) ────────────────────────────────────────────


def test_format_skill_activation_survives_integrity_check_error(tmp_path, monkeypatch):
    from pathlib import Path

    from kazma_core.agent_skills import catalog
    from kazma_core.agent_skills.discovery import AgentSkill
    from kazma_core.agent_skills.parser import ParsedSkill

    skill = AgentSkill(
        name="demo",
        description="demo skill",
        location=Path(tmp_path) / "SKILL.md",
        scope="user",
        parsed=ParsedSkill(name="demo", description="demo skill", body="Do things."),
    )

    def _boom(location):
        raise RuntimeError("integrity backend exploded")

    monkeypatch.setattr("kazma_core.agent_skills.integrity.verify_skill", _boom)

    result = catalog.format_skill_activation(skill)  # must not raise

    assert "unsigned — not integrity-verified" in result
    assert "Do things." in result  # fenced body still loads (warn-only path)


# ── Patch 2 — finding #14: no CWD-relative default paths ────────────────


def test_autoscaler_default_templates_path_is_absolute():
    from kazma_core.swarm.autoscaler import _DEFAULT_TEMPLATES_PATH

    assert _DEFAULT_TEMPLATES_PATH.is_absolute()


def test_cron_store_default_db_path_is_absolute():
    from pathlib import Path

    from kazma_core.cron.scheduler import SQLiteCronStore

    assert Path(SQLiteCronStore()._db_path).is_absolute()
    assert SQLiteCronStore()._db_path.endswith("cron.db")
    # Explicit paths are honored verbatim.
    assert SQLiteCronStore("/tmp/custom.db")._db_path == "/tmp/custom.db"
