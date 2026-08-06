"""Windows asyncio event-loop helpers for psycopg async compatibility.

On Windows, Python 3.8+ defaults to ``ProactorEventLoop``. Uvicorn 0.36+
**also** hardcodes ``ProactorEventLoop`` on Windows in its
``asyncio_loop_factory`` (``uvicorn/loops/asyncio.py``), bypassing the
event-loop policy entirely. However, several async drivers Kazma depends
on are **incompatible** with the Proactor loop and require the older
``SelectorEventLoop``:

  - **psycopg** (async connections, used by LangGraph's
    ``AsyncPostgresSaver`` checkpointer) — raises
    ``Psycopg cannot use the 'ProactorEventLoop' to run in async mode``
    on every connection attempt, so Postgres-backed checkpoints never
    persist on Windows.

This module provides two helpers:

  - :func:`set_windows_selector_policy` — sets the asyncio event-loop
    policy to ``WindowsSelectorEventLoopPolicy``. This alone is NOT
    sufficient under uvicorn 0.36+ (which bypasses the policy), but it
    helps non-uvicorn callers (tests, scripts, the migration importer).

  - :func:`uvicorn_loop_factory` — returns a ``loop_factory`` callable that
    creates a ``SelectorEventLoop``. Pass it to ``uvicorn.run(...,
    loop=uvicorn_loop_factory())`` to override uvicorn's hardcoded
    ProactorEventLoop. This is the fix that actually works under uvicorn
    0.36+.

Both are no-ops on non-Windows platforms.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Callable

logger = logging.getLogger(__name__)

__all__ = ["set_windows_selector_policy", "uvicorn_loop_factory"]


def set_windows_selector_policy() -> None:
    """On Windows, switch asyncio to ``WindowsSelectorEventLoopPolicy``.

    No-op on macOS/Linux. Safe to call multiple times. Call this early in
    the boot path for non-uvicorn callers (tests, scripts). Under uvicorn
    0.36+ you ALSO need ``uvicorn_loop_factory`` (see below) because
    uvicorn bypasses the policy.
    """
    if sys.platform != "win32":
        return
    try:
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]
        logger.debug("[eventloop] Windows: set WindowsSelectorEventLoopPolicy (psycopg/async compat)")
    except Exception as exc:  # noqa: BLE001 — never block boot over the loop policy
        logger.warning("[eventloop] could not set WindowsSelectorEventLoopPolicy: %s", exc)


def uvicorn_loop_factory() -> Callable[..., Any] | None:
    """Return a uvicorn ``loop_factory`` that creates a SelectorEventLoop on Windows.

    Usage::

        from kazma_core.eventloop import uvicorn_loop_factory
        uvicorn.run(app, ..., loop=uvicorn_loop_factory())

    On non-Windows, returns ``None`` (uvicorn keeps its default behavior).
    On Windows, returns a callable that creates a ``SelectorEventLoop``
    instead of the default ``ProactorEventLoop`` — which psycopg's async
    connections require. This bypasses uvicorn 0.36+'s hardcoded
    ``asyncio.ProactorEventLoop`` in its ``asyncio_loop_factory``.
    """
    if sys.platform != "win32":
        return None

    import asyncio

    def _factory(**_kwargs: Any) -> asyncio.AbstractEventLoop:
        return asyncio.SelectorEventLoop()

    logger.debug("[eventloop] Windows: uvicorn loop_factory → SelectorEventLoop")
    return _factory

