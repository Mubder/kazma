"""The forget journal: every forget, in order, outside the memory database.

Memory audits M08 and M10 (2026-10-13). Forget wrote straight into
``memory_state.db`` -- the ledger, the tombstones, then facts, summaries,
mirrors and remote vectors, with a commit in the middle (``invalidate_belief``
commits as it goes). Two things followed:

- **A forget cut short was reported done.** Asking again finished the facts
  only; summaries stayed active, remote vectors and mirror tombstones were
  never retried, and nothing applied the ledger to rows written afterwards.
- **A restore undid every forget since its backup.** The ledger lives in the
  same file as the memories, so yesterday's ``memory_state.db`` brought back
  every turn forgotten since, and turn reconcile rebuilt the rest from the
  chat store.

So each forget -- a turn, a chat's past, a keep-out, letting a chat back in, a
weekly summary -- is first appended here, in its own file
(``paths.memory_forget_journal_db``; backed up with the data directory, never
with the memory database's own backups). The memory database records which
entries it has applied (``memory_forget_applied``, inside it on purpose: an
older copy lacks the marks of later forgets). :func:`apply_pending` applies,
in order, every entry the database in place has not -- at boot, and on the
15-minute maintenance cadence ("forget convergence"), which also retries remote
deletes and applies the ledger to rows a race let through
(:func:`forget.enforce_ledger`). An entry is applied when its local steps are
done; a remote delete that fails waits in :func:`retry_remote_deletes`.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "Entry",
    "append",
    "apply_pending",
    "journaled_seqs",
    "mark_applied",
    "pending",
    "queue_remote_delete",
    "recent_chat_forgets",
    "retry_remote_deletes",
    "state",
]

KINDS = ("turn", "chat", "keep_out", "remember", "summary")

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS forget_journal (
      seq           INTEGER PRIMARY KEY AUTOINCREMENT,
      at            REAL NOT NULL,
      kind          TEXT NOT NULL,
      tenant_id     TEXT NOT NULL DEFAULT 'default',
      session_key   TEXT NOT NULL DEFAULT '',
      turn_number   INTEGER NOT NULL DEFAULT 0,
      question_sha  TEXT NOT NULL DEFAULT '',
      episode_id    TEXT NOT NULL DEFAULT '',
      summary_id    TEXT NOT NULL DEFAULT ''
    )""",
    # A remote vector a forget could not delete (the index unreachable):
    # retried every pass until it is gone. Here, not in the memory database,
    # so a restore does not lose the retry either.
    """CREATE TABLE IF NOT EXISTS forget_remote_pending (
      item_id    TEXT PRIMARY KEY,
      tenant_id  TEXT NOT NULL DEFAULT 'default',
      added_at   REAL NOT NULL,
      attempts   INTEGER NOT NULL DEFAULT 0
    )""",
)


@dataclass(frozen=True, slots=True)
class Entry:
    seq: int
    at: float
    kind: str
    tenant_id: str
    session_key: str
    turn_number: int
    question_sha: str
    episode_id: str
    summary_id: str


def _connect() -> sqlite3.Connection:
    from kazma_core.config_store import apply_sqlite_pragmas
    from kazma_core.paths import memory_forget_journal_db

    # Autocommit: every statement is its own transaction (AGENTS.md section 35).
    conn = sqlite3.connect(memory_forget_journal_db(), timeout=30, isolation_level=None,
                           check_same_thread=False)
    apply_sqlite_pragmas(conn)
    for ddl in _SCHEMA:
        conn.execute(ddl)
    return conn


def append(
    kind: str, *, tenant_id: str, session_key: str = "", turn_number: int = 0,
    question_sha: str = "", episode_id: str = "", summary_id: str = "",
) -> int:
    """Record a forget before it changes anything; its sequence number.

    Raises when it cannot: a forget that starts without its entry is one a
    restore can undo."""
    if kind not in KINDS:
        raise ValueError(f"unknown forget kind {kind!r}")
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO forget_journal (at, kind, tenant_id, session_key, turn_number, question_sha, "
            "episode_id, summary_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), kind, tenant_id or "default", session_key or "", int(turn_number or 0),
             question_sha or "", episode_id or "", summary_id or ""),
        )
        return int(cur.lastrowid)
    finally:
        conn.close()


def _entries(*, kinds: tuple[str, ...] = KINDS, since: float = 0.0) -> list[Entry]:
    conn = _connect()
    try:
        marks = ",".join("?" for _ in kinds)
        rows = conn.execute(
            "SELECT seq, at, kind, tenant_id, session_key, turn_number, question_sha, episode_id, summary_id "
            f"FROM forget_journal WHERE kind IN ({marks}) AND at >= ? ORDER BY seq",
            (*kinds, float(since)),
        ).fetchall()
    finally:
        conn.close()
    return [Entry(int(r[0]), float(r[1]), str(r[2]), str(r[3]), str(r[4]), int(r[5]), str(r[6]),
                  str(r[7]), str(r[8])) for r in rows]


def journaled_seqs() -> list[int]:
    """Every entry's sequence number, read without writing (the restore
    drill: it never writes into the live data directory). Empty when there
    is no journal yet."""
    import os

    from kazma_core.paths import memory_forget_journal_db

    path = memory_forget_journal_db()
    if not os.path.exists(path):
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [int(r[0]) for r in conn.execute("SELECT seq FROM forget_journal ORDER BY seq")]
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise
    finally:
        conn.close()


