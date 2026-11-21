"""Cursors and budgets for the memory sweeps (the memory framework audit's
F15, part H).

Every 15 minutes the maintenance runner runs the memory sweeps one after
another in one lane. Measured on a private store at one and four times the
live size (2026-11-21, SQLite VM steps of a pass with nothing to do): every
memory sweep's idle pass grew four times with the store -- each read every
row of every space to learn there was nothing to do.

* **A cursor**: one counter of the changes a sweep can act on
  (``memory_change_seq``, kept by triggers on episodes, facts and entities,
  in the change's own transaction: a new row, a deleted one, a change to a
  column a sweep reads). A sweep whose work only appears when those rows
  change records the counter when a pass finds nothing to do
  (``memory_sweep_cursors``), and its next pass is skipped while the counter
  has not moved (:func:`idle_skip`; a vector repair's cursor also names the
  embedding model, so a model switch is work). A sweep whose work comes from
  elsewhere -- the clock (weekly summaries, macro sleep), the chat store
  (turn reconcile), the forget journal, a Postgres or Neo4j copy -- keeps
  its own cursor and is declared with it (:data:`SWEEPS`).
* **A budget**: each memory sweep declares how long a pass may take
  (:data:`SWEEPS`); the runner times every pass, keeps the time for memory
  health (``sweep_health``) and says, at WARNING, a pass over its budget.

The counter and the cursors live in ``memory_state.db`` beside the rows they
describe: a restored older copy carries its own.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["SWEEPS", "Sweep", "ensure_cursor_schema", "idle_skip"]


@dataclass(frozen=True)
class Sweep:
    """A memory sweep's budget (seconds a pass) and how it resumes."""

    seconds: float
    #: ``changes`` (skipped while nothing it reads changed: :func:`idle_skip`)
    #: or ``own`` (its own cursor, said in *how*).
    cursor: str
    how: str


#: Every memory sweep of the 15-minute maintenance runner, by its label
#: (``worker_bootstrap._MAINTENANCE_SWEEPS``). ``tests/test_memory_sweep_budgets.py``
#: holds the runner's memory sweeps to this table both ways.
SWEEPS: dict[str, Sweep] = {
    "forget convergence": Sweep(
        30.0, "own", "the forget journal's sequence (memory_forget_applied): only entries not applied are "
        "applied; the ledger is checked against the stored rows every pass (forget must converge, and it "
        "holds a time window and remote retries: never skipped)"),
    "memory vector repair": Sweep(
        25.0, "changes", "skipped while no memory row changed and the embedding model is the same"),
    "memory vector codes": Sweep(
        35.0, "changes", "skipped while no memory row changed; a pass makes at most FILL_PER_PASS codes"),
    "memory recovery": Sweep(
        30.0, "changes", "skipped while no memory row changed once every erased row has its verdict"),
    "retired knowledge promotions": Sweep(5.0, "changes", "skipped while no memory row changed"),
    "memory turn reconcile": Sweep(
        65.0, "own", "the chat store's rowid (memory_reconcile_state): only chats changed since"),
    "memory text fill": Sweep(65.0, "changes", "skipped while no memory row changed; a row is marked once"),
    "memory mirror sync": Sweep(
        60.0, "own", "the mirror outbox (memory_mirror_outbox); the full comparison once a day"),
    "memory graph sync": Sweep(
        125.0, "own", "the graph outbox (memory_graph_outbox); the full comparison once a day"),
    "memory topic summaries": Sweep(
        10.0, "changes", "skipped within the hour while no memory row or summary changed (weeks end, and "
        "lost rebuilds are queued again, by the clock); two weeks a pass; queues only"),
    "entity count repair": Sweep(10.0, "changes", "skipped while no memory row changed; 500 a pass"),
}

