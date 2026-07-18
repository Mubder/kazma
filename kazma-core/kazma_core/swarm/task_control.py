"""Task cancel / retry helpers — extracted from SwarmEngine (S3/S5)."""

from __future__ import annotations

import logging
import threading
from typing import Any

from kazma_core.swarm.task import SwarmTask
from kazma_core.swarm.task_lifecycle import get_task as _hist_get_task

__all__ = ["build_retry_task", "cancel_active_task"]

logger = logging.getLogger(__name__)


def cancel_active_task(
    *,
    task_id: str,
    active_tasks: dict[str, SwarmTask],
    task_handles: dict[str, Any],
    finalize: Any,
) -> bool:
    """Cancel an in-flight task.

    ``finalize`` is a callable matching ``SwarmEngine._finalize_task`` kwargs
    (task=, status=, worker_results=, error=, duration_seconds=).

    Returns True if cancelled, False if not active.
    """
    if task_id not in active_tasks:
        logger.warning("[task_control] cancel: '%s' not in active tasks", task_id)
        return False

    task = active_tasks[task_id]
    handle = task_handles.get(task_id)
    if handle is not None and not handle.done():
        handle.cancel()
        logger.info("[task_control] cancelled asyncio handle for task '%s'", task_id)

    finalize(
        task=task,
        status="cancelled",
        worker_results=[],
        error="Cancelled by user",
        duration_seconds=0.0,
    )
    logger.info("[task_control] task '%s' cancelled", task_id)
    return True


def build_retry_task(
    *,
    task_id: str,
    history: dict[str, SwarmTask],
    active_tasks: dict[str, SwarmTask],
    task_store: Any | None,
    history_lock: threading.Lock | None = None,
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

    new_metadata = dict(original.metadata or {})
    new_metadata["retry_of"] = task_id

    kwargs: dict[str, Any] = {
        "prompt": original.prompt,
        "type": original.type,
        "context": original.context,
        "workers": list(original.workers),
        "timeout": original.timeout,
        "metadata": new_metadata,
    }
    if original.fallback_chain:
        kwargs["fallback_chain"] = list(original.fallback_chain)
    new_task = SwarmTask(**kwargs)
    logger.info("[task_control] retrying task '%s' as '%s'", task_id, new_task.id)
    return new_task
