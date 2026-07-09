"""Task cancel / retry helpers — extracted from SwarmEngine (S3/S5)."""

from __future__ import annotations

import logging
from typing import Any

from kazma_core.swarm.task import SwarmTask

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
) -> SwarmTask | None:
    """Build a fresh SwarmTask for retry lineage, or None if original missing."""
    original = history.get(task_id)
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
