"""Claimed incident work always ends recorded.

A decision (``acting``), a card send (``notify = 'sending'``) and a diagnosis
(``diagnosis_state = 'running'``) each claim their incident row before the
work starts. Work that raised past its handler left the claim in place for
good: the incident could neither be acted on nor expire, the unique index
blocked a new incident for the run, and the next start recorded "Kazma
stopped while the action ran" over a process that never stopped (audit
2026-10-26 AUD-018: only RuntimeError, OSError and ValueError were caught,
and a "database is locked", an httpx error, a KeyError or a TypeError
escaped).

Two parts, used together at each claim:

* :data:`WORK_FAILURES` -- the failure classes an action or a send raises in
  practice, named: each site answers them as an ordinary failed outcome;
* :class:`settle_on_error` -- anything else: the claim is settled as failed,
  then the exception goes on (never swallowed). Cancellation is not a
  failure: a cancelled claim stays as it is, and the next start's recovery
  decides it (``IncidentStore.recover``).
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from types import TracebackType

import httpx

__all__ = ["WORK_FAILURES", "settle_on_error"]

#: What an action, a send or a diagnosis raises in practice: the swarm,
#: scheduled and document controls (SQLite and their own errors), the chat
#: apps' HTTP clients, a malformed row (KeyError / TypeError).
WORK_FAILURES: tuple[type[BaseException], ...] = (
    RuntimeError, OSError, ValueError, LookupError, TypeError, AttributeError,
    sqlite3.Error, httpx.HTTPError,
)


class settle_on_error:  # noqa: N801 -- used as a statement: ``async with settle_on_error(...)``
    """Settle a claim when the block ends with an error no handler took.

    *settle* is awaited with the exception; the exception then propagates
    (``__aexit__`` returns False). A clean exit, a cancellation and a
    ``KeyboardInterrupt``/``SystemExit`` settle nothing.
    """

    def __init__(self, settle: Callable[[BaseException], Awaitable[object]]) -> None:
        self._settle = settle

    async def __aenter__(self) -> settle_on_error:
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                        tb: TracebackType | None) -> bool:
        if isinstance(exc, Exception) and not isinstance(exc, asyncio.CancelledError):
            await self._settle(exc)
        return False
