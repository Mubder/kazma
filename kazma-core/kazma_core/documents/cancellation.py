"""A document worker run stops when the call that started it gives up.

Every async ``DocumentService`` operation runs its isolated worker from a
thread (``asyncio.to_thread``). Cancelling the awaiting coroutine -- the
agent's tool timeout, a turn's Stop -- cancelled only the await: the thread
went on waiting for its worker, and the worker (a container behind the
broker, or a local process tree) kept a CPU core busy until its own time
limit. Live on 2026-10-22 a chat OCR of a 54-page scan left two abandoned
OCR containers running, and every retry the model made shared the CPU with
them and timed out too.

:func:`run_cancellable` runs a blocking call in a thread with a cancel
signal bound for it (a ContextVar, which ``asyncio.to_thread`` carries into
the thread); when the awaiting coroutine is cancelled it sets the signal and
waits, bounded, for the thread to end. The worker runners check
:func:`cancel_requested` while they wait: the broker exchange closes its
connection (the broker then kills and removes the container) and the local
sandbox kills the process tree. Both raise :class:`WorkerCancelled`.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
from collections.abc import Callable
from typing import Any, TypeVar

from .errors import DocumentSandboxError

__all__ = ["WorkerCancelled", "cancel_requested", "run_cancellable"]

logger = logging.getLogger(__name__)

T = TypeVar("T")

_CANCEL: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "kazma_document_worker_cancel", default=None
)

#: How long a cancelled call waits for its worker to be stopped. The broker
#: removes a container within a second of the connection closing.
_STOP_WAIT_SECONDS = 10.0


class WorkerCancelled(DocumentSandboxError):
    """The run was stopped because its caller gave up."""

    def __init__(self) -> None:
        super().__init__("Document worker run was cancelled", code="worker_cancelled")


def cancel_requested() -> bool:
    """True when the call this thread runs for has been cancelled."""

    event = _CANCEL.get()
    return event is not None and event.is_set()


async def run_cancellable(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """``asyncio.to_thread(fn, ...)`` whose worker stops when the await is cancelled."""

    event = threading.Event()

    def call() -> T:
        token = _CANCEL.set(event)
        try:
            return fn(*args, **kwargs)
        finally:
            _CANCEL.reset(token)

    task = asyncio.ensure_future(asyncio.to_thread(call))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        event.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=_STOP_WAIT_SECONDS)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):  # noqa: BLE001
            # The run's own outcome is moot: its caller is gone. What matters
            # is that the worker was told to stop, which it was.
            if not task.done():
                logger.warning(
                    "[documents.cancellation] a cancelled worker run did not stop within %.0f s",
                    _STOP_WAIT_SECONDS,
                )
        raise
