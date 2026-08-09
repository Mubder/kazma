"""Per-LLM-call ledger — durable, queryable record of every model call.

Closes the observability gap from the long-horizon audit: previously token
usage/cost/latency only existed as optional in-process Prometheus counters,
with no persisted per-call history to inspect after a multi-day run.

One row per supervisor LLM call: thread, iteration, model, provider, token
usage, cost, latency, status, error kind, and whether the call was served by
a failover model. SQLite WAL (``kazma-data/llm_calls.db``), module singleton,
thread-safe, and strictly best-effort — a ledger failure must never affect
the turn it describes.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import UTC, datetime
from typing import Any

__all__ = ["close_llm_ledger", "query_recent", "record_llm_call"]

logger = logging.getLogger(__name__)

_DEFAULT_DB = "kazma-data/llm_calls.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    thread_id TEXT DEFAULT '',
    iteration INTEGER DEFAULT 0,
    provider TEXT DEFAULT '',
    model TEXT DEFAULT '',
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    duration_ms REAL DEFAULT 0.0,
    status TEXT DEFAULT 'ok',
    error_kind TEXT DEFAULT '',
    failover_from TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_ts ON llm_calls(ts);
CREATE INDEX IF NOT EXISTS idx_llm_calls_thread ON llm_calls(thread_id);
"""

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()


def _get_conn(db_path: str = _DEFAULT_DB) -> sqlite3.Connection:
    global _conn
    if _conn is None:
        from pathlib import Path

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(db_path, check_same_thread=False)
        from kazma_core.config_store import apply_sqlite_pragmas

        apply_sqlite_pragmas(_conn)
        _conn.executescript(_SCHEMA)
    return _conn


def record_llm_call(
    *,
    thread_id: str = "",
    iteration: int = 0,
    provider: str = "",
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cost_usd: float = 0.0,
    duration_ms: float = 0.0,
    status: str = "ok",
    error_kind: str = "",
    failover_from: str = "",
) -> None:
    """Record one LLM call. Best-effort: never raises, never blocks the turn."""
    try:
        with _lock:
            conn = _get_conn()
            conn.execute(
                """INSERT INTO llm_calls
                   (ts, thread_id, iteration, provider, model,
                    prompt_tokens, completion_tokens, total_tokens,
                    cost_usd, duration_ms, status, error_kind, failover_from)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(UTC).isoformat(),
                    thread_id,
                    int(iteration or 0),
                    provider,
                    model,
                    int(prompt_tokens or 0),
                    int(completion_tokens or 0),
                    int(prompt_tokens or 0) + int(completion_tokens or 0),
                    float(cost_usd or 0.0),
                    float(duration_ms or 0.0),
                    status,
                    error_kind,
                    failover_from,
                ),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 — observability must be invisible
        logger.debug("[llm-ledger] record failed (non-fatal): %s", exc)


def query_recent(limit: int = 100, thread_id: str | None = None) -> list[dict[str, Any]]:
    """Return the most recent ledger rows (newest first)."""
    with _lock:
        conn = _get_conn()
        conn.row_factory = sqlite3.Row
        if thread_id:
            rows = conn.execute(
                "SELECT * FROM llm_calls WHERE thread_id = ? ORDER BY id DESC LIMIT ?",
                (thread_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM llm_calls ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def close_llm_ledger() -> None:
    """Close the ledger connection (process shutdown)."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:  # noqa: BLE001
                pass
            try:
                _conn.close()
            except Exception:  # noqa: BLE001
                pass
            _conn = None
