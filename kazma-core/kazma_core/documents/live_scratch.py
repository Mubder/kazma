"""Scratch folders under the storage root that a running operation still holds.

Garbage collection sweeps a scratch folder (``.stage`` and the like) by its
age: one older than the grace period is taken for a crash's leftover. A long
durable OCR run holds one staged copy of its document for its whole life and
never writes to it, so a scan read for more than the grace period (an hour by
default, about 110 pages) lost its source to a GC pass mid-job: the next
batch could not read it and the job restarted from page 1, possibly into a
dead letter (audit 2026-10-26 AUD-023).

A folder is ``hold``-ed while it is in use and ``release``-d after. Garbage
collection in this process asks ``is_held`` and never takes one; a keeper
thread refreshes each held folder's modification time every
``TOUCH_SECONDS``, so a sweep in ANOTHER process (which cannot see this
registry) still reads it as fresh.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["TOUCH_SECONDS", "hold", "is_held", "release"]

#: How often a held folder's time is refreshed. Far below any sensible GC
#: grace period (the default is an hour).
TOUCH_SECONDS = 60.0

_lock = threading.Lock()
_held: dict[Path, int] = {}
_keeper: threading.Thread | None = None
_wake = threading.Event()


def _key(path: Path) -> Path:
    # Resolved, as garbage collection resolves its storage root: the same
    # folder must compare equal however each side spelled the path.
    return Path(path).resolve()


def hold(path: Path) -> None:
    """Mark *path* in use until :func:`release` (re-entrant: counted)."""
    global _keeper
    key = _key(path)
    with _lock:
        _held[key] = _held.get(key, 0) + 1
        if _keeper is None or not _keeper.is_alive():
            _keeper = threading.Thread(target=_keep, name="documents-live-scratch", daemon=True)
            _keeper.start()
        else:
            _wake.set()  # a running keeper refreshes the new folder now


def release(path: Path) -> None:
    """End one :func:`hold` of *path*."""
    key = _key(path)
    with _lock:
        count = _held.get(key, 0) - 1
        if count > 0:
            _held[key] = count
        else:
            _held.pop(key, None)


def is_held(path: Path) -> bool:
    """True while some operation in this process holds *path*."""
    with _lock:
        return _key(path) in _held


def _keep() -> None:
    """Refresh every held folder's time; exit once nothing is held."""
    global _keeper
    while True:
        _wake.wait(TOUCH_SECONDS)
        _wake.clear()
        with _lock:
            paths = list(_held)
            if not paths:
                _keeper = None
                return
        for path in paths:
            try:
                os.utime(path)
            except FileNotFoundError:
                continue  # released and removed meanwhile
            except OSError as exc:
                logger.warning("[documents.scratch] could not refresh %s: %s", path, exc)
