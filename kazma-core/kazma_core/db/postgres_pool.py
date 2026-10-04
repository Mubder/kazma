"""Process-wide psycopg connection pool for shared Kazma state.

Used when ``KAZMA_DATABASE_URL`` points at Postgres. Safe for multiple
uvicorn workers / replicas; SQLite must not be used for multi-replica.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from kazma_core.db.backend import get_database_url, is_postgres, require_postgres_driver

__all__ = ["PostgresPool", "get_postgres_pool", "pool_connection_kwargs", "reset_postgres_pool"]

logger = logging.getLogger(__name__)

_pool: PostgresPool | None = None
_lock = threading.Lock()

#: Seconds a new connection may take before psycopg gives up and the pool
#: retries (with its own backoff). psycopg's default is 130.
_CONNECT_TIMEOUT_S = 5


def _dsn_sets(dsn: str, param: str) -> bool:
    """Whether the operator's DSN names *param* (URL or key=value form)."""
    try:
        from psycopg import Error as PsycopgError
        from psycopg.conninfo import conninfo_to_dict
    except ImportError:
        return param in (dsn or "")
    try:
        return param in conninfo_to_dict(dsn)
    except PsycopgError:  # an odd DSN is the pool's to reject
        return param in (dsn or "")


def pool_connection_kwargs(dsn: str, **base: Any) -> dict[str, Any]:
    """Connection keywords for every psycopg pool Kazma opens.

    *base* plus a connect timeout (unless the DSN sets one). psycopg waits up
    to 130 s for a connection that does not answer, and Docker Desktop's port
    proxy accepts the TCP connection while the database container is down:
    after the Docker engine restarted on 2026-09-28 the pools' reconnects
    hung, and they stayed empty for a minute after Postgres was back. Used by
    the shared pool here and the checkpointer's (``checkpoints_pg``).
    """
    kwargs = dict(base)
    if not _dsn_sets(dsn, "connect_timeout"):
        kwargs["connect_timeout"] = _CONNECT_TIMEOUT_S
    return kwargs


def _without_nul(params: tuple | list | dict) -> tuple | list | dict:
    """String parameters with U+0000 replaced by U+FFFD (Postgres rejects NUL)."""
    def clean(v: object) -> object:
        return v.replace("\x00", "�") if isinstance(v, str) and "\x00" in v else v

    if isinstance(params, dict):
        return {k: clean(v) for k, v in params.items()}
    return type(params)(clean(v) for v in params)


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
            kwargs=pool_connection_kwargs(dsn, row_factory=dict_row, autocommit=False),
            # A connection the server closed (a restart, an idle kill) is
            # replaced at checkout, not handed to a caller as an error: on
            # 2026-09-28 the document worker's claims failed with "the
            # connection is closed" after the database came back.
            check=ConnectionPool.check_connection,
            open=True,
        )
        logger.info(
            "[PostgresPool] opened min=%s max=%s timeout=%s",
            min_size,
            max_size,
            checkout_s,
        )

    @contextmanager
    def connection(self, *, timeout: float | None = None) -> Generator[Any, None, None]:
        with self._pool.connection(timeout=timeout) as conn:
            yield conn

    def execute(self, sql: str, params: tuple | list | dict | None = None) -> list[dict]:
        """Run *sql* and commit; return its rows.

        Only a statement that produces rows returns any: an ``UPDATE`` or
        ``DELETE`` without ``RETURNING`` gives ``[]`` however many rows it
        touched. To count what a write changed, add ``RETURNING`` and count
        the rows (``TaskStore.prune_tasks`` reported 0 until it did).
        """
        # Postgres text cannot hold NUL: one raw U+0000 in any string
        # parameter fails the whole statement. JSON parameters are cleaned
        # where they are encoded (pg_helpers.json_dumps).
        if params is not None:
            params = _without_nul(params)
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

            attempts = max(1, min(int(os_env("KAZMA_PG_POOL_RETRIES", "5")), 5))
            delay = max(0.0, min(float(os_env("KAZMA_PG_POOL_RETRY_DELAY", "1.0")), 5.0))
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
                        "[PostgresPool] attempt %d/%d failed (%s)",
                        attempt,
                        attempts,
                        type(exc).__name__,
                    )
                    if _pool is not None:
                        _pool.close()
                    _pool = None
                    if attempt < attempts:
                        time.sleep(delay)
            if _pool is None:
                # All retries exhausted — surface the underlying cause so
                # callers see WHY (not just "pool unavailable").
                raise RuntimeError(
                    f"Postgres pool unreachable after {attempts} attempts"
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
    with pool.connection(timeout=5.0) as conn:
        with conn.cursor() as cur:
            # Boot schema locks must not turn a bounded connection retry
            # into an unlimited wait. Scoped to this transaction only.
            cur.execute("SET LOCAL statement_timeout = '5s'")
            cur.execute(ddl)
            # Idempotent column migrations for pre-existing databases
            # (CREATE TABLE IF NOT EXISTS only helps fresh installs). IF NOT
            # EXISTS makes a present column a no-op, so an error here is real
            # -- and it aborts this transaction, the tables above included --
            # so it goes to the caller's retry loop, never swallowed.
            cur.execute(
                "ALTER TABLE kazma_chat_sessions ADD COLUMN IF NOT EXISTS "
                "pinned BOOLEAN DEFAULT FALSE"
            )
        conn.commit()
    logger.info("[PostgresPool] core schema ensured")
