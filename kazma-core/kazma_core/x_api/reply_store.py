"""Durable state for X auto-replies: idempotency and per-target caps.

Two jobs the post ledger cannot do:

**Idempotency.** The mentions poller restarts. Without a record of which
summons have been handled, a restart re-reads the same mention window and
replies again — and :class:`XClient` deliberately never retries writes,
precisely so that a double-post can only ever come from a caller like this
one. Every summon is claimed here BEFORE any draft or publish, so a crash
between draft and post leaves a row in ``drafting`` rather than a silent
re-run.

**Per-target caps.** ``x_api.policy`` caps total posts per day/month and
blocks duplicates. It does not know that four of today's eight posts were
replies aimed at the same account. Replying repeatedly to one person is what
gets reported as targeted harassment regardless of how mild each reply is,
so the count belongs here, keyed by target.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas

logger = logging.getLogger(__name__)

__all__ = [
    "ReplyRecord",
    "XReplyStore",
    "get_reply_store",
    "reset_reply_store",
    "STATUS_DRAFTING",
    "STATUS_AWAITING",
    "STATUS_POSTED",
    "STATUS_SKIPPED",
    "STATUS_FAILED",
]

STATUS_DRAFTING = "drafting"
STATUS_AWAITING = "awaiting_approval"
STATUS_POSTED = "posted"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

_CREATE = """
CREATE TABLE IF NOT EXISTS x_replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summon_id TEXT NOT NULL UNIQUE,
    parent_id TEXT NOT NULL DEFAULT '',
    target_handle TEXT NOT NULL DEFAULT '',
    summoner TEXT NOT NULL DEFAULT '',
    subject_id TEXT NOT NULL DEFAULT '',
    -- The other half of the conversation. Without these the log shows
    -- Kazma talking to itself: a handle, a draft, and no idea what was
    -- said to provoke it. Stored at claim time because the poller has
    -- them in hand and re-fetching later costs read quota (and on a
    -- deleted tweet is impossible).
    parent_text TEXT NOT NULL DEFAULT '',
    summon_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'drafting',
    draft_text TEXT NOT NULL DEFAULT '',
    proposal_id TEXT NOT NULL DEFAULT '',
    tweet_id TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_x_replies_status ON x_replies(status, created_at);
CREATE INDEX IF NOT EXISTS idx_x_replies_target ON x_replies(target_handle, created_at);
CREATE INDEX IF NOT EXISTS idx_x_replies_parent ON x_replies(parent_id, created_at);

