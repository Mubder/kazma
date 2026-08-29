"""Retained background tasks (audit F-07).

``asyncio`` keeps only a **weak** reference to a running task, so a task whose
only strong reference was the discarded result of ``asyncio.create_task(...)``
can be garbage-collected mid-execution. The failure is silent and
load-dependent: a knowledge-base crawl or an embedding rebuild simply stops,
with no exception, no log line, and a job record frozen at whatever phase it
reached while the UI keeps polling a status that never advances.

Use :func:`spawn_background` instead of a bare ``create_task`` for anything
that outlives the request that started it::

    from kazma_core.background import spawn_background

    spawn_background(_run_crawl(job_id), name=f"kb-crawl:{job_id}")

The helper also fixes a second, quieter problem: a fire-and-forget task that
raises currently logs nothing until interpreter shutdown prints
"Task exception was never retrieved". Here, failures are logged when they
happen, with the task's name.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

__all__ = [
    "spawn_background",
    "background_tasks",
    "drain_background",
]

_T = TypeVar("_T")

#: Strong references to in-flight tasks. Entries remove themselves on
#: completion, so this is a live set and not a leak.
_background: set[asyncio.Task[Any]] = set()


def _on_done(task: asyncio.Task[Any]) -> None:
    _background.discard(task)
    if task.cancelled():
        logger.debug("[bg] %s cancelled", task.get_name())
        return
    exc = task.exception()
    if exc is not None:
        logger.error("[bg] %s failed: %s", task.get_name(), exc, exc_info=exc)


def spawn_background(
    coro: Coroutine[Any, Any, _T],
    *,
    name: str,
) -> asyncio.Task[_T]:
    """Start *coro* as a background task that cannot be garbage-collected.

    Args:
        coro: The coroutine to run. Ownership transfers to the returned task.
        name: Short identifier used in logs, e.g. ``"kb-crawl:<job_id>"``.
            Required — an unnamed background task is unattributable when it
            fails, which is the whole reason this helper exists.

    Returns:
        The created task. Callers that do not need it may discard it safely;
        the module holds its own reference until completion.
    """
    task = asyncio.create_task(coro, name=name)
    _background.add(task)
    task.add_done_callback(_on_done)
    return task


def background_tasks() -> frozenset[asyncio.Task[Any]]:
    """Snapshot of currently in-flight background tasks (diagnostics)."""
    return frozenset(_background)


async def drain_background(timeout: float = 10.0) -> int:
    """Await in-flight background tasks during shutdown.

    Returns the number of tasks that were still running. Never raises — a
    failing task has already been logged by :func:`_on_done`.
    """
    pending = list(_background)
    if not pending:
        return 0
    logger.info("[bg] draining %d background task(s)", len(pending))
    done, still_pending = await asyncio.wait(pending, timeout=timeout)
    for task in still_pending:
        logger.warning("[bg] %s did not finish in %.0fs — cancelling", task.get_name(), timeout)
        task.cancel()
    return len(pending)
