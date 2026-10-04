"""Local post ledger — quota + duplicate detection. Never talks to X."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas
from kazma_core.x_api.ownership import x_tenant_id

logger = logging.getLogger(__name__)

__all__ = ["XPostLedger", "normalize_text", "text_hash"]

_CREATE = """
CREATE TABLE IF NOT EXISTS x_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tweet_id TEXT UNIQUE,
    text_hash TEXT NOT NULL,
    text_preview TEXT,
    handle TEXT,
    created_at REAL NOT NULL,
    deleted_at REAL
);
CREATE INDEX IF NOT EXISTS idx_x_posts_hash ON x_posts(text_hash);
CREATE INDEX IF NOT EXISTS idx_x_posts_created ON x_posts(created_at);
"""


def normalize_text(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


class XPostLedger:
    """SQLite WAL ledger under kazma-data/x_posts.db."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from kazma_core.paths import data_dir

            db_path = data_dir() / "x_posts.db"
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

                add_missing_columns(conn, "x_posts", (("tenant_id", "TEXT NOT NULL DEFAULT 'default'"),))
                conn.commit()
            finally:
                conn.close()

    def count_since(self, epoch: float) -> int:
        """Posts CREATED since *epoch*, deleted ones included.

        The caps this feeds are a fail-safe under X's API quota, and X counts
        every post it created -- deleting one does not give the request back.
        This counted only posts still up, so a post-then-delete freed a slot
        X had not (the live end-to-end test showed "0/16" after one post and
        its delete, 2026-09-28). The duplicate check still ignores deleted
        posts: their text may be posted again.
        """
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT COUNT(*) FROM x_posts WHERE created_at >= ? AND tenant_id = ?",
                    (epoch, x_tenant_id()),
                )
                return int(cur.fetchone()[0])
            finally:
                conn.close()

    def has_duplicate(self, text: str, *, window_days: int) -> bool:
        h = text_hash(text)
        since = time.time() - max(1, window_days) * 86400
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT 1 FROM x_posts WHERE text_hash = ? "
                    "AND created_at >= ? AND deleted_at IS NULL AND tenant_id = ? LIMIT 1",
                    (h, since, x_tenant_id()),
                )
                return cur.fetchone() is not None
            finally:
                conn.close()

    def record(
        self,
        *,
        tweet_id: str,
        text: str,
        handle: str = "",
    ) -> None:
        preview = (text or "")[:280]
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO x_posts "
                    "(tweet_id, text_hash, text_preview, handle, created_at, deleted_at, tenant_id) "
                    "VALUES (?, ?, ?, ?, ?, NULL, ?)",
                    (tweet_id, text_hash(text), preview, handle, time.time(), x_tenant_id()),
                )
                conn.commit()
            finally:
                conn.close()

    def mark_deleted(self, tweet_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "UPDATE x_posts SET deleted_at = ? WHERE tweet_id = ? "
                    "AND deleted_at IS NULL AND tenant_id = ?",
                    (time.time(), tweet_id, x_tenant_id()),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def text_for_tweet(self, tweet_id: str) -> str:
        """Return the stored text preview for a tweet id ("" when unknown).

        Used by the audit-log enrichment to show what a *delete* row removed —
        the delete request body carries no text, only the id.
        """
        tid = str(tweet_id or "").strip()
        if not tid:
            return ""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT text_preview FROM x_posts WHERE tweet_id = ? AND tenant_id = ? LIMIT 1",
                    (tid, x_tenant_id()),
                )
                row = cur.fetchone()
                return str(row["text_preview"] or "") if row is not None else ""
            finally:
                conn.close()

    def first_post_for(self, text: str) -> dict[str, Any] | None:
        """The first ledger row whose text matches *text*, deleted or not.

        Evidence that a saved draft went out (``ArtifactStore.
        heal_legacy_posted``). A later delete does not un-send it.
        """
        h = text_hash(text)
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT tweet_id, created_at FROM x_posts WHERE text_hash = ? "
                    "AND tenant_id = ? ORDER BY created_at ASC LIMIT 1",
                    (h, x_tenant_id()),
                ).fetchone()
                return dict(row) if row is not None else None
            finally:
                conn.close()

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        lim = max(1, min(50, int(limit)))
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT tweet_id, text_preview, handle, created_at, deleted_at "
                    "FROM x_posts WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
                    (x_tenant_id(), lim),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def publication_history(self) -> list[dict[str, Any]]:
        """Full current-tenant receipts for idempotent reservation backfill."""
        with self._lock:
            conn = self._connect()
            try:
                return [dict(r) for r in conn.execute("SELECT * FROM x_posts WHERE tenant_id = ?", (x_tenant_id(),)).fetchall()]
            finally:
                conn.close()


_ledger: XPostLedger | None = None
_ledger_lock = threading.Lock()


def get_ledger() -> XPostLedger:
    global _ledger
    with _ledger_lock:
        if _ledger is None:
            _ledger = XPostLedger()
        return _ledger
