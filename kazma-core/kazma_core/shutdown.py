"""Global shutdown signal for graceful server termination.

All infinite loops (SSE streams, WebSocket handlers, background tasks)
must check ``is_shutting_down()`` and exit cleanly when it returns True.

Usage:
    from kazma_core.shutdown import is_shutting_down, signal_shutdown

    while not is_shutting_down():
        yield data
        await asyncio.sleep(1)
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_shutdown_event: asyncio.Event | None = None


def _get_event() -> asyncio.Event:
    global _shutdown_event
    if _shutdown_event is None:
        _shutdown_event = asyncio.Event()
    return _shutdown_event


def is_shutting_down() -> bool:
    """Check if the server is shutting down.

    Returns True after signal_shutdown() has been called.
    All infinite loops should check this and exit when True.
    """
    return _get_event().is_set()


def signal_shutdown() -> None:
    """Signal all loops to stop. Called once during app shutdown."""
    event = _get_event()
    if not event.is_set():
        logger.info("Shutdown signal sent — terminating all streams")
        event.set()


def reset_shutdown() -> None:
    """Reset the shutdown signal (for testing or restart)."""
    _get_event().clear()


async def wait_for_shutdown(timeout: float | None = None) -> None:
    """Await the shutdown signal (for background tasks)."""
    try:
        await asyncio.wait_for(_get_event().wait(), timeout=timeout)
    except TimeoutError:
        pass
