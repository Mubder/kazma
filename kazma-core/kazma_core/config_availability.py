"""Durable settings failures shared by startup, stores and transports."""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from kazma_core.db.backend import get_backend

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: How long a boot waits for a settings database that is away (FA-259),
#: when no guard watches it. Under the guard's start budget (900 s), so a
#: boot that never gets it ends on its own, with the reason.
BOOT_WAIT_S = 780.0
#: The file the guard hands the server (resilience plan A3). While the boot
#: waits for its database it writes there what it waits for, on every try;
#: the guard then keeps the start open, pages once, and does not restart a
#: server that is only waiting (on 2026-11-03/04 33 of 34 restarts were a
#: boot that gave up after 780 s, was restarted after 300 s, and paged each
#: time). With the guard there, the guard decides how long: no budget here.
BOOT_STATUS_ENV = "KAZMA_GUARD_BOOT_FILE"
#: Between two tries. Each try is the pool's own five attempts (~30 s with
#: the database away), so this is a pause, not the pace.
BOOT_RETRY_S = 10.0
#: How often the wait says it is still waiting.
_BOOT_REPORT_S = 60.0


class ConfigStoreUnavailableError(RuntimeError):
    """No settings write was acknowledged; retry after storage recovers."""

    #: True when the cause is Kazma's own missing Postgres driver: the
    #: database may be fine, so this is never counted as an outage.
    driver_missing = False

    def __init__(self, *, opening: bool = False, reason: str | None = None) -> None:
        self.backend = str(get_backend())
        self.reason = reason
        hint = (
            "Check PostgreSQL connectivity and credentials."
            if self.backend == "postgres"
            else "Check settings.db permissions, disk space and locks."
        )
        if reason and reason.startswith("Kazma's Postgres driver is not installed"):
            # postgres_pool.DRIVER_MISSING already says what to run, and
            # "check connectivity" sent the drill VM's owner to a database
            # that was fine (2026-11-06).
            super().__init__(f"Kazma could not open its settings database ({self.backend}): {reason}.")
            self.driver_missing = True  # no outage (db.availability._outage_one)
            return
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


def _boot_status_path() -> Path | None:
    """The guard's boot-status file, or None when no guard watches this boot."""
    raw = (os.environ.get(BOOT_STATUS_ENV) or "").strip()
    return Path(raw) if raw else None


def write_boot_status(waiting_for: str | None, *, since: float | None = None,
                      reason: str | None = None) -> bool:
    """Tell the guard what this boot waits for (None: nothing any more).

    Written whole (a temporary file, then a rename), so the guard never reads
    half a record. ``at`` is wall-clock time: the guard compares it with when
    it spawned the server, and the server's pid is not the guard's child's on
    Windows (the venv's python.exe is a launcher). A failure is a WARNING and
    False: the boot goes on whether or not the guard can be told.
    """
    path = _boot_status_path()
    if path is None:
        return False
    record = {
        "pid": os.getpid(),
        "waiting_for": waiting_for,
        "since": since,
        "at": time.time(),
        "reason": (reason or "")[:300] or None,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(record), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError as exc:
        logger.warning("[startup] could not tell the guard what the boot waits for (%s): %s",
                       path, exc)
        return False


def wait_for_settings_store(
    open_store: Callable[[], _T],
    *,
    budget_s: float | None = None,
    retry_s: float = BOOT_RETRY_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    on_wait: Callable[[float], None] | None = None,
) -> _T:
    """Open the settings store at boot, waiting for its database to answer.

    A boot during a database outage used to give up on the store after the
    pool's five attempts (~2 min) and exit; the guard then waited 60, 120,
    300 s between boots, so a 12-minute Postgres outage kept Kazma down 15
    (FA-259). Now the boot tries again, saying so at WARNING (the first time,
    then once a minute at INFO), and goes on the moment the database answers.
    Only ``ConfigStoreUnavailableError`` is waited out: any other failure is a
    bug, raised at once.

    How long: ``budget_s`` when given; else, with a guard watching
    (``KAZMA_GUARD_BOOT_FILE``), for as long as the guard lets it -- every try
    rewrites the boot-status file, which is how the guard knows the boot is
    waiting and not hung -- and without one ``BOOT_WAIT_S``. Past the budget
    the last error is raised, as before.

    *on_wait* is told the seconds waited after each failed try; it may stop
    the wait by raising (``read_only_mode.read_only_after``: the read-only
    start from the settings' copy, resilience plan A4).
    """
    if budget_s is None:
        budget_s = math.inf if _boot_status_path() is not None else BOOT_WAIT_S
    started = clock()
    since_wall = time.time()
    deadline = started + max(0.0, budget_s)
    reported_at: float | None = None
    told_guard = False
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
                if told_guard:
                    write_boot_status(None)
                raise
            told_guard = write_boot_status(
                "database", since=since_wall,
                reason=getattr(exc, "reason", None) or str(exc),
            ) or told_guard
            if on_wait is not None:
                on_wait(now - started)
            if reported_at is None or now - reported_at >= _BOOT_REPORT_S:
                # The first time at WARNING; later reminders at INFO: the
                # outage itself is said once by db.availability.
                remaining = ("as long as the guard lets it" if math.isinf(deadline)
                             else f"up to {deadline - now:.0f} s more")
                logger.log(
                    logging.WARNING if reported_at is None else logging.INFO,
                    "[startup] waiting for the settings database (%s): %.0f s waited, "
                    "trying again for %s",
                    exc, now - started, remaining,
                )
                reported_at = now
            sleep(retry_s)
            continue
        if told_guard:
            write_boot_status(None)
        if reported_at is not None:
            logger.info(
                "[startup] the settings database answers after %.0f s; the boot goes on",
                clock() - started,
            )
        return store