-- The mentions cursor. One row; X's since_id is monotonic per account.
CREATE TABLE IF NOT EXISTS x_mentions_cursor (
    k TEXT PRIMARY KEY,
    since_id TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS x_closed_threads (
    conversation_id TEXT PRIMARY KEY,
    closed_at REAL NOT NULL,
    closed_by TEXT NOT NULL DEFAULT ''
);
"""


class ReplyRecord:
    """Read-only view of one row."""

    def __init__(self, row: sqlite3.Row) -> None:
        self.id = int(row["id"])
        self.summon_id = str(row["summon_id"])
        self.parent_id = str(row["parent_id"])
        self.target_handle = str(row["target_handle"])
        self.summoner = str(row["summoner"])
        self.subject_id = str(row["subject_id"])
        self.parent_text = str(row["parent_text"] or "")
        self.summon_text = str(row["summon_text"] or "")
        self.status = str(row["status"])
        self.draft_text = str(row["draft_text"])
        self.proposal_id = str(row["proposal_id"])
        self.tweet_id = str(row["tweet_id"])
        self.reason = str(row["reason"])
        self.tenant_id = str(row["tenant_id"])
        self.created_at = float(row["created_at"])
        self.updated_at = float(row["updated_at"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "summon_id": self.summon_id,
            "parent_id": self.parent_id,
            "target": self.target_handle,
            "summoner": self.summoner,
            "subject": self.subject_id,
            "parent_text": self.parent_text,
            "summon_text": self.summon_text,
            "status": self.status,
            "draft": self.draft_text,
            "proposal_id": self.proposal_id,
            "tweet_id": self.tweet_id,
            "reason": self.reason,
            "created_at": self.created_at,
        }


class XReplyStore:
    """SQLite WAL store for auto-reply state."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from kazma_core.paths import data_dir

            db_path = data_dir() / "x_replies.db"
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=5.0)
        apply_sqlite_pragmas(conn)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(_CREATE)
                # Additive migration for stores created before the
                # conversation log existed. CREATE TABLE IF NOT EXISTS is
                # a no-op on an existing table, so new columns need this
                # or an upgraded install silently keeps the old shape.
                have = {
                    str(r[1])
                    for r in conn.execute('PRAGMA table_info(x_replies)')
                }
                for col in ('parent_text', 'summon_text'):
                    if col not in have:
                        conn.execute(
                            f'ALTER TABLE x_replies ADD COLUMN {col} '
                            "TEXT NOT NULL DEFAULT ''"
                        )
                        logger.info('[x-reply] added column %s', col)
                conn.commit()
            finally:
                conn.close()

    # ── Claim / idempotency ───────────────────────────────────────────

    def claim(
        self,
        *,
        summon_id: str,
        parent_id: str,
        target_handle: str,
        summoner: str,
        parent_text: str = "",
        summon_text: str = "",
        tenant_id: str = "default",
    ) -> bool:
        """Claim *summon_id* for processing. False if already claimed.

        The UNIQUE constraint is the lock. Two pollers (or a poller and a
        manual ``/x`` command) racing on the same mention both call this;
        exactly one gets True. The loser must do nothing — not even draft,
        because drafting costs an LLM call.
        """
        now = time.time()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO x_replies
                       (summon_id, parent_id, target_handle, summoner, status,
                        parent_text, summon_text, tenant_id, created_at,
                        updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(summon_id), str(parent_id), str(target_handle).lstrip("@").lower(),
                        str(summoner).lstrip("@").lower(), STATUS_DRAFTING,
                        str(parent_text or "")[:2000], str(summon_text or "")[:500],
                        str(tenant_id or "default"), now, now,
                    ),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False
            finally:
                conn.close()

    def seen(self, summon_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT 1 FROM x_replies WHERE summon_id = ?", (str(summon_id),)
                ).fetchone()
                return row is not None
            finally:
                conn.close()

    # ── Transitions ───────────────────────────────────────────────────

    def _update(self, summon_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"UPDATE x_replies SET {cols} WHERE summon_id = ?",
                    (*fields.values(), str(summon_id)),
                )
                conn.commit()
            finally:
                conn.close()

    def mark_awaiting(self, summon_id: str, *, draft: str, subject_id: str,
                      proposal_id: str = "") -> None:
        self._update(summon_id, status=STATUS_AWAITING, draft_text=draft,
                     subject_id=subject_id, proposal_id=proposal_id)

    def mark_posted(self, summon_id: str, *, tweet_id: str, draft: str = "",
                    subject_id: str = "") -> None:
        fields: dict[str, Any] = {"status": STATUS_POSTED, "tweet_id": str(tweet_id)}
        if draft:
            fields["draft_text"] = draft
        if subject_id:
            fields["subject_id"] = subject_id
        self._update(summon_id, **fields)

    def mark_skipped(self, summon_id: str, reason: str) -> None:
        self._update(summon_id, status=STATUS_SKIPPED, reason=reason[:500])

    def mark_failed(self, summon_id: str, reason: str) -> None:
        self._update(summon_id, status=STATUS_FAILED, reason=reason[:500])

    def forget(self, summon_id: str) -> bool:
        """Remove a row from the log. Posted tweets must be deleted on X first."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "DELETE FROM x_replies WHERE summon_id = ?",
                    (str(summon_id),),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def release(self, summon_id: str) -> bool:
        """Delete a non-posted row so ``claim`` can take it again.

        Posted rows are left alone — retry is not a delete-and-repost.
        Returns True if a row was removed.
        """
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "DELETE FROM x_replies WHERE summon_id = ? AND status != ?",
                    (str(summon_id), STATUS_POSTED),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def get(self, summon_id: str) -> ReplyRecord | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM x_replies WHERE summon_id = ?", (str(summon_id),)
                ).fetchone()
                return ReplyRecord(row) if row else None
            finally:
                conn.close()

    # ── Caps ──────────────────────────────────────────────────────────

    def posted_since(self, epoch: float) -> int:
        """Replies actually POSTED since *epoch*. Drafts do not count."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM x_replies "
                    "WHERE status = ? AND updated_at >= ?",
                    (STATUS_POSTED, float(epoch)),
                ).fetchone()
                return int(row["n"] if row else 0)
            finally:
                conn.close()

    def posted_to_target_since(self, handle: str, epoch: float) -> int:
        h = (handle or "").lstrip("@").lower()
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM x_replies "
                    "WHERE status = ? AND target_handle = ? AND updated_at >= ?",
                    (STATUS_POSTED, h, float(epoch)),
                ).fetchone()
                return int(row["n"] if row else 0)
            finally:
                conn.close()

    def last_reply_in_thread(self, parent_id: str) -> float:
        """Epoch of the most recent POSTED reply under *parent_id*, or 0.0."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT MAX(updated_at) AS t FROM x_replies "
                    "WHERE status = ? AND parent_id = ?",
                    (STATUS_POSTED, str(parent_id)),
                ).fetchone()
                return float(row["t"] or 0.0) if row else 0.0
            finally:
                conn.close()

    def close_conversation(self, conversation_id: str, *, closed_by: str = "") -> None:
        cid = str(conversation_id or "").strip()
        if not cid:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO x_closed_threads "
                    "(conversation_id, closed_at, closed_by) VALUES (?, ?, ?)",
                    (cid, time.time(), str(closed_by or "")),
                )
                conn.commit()
            finally:
                conn.close()

    def is_conversation_closed(self, conversation_id: str) -> bool:
        cid = str(conversation_id or "").strip()
        if not cid:
            return False
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT 1 FROM x_closed_threads WHERE conversation_id = ?",
                    (cid,),
                ).fetchone()
                return row is not None
            finally:
                conn.close()

    def recent(self, limit: int = 20) -> list[ReplyRecord]:
        """Newest mention on X first — not last time we touched the row.

        ``ORDER BY created_at`` put a retried old summon above a newer one
        because retry deletes and re-claims (new created_at). Tweet ids are
        snowflakes: larger id is later on X. Manual ``/x roast`` rows have
        no snowflake and sort after real mentions, by created_at.
        """
        cap = max(1, min(200, int(limit)))
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT * FROM x_replies").fetchall()
            finally:
                conn.close()
        recs = [ReplyRecord(r) for r in rows]
        recs.sort(key=lambda r: _mention_recency_key(r), reverse=True)
        return recs[:cap]

    # ── Mentions cursor ───────────────────────────────────────────────

    def get_since_id(self) -> str:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT since_id FROM x_mentions_cursor WHERE k = 'mentions'"
                ).fetchone()
                return str(row["since_id"]) if row else ""
            finally:
                conn.close()

    def set_since_id(self, since_id: str) -> None:
        sid = str(since_id or "").strip()
        if not sid:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO x_mentions_cursor (k, since_id, updated_at)
                       VALUES ('mentions', ?, ?)
                       ON CONFLICT(k) DO UPDATE SET since_id = excluded.since_id,
                                                    updated_at = excluded.updated_at""",
                    (sid, time.time()),
                )
                conn.commit()
            finally:
                conn.close()


def _mention_recency_key(rec: ReplyRecord) -> tuple[int, int]:
    """Sort key: larger is newer. Snowflake mentions beat manual roasts."""
    sid = str(rec.summon_id or "").strip()
    if sid.isdigit():
        return (1, int(sid))
    return (0, int((rec.created_at or 0.0) * 1000))


_store: XReplyStore | None = None
_store_lock = threading.Lock()


def get_reply_store() -> XReplyStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = XReplyStore()
    return _store


def reset_reply_store(db_path: str | Path | None = None) -> XReplyStore:
    """Test hook — rebind the singleton to a temp path."""
    global _store
    with _store_lock:
        _store = XReplyStore(db_path)
    return _store
