"""Workspace file checkpoints — undo agent edits, not graph /undo.

DB under kazma-data/ (universal backup). WAL via apply_sqlite_pragmas.
Env ``KAZMA_FILE_CHECKPOINTS_DB`` for tests.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from kazma_core.workspace.binding import resolve_active_root
from kazma_core.workspace.path_policy import check_path_access

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_FILE_BYTES",
    "MAX_FILES",
    "FileCheckpointStore",
    "create_checkpoint",
    "get_file_checkpoint_store",
    "reset_file_checkpoint_store",
    "restore_checkpoint",
]

MAX_FILES = 20
MAX_FILE_BYTES = 1_000_000
_KEEP = 20

_SCHEMA = """
CREATE TABLE IF NOT EXISTS file_checkpoints (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL DEFAULT '',
    workspace_root TEXT NOT NULL,
    created_at REAL NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    files_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_file_ck_ws ON file_checkpoints(workspace_root, created_at);
"""

_store: FileCheckpointStore | None = None
_lock = threading.Lock()


def _db_path() -> Path:
    env = (os.environ.get("KAZMA_FILE_CHECKPOINTS_DB") or "").strip()
    if env:
        return Path(env)
    from kazma_core.paths import data_dir

    return data_dir() / "file_checkpoints.db"


class FileCheckpointStore:
    def __init__(self, db_path: str | os.PathLike[str]) -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        parent = os.path.dirname(self._db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self._db_path, timeout=10.0)
        from kazma_core.config_store import apply_sqlite_pragmas

        apply_sqlite_pragmas(conn)
        return conn

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    def create(
        self,
        paths: list[str],
        *,
        reason: str = "",
        thread_id: str = "",
        root: Path | None = None,
    ) -> str:
        ws = Path(root) if root is not None else resolve_active_root()
        files: list[dict[str, Any]] = []
        for raw in paths[:MAX_FILES]:
            access = check_path_access(str(raw), "read")
            if not access.allowed:
                continue
            p = Path(access.resolved)
            if not p.is_file():
                files.append({"path": str(p), "missing": True, "content": "", "sha256": ""})
                continue
            size = p.stat().st_size
            if size > MAX_FILE_BYTES:
                logger.info("[file_checkpoints] skip large file %s (%s bytes)", p, size)
                continue
            data = p.read_bytes()
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            files.append({
                "path": str(p),
                "missing": False,
                "content": text,
                "sha256": hashlib.sha256(data).hexdigest(),
            })
        if not files:
            raise ValueError("no checkpointable files")
        cid = uuid.uuid4().hex
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO file_checkpoints (id, thread_id, workspace_root, created_at, reason, files_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (cid, thread_id or "", str(ws), now, reason[:200], json.dumps(files)),
            )
            rows = conn.execute(
                "SELECT id FROM file_checkpoints WHERE workspace_root = ? ORDER BY created_at DESC",
                (str(ws),),
            ).fetchall()
            extra = [r[0] for r in rows[_KEEP:]]
            if extra:
                conn.executemany("DELETE FROM file_checkpoints WHERE id = ?", [(e,) for e in extra])
        return cid

    def get(self, checkpoint_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, thread_id, workspace_root, created_at, reason, files_json "
                "FROM file_checkpoints WHERE id = ?",
                (checkpoint_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "thread_id": row[1],
            "workspace_root": row[2],
            "created_at": row[3],
            "reason": row[4],
            "files": json.loads(row[5]),
        }

    def restore(self, checkpoint_id: str) -> list[str]:
        """Write stored bytes back (containment-checked). Used as in-tool rollback."""
        rec = self.get(checkpoint_id)
        if rec is None:
            raise ValueError(f"unknown checkpoint {checkpoint_id}")
        ws = Path(rec["workspace_root"])
        restored: list[str] = []
        for item in rec["files"]:
            raw = str(item.get("path") or "")
            access = check_path_access(raw, "write")
            if not access.allowed:
                continue
            p = Path(access.resolved)
            if item.get("missing"):
                if p.is_file():
                    p.unlink()
                restored.append(str(p))
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(item.get("content") or ""), encoding="utf-8")
            restored.append(str(p))
        return restored

    def list_for_workspace(self, root: Path | None = None) -> list[dict[str, Any]]:
        ws = str(root or resolve_active_root())
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, created_at, reason FROM file_checkpoints "
                "WHERE workspace_root = ? ORDER BY created_at DESC LIMIT ?",
                (ws, _KEEP),
            ).fetchall()
        return [{"id": r[0], "created_at": r[1], "reason": r[2]} for r in rows]


def get_file_checkpoint_store() -> FileCheckpointStore:
    global _store
    with _lock:
        if _store is None:
            _store = FileCheckpointStore(_db_path())
        return _store


def reset_file_checkpoint_store() -> None:
    global _store
    with _lock:
        _store = None


def create_checkpoint(paths: list[str], *, reason: str = "", thread_id: str = "") -> str:
    return get_file_checkpoint_store().create(paths, reason=reason, thread_id=thread_id)


def restore_checkpoint(checkpoint_id: str) -> list[str]:
    return get_file_checkpoint_store().restore(checkpoint_id)
