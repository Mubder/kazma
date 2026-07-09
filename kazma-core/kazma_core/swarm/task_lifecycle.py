"""Task history lifecycle helpers — extracted from SwarmEngine (S3 split).

Thread-safe record / update / trim of the in-memory task history dict.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kazma_core.swarm.task import SwarmTask

logger = logging.getLogger(__name__)

DEFAULT_MAX_HISTORY = 500


def record_task(
    history: dict[str, SwarmTask],
    lock: threading.Lock,
    task: SwarmTask,
    *,
    max_history: int = DEFAULT_MAX_HISTORY,
) -> None:
    """Store a snapshot of *task* and trim history to *max_history* (LRU by insert order)."""
    from kazma_core.swarm.task import SwarmTask as _ST

    with lock:
        history[task.id] = _ST.from_dict(task.to_dict())
        _trim_history(history, max_history)


def update_task(
    history: dict[str, SwarmTask],
    lock: threading.Lock,
    task_id: str,
    mutator: Callable[[SwarmTask], None],
    *,
    max_history: int = DEFAULT_MAX_HISTORY,
) -> SwarmTask | None:
    """Apply *mutator* to a history entry under lock; re-snapshot and return task or None."""
    from kazma_core.swarm.task import SwarmTask as _ST

    with lock:
        task = history.get(task_id)
        if task is None:
            return None
        mutator(task)
        history[task_id] = _ST.from_dict(task.to_dict())
        _trim_history(history, max_history)
        return history[task_id]


def get_task(
    history: dict[str, SwarmTask],
    lock: threading.Lock,
    task_id: str,
) -> SwarmTask | None:
    """Return a task from history under lock."""
    with lock:
        return history.get(task_id)


def _trim_history(history: dict[str, SwarmTask], max_history: int) -> None:
    if max_history <= 0:
        return
    if len(history) > max_history:
        excess = len(history) - max_history
        for old_key in list(history.keys())[:excess]:
            history.pop(old_key, None)
