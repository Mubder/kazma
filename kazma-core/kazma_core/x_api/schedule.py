"""Scheduled X posts â€” Kazma-side store + deterministic fire loop.

X (Twitter) has NO native scheduled-post API: the ``/2/broadcasts/scheduled``
endpoint schedules live *video* streams (it requires an RTMP ``source_id``),
and ``POST /2/tweets`` has no scheduling field. So Kazma owns the clock â€”
a post is stored here and fired by calling ``POST /2/tweets`` directly at the
appointed time. This mirrors how every X scheduling tool works (see X's own
Typefully success story: client-side scheduling over ``POST /2/tweets``).

Design notes:
  * The fire loop calls :meth:`XClient.create_tweet` DIRECTLY â€” no LangGraph,
    no LLM â€” so a scheduled post is deterministic. Approval happened once at
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

import json
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
STATUS_SENDING = "sending"
STATUS_UNKNOWN = "outcome_unknown"
STATUS_MANAGED = "managed"

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
        self.operation_id = str(row["operation_id"] or "") if "operation_id" in row.keys() else ""
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
            "operation_id": self.operation_id,
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
                from kazma_core.db.sqlite_columns import add_missing_columns

                add_missing_columns(conn, "x_scheduled_posts", (("operation_id", "TEXT NOT NULL DEFAULT ''"),
                                                            ("operation_version", "INTEGER NOT NULL DEFAULT 0")))
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_x_sched_operation ON x_scheduled_posts(operation_id) WHERE operation_id != ''")
                conn.commit()
            finally:
                conn.close()

    # â”€â”€ Booking â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
        operation_id: str = "",
        status: str = STATUS_PENDING,
    ) -> int:
        """Insert a pending scheduled post. Returns the row id."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "INSERT INTO x_scheduled_posts "
                    "(text, fire_at, tz, reply_to_id, status, thread_id, "
                    " delivery_target, tenant_id, created_at, operation_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        text, fire_at, tz, reply_to_id, status,
                        thread_id, delivery_target, tenant_id, time.time(), operation_id,
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)
            finally:
                conn.close()

    # â”€â”€ Queries â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def project_operation(self, operation: dict[str, Any]) -> int:
        """Idempotent compatibility projection; managed rows cannot fire in old builds."""
        ident = str(operation["id"])
        metadata = json.loads(operation.get("metadata") or "{}")
        states = {"scheduled": STATUS_MANAGED, "deferred": STATUS_MANAGED, "sending": STATUS_SENDING,
                  "published": STATUS_FIRED, "cancelled": STATUS_CANCELLED, "failed_permanent": STATUS_FAILED,
                  "outcome_unknown": STATUS_UNKNOWN, "awaiting_approval": "held", "expired": "expired"}
        status = states[operation["state"]]
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT id, operation_version FROM x_scheduled_posts WHERE operation_id = ? AND tenant_id = ?", (ident, operation["tenant_id"])).fetchone()
                if row is not None and row["operation_version"] >= operation["version"]:
                    conn.commit()
                    return int(row["id"])
                if row is None and metadata.get("legacy_schedule_id"):
                    row = conn.execute("SELECT id FROM x_scheduled_posts WHERE id = ? AND tenant_id = ? AND operation_id = '' AND status IN ('pending', 'sending', 'held', 'outcome_unknown')", (metadata["legacy_schedule_id"], operation["tenant_id"])).fetchone()
                if row is None:
                    cur = conn.execute("INSERT INTO x_scheduled_posts (text, fire_at, tz, reply_to_id, status, thread_id, delivery_target, tenant_id, created_at, operation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                       (operation["text"], operation["due_at"], metadata.get("tz", ""), operation["reply_to_id"], status,
                                        metadata.get("thread_id", ""), metadata.get("delivery_target", ""), operation["tenant_id"], operation["created_at"], ident))
                    post_id = int(cur.lastrowid)
                else:
                    post_id = int(row["id"])
                conn.execute("UPDATE x_scheduled_posts SET operation_id = ?, operation_version = ?, status = ?, fire_at = ?, tweet_id = ?, fired_at = ?, attempts = ?, error = ? WHERE id = ? AND tenant_id = ?",
                             (ident, operation["version"], status, operation["due_at"], operation["tweet_id"], operation["published_at"], operation["attempts"], operation["reason"], post_id, operation["tenant_id"]))
                conn.commit()
                return post_id
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def by_operation(self, operation_id: str, *, tenant_id: str) -> ScheduledXPost | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM x_scheduled_posts WHERE operation_id = ? AND tenant_id = ?", (operation_id, tenant_id)).fetchone()
                return ScheduledXPost(row) if row else None
            finally:
                conn.close()

    def legacy_tenants(self) -> list[str]:
        """Internal migration inventory, including fenced rows awaiting repair."""
        with self._lock:
            conn = self._connect()
            try:
                return [str(r[0]) for r in conn.execute("SELECT DISTINCT tenant_id FROM x_scheduled_posts WHERE operation_id = '' AND status IN ('pending', 'sending', 'held', 'outcome_unknown')")]
            finally:
                conn.close()

    def fence_legacy(self, *, tenant_id: str) -> list[ScheduledXPost]:
        """Hold old account-unbound bookings before newer reservations or sends."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("UPDATE x_scheduled_posts SET status = CASE WHEN status = 'sending' THEN 'outcome_unknown' ELSE 'held' END, "
                             "error = 'Legacy account binding unavailable; review before release' "
                             "WHERE tenant_id = ? AND operation_id = '' AND status IN ('pending', 'sending', 'held', 'outcome_unknown')", (tenant_id,))
                rows = conn.execute("SELECT * FROM x_scheduled_posts WHERE tenant_id = ? AND operation_id = '' AND status IN ('held', 'outcome_unknown')", (tenant_id,)).fetchall()
                conn.commit()
                return [ScheduledXPost(r) for r in rows]
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def get(self, post_id: int, *, tenant_id: str | None = None) -> ScheduledXPost | None:
        from kazma_core.x_api.ownership import x_tenant_id

        tenant_id = tenant_id or x_tenant_id()
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM x_scheduled_posts WHERE id = ? AND (? IS NULL OR tenant_id = ?)",
                    (post_id, tenant_id, tenant_id),
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
                        "WHERE status IN (?, ?) AND tenant_id = ?",
                        (STATUS_PENDING, STATUS_MANAGED, tenant_id),
                    )
                else:
                    cur = conn.execute(
                        "SELECT COUNT(*) FROM x_scheduled_posts WHERE status IN (?, ?)",
                        (STATUS_PENDING, STATUS_MANAGED),
                    )
                return int(cur.fetchone()[0])
            finally:
                conn.close()

    # â”€â”€ State transitions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def claim_send(self, post_id: int, *, tenant_id: str) -> ScheduledXPost | None:
        """Only one process may move a due pending row across the send boundary."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "UPDATE x_scheduled_posts SET status = ?, attempts = attempts + 1 "
                    "WHERE id = ? AND tenant_id = ? AND status = ? AND fire_at <= ?",
                    (STATUS_SENDING, post_id, tenant_id, STATUS_PENDING, time.time()),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    return None
                row = conn.execute("SELECT * FROM x_scheduled_posts WHERE id = ?", (post_id,)).fetchone()
                conn.commit()
                return ScheduledXPost(row)
            finally:
                conn.close()

    def mark_unknown(self, post_id: int, error: str) -> None:
        """Hold a possibly accepted write; never make it pending on restart."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("UPDATE x_scheduled_posts SET status = ?, error = ? WHERE id = ? AND status = ?",
                             (STATUS_UNKNOWN, str(error)[:500], post_id, STATUS_SENDING))
                conn.commit()
            finally:
                conn.close()

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
                    "UPDATE x_scheduled_posts SET status = ?, error = ? WHERE id = ? AND status IN (?, ?)",
                    (STATUS_FAILED, str(error)[:500], post_id, STATUS_PENDING, STATUS_SENDING),
                )
                conn.commit()
            finally:
                conn.close()

    def cancel(self, post_id: int, *, tenant_id: str | None = None) -> bool:
        """Cancel a pending post (releases its reserved quota). True if found."""
        from kazma_core.x_api.ownership import x_tenant_id

        tenant_id = tenant_id or x_tenant_id()
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "UPDATE x_scheduled_posts SET status = ? "
                    "WHERE id = ? AND (status = ? OR (status = 'held' AND operation_id = '')) AND (? IS NULL OR tenant_id = ?)",
                    (STATUS_CANCELLED, post_id, STATUS_PENDING, tenant_id, tenant_id),
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
                    "UPDATE x_scheduled_posts SET fire_at = ?, status = ? "
                    "WHERE id = ? AND status = ?",
                    (new_fire_at, STATUS_PENDING, post_id, STATUS_SENDING),
                )
                conn.commit()
            finally:
                conn.close()

    def set_fire_time(self, post_id: int, new_fire_at: float, *, tenant_id: str | None = None) -> bool:
        """Move a pending post's fire time (Web/chat edit). True if updated."""
        from kazma_core.x_api.ownership import x_tenant_id

        tenant_id = tenant_id or x_tenant_id()
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "UPDATE x_scheduled_posts SET fire_at = ? "
                    "WHERE id = ? AND status = ? AND (? IS NULL OR tenant_id = ?)",
                    (new_fire_at, post_id, STATUS_PENDING, tenant_id, tenant_id),
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
    """(Re)create the singleton â€” test isolation helper."""
    global _store
    with _store_lock:
        _store = XScheduledStore(db_path)
    return _store
