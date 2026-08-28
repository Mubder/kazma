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
                conn.commit()
            finally:
                conn.close()

    def count_since(self, epoch: float) -> int:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT COUNT(*) FROM x_posts "
                    "WHERE created_at >= ? AND deleted_at IS NULL",
                    (epoch,),
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
                    "AND created_at >= ? AND deleted_at IS NULL LIMIT 1",
                    (h, since),
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
                    "INSERT OR REPLACE INTO x_posts "
                    "(tweet_id, text_hash, text_preview, handle, created_at, deleted_at) "
                    "VALUES (?, ?, ?, ?, ?, NULL)",
                    (tweet_id, text_hash(text), preview, handle, time.time()),
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
                    "AND deleted_at IS NULL",
                    (time.time(), tweet_id),
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
                    "SELECT text_preview FROM x_posts WHERE tweet_id = ? LIMIT 1",
                    (tid,),
                )
                row = cur.fetchone()
                return str(row["text_preview"] or "") if row is not None else ""
            finally:
                conn.close()

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        lim = max(1, min(50, int(limit)))
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT tweet_id, text_preview, handle, created_at, deleted_at "
                    "FROM x_posts ORDER BY created_at DESC LIMIT ?",
                    (lim,),
                ).fetchall()
                return [dict(r) for r in rows]
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
