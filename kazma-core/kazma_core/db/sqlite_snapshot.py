"""The one way to copy a live SQLite database: a single-step online backup.

SQLite's Online Backup API restarts a batched copy (``pages=N``) every time
another connection writes the source, so under continuous writes it never
finishes -- the universal backup lost two thirds of its data that way on
2026-09-13, and the native memory backup (the declared restore source) did
the same until 2026-10-13 (Memory audit M19: 778 restarts in two seconds).
One step takes a single read transaction with no restart window; in WAL mode
readers do not block writers, so the cost is a longer-held read lock.

``tests/test_sqlite_snapshot.py`` holds every copier to this helper.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

__all__ = ["snapshot_copy"]


def snapshot_copy(
    src: str | Path, dest: str | Path, *, timeout_s: float = 30.0, read_only: bool = False,
) -> None:
    """Copy the database *src* into *dest* in one step; raises on failure.

    *read_only* opens the source with ``mode=ro`` (a store this process must
    not write). ``closing()``, never a bare ``with sqlite3.connect()``: that
    commits and does not close, and on Windows an open file cannot be renamed
    or removed.
    """
    if read_only:
        source = sqlite3.connect(Path(src).resolve().as_uri() + "?mode=ro", uri=True, timeout=timeout_s)
    else:
        source = sqlite3.connect(str(src), timeout=timeout_s)
    with closing(source) as s, closing(sqlite3.connect(str(dest), timeout=timeout_s)) as d:
        s.backup(d)  # pages=-1: the whole database, one read transaction
        d.commit()
