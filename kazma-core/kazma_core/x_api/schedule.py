"""Scheduled X posts — Kazma-side store + deterministic fire loop.

X (Twitter) has NO native scheduled-post API: the ``/2/broadcasts/scheduled``
endpoint schedules live *video* streams (it requires an RTMP ``source_id``),
and ``POST /2/tweets`` has no scheduling field. So Kazma owns the clock —
a post is stored here and fired by calling ``POST /2/tweets`` directly at the
appointed time. This mirrors how every X scheduling tool works (see X's own
Typefully success story: client-side scheduling over ``POST /2/tweets``).

Design notes:
  * The fire loop calls :meth:`XClient.create_tweet` DIRECTLY — no LangGraph,
    no LLM — so a scheduled post is deterministic. Approval happened once at
    booking time (always-HITL); the fire is the execution of that approval.
  * Double-post guard: a failed fire is NEVER auto-retried on an ambiguous
    error (timeout / mid-stream drop) because we cannot know whether the post
    reached X. Only a provably-unsent error (connection refused before send)
    is retried. This matches ``XClient``'s "writes are not retried" contract.
  * Quota is reserved at BOOKING time: pending scheduled posts count toward
    the daily/monthly caps so the schedule cannot be used to exceed them.
  * Kill-switch ``KAZMA_X_SCHEDULE=0`` disables the fire loop (and booking).
    ``KAZMA_X_POST=0`` (the posting kill-switch) also disables everything.

The store is SQLite WAL under ``kazma-data/x_scheduled.db`` (separate from the
post ledger ``x_posts.db`` and the audit log ``x_audit.db`` by design).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas

logger = logging.getLogger(__name__)

__all__ = [
    "ScheduledXPost",
    "XScheduledStore",
    "get_x_scheduled_store",
    "reset_x_scheduled_store",
    "x_schedule_enabled",
]

# Statuses for a scheduled post.
STATUS_PENDING = "pending"
STATUS_FIRED = "fired"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED = "failed"

_CREATE = """
CREATE TABLE IF NOT EXISTS x_scheduled_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    fire_at REAL NOT NULL,
    tz TEXT NOT NULL DEFAULT '',
    reply_to_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    thread_id TEXT NOT NULL DEFAULT '',
    delivery_target TEXT NOT NULL DEFAULT '',
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at REAL NOT NULL,
    fired_at REAL,
    tweet_id TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_x_sched_status_fire ON x_scheduled_posts(status, fire_at);
