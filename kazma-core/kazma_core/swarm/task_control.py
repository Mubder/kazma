"""Task cancel / retry helpers — extracted from SwarmEngine (S3/S5)."""

from __future__ import annotations

import logging
import threading
from typing import Any

from kazma_core.swarm.task import SwarmTask, TaskStatus
from kazma_core.swarm.task_lifecycle import get_task as _hist_get_task

__all__ = ["RETRY_EXCLUDED_FIELDS", "RetryNeedsAcknowledgement", "build_retry_task", "cancel_active_task"]

#: SwarmTask fields a retry does not copy, each with why: they describe the
#: original run, not what to run (Swarm audit SW-E08: the retry copied six
#: fields by hand and dropped the workspace, the schema and the aggregation,
#: so a retry ran on the global workspace). Every other field is copied.
RETRY_EXCLUDED_FIELDS: dict[str, str] = {
    "id": "the retry is a new task",
    "status": "the retry starts pending",
    "result": "the original's result",
    "created_at": "the retry's own",
    "started_at": "the original run's",
    "completed_at": "the original run's",
    "generation": "the retry's own runs",
    "run_id": "the original run's",
}

#: Metadata that records how the original run went, not what to run.
_RUN_METADATA = frozenset({
    "execution_outcome", "interrupted_by", "invoked_tools", "orphan_results",
    "step_journal", "recovery_count", "recovery_error", "result_status",
    "effects_uncertain", "hitl_checkpoint", "paused_blackboard", "checkpoint",
    "paused_step", "admission_denied", "duplicate_admission", "retry_acknowledged_unknown",
})


class RetryNeedsAcknowledgement(Exception):
    """The original's outcome is unknown: a retry may repeat what it did."""

    def __init__(self, task_id: str, tools: list[str]) -> None:
        self.task_id = task_id
        self.tools = tools
        super().__init__(
            f"Task '{task_id}' may already have changed things"
            + (f" ({', '.join(tools)})" if tools else "")
            + "; retrying could do it again. Confirm to retry anyway."
        )


def _outcome_unknown(task: SwarmTask) -> tuple[bool, list[str]]:
    """Whether the original's effects are unknown, and the tools it ran."""
    tools: list[str] = []
    metas = [task.metadata or {}]
    if task.result is not None:
        metas.append(task.result.metadata or {})
    unknown = False
    for meta in metas:
        if meta.get("execution_outcome") == "unknown" or meta.get("effects_uncertain"):
            unknown = True
        tools += [t for t in (meta.get("invoked_tools") or []) if t not in tools]
    if task.result is not None and any(
        not getattr(wr, "retry_safe", True) for wr in (task.result.worker_results or [])
    ):
        unknown = True
    return unknown, tools

logger = logging.getLogger(__name__)


def cancel_active_task(
    *,
    task_id: str,
    active_tasks: dict[str, SwarmTask],
    task_handles: dict[str, Any],
    finalize: Any,
    history: dict[str, SwarmTask] | None = None,
    history_lock: threading.Lock | None = None,
) -> bool:
    """Cancel an in-flight task, or a paused one restored after a restart.

    ``finalize`` is a callable matching ``SwarmEngine._finalize_task`` kwargs
    (task=, status=, worker_results=, error=, duration_seconds=).

    When a live asyncio handle exists, only ``handle.cancel()`` is called and
    ``dispatch``'s ``CancelledError`` path finalizes once (audit H7). When no
    live handle exists, finalize immediately.

    A pipeline paused at a checkpoint before a restart comes back in
    ``history`` only (``restore_paused_tasks``), never in ``active_tasks``.
    The panel offers Cancel on every paused task, and on those it used to
    answer "not active". Pass ``history`` so they cancel too.

    Returns True if cancelled, False if not active.
    """
    if task_id not in active_tasks:
        restored = (
            _hist_get_task(history, history_lock or threading.Lock(), task_id)
            if history is not None
            else None
        )
        if restored is not None and restored.status == TaskStatus.PAUSED:
            finalize(
                task=restored,
                status="cancelled",
                worker_results=[],
                error="Cancelled by user",
                duration_seconds=0.0,
            )
            logger.info(
                "[task_control] paused task '%s' cancelled (restored after a restart)",
                task_id,
            )
            return True
        logger.warning("[task_control] cancel: '%s' not in active tasks", task_id)
        return False

    task = active_tasks[task_id]
    handle = task_handles.get(task_id)
    if handle is not None and not handle.done():
        handle.cancel()
        logger.info(
            "[task_control] cancelled asyncio handle for task '%s' "
            "(finalize deferred to CancelledError path)",
            task_id,
        )
        return True

    finalize(
        task=task,
        status="cancelled",
        worker_results=[],
        error="Cancelled by user",
        duration_seconds=0.0,
    )
    logger.info("[task_control] task '%s' cancelled (no live handle)", task_id)
    return True


def build_retry_task(
    *,
    task_id: str,
    history: dict[str, SwarmTask],
    active_tasks: dict[str, SwarmTask],
    task_store: Any | None,
    history_lock: threading.Lock | None = None,
    acknowledge_unknown: bool = False,
) -> SwarmTask | None:
    """Build a fresh SwarmTask for retry lineage, or None if original missing.

    ``history_lock`` should be the same lock guarding all other mutations of
    ``history`` (e.g. ``SwarmEngine._task_lock``) so this read can't race a
    concurrent ``record_task``/``update_task`` call and observe a
    partially-mutated task. Defaults to a throwaway lock for callers (tests)
    that only ever touch ``history`` from one thread.
    """
    original = _hist_get_task(history, history_lock or threading.Lock(), task_id)
    if original is None:
        original = active_tasks.get(task_id)
    if original is None and task_store is not None:
        original = task_store.get_task(task_id)
    if original is None:
        logger.warning("[task_control] retry: '%s' not found", task_id)
        return None

    unknown, tools = _outcome_unknown(original)
    if unknown and not acknowledge_unknown:
        raise RetryNeedsAcknowledgement(task_id, tools)

    new_metadata = {k: v for k, v in (original.metadata or {}).items() if k not in _RUN_METADATA}
    new_metadata["retry_of"] = task_id
    if unknown:
        new_metadata["retry_acknowledged_unknown"] = True

    import copy
    import dataclasses

    kwargs: dict[str, Any] = {
        f.name: copy.deepcopy(getattr(original, f.name))
        for f in dataclasses.fields(SwarmTask)
        if f.name not in RETRY_EXCLUDED_FIELDS
    }
    kwargs["metadata"] = new_metadata
    new_task = SwarmTask(**kwargs)
    logger.info("[task_control] retrying task '%s' as '%s'", task_id, new_task.id)
    return new_task
