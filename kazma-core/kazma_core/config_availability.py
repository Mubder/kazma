"""Durable settings failures shared by startup, stores and transports."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from kazma_core.db.backend import get_backend


class ConfigStoreUnavailableError(RuntimeError):
    """No settings write was acknowledged; retry after storage recovers."""

    def __init__(self) -> None:
        self.backend = str(get_backend())
        hint = (
            "Check PostgreSQL connectivity and credentials."
            if self.backend == "postgres"
            else "Check settings.db permissions, disk space and locks."
        )
        super().__init__(f"Settings save could not be confirmed in durable storage ({self.backend}). {hint} Reload and review before retrying.")


def _storage_unavailable(exc: Exception) -> bool:
    """Classify operational failures without hiding schema/validation bugs."""
    cause = exc.__cause__
    if isinstance(cause, Exception) and cause is not exc:
        if _storage_unavailable(cause):
            return True
    if isinstance(exc, sqlite3.OperationalError):
        return any(word in str(exc).lower() for word in (
            "locked", "busy", "readonly", "read-only", "disk", "unable to open",
        ))
    try:
        from psycopg import InterfaceError, OperationalError
        from psycopg_pool import PoolTimeout
    except ImportError:
        return False
    return isinstance(exc, (InterfaceError, OperationalError, PoolTimeout))


def _storage_failures() -> tuple[type[Exception], ...]:
    """Expected backend failures, including the pool's RuntimeError wrapper."""
    failures: tuple[type[Exception], ...] = (sqlite3.OperationalError, OSError, RuntimeError)
    try:
        from psycopg import InterfaceError, OperationalError
        from psycopg_pool import PoolTimeout
    except ImportError:
        return failures
    return (*failures, InterfaceError, OperationalError, PoolTimeout)


@contextmanager
def durable_settings_write() -> Iterator[None]:
    """Translate operational errors after a transaction has rolled back."""
    try:
        yield
    except _storage_failures() as exc:
        if isinstance(exc, ConfigStoreUnavailableError):
            raise
        if _storage_unavailable(exc):
            raise ConfigStoreUnavailableError() from exc
        raise
