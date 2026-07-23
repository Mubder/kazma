"""Database backend selection for multi-replica / SaaS deployments.

Default remains SQLite (single-node). Set::

    KAZMA_DATABASE_URL=postgresql://user:pass@host:5432/kazma

to enable the Postgres pool for shared state that must survive horizontal
scale-out (config, web sessions, platform RBAC, task metadata).

LangGraph checkpoints can use the same URL via
``KAZMA_CHECKPOINT_BACKEND=postgres`` (requires ``psycopg[binary]`` and
``langgraph-checkpoint-postgres``).
"""

from __future__ import annotations

from kazma_core.db.backend import (
    DatabaseBackend,
    get_backend,
    get_database_url,
    is_postgres,
    require_postgres_driver,
)

__all__ = [
    "DatabaseBackend",
    "get_backend",
    "get_database_url",
    "is_postgres",
    "require_postgres_driver",
]
