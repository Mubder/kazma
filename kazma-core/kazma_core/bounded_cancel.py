"""Cancel a task, or wait for one, without waiting forever.

``task.cancel(); await task`` trusts the task to end once it is cancelled.
An httpx request in flight does not always: on Python 3.11 with the selector
loop, a cancel that lands while anyio reads the socket is sometimes absorbed
(the task's ``cancelling()`` stays 1, no ``CancelledError`` reaches it), and
the task goes on as if nothing happened -- measured 3 times in 4,000 cancels
of a request against a local server. A loop that catches errors and runs again
then never ends, and whoever awaited it waits for good.

That happened live on 2026-10-23: the Telegram typing indicator's ``stop()``
cancelled its loop mid ``sendChatAction`` and awaited it while holding the
indicator's lock; every later message, on every chat app, waited on that
lock before its turn began. No error, no log line, readiness green, and two
hours of "typing..." in the owner's chat with no answer.

``asyncio.wait_for(task, timeout)`` is the same trap on 3.11: when the
timeout fires it cancels the task and then waits for it without a bound.

So every cancel-and-wait in product code goes through :func:`cancel_and_wait`
(cancel, re-deliver the cancel while waiting, give up after a bound with a
WARNING naming the task), and every wait on an existing task or awaitable with
a timeout through :func:`wait_then_cancel` or :func:`wait_for_bounded`.
``tests/test_bounded_cancel.py`` refuses the old shapes in product code.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

__all__ = ["cancel_and_wait", "wait_for_bounded", "wait_then_cancel"]

_T = TypeVar("_T")

#: How often a cancel is delivered again while waiting: an absorbed cancel
#: gets another chance at the task's next await.
_REDELIVER_S = 0.5


async def cancel_and_wait(task: asyncio.Future[Any] | None, *, what: str, timeout: float = 5.0) -> bool:
    """Cancel *task* and wait at most *timeout* seconds for it to end.

    Returns True when it ended (cancelled, finished or failed: its outcome is
    retrieved, never raised here), False when it is still running -- logged
    at WARNING with *what*; the task is left to end on its own. A cancel of
    the CALLER while it waits propagates as usual.
    """
    if task is None:
        return True
    if not task.done():
        task.cancel()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        while not task.done():
            remaining = deadline - loop.time()
            if remaining <= 0:
                logger.warning(
                    "[cancel] %s did not end %.0f s after it was cancelled; left running "
                    "(a cancel absorbed by the code it was in)", what, timeout,
                )
                return False
            await asyncio.wait({task}, timeout=min(remaining, _REDELIVER_S))
            if not task.done():
                task.cancel()
    _retrieve(task, what)
    return True


async def wait_then_cancel(
    task: asyncio.Future[Any] | None, *, what: str, wait: float, cancel_timeout: float = 5.0,
) -> bool:
    """Give *task* *wait* seconds to end on its own, then :func:`cancel_and_wait`.

    Returns True when it ended either way. Unlike ``asyncio.wait_for`` on
    3.11, the wait after the cancel is bounded too.
    """
    if task is None:
        return True
    if not task.done():
        await asyncio.wait({task}, timeout=max(0.0, wait))
    if task.done():
        _retrieve(task, what)
        return True
    return await cancel_and_wait(task, what=what, timeout=cancel_timeout)


async def wait_for_bounded(
    aw: Awaitable[_T], timeout: float, *, what: str, cancel_timeout: float = 5.0,
) -> _T:
    """``asyncio.wait_for`` whose cancel on timeout is bounded too.

    Returns *aw*'s result or raises its exception; raises ``TimeoutError``
    after *timeout* seconds, having waited at most *cancel_timeout* more for
    *aw* to end. A cancel of the caller cancels *aw* the same bounded way.
    """
    task = asyncio.ensure_future(aw)
    try:
        await asyncio.wait({task}, timeout=max(0.0, timeout))
    except asyncio.CancelledError:
        await cancel_and_wait(task, what=what, timeout=cancel_timeout)
        raise
    if task.done():
        return task.result()
    await cancel_and_wait(task, what=what, timeout=cancel_timeout)
    raise TimeoutError(f"{what} did not finish in {timeout:.0f} s")


def _retrieve(task: asyncio.Future[Any], what: str) -> None:
    """Read a finished task's outcome so asyncio never reports it unretrieved."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.debug("[cancel] %s ended with %s", what, type(exc).__name__, exc_info=exc)
