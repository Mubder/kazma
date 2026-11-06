"""What one send did, as the adapter that made it saw it (resilience plan F3).

An adapter's ``send`` answers True or False, and False covered three
different things: nothing reached the platform (a refused connection, an
HTTP error), part of the reply reached it (the first chunks went, a later one
failed), and nobody knows (a chunk's request went out and its answer was
lost: a read timeout, a dropped connection). The chat outbox
(``chat_outbox.py``) may send the first again; sending the other two again
would show the person the same words twice.

``GatewayManager`` opens a report around each send (:func:`reporting`); the
adapters' send loops say each part the platform accepted
(:func:`part_delivered`) and each part that failed (:func:`part_failed`,
which decides from the exception whether the request may have arrived). An
adapter that says nothing leaves an empty report: its False reads as
"nothing arrived", the answer it always gave.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass

import httpx

__all__ = ["part_delivered", "part_failed", "reporting"]


@dataclass
class _SendReport:
    """Parts the platform accepted, and whether one may have arrived unseen."""

    parts: int = 0
    uncertain: bool = False


_CURRENT: ContextVar[_SendReport | None] = ContextVar("kazma_send_report", default=None)

# The request never left: nothing can have arrived.
_NOT_SENT = (
    httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout,
    httpx.WriteError, httpx.WriteTimeout, ConnectionRefusedError,
)
# The request went out and its answer was lost: it may have arrived.
_MAYBE_SENT = (
    httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError,
    ConnectionResetError, BrokenPipeError, TimeoutError,
)


def _may_have_arrived(exc: BaseException) -> bool:
    """True when a send that raised *exc* may still have reached the platform."""
    if isinstance(exc, _NOT_SENT):
        return False
    return isinstance(exc, _MAYBE_SENT)


@contextlib.contextmanager
def reporting() -> Iterator[_SendReport]:
    """A report for the sends made inside the block (this task's only)."""
    report = _SendReport()
    token = _CURRENT.set(report)
    try:
        yield report
    finally:
        _CURRENT.reset(token)


def part_delivered() -> None:
    """The platform accepted one part of the current send."""
    report = _CURRENT.get()
    if report is not None:
        report.parts += 1


def part_failed(exc: BaseException) -> None:
    """A part of the current send raised *exc*."""
    report = _CURRENT.get()
    if report is not None and _may_have_arrived(exc):
        report.uncertain = True
