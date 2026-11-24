"""The swarm tasks a chat started, for a chat that leaves its space (Spaces
S2-12): one that is not over keeps the chat where it is, and the finished
ones its maker started in a shared space are removed there with it.

Blocking (the task store): call it off the event loop, in the chat's space.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from kazma_core.swarm.task import SwarmTask, is_terminal
from kazma_core.swarm.task_store import TaskStore

__all__ = ["forget_finished_tasks", "open_task_of_chat"]

_PAGE = 100


@contextmanager
def _store() -> Iterator[TaskStore | None]:
    """The running engine's task store, else one opened for this call; None
    when the install has none (no task was ever stored)."""
    from kazma_core import paths
    from kazma_core.db.pg_helpers import use_postgres
    from kazma_core.swarm.engine import peek_task_store

    running = peek_task_store()
    if running is not None:
        yield running
    elif not use_postgres() and not Path(paths.swarm_tasks_db()).is_file():
        yield None
    else:
        store = TaskStore()
        try:
            yield store
        finally:
            store.close()


def _tasks_of(store: TaskStore, thread_id: str) -> list[SwarmTask]:
    """Every stored task the chat *thread_id* started in the bound space."""
    from kazma_core.spaces.scope import require_space_id

    wanted = {"source_thread_id": thread_id, "tenant_id": require_space_id()}
    found: list[SwarmTask] = []
    page = 1
    while True:
        batch = store.list_tasks(page=page, page_size=_PAGE, metadata_filter=wanted)
        assert isinstance(batch, list)
        found.extend(batch)
        if len(batch) < _PAGE:
            return found
        page += 1


def open_task_of_chat(thread_id: str) -> str:
    """The id of a task the chat started that is not over (waiting, running,
    paused), or ``""``."""
    with _store() as store:
        for task in _tasks_of(store, thread_id) if store is not None else ():
            if not is_terminal(task.status):
                return task.id
    return ""


def forget_finished_tasks(thread_id: str, asker: str) -> int:
    """Remove the finished tasks *asker* started from the chat. Returns how
    many. A task still open is never removed."""
    from kazma_core.swarm.engine import get_swarm_engine

    removed = 0
    with _store() as store:
        for task in _tasks_of(store, thread_id) if store is not None else ():
            if not is_terminal(task.status) or str((task.metadata or {}).get("asker") or "") != asker:
                continue
            removed += int(store.delete_task(task.id))
            engine = get_swarm_engine()
            if engine is not None:
                engine.forget_task(task.id)
    return removed
