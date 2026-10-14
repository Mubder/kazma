"""The scheduler's clock: wall time and the poll loop's sleep, in one place.

Every time the cron engine reads -- a job's due check, a fire's lease and
lateness, the outbox's backoff, an event's timestamp, the poll heartbeat --
and the poll loop's wait come from one :class:`CronClock`. In production it
is :data:`SYSTEM_CLOCK` (``time.time``, ``datetime.now(UTC)``,
``asyncio.sleep``). The Scheduled qualification soak
(``scripts/scheduled_soak.py``) passes a fake clock instead, so the real
scheduler and store run through seven simulated days in minutes: a seam the
engine reads, never a patch of ``time`` or ``datetime``.

A store and the scheduler built on it share the store's clock unless the
scheduler is given its own.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

__all__ = ["SYSTEM_CLOCK", "CronClock"]


@runtime_checkable
class CronClock(Protocol):
    """Wall time and the poll loop's sleep."""

    def time(self) -> float:
        """Now, as epoch seconds."""

    def now(self) -> datetime:
        """Now, as an aware UTC datetime (the same instant as :meth:`time`)."""

    async def sleep(self, seconds: float) -> None:
        """Wait *seconds* of this clock's time."""


class _SystemClock:
    """The real clock: what the engine reads in production."""

    def time(self) -> float:
        return time.time()

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


#: The process's real clock (the default of every store and scheduler).
SYSTEM_CLOCK = _SystemClock()
