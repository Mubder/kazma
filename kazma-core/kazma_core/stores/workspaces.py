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

# ── Repo-identity columns (Phase 2) ────────────────────────────────────
# These are added via idempotent ALTER TABLE on init (SQLite has no
# ADD COLUMN IF NOT EXISTS). They persist the GitHub repo identity tied to
# a workspace so it doesn't have to be re-derived from `git remote` on
# every call. All nullable — a non-git directory has NULLs.
_REPO_COLUMNS: tuple[tuple[str, str], ...] = (
    ("repo_url", "TEXT"),           # remote.origin.url
    ("owner", "TEXT"),              # parsed owner
    ("repo", "TEXT"),               # parsed repo name
    ("default_branch", "TEXT"),     # 'main' / 'master' / ...
    ("is_github", "INTEGER"),       # 1 if origin is github.com
)

# Comma-joined repo column names for SELECT statements.
_REPO_COLS = "repo_url, owner, repo, default_branch, is_github"


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
            self._migrate_repo_columns(conn)

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
                except sqlite3.Error as rollback_exc:
                    logger.debug(
                        "[WorkspaceStore] Failed to rollback transaction during workspace initialization: %s",
                        rollback_exc,
                        exc_info=True,
                    )
                logger.error("[WorkspaceStore] Failed to initialize Default Workspace: %s", exc)
                raise exc

    # ------------------------------------------------------------------
    # Schema migration
    # ------------------------------------------------------------------

    @staticmethod
    def _migrate_repo_columns(conn: sqlite3.Connection) -> None:
        """Idempotently add the repo-identity columns if absent.

        SQLite lacks ``ADD COLUMN IF NOT EXISTS``; we probe ``PRAGMA
        table_info`` and only ALTER when the column is missing. Safe to
        call on every init — existing columns are a no-op. This mirrors
        the auto-migrate pattern used by TaskStore (AGENTS.md §6).
        """
        existing = {row[1] for row in conn.execute("PRAGMA table_info(workspaces)")}
        for col_name, col_type in _REPO_COLUMNS:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE workspaces ADD COLUMN {col_name} {col_type}")
                logger.debug("[WorkspaceStore] Migrated column %s", col_name)

    # ------------------------------------------------------------------
    # Repo identity (Phase 2)
    # ------------------------------------------------------------------

    def repo_for(self, root_path: str) -> dict[str, Any] | None:
        """Return the persisted repo identity for a workspace root, or None.

        Looks up the workspace row by ``root_path`` and returns the repo
        columns. Returns None if the workspace isn't registered or has no
        repo identity cached. Callers should fall back to live git
        detection when this returns None (see ``env_context``).
        """
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                f"SELECT {_REPO_COLS} FROM workspaces WHERE root_path = ?",
                (str(Path(root_path).resolve()),),
            ).fetchone()
        if not row:
            return None
        # All-NULL row = no identity cached.
        if all(row[k] is None for k in ("repo_url", "owner", "repo", "default_branch", "is_github")):
            return None
        return {
            "repo_url": row["repo_url"],
            "owner": row["owner"],
            "repo": row["repo"],
            "default_branch": row["default_branch"],
            "is_github": bool(row["is_github"]) if row["is_github"] is not None else None,
        }

    def set_repo_identity(self, root_path: str, *, repo_url: str | None,
                          owner: str | None, repo: str | None,
                          default_branch: str | None = None,
                          is_github: bool | None = None) -> bool:
        """Persist the repo identity against the workspace at ``root_path``.

        Returns True if a row was updated, False if no workspace matches.
        """
        resolved = str(Path(root_path).resolve())
        gh_val = None
        if is_github is not None:
            gh_val = 1 if is_github else 0
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                f"""UPDATE workspaces
                    SET repo_url = ?, owner = ?, repo = ?,
                        default_branch = ?, is_github = ?
                    WHERE root_path = ?""",
                (repo_url, owner, repo, default_branch, gh_val, resolved),
            )
            updated = cur.rowcount > 0
        if updated:
            logger.debug(
                "[WorkspaceStore] Cached repo identity for %s: %s/%s",
                resolved, owner, repo,
            )
        return updated

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
        """Return all registered workspaces (including repo identity)."""
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                f"SELECT id, name, root_path, created_at, is_active, {_REPO_COLS} "
                "FROM workspaces ORDER BY created_at"
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_active_workspace(self) -> dict[str, Any] | None:
        """Return the currently active workspace dictionary or None."""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                f"SELECT id, name, root_path, created_at, is_active, {_REPO_COLS} "
                "FROM workspaces WHERE is_active = 1"
            ).fetchone()
        if row:
            return self._row_to_dict(row, active=True)
        return None

    @staticmethod
    def _row_to_dict(row: sqlite3.Row, *, active: bool | None = None) -> dict[str, Any]:
        """Marshal a workspace row (with repo columns) into a dict."""
        return {
            "id": row["id"],
            "name": row["name"],
            "root_path": row["root_path"],
            "created_at": row["created_at"],
            "is_active": bool(row["is_active"]) if active is None else active,
            "repo_url": row["repo_url"],
            "owner": row["owner"],
            "repo": row["repo"],
            "default_branch": row["default_branch"],
            "is_github": bool(row["is_github"]) if row["is_github"] is not None else None,
        }

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
