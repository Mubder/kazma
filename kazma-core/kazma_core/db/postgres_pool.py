"""Process-wide psycopg connection pool for shared Kazma state.

Used when ``KAZMA_DATABASE_URL`` points at Postgres. Safe for multiple
uvicorn workers / replicas; SQLite must not be used for multi-replica.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any, Generator

from kazma_core.db.backend import get_database_url, is_postgres, require_postgres_driver

__all__ = ["PostgresPool", "get_postgres_pool", "reset_postgres_pool"]

logger = logging.getLogger(__name__)

_pool: PostgresPool | None = None
_lock = threading.Lock()


class PostgresPool:
    """Thin wrapper around psycopg ConnectionPool (sync, shared)."""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10) -> None:
        psycopg, dict_row = require_postgres_driver()
        try:
            from psycopg_pool import ConnectionPool  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "Postgres pool requires: pip install 'psycopg[binary,pool]>=3.1'"
            ) from exc

        self._dict_row = dict_row
        # timeout = seconds a caller waits for a free connection. Default 30
        # let /health/ready pin the event loop while pool workers sat in
        # wait_conn (live 2026-08-31). Fail the ping instead of hanging.
        try:
            checkout_s = float(os_env("KAZMA_PG_POOL_TIMEOUT", "5"))
        except ValueError:
            checkout_s = 5.0
        checkout_s = max(1.0, min(checkout_s, 30.0))
        self._pool = ConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            timeout=checkout_s,
            kwargs={"row_factory": dict_row, "autocommit": False},
            open=True,
        )
        logger.info(
            "[PostgresPool] opened min=%s max=%s timeout=%s",
            min_size,
            max_size,
            checkout_s,
        )

    @contextmanager
    def connection(self) -> Generator[Any, None, None]:
        with self._pool.connection() as conn:
            yield conn

    def execute(self, sql: str, params: tuple | list | dict | None = None) -> list[dict]:
        with self.connection() as conn:
            with conn.cursor() as cur:
                if params is None:
                    cur.execute(sql)
                else:
                    cur.execute(sql, params)
                if cur.description:
                    rows = list(cur.fetchall())
                else:
                    rows = []
            conn.commit()
            return rows

    def execute_one(self, sql: str, params: tuple | list | dict | None = None) -> dict | None:
        rows = self.execute(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        try:
            self._pool.close()
        except Exception:
            pass


def get_postgres_pool() -> PostgresPool | None:
    """Return shared pool when Postgres is configured; else None.

    Retries pool creation on transient connection failures — psycopg's
    ``ConnectionPool(open=True)`` can fail its first ``check()`` on a brief
    network blip (common on Windows against a Docker-bridge, or when the DB
    container just started). A transient failure here previously surfaced as
    ``RuntimeError("Postgres pool unavailable")`` in ``reconcile_from_yaml``
    and crashed boot. We now retry a few times with a short backoff so a
    momentary unreachable DB doesn't kill startup.

    Note: we check the DSN directly rather than ``is_postgres()`` so that a
    caller that already determined Postgres is active (``_use_postgres()``)
    isn't tripped up by an env-var resolution race between that check and
    this call (the .env load can settle at slightly different moments during
    early boot). If a DSN is present, we honor it.
    """
    global _pool
    dsn = get_database_url()
    if not dsn and not is_postgres():
        return None
    with _lock:
        if _pool is None:
            if not dsn:
                dsn = get_database_url()
            if not dsn:
                return None
            # Normalize postgres:// → postgresql:// for psycopg
            if dsn.startswith("postgres://"):
                dsn = "postgresql://" + dsn[len("postgres://") :]
            min_size = int(os_env("KAZMA_PG_POOL_MIN", "1"))
            max_size = int(os_env("KAZMA_PG_POOL_MAX", "10"))

            import time

            attempts = int(os_env("KAZMA_PG_POOL_RETRIES", "5"))
            delay = float(os_env("KAZMA_PG_POOL_RETRY_DELAY", "1.0"))
            last_exc: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    _pool = PostgresPool(dsn, min_size=min_size, max_size=max_size)
                    _ensure_core_schema(_pool)
                    break
                except Exception as exc:
                    # Pool construction/health-check failed. Reset any
                    # half-built pool and retry — the DB may be mid-startup.
                    last_exc = exc
                    logger.warning(
                        "[PostgresPool] attempt %d/%d failed: %s",
                        attempt,
                        attempts,
                        exc,
                    )
                    _pool = None
                    if attempt < attempts:
                        time.sleep(delay)
            if _pool is None:
                # All retries exhausted — surface the underlying cause so
                # callers see WHY (not just "pool unavailable").
                raise RuntimeError(
                    f"Postgres pool unreachable after {attempts} attempts: {last_exc}"
                ) from last_exc
        return _pool


def reset_postgres_pool() -> None:
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None


def os_env(key: str, default: str) -> str:
    import os

    return (os.environ.get(key) or default).strip() or default


def _ensure_core_schema(pool: PostgresPool) -> None:
    """Idempotent schema for multi-replica shared tables."""
    ddl = """
    CREATE TABLE IF NOT EXISTS kazma_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        category TEXT DEFAULT 'general',
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS kazma_web_sessions (
        session_hash TEXT PRIMARY KEY,
        payload JSONB NOT NULL,
        expires_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS kazma_platform_users (
        user_id TEXT PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'operator',
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        meta JSONB DEFAULT '{}'::jsonb
    );
    CREATE TABLE IF NOT EXISTS kazma_chat_sessions (
        tenant_id TEXT NOT NULL DEFAULT 'default',
        session_id TEXT NOT NULL,
        messages JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_at TEXT DEFAULT '',
        updated_at TEXT DEFAULT '',
        total_cost DOUBLE PRECISION DEFAULT 0,
        total_tokens INTEGER DEFAULT 0,
        thread_id TEXT DEFAULT '',
        title TEXT DEFAULT '',
        archived BOOLEAN DEFAULT FALSE,
        pinned BOOLEAN DEFAULT FALSE,
        PRIMARY KEY (tenant_id, session_id)
    );
    CREATE TABLE IF NOT EXISTS kazma_swarm_tasks (
        id TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        prompt TEXT NOT NULL,
        status TEXT NOT NULL,
        workers JSONB DEFAULT '[]'::jsonb,
        result JSONB,
        context TEXT DEFAULT '',
        metadata JSONB DEFAULT '{}'::jsonb,
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        cost DOUBLE PRECISION DEFAULT 0,
        tokens INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_web_sessions_exp ON kazma_web_sessions(expires_at);
    CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON kazma_chat_sessions(updated_at);
    CREATE INDEX IF NOT EXISTS idx_swarm_tasks_status ON kazma_swarm_tasks(status);
    """
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
            # Idempotent column migrations for pre-existing databases
            # (CREATE TABLE IF NOT EXISTS only helps fresh installs).
            try:
                cur.execute(
                    "ALTER TABLE kazma_chat_sessions ADD COLUMN IF NOT EXISTS "
                    "pinned BOOLEAN DEFAULT FALSE"
                )
            except Exception:
                pass
        conn.commit()
    logger.info("[PostgresPool] core schema ensured")