CREATE INDEX IF NOT EXISTS idx_x_sched_tenant ON x_scheduled_posts(tenant_id);
"""


def x_schedule_enabled() -> bool:
    """Live kill-switch check. ``KAZMA_X_SCHEDULE=0`` disables scheduling.

    The posting kill-switch ``KAZMA_X_POST=0`` also disables scheduling, since
    a scheduled post is still a post.
    """
    if (os.environ.get("KAZMA_X_POST") or "").strip().lower() in ("0", "false", "no", "off"):
        return False
    return (os.environ.get("KAZMA_X_SCHEDULE") or "").strip().lower() not in (
        "0", "false", "no", "off",
    )


class ScheduledXPost:
    """A row from ``x_scheduled_posts`` as a plain object."""

    def __init__(self, row: sqlite3.Row) -> None:
        self.id = int(row["id"])
        self.text = str(row["text"])
        self.fire_at = float(row["fire_at"])
        self.tz = str(row["tz"] or "")
        self.reply_to_id = str(row["reply_to_id"] or "")
        self.status = str(row["status"])
        self.thread_id = str(row["thread_id"] or "")
        self.delivery_target = str(row["delivery_target"] or "")
        self.tenant_id = str(row["tenant_id"] or "default")
        self.created_at = float(row["created_at"])
        self.fired_at = row["fired_at"]
        self.tweet_id = str(row["tweet_id"] or "")
        self.error = str(row["error"] or "")
        # ``attempts`` may be absent on rows created before the column existed.
        try:
            self.attempts = int(row["attempts"] or 0)
        except (IndexError, KeyError, TypeError):
            self.attempts = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "fire_at": self.fire_at,
            "tz": self.tz,
            "reply_to_id": self.reply_to_id,
            "status": self.status,
            "thread_id": self.thread_id,
            "delivery_target": self.delivery_target,
            "tenant_id": self.tenant_id,
            "created_at": self.created_at,
            "fired_at": self.fired_at,
            "tweet_id": self.tweet_id,
            "error": self.error,
            "attempts": self.attempts,
        }


class XScheduledStore:
    """SQLite WAL store for scheduled X posts."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from kazma_core.paths import data_dir

            db_path = data_dir() / "x_scheduled.db"
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
                conn.commit()
            finally:
                conn.close()

    # ── Booking ───────────────────────────────────────────────────────

    def add(
        self,
        *,
        text: str,
        fire_at: float,
        tz: str = "",
        reply_to_id: str = "",
        thread_id: str = "",
        delivery_target: str = "",
        tenant_id: str = "default",
    ) -> int:
        """Insert a pending scheduled post. Returns the row id."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "INSERT INTO x_scheduled_posts "
                    "(text, fire_at, tz, reply_to_id, status, thread_id, "
                    " delivery_target, tenant_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        text, fire_at, tz, reply_to_id, STATUS_PENDING,
                        thread_id, delivery_target, tenant_id, time.time(),
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)
            finally:
                conn.close()

    # ── Queries ───────────────────────────────────────────────────────

    def get(self, post_id: int) -> ScheduledXPost | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM x_scheduled_posts WHERE id = ?", (post_id,)
                ).fetchone()
                return ScheduledXPost(row) if row is not None else None
            finally:
                conn.close()

    def list_all(self, *, tenant_id: str | None = None, limit: int = 200) -> list[ScheduledXPost]:
        """Newest-first scheduled posts, optionally tenant-scoped."""
        lim = max(1, min(int(limit), 1000))
        with self._lock:
            conn = self._connect()
            try:
                if tenant_id:
                    rows = conn.execute(
                        "SELECT * FROM x_scheduled_posts WHERE tenant_id = ? "
                        "ORDER BY id DESC LIMIT ?",
                        (tenant_id, lim),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM x_scheduled_posts ORDER BY id DESC LIMIT ?",
                        (lim,),
                    ).fetchall()
                return [ScheduledXPost(r) for r in rows]
            finally:
                conn.close()

    def list_due(self, now: float | None = None) -> list[ScheduledXPost]:
        """Pending posts whose fire time has arrived (oldest first)."""
        ts = now if now is not None else time.time()
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM x_scheduled_posts "
                    "WHERE status = ? AND fire_at <= ? ORDER BY fire_at ASC",
                    (STATUS_PENDING, ts),
                ).fetchall()
                return [ScheduledXPost(r) for r in rows]
            finally:
                conn.close()

    def count_pending(self, *, tenant_id: str | None = None) -> int:
        """Number of pending posts (used to reserve quota at booking)."""
        with self._lock:
            conn = self._connect()
            try:
                if tenant_id:
                    cur = conn.execute(
                        "SELECT COUNT(*) FROM x_scheduled_posts "
                        "WHERE status = ? AND tenant_id = ?",
                        (STATUS_PENDING, tenant_id),
                    )
                else:
                    cur = conn.execute(
                        "SELECT COUNT(*) FROM x_scheduled_posts WHERE status = ?",
                        (STATUS_PENDING,),
                    )
                return int(cur.fetchone()[0])
            finally:
                conn.close()

    # ── State transitions ─────────────────────────────────────────────

    def mark_fired(self, post_id: int, tweet_id: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE x_scheduled_posts SET status = ?, fired_at = ?, "
                    "tweet_id = ?, error = '' WHERE id = ?",
                    (STATUS_FIRED, time.time(), tweet_id, post_id),
                )
                conn.commit()
            finally:
                conn.close()

    def mark_failed(self, post_id: int, error: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE x_scheduled_posts SET status = ?, error = ? WHERE id = ?",
                    (STATUS_FAILED, str(error)[:500], post_id),
                )
                conn.commit()
            finally:
                conn.close()

    def cancel(self, post_id: int) -> bool:
        """Cancel a pending post (releases its reserved quota). True if found."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "UPDATE x_scheduled_posts SET status = ? "
                    "WHERE id = ? AND status = ?",
                    (STATUS_CANCELLED, post_id, STATUS_PENDING),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def bump_attempts(self, post_id: int) -> int:
        """Increment and return the fire-attempt counter (bounds 429 deferrals)."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE x_scheduled_posts SET attempts = attempts + 1 WHERE id = ?",
                    (post_id,),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT attempts FROM x_scheduled_posts WHERE id = ?", (post_id,)
                ).fetchone()
                return int(row["attempts"]) if row is not None else 0
            finally:
                conn.close()

    def defer(self, post_id: int, new_fire_at: float) -> None:
        """Push a pending post's fire time forward (429 Retry-After backoff)."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE x_scheduled_posts SET fire_at = ? "
                    "WHERE id = ? AND status = ?",
                    (new_fire_at, post_id, STATUS_PENDING),
                )
                conn.commit()
            finally:
                conn.close()

    def set_fire_time(self, post_id: int, new_fire_at: float) -> bool:
        """Move a pending post's fire time (Web/chat edit). True if updated."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "UPDATE x_scheduled_posts SET fire_at = ? "
                    "WHERE id = ? AND status = ?",
                    (new_fire_at, post_id, STATUS_PENDING),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()


_store: XScheduledStore | None = None
_store_lock = threading.Lock()


def get_x_scheduled_store() -> XScheduledStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = XScheduledStore()
        return _store


def reset_x_scheduled_store(db_path: str | Path | None = None) -> XScheduledStore:
    """(Re)create the singleton — test isolation helper."""
    global _store
    with _store_lock:
        _store = XScheduledStore(db_path)
    return _store
