"""Workspace Store — SQLite-backed workspace database persistence.

Tracks all registered workspace roots across the system and manages the
currently active workspace pointer.

Concurrency model
-----------------
- WAL + ``busy_timeout=5000`` identical to ConfigStore and BookmarkStore.
- The process-wide singleton :func:`get_workspace_store` ensures all
  components share a single connection and threading.Lock.
- Multi-row mutations use explicit ``BEGIN`` / ``COMMIT`` transactions
  with a ``ROLLBACK`` on failure.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas

logger = logging.getLogger(__name__)

_DEFAULT_DB = "kazma-data/settings.db"

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS workspaces (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    root_path   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_workspaces_active ON workspaces(is_active);
"""


class WorkspaceStore:
    """SQLite-backed store for multi-project workspaces.

    Use :func:`get_workspace_store` to obtain the process-wide singleton
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

            # Boot-time check: If no workspaces are recorded, initialize current CWD
            try:
                row = conn.execute("SELECT COUNT(*) as count FROM workspaces").fetchone()
                if row and row["count"] == 0:
                    ws_id = str(uuid.uuid4())
                    name = "Default Workspace"
                    root_path = str(Path.cwd().resolve())
                    created_at = datetime.now(UTC).isoformat()
                    
                    conn.execute("BEGIN")
                    conn.execute(
                        "INSERT INTO workspaces (id, name, root_path, created_at, is_active) VALUES (?, ?, ?, ?, 1)",
                        (ws_id, name, root_path, created_at),
                    )
                    conn.execute("COMMIT")
                    logger.info("[WorkspaceStore] Initialized Default Workspace at %s", root_path)
            except Exception as exc:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                logger.error("[WorkspaceStore] Failed to initialize Default Workspace: %s", exc)
                raise exc

    # ------------------------------------------------------------------
    # Storage and Retrieval
    # ------------------------------------------------------------------

    def create_workspace(self, name: str, path: str) -> dict[str, Any]:
        """Create and record a new workspace inside the registry.

        Args:
            name: Label of the workspace.
            path: Target directory path on the filesystem.

        Returns:
            The created workspace dictionary record.
        """
        ws_id = str(uuid.uuid4())
        created_at = datetime.now(UTC).isoformat()
        resolved_path = str(Path(path).resolve())

        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN")
                conn.execute(
                    "INSERT INTO workspaces (id, name, root_path, created_at, is_active) VALUES (?, ?, ?, ?, 0)",
                    (ws_id, name.strip(), resolved_path, created_at),
                )
                conn.execute("COMMIT")
            except Exception as exc:
                conn.execute("ROLLBACK")
                logger.error("[WorkspaceStore] Failed to create workspace: %s", exc)
                raise exc

        logger.info("[WorkspaceStore] Registered new workspace %r at %s", name, resolved_path)
        return {
            "id": ws_id,
            "name": name.strip(),
            "root_path": resolved_path,
            "created_at": created_at,
            "is_active": False,
        }

    def list_workspaces(self) -> list[dict[str, Any]]:
        """Return all registered workspaces."""
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT id, name, root_path, created_at, is_active FROM workspaces ORDER BY created_at"
            ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "root_path": row["root_path"],
                "created_at": row["created_at"],
                "is_active": bool(row["is_active"]),
            }
            for row in rows
        ]

    def get_active_workspace(self) -> dict[str, Any] | None:
        """Return the currently active workspace dictionary or None."""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT id, name, root_path, created_at, is_active FROM workspaces WHERE is_active = 1"
            ).fetchone()
        if row:
            return {
                "id": row["id"],
                "name": row["name"],
                "root_path": row["root_path"],
                "created_at": row["created_at"],
                "is_active": True,
            }
        return None

    def set_active_workspace(self, workspace_id: str) -> bool:
        """Set the workspace with workspace_id as active and others as inactive.

        Returns:
            True if setting succeeded, False if the workspace was not found.
        """
        with self._lock:
            conn = self._get_conn()
            # Validate workspace exists
            row = conn.execute(
                "SELECT 1 FROM workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
            if not row:
                return False

            try:
                conn.execute("BEGIN")
                conn.execute("UPDATE workspaces SET is_active = 0")
                conn.execute(
                    "UPDATE workspaces SET is_active = 1 WHERE id = ?",
                    (workspace_id,),
                )
                conn.execute("COMMIT")
                logger.info("[WorkspaceStore] Set active workspace to %s", workspace_id)
                return True
            except Exception as exc:
                conn.execute("ROLLBACK")
                logger.error("[WorkspaceStore] Failed to activate workspace %s: %s", workspace_id, exc)
                raise exc

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# ══════════════════════════════════════════════════════════════════════════
# Process-wide singleton
# ══════════════════════════════════════════════════════════════════════════

_workspace_store: WorkspaceStore | None = None


def get_workspace_store() -> WorkspaceStore:
    """Return the shared WorkspaceStore singleton.

    Lazily creates a default instance on first call. All components must
    use this instead of constructing WorkspaceStore() directly, so they
    share one SQLite connection and one threading.Lock.
    """
    global _workspace_store
    if _workspace_store is None:
        _workspace_store = WorkspaceStore()
    return _workspace_store


def reset_workspace_store() -> None:
    """Drop the singleton reference (used by test teardown)."""
    global _workspace_store
    if _workspace_store is not None:
        _workspace_store.close()
    _workspace_store = None
