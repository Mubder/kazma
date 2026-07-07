"""Bookmark Store — SQLite-backed project bookmark persistence.

All bookmarks are stored in the same ``kazma-data/settings.db`` database
used by :class:`~kazma_core.config_store.ConfigStore`.  A separate table
(``bookmarks``) is created on first access so the two stores never
interfere with each other.

Concurrency model
-----------------
- WAL + ``busy_timeout=5000`` identical to ConfigStore.
- The process-wide singleton :func:`get_bookmark_store` ensures all
  components share a single connection and threading.Lock.
- Multi-row mutations use explicit ``BEGIN`` / ``COMMIT`` transactions
  with a ``ROLLBACK`` on failure.

Schema
------
  bookmarks(
      id     INTEGER PRIMARY KEY AUTOINCREMENT,
      name   TEXT NOT NULL,
      type   TEXT NOT NULL CHECK(type IN ('file', 'url')),
      target TEXT NOT NULL
  )
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas

logger = logging.getLogger(__name__)

_DEFAULT_DB = "kazma-data/settings.db"

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS bookmarks (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT    NOT NULL,
    type   TEXT    NOT NULL DEFAULT 'file',
    target TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bookmarks_type ON bookmarks(type);
"""


class BookmarkStore:
    """SQLite-backed store for project bookmarks.

    Use :func:`get_bookmark_store` to obtain the process-wide singleton
    instead of constructing this class directly.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = Path(db_path or _DEFAULT_DB)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._init_db()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
                isolation_level=None,
            )
            self._conn.row_factory = sqlite3.Row
            apply_sqlite_pragmas(self._conn)
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------
    # CRUD helpers
    # ------------------------------------------------------------------

    def list_bookmarks(self) -> list[dict[str, Any]]:
        """Return all bookmarks ordered by id."""
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT id, name, type, target FROM bookmarks ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_bookmark(self, bookmark_id: int) -> dict[str, Any] | None:
        """Return a single bookmark by *bookmark_id*, or ``None`` if not found."""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT id, name, type, target FROM bookmarks WHERE id = ?",
                (bookmark_id,),
            ).fetchone()
        return dict(row) if row else None

    def create_bookmark(self, name: str, type_str: str, target: str) -> dict[str, Any]:
        """Insert a new bookmark and return the created record.

        Args:
            name: Human-readable label.
            type_str: One of ``"file"`` or ``"url"``.
            target: Absolute file path or full URL.

        Returns:
            A dict with ``id``, ``name``, ``type``, and ``target`` keys.

        Raises:
            ValueError: When *type_str* is not ``"file"`` or ``"url"``.
        """
        if type_str not in ("file", "url"):
            raise ValueError(f"Invalid bookmark type: {type_str!r}. Must be 'file' or 'url'.")

        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN")
                cursor = conn.execute(
                    "INSERT INTO bookmarks (name, type, target) VALUES (?, ?, ?)",
                    (name.strip(), type_str, target.strip()),
                )
                new_id = cursor.lastrowid
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

        record = self.get_bookmark(new_id)
        if record is None:
            raise RuntimeError("Bookmark insert succeeded but record not found — internal error.")
        logger.debug("[BookmarkStore] Created bookmark id=%s name=%r", new_id, name)
        return record

    def delete_bookmark(self, bookmark_id: int) -> bool:
        """Delete a bookmark by *bookmark_id*.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.
        """
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN")
                cursor = conn.execute(
                    "DELETE FROM bookmarks WHERE id = ?", (bookmark_id,)
                )
                conn.execute("COMMIT")
                deleted = cursor.rowcount > 0
            except Exception:
                conn.execute("ROLLBACK")
                raise

        if deleted:
            logger.debug("[BookmarkStore] Deleted bookmark id=%s", bookmark_id)
        return deleted

    def update_bookmark(
        self,
        bookmark_id: int,
        *,
        name: str | None = None,
        type_str: str | None = None,
        target: str | None = None,
    ) -> dict[str, Any] | None:
        """Partially update a bookmark.  Returns the updated record or ``None`` if not found."""
        if type_str is not None and type_str not in ("file", "url"):
            raise ValueError(f"Invalid bookmark type: {type_str!r}. Must be 'file' or 'url'.")

        existing = self.get_bookmark(bookmark_id)
        if existing is None:
            return None

        new_name = name.strip() if name is not None else existing["name"]
        new_type = type_str if type_str is not None else existing["type"]
        new_target = target.strip() if target is not None else existing["target"]

        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN")
                conn.execute(
                    "UPDATE bookmarks SET name = ?, type = ?, target = ? WHERE id = ?",
                    (new_name, new_type, new_target, bookmark_id),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

        logger.debug("[BookmarkStore] Updated bookmark id=%s", bookmark_id)
        return self.get_bookmark(bookmark_id)

    def close(self) -> None:
        """Close the underlying database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# ══════════════════════════════════════════════════════════════════════════
# Process-wide singleton
# ══════════════════════════════════════════════════════════════════════════

_bookmark_store: BookmarkStore | None = None


def get_bookmark_store() -> BookmarkStore:
    """Return the shared :class:`BookmarkStore` singleton.

    Lazily creates a default instance on first call.  All components must
    use this instead of constructing ``BookmarkStore()`` directly, so they
    share one SQLite connection and one ``threading.Lock``.
    """
    global _bookmark_store
    if _bookmark_store is None:
        _bookmark_store = BookmarkStore()
    return _bookmark_store


def reset_bookmark_store() -> None:
    """Drop the singleton reference (used by test teardown)."""
    global _bookmark_store
    if _bookmark_store is not None:
        _bookmark_store.close()
    _bookmark_store = None
