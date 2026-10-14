"""Every copy of a live SQLite database is one step, through one helper
(Memory audit M19, MEM-L06).

The native memory backup -- the declared restore source -- copied with
``backup(pages=100, sleep=0.01)``. SQLite restarts a batched copy whenever
another connection writes the source, so under continuous writes it finished
only in a quiet window (778 restarts in two seconds measured). The universal
backup had the same failure on 2026-09-13 and was fixed alone. Now
``kazma_core.db.sqlite_snapshot.snapshot_copy`` is the one copier.
"""

from __future__ import annotations

import ast
import os
import sqlite3
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_PRODUCT = ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
            "kazma-cli/kazma_cli", "kazma-tui/kazma_tui", "kazma-skills/kazma_skills", "scripts")


def _busy_source(tmp_path: Path) -> tuple[Path, threading.Event, threading.Thread]:
    src = tmp_path / "memory_state.db"
    c = sqlite3.connect(str(src))
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v BLOB)")
    c.executemany("INSERT INTO t(v) VALUES (?)", [(os.urandom(3000),) for _ in range(2000)])
    c.commit()
    c.close()
    stop = threading.Event()

    def writer():
        w = sqlite3.connect(str(src), timeout=5)
        while not stop.is_set():
            w.execute("INSERT INTO t(v) VALUES (?)", (b"x" * 100,))
            w.commit()
            time.sleep(0.002)
        w.close()

    th = threading.Thread(target=writer, daemon=True)
    th.start()
    time.sleep(0.2)
    return src, stop, th


def test_the_native_memory_backup_finishes_while_the_source_is_written(tmp_path):
    from kazma_core.memory.backup import backup_one

    src, stop, th = _busy_source(tmp_path)
    try:
        started = time.monotonic()
        assert backup_one(src, tmp_path / "copy.db")
        took = time.monotonic() - started
    finally:
        stop.set()
        th.join(5)
    assert took < 1.0, took
    assert sqlite3.connect(str(tmp_path / "copy.db")).execute("SELECT COUNT(*) FROM t").fetchone()[0] >= 2000


def test_the_batched_copy_waited_for_the_writes_to_stop(tmp_path):
    """Negative control: pages=100 finishes only once the writer stops (2 s).

    Another connection writes after EVERY batch step (the progress callback),
    so each step finds the source changed and the copy restarts -- certain,
    not a race with the background writer's timing (it finished in 0.78 s on
    a run where that writer slept through the gaps)."""
    src, stop, th = _busy_source(tmp_path)
    threading.Timer(2.0, stop.set).start()
    try:
        started = time.monotonic()
        s, d = sqlite3.connect(str(src)), sqlite3.connect(str(tmp_path / "batched.db"))
        w = sqlite3.connect(str(src), timeout=5)

        def write_between_steps(status, remaining, total):
            if not stop.is_set():
                w.execute("INSERT INTO t(v) VALUES (?)", (b"y" * 100,))
                w.commit()

        try:
            s.backup(d, pages=100, progress=write_between_steps, sleep=0.01)
        finally:
            w.close()
            s.close()
            d.close()
        took = time.monotonic() - started
    finally:
        stop.set()
        th.join(5)
    assert took >= 1.9, took


def _copies(source: str) -> list[int]:
    """Lines calling a connection's ``.backup(dest, ...)`` in a module that uses sqlite3."""
    tree = ast.parse(source)
    if "sqlite3" not in source:
        return []
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "backup" and (node.args or any(k.arg == "pages" for k in node.keywords))]


def test_every_copier_goes_through_the_helper():
    offenders = []
    for root in _PRODUCT:
        for path in sorted((REPO / root).rglob("*.py")):
            if path.name == "sqlite_snapshot.py" or "__pycache__" in path.parts:
                continue
            offenders += [f"{path.relative_to(REPO).as_posix()}:{n}"
                          for n in _copies(path.read_text(encoding="utf-8"))]
    assert offenders == [], offenders
    planted = "import sqlite3\ndef copy(s, d):\n    s.backup(d, pages=100, sleep=0.01)\n"
    assert _copies(planted) == [3]  # negative control
