"""Durable settings failures shared by startup, stores and transports."""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

from kazma_core.db.backend import get_backend

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: How long a boot waits for a settings database that is away (FA-259).
#: Under the guard's start budget (900 s), so a boot that never gets it ends
#: on its own, with the reason, before the guard gives up on it.
BOOT_WAIT_S = 780.0
#: Between two tries. Each try is the pool's own five attempts (~30 s with
#: the database away), so this is a pause, not the pace.
BOOT_RETRY_S = 10.0
#: How often the wait says it is still waiting.
_BOOT_REPORT_S = 60.0


class ConfigStoreUnavailableError(RuntimeError):
    """No settings write was acknowledged; retry after storage recovers."""

    def __init__(self, *, opening: bool = False, reason: str | None = None) -> None:
        self.backend = str(get_backend())
        self.reason = reason
        hint = (
            "Check PostgreSQL connectivity and credentials."
            if self.backend == "postgres"
            else "Check settings.db permissions, disk space and locks."
        )
        if opening and reason:
            # The database's own answer (a refused password, a missing role):
            # "it may still be starting" hid one for 18 minutes (2026-11-03).
            super().__init__(f"Kazma could not open its settings database ({self.backend}): "
                             f"{reason}. {hint}")
        elif opening:
            # Startup reads settings; it saves nothing. The save wording made
            # a boot before Postgres was up read as a lost save (2026-10-29).
            super().__init__(f"Kazma could not open its settings database ({self.backend}); "
                             f"it may still be starting. {hint}")
        else:
            super().__init__(f"Settings save could not be confirmed in durable storage ({self.backend}). {hint} Reload and review before retrying.")


def _storage_unavailable(exc: Exception) -> bool:
    """Classify operational failures without hiding schema/validation bugs."""
    if isinstance(exc, ConfigStoreUnavailableError):
        return True  # already classified, by the store itself (FA-259)
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


def wait_for_settings_store(
    open_store: Callable[[], _T],
    *,
    budget_s: float = BOOT_WAIT_S,
    retry_s: float = BOOT_RETRY_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> _T:
    """Open the settings store at boot, waiting for its database to answer.

    A boot during a database outage used to give up on the store after the
    pool's five attempts (~2 min) and exit; the guard then waited 60, 120,
    300 s between boots, so a 12-minute Postgres outage kept Kazma down 15
    (FA-259). Now the boot tries again until ``budget_s``, saying so at
    WARNING (the first time and once a minute), and goes on the moment the
    database answers. Only ``ConfigStoreUnavailableError`` is waited out:
    any other failure is a bug, raised at once. Past the budget the last
    error is raised, as before.
    """
    started = clock()
    deadline = started + max(0.0, budget_s)
    reported_at: float | None = None
    while True:
        try:
            store = open_store()
        except ConfigStoreUnavailableError as exc:
            now = clock()
            if now + retry_s > deadline:
                logger.critical(
                    "[startup] gave up waiting for the settings database after %.0f s: %s",
                    now - started, exc,
                )
                raise
            if reported_at is None or now - reported_at >= _BOOT_REPORT_S:
                logger.warning(
                    "[startup] waiting for the settings database (%s): %.0f s waited, "
                    "trying again for up to %.0f s more",
                    exc, now - started, deadline - now,
                )
                reported_at = now
            sleep(retry_s)
            continue
        if reported_at is not None:
            logger.warning(
                "[startup] the settings database answers after %.0f s; the boot goes on",
                clock() - started,
            )
        return store
