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

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from kazma_core.spaces.scope import space_named_or_bound

logger = logging.getLogger(__name__)

__all__ = [
    "Entry",
    "append",
    "apply_pending",
    "chat_entries",
    "chat_forget_pending",
    "entry_keys",
    "health",
    "keys_detail",
    "journaled_seqs",
    "mark_applied",
    "pending",
    "queue_remote_delete",
    "recent_chat_forgets",
    "retry_remote_deletes",
    "state",
]

#: ``entry``: every fact filed under a member's entry in a shared space
#: (Spaces S1-11, N36); the entry's id is the ``session_key``.
#: ``mentions``: "forget everything that mentions X" (the memory framework
#: audit's F5, part D): the ids it forgot, never the text (``detail``).
KINDS = ("turn", "chat", "keep_out", "remember", "summary", "rewind", "entry", "mentions")

#: The kinds whose ``detail`` records the chat's keys when it was forgotten
#: (``{"keys": [...]}``): a chat deleted since has no copy in the chat store
#: to name its other key, and a restore brought the memory back under it
#: (the memory framework audit's F11).
_KEYED_KINDS = ("turn", "chat", "keep_out", "remember")

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
    #: A ``rewind``'s questions: JSON ``{turn: question_sha}`` (FA-102).
    detail: str = ""


def _connect() -> sqlite3.Connection:
    from kazma_core.config_store import apply_sqlite_pragmas
    from kazma_core.paths import memory_forget_journal_db

    # Autocommit: every statement is its own transaction (AGENTS.md section 35).
    conn = sqlite3.connect(memory_forget_journal_db(), timeout=30, isolation_level=None,
                           check_same_thread=False)
    apply_sqlite_pragmas(conn)
    for ddl in _SCHEMA:
        conn.execute(ddl)
    from kazma_core.db.sqlite_columns import add_missing_columns

    add_missing_columns(conn, "forget_journal", [("detail", "TEXT NOT NULL DEFAULT ''")])
    return conn


def append(
    kind: str, *, tenant_id: str, session_key: str = "", turn_number: int = 0,
    question_sha: str = "", episode_id: str = "", summary_id: str = "", detail: str = "",
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
            "episode_id, summary_id, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), kind, space_named_or_bound(tenant_id), session_key or "", int(turn_number or 0),
             question_sha or "", episode_id or "", summary_id or "", detail or ""),
        )
        return int(cur.lastrowid)
    finally:
        conn.close()


def keys_detail(keys: list[str]) -> str:
    """The ``detail`` of an entry of :data:`_KEYED_KINDS`: the chat's keys."""
    return json.dumps({"keys": [k for k in dict.fromkeys(keys) if k]})


def entry_keys(e: Entry) -> list[str]:
    """Every key the entry's chat is stored under: those recorded when it was
    forgotten (:func:`keys_detail`), those the chat store knows now, and the
    entry's own. [] for an entry that names no chat."""
    if not e.session_key:
        return []
    recorded: list[str] = []
    if e.kind in _KEYED_KINDS and e.detail:
        try:
            data = json.loads(e.detail)
        except (TypeError, ValueError):
            data = None
        if isinstance(data, dict) and isinstance(data.get("keys"), list):
            recorded = [str(k) for k in data["keys"] if k]
    from kazma_core.memory.forget import chat_keys

    return list(dict.fromkeys([*recorded, *chat_keys(e.session_key), e.session_key]))


def _entries(*, kinds: tuple[str, ...] = KINDS, since: float = 0.0) -> list[Entry]:
    conn = _connect()
    try:
        marks = ",".join("?" for _ in kinds)
        rows = conn.execute(
            "SELECT seq, at, kind, tenant_id, session_key, turn_number, question_sha, episode_id, summary_id, "
            f"detail FROM forget_journal WHERE kind IN ({marks}) AND at >= ? ORDER BY seq",
            (*kinds, float(since)),
        ).fetchall()
    finally:
        conn.close()
    return [Entry(int(r[0]), float(r[1]), str(r[2]), str(r[3]), str(r[4]), int(r[5]), str(r[6]),
                  str(r[7]), str(r[8]), str(r[9] or "")) for r in rows]


def forgotten_summaries(tenant_id: str) -> set[str]:
    """The summaries (weekly or roll-up) forgotten in *tenant_id*, by id, as
    the ledger holds them: a planner asks it before writing one again, so a
    restored database that has lost the tombstone does not bring it back."""
    return {e.summary_id for e in _entries(kinds=("summary",)) if e.tenant_id == tenant_id and e.summary_id}


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
    elif e.kind == "rewind":
        forget.replay_rewind(conn, e)
    elif e.kind == "chat":
        out = forget.forget_chat(e.session_key, tenant_id=e.tenant_id, conn=conn, journal=False,
                                 keys=entry_keys(e))
        if not out.get("ok") and out.get("error") != "chat required":
            raise RuntimeError(f"forget of chat {e.session_key} failed: {out}")
    elif e.kind in ("keep_out", "remember"):
        forget.set_chat_remembered(e.session_key, e.kind == "remember", tenant_id=e.tenant_id,
                                   conn=conn, journal=False, keys=entry_keys(e))
    elif e.kind == "mentions":
        forget.replay_mentions(conn, e)
    elif e.kind == "entry":
        if _still_a_member(e):
            # Journaled before a membership change that did not happen (it
            # was refused, or the store went away), or the person is back:
            # a current member's facts are theirs (the S2-12 review).
            return
        out = forget.forget_entry(e.session_key, tenant_id=e.tenant_id, conn=conn, journal=False)
        if not out.get("ok"):
            raise RuntimeError(f"forget of entry {e.session_key} failed: {out}")
    elif e.kind == "summary":
        from kazma_core.memory.topic_summaries import forget_summary

        out = forget_summary(e.summary_id, tenant_id=e.tenant_id, conn=conn, journal=False)
        if not out.get("ok") and out.get("error") != "not_found":
            raise RuntimeError(f"forget of summary {e.summary_id} failed: {out}")


