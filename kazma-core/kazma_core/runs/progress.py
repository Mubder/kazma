"""When a run last made progress, for kinds whose store does not keep it.

A swarm task's row changes only at its start and its end; the work between is
seen as engine events (worker started, step finished, handoff). The engine's
event bridge stamps them here (``note``), and the swarm adapter reads the
latest (``last``). In memory, bounded, per process: a restarted process has
no stamp, and the adapter falls back to the row's own times.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict

__all__ = ["forget", "last", "note", "reset"]

_MAX = 4096
_stamps: OrderedDict[str, tuple[float, str]] = OrderedDict()
_lock = threading.Lock()


def note(run_id: str, what: str = "", *, now: float | None = None) -> None:
    """Record that *run_id* made progress (*what*: a short note, no content)."""
    if not run_id:
        return
    stamp = time.time() if now is None else now
    with _lock:
        _stamps[run_id] = (stamp, str(what)[:80])
        _stamps.move_to_end(run_id)
        while len(_stamps) > _MAX:
            _stamps.popitem(last=False)


def last(run_id: str) -> tuple[float, str] | None:
    """``(epoch, note)`` of *run_id*'s latest progress, or None."""
    with _lock:
        return _stamps.get(run_id)


def forget(run_id: str) -> None:
    with _lock:
        _stamps.pop(run_id, None)


def reset() -> None:
    """Drop every stamp (tests)."""
    with _lock:
        _stamps.clear()
