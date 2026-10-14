"""One per-thread lock for everything that writes a conversation's checkpoint.

Time Travel audit TT-04 (2026-10-14). Restore and fork took no lock and never
asked whether a turn was running; the gateway's per-thread lock covered
gateway turns and commands only, and the web approve route had a lock of its
own. A restore during a running turn answered ``{"ok": true}`` and the
running graph then checkpointed over it -- the restore was silently lost.

Now every checkpoint rewriter takes :func:`thread_mutation` for its thread:
gateway turns and commands (``/reset``, ``/compact``, ``/undo``, ``/edit``,
``/replay``, ``/fork``, ``/abort``), the web abort, the web approve route,
the start of a web turn, and ``kazma_ui.thread_rewind`` (restore and fork on
every surface). ``tests/test_time_travel_rewind.py`` holds every product
function that calls ``aupdate_state`` to it.

The lock is task-reentrant: the gateway handler holds it around a command
and the command's helper takes it again in the same task. A rewriter that
must not wait behind a running turn asks :func:`thread_busy` first and
refuses (409 ``turn_running``).

:func:`rewind_generation` counts the rewinds of a thread. A web turn reads
the checkpoint to build its input before its stream starts; it takes the
lock and compares the count right before it starts the graph, so a restore
landing in between is never overwritten by the turn's older messages.
"""

from __future__ import annotations

import asyncio
import threading
import weakref
from collections import OrderedDict
from typing import Any

__all__ = [
    "MAX_LOCKS",
    "bump_rewind_generation",
    "rewind_generation",
    "thread_busy",
    "thread_mutation",
]

#: Locks kept per event loop; unheld ones beyond this are dropped, oldest first.
MAX_LOCKS = 10_000


class _ThreadMutex(asyncio.Lock):
    """An ``asyncio.Lock`` the task that holds it may take again."""

    def __init__(self) -> None:
        super().__init__()
        self._owner: asyncio.Task[Any] | None = None
        self._depth = 0

    def held_by_current_task(self) -> bool:
        try:
            return self._owner is not None and self._owner is asyncio.current_task()
        except RuntimeError:
            return False

    async def acquire(self) -> bool:  # type: ignore[override]
        task = asyncio.current_task()
        if task is not None and self._owner is task:
            self._depth += 1
            return True
        await super().acquire()
        self._owner = task
        self._depth = 1
        return True

    def release(self) -> None:
        if self._depth > 1:
            self._depth -= 1
            return
        self._owner = None
        self._depth = 0
        super().release()


_lock = threading.Lock()
_by_loop: weakref.WeakKeyDictionary[Any, OrderedDict[str, _ThreadMutex]] = weakref.WeakKeyDictionary()
_generations: dict[str, int] = {}


def thread_mutation(thread_id: str) -> _ThreadMutex:
    """The checkpoint-mutation lock of *thread_id* on the running loop."""
    loop = asyncio.get_running_loop()
    key = str(thread_id or "")
    with _lock:
        table = _by_loop.get(loop)
        if table is None:
            table = OrderedDict()
            _by_loop[loop] = table
        lock = table.get(key)
        if lock is None:
            lock = _ThreadMutex()
            table[key] = lock
            if len(table) > MAX_LOCKS:
                for old in list(table):
                    if len(table) <= MAX_LOCKS:
                        break
                    if old != key and not table[old].locked():
                        table.pop(old, None)
        else:
            table.move_to_end(key)
        return lock


def thread_busy(thread_id: str) -> bool:
    """True when a turn runs on *thread_id*, or another task holds its lock."""
    from kazma_ui.active_turns import is_turn_running

    if is_turn_running(thread_id):
        return True
    lock = thread_mutation(thread_id)
    return lock.locked() and not lock.held_by_current_task()


def rewind_generation(thread_id: str) -> int:
    """How many times *thread_id* was rewound in this process."""
    with _lock:
        return _generations.get(str(thread_id or ""), 0)


def bump_rewind_generation(thread_id: str) -> int:
    """Count one rewind of *thread_id* (called under its mutation lock)."""
    key = str(thread_id or "")
    with _lock:
        _generations[key] = _generations.get(key, 0) + 1
        if len(_generations) > MAX_LOCKS:
            _generations.pop(next(iter(_generations)))
        return _generations[key]