def _still_a_member(e: Entry) -> bool:
    """Whether the account of the entry *e* forgets is a member of its space
    now. Raises when accounts cannot be read: the entry stays pending."""
    from kazma_core.memory.speakers import account_of_entry
    from kazma_core.spaces import identity
    from kazma_core.spaces.scope import spaces_enforced

    account = account_of_entry(e.session_key)
    if account is None or not spaces_enforced():
        return False
    return identity.is_member(account, e.tenant_id)


#: This process's last failure of each entry still pending: its sequence
#: number -> (the exception's class, when). Memory health reads it; the class
#: only, never the message (it can quote a row).
_FAILURES: dict[int, tuple[str, float]] = {}


def apply_pending(conn: sqlite3.Connection | None = None, *, limit: int = 200) -> dict[str, Any]:
    """Apply, in order, the entries the memory database has not.

    An entry that fails stays pending, and so does every later entry of the
    same chat (order matters there: a keep-out and the "remember" after it);
    the entries of other chats are applied (the memory framework audit's F11:
    one failing entry held every forget behind it, and nothing said so). At
    most *limit* entries are tried a pass; what is left is in
    :func:`health`.
    """
    from kazma_core.memory.forget import _open

    own = conn is None
    conn = conn or _open()
    applied = 0
    failed: list[int] = []
    waiting: set[str] = set()
    try:
        todo = pending(conn)
        tried = 0
        for e in todo:
            if tried >= limit:
                break
            keys = set(entry_keys(e))
            if keys & waiting:
                continue
            tried += 1
            try:
                _apply(conn, e)
                mark_applied(conn, e.seq)
                conn.commit()
                applied += 1
                _FAILURES.pop(e.seq, None)
            except Exception as exc:
                if conn.in_transaction:
                    conn.rollback()
                logger.warning("[forget_journal] entry %d (%s) not applied yet; retried next pass",
                               e.seq, e.kind, exc_info=True)
                _FAILURES[e.seq] = (type(exc).__name__, time.time())
                failed.append(e.seq)
                waiting |= keys
        left = len(todo) - applied
        if applied:
            logger.info("[forget_journal] applied %d forget(s); %d pending", applied, left)
        return {"applied": applied, "pending": left, "failed": failed}
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
            (item_id, space_named_or_bound(tenant_id), time.time()),
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


def health(memory_db: str | None = None) -> dict[str, Any]:
    """For memory health: the forgets the memory database has not applied
    (how many, how old the oldest), the ones that failed in this process, and
    the remote deletes waiting. Read-only on both files. Raises when the
    journal or the memory database cannot be read."""
    import os

    from kazma_core.paths import memory_forget_journal_db, primary_memory_db

    path = memory_forget_journal_db()
    out: dict[str, Any] = {"entries": 0, "pending": 0, "oldest_pending_s": None, "failing": [],
                           "remote_deletes_waiting": 0, "oldest_remote_delete_s": None}
    if not os.path.exists(path):
        return out
    now = time.time()
    jconn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = jconn.execute("SELECT seq, at, kind FROM forget_journal ORDER BY seq").fetchall()
        remote = jconn.execute("SELECT COUNT(*), MIN(added_at) FROM forget_remote_pending").fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return out
        raise
    finally:
        jconn.close()
    out["entries"] = len(rows)
    out["remote_deletes_waiting"] = int(remote[0] or 0)
    if remote[1] is not None:
        out["oldest_remote_delete_s"] = round(now - float(remote[1]), 1)
    mpath = memory_db or str(primary_memory_db())
    applied: set[int] = set()
    if os.path.exists(mpath):
        mconn = sqlite3.connect(f"file:{mpath}?mode=ro", uri=True)
        try:
            applied = {int(r[0]) for r in mconn.execute("SELECT seq FROM memory_forget_applied")}
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
        finally:
            mconn.close()
    waiting = [(int(seq), float(at), str(kind)) for seq, at, kind in rows if int(seq) not in applied]
    out["pending"] = len(waiting)
    if waiting:
        out["oldest_pending_s"] = round(now - min(at for _s, at, _k in waiting), 1)
    out["failing"] = [
        {"seq": seq, "kind": kind, "error": _FAILURES[seq][0], "at": _FAILURES[seq][1]}
        for seq, _at, kind in waiting if seq in _FAILURES
    ]
    return out


def chat_entries(kind: str, *, tenant_id: str, keys: list[str], since: float = 0.0) -> list[Entry]:
    """The entries of *kind* of one chat (under any of *keys*) in one space,
    since *since*. Blocking."""
    return [e for e in _entries(kinds=(kind,), since=since) if e.tenant_id == tenant_id and e.session_key in keys]


def chat_forget_pending(tenant_id: str, keys: list[str]) -> bool:
    """Whether a forget of the chat, under any of *keys*, waits to be
    finished. Blocking."""
    from kazma_core.memory.forget import _open

    conn = _open()
    try:
        return any(e.kind == "chat" and e.tenant_id == tenant_id and e.session_key in keys for e in pending(conn))
    finally:
        conn.close()


def recent_chat_forgets(*, within_s: float = 86400.0) -> list[Entry]:
    """Forgets of a chat's past in the last *within_s* (for the race sweep)."""
    return _entries(kinds=("chat",), since=time.time() - within_s)