#: The columns a sweep reads, per table: a change to one is a change.
_WATCHED: dict[str, tuple[str, ...]] = {
    "episodes": ("tenant_id", "tier", "user_text", "assistant_text", "summary_text", "metadata_json",
                 "embedding", "embedding_model_version"),
    "beliefs": ("tenant_id", "subject", "predicate", "object", "valid_until", "invalidated_at",
                "metadata_json", "embedding", "embedding_model_version", "extraction_method"),
    "entities": ("tenant_id", "belief_count", "graph_degree", "metadata_json", "embedding",
                 "embedding_model_version"),
    "memory_summaries": ("tenant_id", "status", "summary_text", "level", "queued_at"),
}


def ensure_cursor_schema(conn: Any) -> None:
    """The change counter, its triggers and the sweeps' cursors."""
    conn.execute("CREATE TABLE IF NOT EXISTS memory_change_seq (name TEXT PRIMARY KEY, seq INTEGER NOT NULL)")
    # Read first: a GET that opens the schema writes nothing (memory routes'
    # read-only gate); the row is made once.
    if not conn.execute("SELECT 1 FROM memory_change_seq WHERE name = 'rows'").fetchall():
        conn.execute("INSERT OR IGNORE INTO memory_change_seq (name, seq) VALUES ('rows', 0)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memory_sweep_cursors ("
        " name TEXT PRIMARY KEY, seq INTEGER NOT NULL, extra TEXT NOT NULL DEFAULT '', at REAL NOT NULL)")
    bump = "UPDATE memory_change_seq SET seq = seq + 1 WHERE name = 'rows';"
    for table, columns in _WATCHED.items():
        changed = " OR ".join(f"old.{c} IS NOT new.{c}" for c in columns)
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS kazma_change_seq_ai_{table} AFTER INSERT ON {table} "
                     f"BEGIN {bump} END")
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS kazma_change_seq_ad_{table} AFTER DELETE ON {table} "
                     f"BEGIN {bump} END")
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS kazma_change_seq_au_{table} "
                     f"AFTER UPDATE OF {', '.join(columns)} ON {table} WHEN {changed} BEGIN {bump} END")


@dataclass
class _Cursor:
    skip: bool
    seq: int | None
    #: Set by the sweep: True when the pass found nothing to do.
    idle: bool = False


def _seq(conn: sqlite3.Connection) -> int | None:
    try:
        rows = conn.execute("SELECT seq FROM memory_change_seq WHERE name = 'rows'").fetchall()
    except sqlite3.OperationalError:
        return None  # a database from before the counter
    return int(rows[0][0]) if rows else None


@contextlib.contextmanager
def idle_skip(name: str, *, extra: str = "", db: str | None = None) -> Iterator[_Cursor]:
    """Run the sweep *name* only when what it reads changed since it last
    found nothing to do. Yields a cursor: ``skip`` True means return at once;
    otherwise set ``idle`` when the pass found nothing, and the counter as it
    was when the pass began is kept (a change during the pass runs it
    again). *extra* is part of the cursor (the embedding model).
    Blocking; a store that cannot be read runs the sweep."""
    from pathlib import Path

    from kazma_core.config_store import apply_sqlite_pragmas
    from kazma_core.paths import primary_memory_db

    path = db or primary_memory_db()
    if not path or not Path(path).is_file():
        yield _Cursor(skip=False, seq=None)
        return
    conn = sqlite3.connect(path, timeout=15)
    try:
        apply_sqlite_pragmas(conn, busy_timeout=15000)
        seq = _seq(conn)
        held = None
        if seq is not None:
            try:
                found = conn.execute("SELECT seq, extra FROM memory_sweep_cursors WHERE name = ?",
                                     (name,)).fetchall()
                held = found[0] if found else None
            except sqlite3.OperationalError:
                held = None
        cursor = _Cursor(skip=bool(held is not None and int(held[0]) == seq and str(held[1]) == extra),
                         seq=seq)
        yield cursor
        if cursor.skip or seq is None:
            return
        if cursor.idle:
            conn.execute("INSERT OR REPLACE INTO memory_sweep_cursors (name, seq, extra, at) VALUES (?, ?, ?, ?)",
                         (name, seq, extra, time.time()))
        else:
            conn.execute("DELETE FROM memory_sweep_cursors WHERE name = ?", (name,))
        conn.commit()
    finally:
        conn.close()
