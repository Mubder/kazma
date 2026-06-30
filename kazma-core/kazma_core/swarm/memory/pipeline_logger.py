"""Unified pipeline logger — SQLite-backed SwarmMessageBus listener.

Captures EVERY pipeline step, tool execution, raw worker output, and
intermediate state to a SQLite database.  The Web UI reads this for
dense diagnostic logs regardless of where the command originated.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DB = "kazma-data/pipeline_logs.db"

# Singleton connection
_conn: sqlite3.Connection | None = None


def _get_conn(db_path: str = _DEFAULT_DB) -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(db_path)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            correlation_id TEXT NOT NULL,
            worker_name TEXT DEFAULT '',
            stage TEXT DEFAULT '',
            level TEXT DEFAULT 'info',
            event_type TEXT NOT NULL,
            message TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}',
            raw_output TEXT DEFAULT ''
        )
    """)
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_correlation ON pipeline_logs(correlation_id)")
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_worker ON pipeline_logs(worker_name)")
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON pipeline_logs(timestamp)")
    _conn.commit()
    return _conn


class PipelineLogger:
    """SQLite-backed listener for every pipeline event.

    Usage::

        plog = PipelineLogger()
        plog.log_step("corr-123", "core", "researcher", "info", "step_start", "Starting research")
        plog.log_tool_exec("corr-123", "core", "shell_exec", "ls -la", "file1\\nfile2")
        plog.log_output("corr-123", "core", "researcher", "Research complete: ...")

    The Web UI reads from ``pipeline_logs`` table for real-time diagnostics.
    """

    def __init__(self, db_path: str = _DEFAULT_DB) -> None:
        self._conn = _get_conn(db_path)

    def log_step(
        self,
        correlation_id: str,
        worker_name: str,
        stage: str,
        level: str,
        event_type: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log a pipeline step/transition."""
        self._conn.execute(
            """INSERT INTO pipeline_logs
               (timestamp, correlation_id, worker_name, stage, level, event_type, message, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                time.time(),
                correlation_id,
                worker_name,
                stage,
                level,
                event_type,
                message,
                json.dumps(metadata or {}),
            ),
        )
        self._conn.commit()

    def log_tool_exec(
        self,
        correlation_id: str,
        worker_name: str,
        tool_name: str,
        tool_args: str,
        tool_output: str,
    ) -> None:
        """Log a tool execution with its raw output."""
        self._conn.execute(
            """INSERT INTO pipeline_logs
               (timestamp, correlation_id, worker_name, level, event_type, message, raw_output)
               VALUES (?, ?, ?, 'info', 'tool_exec', ?, ?)""",
            (
                time.time(),
                correlation_id,
                worker_name,
                f"{tool_name}: {tool_args[:200]}",
                tool_output[:5000],
            ),
        )
        self._conn.commit()

    def log_output(
        self,
        correlation_id: str,
        worker_name: str,
        stage: str,
        output: str,
    ) -> None:
        """Log a worker's final stage output."""
        self._conn.execute(
            """INSERT INTO pipeline_logs
               (timestamp, correlation_id, worker_name, stage, level, event_type, raw_output)
               VALUES (?, ?, ?, ?, 'info', 'worker_output', ?)""",
            (
                time.time(),
                correlation_id,
                worker_name,
                stage,
                output[:10000],
            ),
        )
        self._conn.commit()

    def query_by_correlation(self, correlation_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Return all logs for a given correlation ID."""
        cursor = self._conn.execute(
            """SELECT * FROM pipeline_logs
               WHERE correlation_id = ?
               ORDER BY timestamp ASC
               LIMIT ?""",
            (correlation_id, limit),
        )
        return [_row_to_dict(row) for row in cursor.fetchall()]

    def query_by_worker(self, worker_name: str, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent logs for a worker."""
        cursor = self._conn.execute(
            """SELECT * FROM pipeline_logs
               WHERE worker_name = ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (worker_name, limit),
        )
        return [_row_to_dict(row) for row in cursor.fetchall()]

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent log entries."""
        cursor = self._conn.execute(
            "SELECT * FROM pipeline_logs ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        return [_row_to_dict(row) for row in cursor.fetchall()]


def _row_to_dict(row: tuple) -> dict[str, Any]:
    cols = ["id", "timestamp", "correlation_id", "worker_name", "stage",
            "level", "event_type", "message", "metadata", "raw_output"]
    return dict(zip(cols, row))


# Module-level singleton
_pipeline_logger: PipelineLogger | None = None


def get_pipeline_logger() -> PipelineLogger:
    global _pipeline_logger
    if _pipeline_logger is None:
        _pipeline_logger = PipelineLogger()
    return _pipeline_logger
