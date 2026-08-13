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
import signal

__all__ = [
    "is_shutting_down",
    "reset_shutdown",
    "signal_shutdown",
    "wait_for_shutdown",
    "install_shutdown_signal_hooks",
    "uninstall_shutdown_signal_hooks",
]

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


# ── Early shutdown signal hooks ────────────────────────────────────────
# uvicorn cancels still-open streaming connections (SSE/WS) during its
# graceful-shutdown wait — BEFORE the ASGI lifespan shutdown runs. So a
# signal_shutdown() call placed in lifespan shutdown fires too late, and any
# stream still open gets hard-cancelled, logging a noisy
# ``CancelledError: Task cancelled, timeout graceful shutdown exceeded``
# traceback via Starlette's middleware. By flipping the shutdown flag at
# signal time, long-lived streams that check is_shutting_down() self-close
# within the graceful window and uvicorn never has to cancel them.
_shutdown_hooks_installed: dict[int, tuple] = {}


def install_shutdown_signal_hooks() -> None:
    """Install SIGINT/SIGTERM (and SIGBREAK on Windows) hooks that fire
    ``signal_shutdown()`` immediately when the operator stops the server,
    chaining to whatever handler the server (uvicorn) already installed.

    Must run on the main thread; idempotent. Captured previous handlers are
    restored by :func:`uninstall_shutdown_signal_hooks` (and, as a backstop, by
    uvicorn's own ``capture_signals`` teardown).
    """
    global _shutdown_hooks_installed
    if _shutdown_hooks_installed:
        return
    sigs = [signal.SIGINT, signal.SIGTERM]
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        sigs.append(sigbreak)
    for sig in sigs:
        try:
            prev = signal.getsignal(sig)
        except (ValueError, OSError):
            continue  # not the main thread / unsupported

        def _hook(signum, frame, _prev=prev):  # noqa: ANN001
            try:
                signal_shutdown()
            except Exception:  # noqa: BLE001
                logger.debug("[shutdown] signal_shutdown in hook failed", exc_info=True)
            if callable(_prev) and _prev not in (signal.SIG_DFL, signal.SIG_IGN):
                try:
                    _prev(signum, frame)
                except Exception:  # noqa: BLE001
                    logger.debug("[shutdown] chained handler failed", exc_info=True)

        try:
            signal.signal(sig, _hook)
            _shutdown_hooks_installed[sig] = (prev, _hook)
        except (ValueError, OSError):
            pass  # main-thread-only; ignore silently
    if _shutdown_hooks_installed:
        logger.debug(
            "[shutdown] early shutdown hooks installed for signals: %s",
            sorted(_shutdown_hooks_installed),
        )


def uninstall_shutdown_signal_hooks() -> None:
    """Restore the signal handlers captured by :func:`install_shutdown_signal_hooks`."""
    global _shutdown_hooks_installed
    for sig, (prev, hook) in list(_shutdown_hooks_installed.items()):
        try:
            if signal.getsignal(sig) is hook:
                signal.signal(sig, prev)
        except (ValueError, OSError):
            pass
    _shutdown_hooks_installed.clear()
