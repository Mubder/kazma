"""The Time Travel audit trail: one row per restore, fork and clear (TT-11).

Restore and fork wrote an INFO log line and nothing else, so nobody could say
afterwards who rewound a conversation, to which step, or what it held before.
Each row names the action, its outcome (``ok`` or the refusal code), who
asked and from which surface, the conversation, the snapshot, the new
conversation of a fork, and the checkpoint the conversation was at before
(``prior_checkpoint_id``: LangGraph keeps it, so the state before a restore
can still be found while checkpoint retention, AGENTS §41, holds it).

``<data dir>/time_travel_audit.db``, a bundle store (``store_registry``). It
holds ids and counts, never message text. Bounded: rows older than
:data:`RETENTION_DAYS` and beyond :data:`MAX_ROWS` are dropped as rows are
written. Writes are blocking: call them off the event loop.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["MAX_ROWS", "RETENTION_DAYS", "db_path", "set_db_path_for_tests",
           "write_audit_row"]

#: Rows kept at most, and for how long.
MAX_ROWS = 20_000
RETENTION_DAYS = 365

_SCHEMA = """
CREATE TABLE IF NOT EXISTS time_travel_audit (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  at                  REAL NOT NULL,
  action              TEXT NOT NULL,
  outcome             TEXT NOT NULL,
  actor               TEXT NOT NULL DEFAULT '',
  surface             TEXT NOT NULL DEFAULT '',
  thread_id           TEXT NOT NULL DEFAULT '',
  snapshot_id         TEXT NOT NULL DEFAULT '',
  target_thread_id    TEXT NOT NULL DEFAULT '',
  prior_checkpoint_id TEXT NOT NULL DEFAULT '',
  detail_json         TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_tt_audit_thread ON time_travel_audit(thread_id, at);
"""

_override: str | None = None
_ready: set[str] = set()
_lock = threading.Lock()


def db_path() -> str:
    if _override:
        return _override
    from kazma_core.paths import data_dir

    return str(Path(data_dir()) / "time_travel_audit.db")


def set_db_path_for_tests(path: str | None) -> None:
    """Point the trail at a different file (tests only)."""
    global _override
    _override = path


def _connect() -> sqlite3.Connection:
    from kazma_core.config_store import apply_sqlite_pragmas

    path = db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    apply_sqlite_pragmas(conn)
    with _lock:
        if path not in _ready:
            conn.executescript(_SCHEMA)
            _ready.add(path)
    return conn


def write_audit_row(
    action: str,
    outcome: str,
    *,
    actor: str = "",
    surface: str = "",
    thread_id: str = "",
    snapshot_id: str = "",
    target_thread_id: str = "",
    prior_checkpoint_id: str = "",
    detail: dict[str, Any] | None = None,
) -> bool:
    """Append one row. False when it could not be written (logged at WARNING:
    the action already happened, the trail must say it is missing a row)."""
    from kazma_core.db.sqlite_session import committed_and_closed

    try:
        with committed_and_closed(_connect()) as conn:
            now = time.time()
            conn.execute(
                "INSERT INTO time_travel_audit (at, action, outcome, actor, surface, thread_id, "
                "snapshot_id, target_thread_id, prior_checkpoint_id, detail_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now, action, outcome, actor[:200], surface[:40], thread_id, snapshot_id,
                 target_thread_id, prior_checkpoint_id,
                 json.dumps(detail or {}, default=str)[:4000]),
            )
            conn.execute("DELETE FROM time_travel_audit WHERE at < ?", (now - RETENTION_DAYS * 86400,))
            conn.execute(
                "DELETE FROM time_travel_audit WHERE id <= "
                "(SELECT id FROM time_travel_audit ORDER BY id DESC LIMIT 1 OFFSET ?)",
                (MAX_ROWS,),
            )
        return True
    except (sqlite3.Error, OSError):
        logger.warning(
            "[TimeTravel] audit row NOT written: action=%s outcome=%s thread=%s snapshot=%s",
            action, outcome, thread_id[:12], snapshot_id[:12], exc_info=True,
        )
        return False


def _recent_audit_rows(thread_id: str | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
    """The newest rows, of one conversation or of all."""
    from kazma_core.db.sqlite_session import committed_and_closed

    with committed_and_closed(_connect()) as conn:
        conn.row_factory = sqlite3.Row
        if thread_id:
            rows = conn.execute(
                "SELECT * FROM time_travel_audit WHERE thread_id = ? OR target_thread_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (thread_id, thread_id, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM time_travel_audit ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        try:
            item["detail"] = json.loads(item.pop("detail_json") or "{}")
        except ValueError:
            item["detail"] = {}
        out.append(item)
    return out
