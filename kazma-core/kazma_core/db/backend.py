"""Database backend detection (SQLite default vs Postgres)."""

from __future__ import annotations

import logging
import os
from enum import StrEnum

__all__ = [
    "DatabaseBackend",
    "get_backend",
    "get_database_url",
    "is_postgres",
    "require_postgres_driver",
]

logger = logging.getLogger(__name__)


class DatabaseBackend(StrEnum):
    SQLITE = "sqlite"
    POSTGRES = "postgres"


def get_database_url() -> str | None:
    """Return ``KAZMA_DATABASE_URL`` / ``DATABASE_URL`` if set."""
    for key in ("KAZMA_DATABASE_URL", "DATABASE_URL"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return raw
    return None


def get_backend() -> DatabaseBackend:
    """Resolve active backend from env."""
    forced = (os.environ.get("KAZMA_DB_BACKEND") or "").strip().lower()
    if forced in ("postgres", "postgresql", "pg"):
        return DatabaseBackend.POSTGRES
    if forced in ("sqlite", "sqlite3"):
        return DatabaseBackend.SQLITE
    url = get_database_url() or ""
    if url.startswith("postgres://") or url.startswith("postgresql://"):
        return DatabaseBackend.POSTGRES
    return DatabaseBackend.SQLITE


def is_postgres() -> bool:
    return get_backend() == DatabaseBackend.POSTGRES


def require_postgres_driver() -> Any:  # type: ignore[name-defined]
    """Import psycopg (v3) or raise a clear install hint."""
    try:
        import psycopg  # type: ignore
        from psycopg.rows import dict_row  # type: ignore

        return psycopg, dict_row
    except ImportError as exc:
        raise ImportError(
            "Postgres backend requires: pip install 'psycopg[binary]>=3.1' "
            "(or kazma[postgres]). Set KAZMA_DATABASE_URL to a postgresql:// URL."
        ) from exc


# Late import for type checkers
from typing import Any  # noqa: E402