def mark_applied(conn: sqlite3.Connection, seq: int | None) -> None:
    """Record in the memory database that it holds *seq*'s effect (the
    caller commits)."""
    if seq is None:
        return
    conn.execute("INSERT OR IGNORE INTO memory_forget_applied (seq, applied_at) VALUES (?, ?)",
                 (int(seq), time.time()))


def pending(conn: sqlite3.Connection) -> list[Entry]:
    """The entries the memory database *conn* has not applied, in order."""
    applied = {int(r[0]) for r in conn.execute("SELECT seq FROM memory_forget_applied")}
    return [e for e in _entries() if e.seq not in applied]


def _apply(conn: sqlite3.Connection, e: Entry) -> None:
    """One entry's local effect on *conn* (idempotent); raises when it fails."""
    from kazma_core.memory import forget

    if e.kind == "turn":
        forget.replay_turn(conn, e)
    elif e.kind == "chat":
        out = forget.forget_chat(e.session_key, tenant_id=e.tenant_id, conn=conn, journal=False)
        if not out.get("ok") and out.get("error") != "chat required":
            raise RuntimeError(f"forget of chat {e.session_key} failed: {out}")
    elif e.kind in ("keep_out", "remember"):
        forget.set_chat_remembered(e.session_key, e.kind == "remember", tenant_id=e.tenant_id,
                                   conn=conn, journal=False)
    elif e.kind == "summary":
        from kazma_core.memory.topic_summaries import forget_summary

        out = forget_summary(e.summary_id, tenant_id=e.tenant_id, conn=conn, journal=False)
        if not out.get("ok") and out.get("error") != "not_found":
            raise RuntimeError(f"forget of summary {e.summary_id} failed: {out}")


def apply_pending(conn: sqlite3.Connection | None = None, *, limit: int = 200) -> dict[str, Any]:
    """Apply, in order, the entries the memory database has not; stop at the
    first that fails (order matters: a keep-out and the "remember" after it).
    """
    from kazma_core.memory.forget import _open

    own = conn is None
    conn = conn or _open()
    applied = 0
    try:
        todo = pending(conn)
        for e in todo[:limit]:
            try:
                _apply(conn, e)
                mark_applied(conn, e.seq)
                conn.commit()
                applied += 1
            except Exception:
                if conn.in_transaction:
                    conn.rollback()
                logger.warning("[forget_journal] entry %d (%s) not applied yet; retried next pass",
                               e.seq, e.kind, exc_info=True)
                break
        left = len(todo) - applied
        if applied:
            logger.info("[forget_journal] applied %d forget(s); %d pending", applied, left)
        return {"applied": applied, "pending": left}
    finally:
        if own:
            conn.close()


def queue_remote_delete(item_id: str, tenant_id: str) -> None:
    """A remote vector a forget could not delete: retried every pass."""
    try:
        conn = _connect()
    except sqlite3.Error:
        logger.warning("[forget_journal] cannot record remote delete of %s", item_id, exc_info=True)
        return
    try:
        conn.execute(
            "INSERT OR IGNORE INTO forget_remote_pending (item_id, tenant_id, added_at) VALUES (?, ?, ?)",
            (item_id, tenant_id or "default", time.time()),
        )
    finally:
        conn.close()


def retry_remote_deletes(memory_conn: sqlite3.Connection, *, limit: int = 200) -> dict[str, int]:
    """Delete from the remote vector index what a forget could not."""
    from kazma_core.memory.backends import get_remote_vector_backend

    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT item_id, tenant_id FROM forget_remote_pending ORDER BY added_at LIMIT ?", (int(limit),)
        ).fetchall()
        if not rows:
            return {"deleted": 0, "pending": 0}
        del memory_conn
        backend = get_remote_vector_backend()
        if backend is None:
            # No remote index any more: nothing holds these vectors.
            conn.execute("DELETE FROM forget_remote_pending")
            return {"deleted": 0, "pending": 0}
        deleted = 0
        for item_id, tenant_id in rows:
            if backend.delete(item_id, tenant_id=tenant_id):
                conn.execute("DELETE FROM forget_remote_pending WHERE item_id = ?", (item_id,))
                deleted += 1
            else:
                conn.execute("UPDATE forget_remote_pending SET attempts = attempts + 1 WHERE item_id = ?",
                             (item_id,))
        left = conn.execute("SELECT COUNT(*) FROM forget_remote_pending").fetchone()[0]
        if left:
            logger.warning("[forget_journal] %d remote vector(s) of forgotten memories still to delete", left)
        return {"deleted": deleted, "pending": int(left)}
    finally:
        conn.close()


def state() -> dict[str, Any]:
    """For memory health: entries, and remote deletes waiting."""
    conn = _connect()
    try:
        return {
            "entries": int(conn.execute("SELECT COUNT(*) FROM forget_journal").fetchone()[0]),
            "remote_deletes_waiting": int(conn.execute(
                "SELECT COUNT(*) FROM forget_remote_pending").fetchone()[0]),
        }
    finally:
        conn.close()


def recent_chat_forgets(*, within_s: float = 86400.0) -> list[Entry]:
    """Forgets of a chat's past in the last *within_s* (for the race sweep)."""
    return _entries(kinds=("chat",), since=time.time() - within_s)
