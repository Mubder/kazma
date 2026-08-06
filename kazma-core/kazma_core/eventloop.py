"""Windows asyncio event-loop policy helper.

On Windows, Python 3.8+ defaults to ``ProactorEventLoop`` (better for
subprocesses and pipes). However, several async drivers Kazma depends on
are **incompatible** with the Proactor loop and require the older
``SelectorEventLoop``:

  - **psycopg** (async connections, used by LangGraph's
    ``AsyncPostgresSaver`` checkpointer) — raises
    ``Psycopg cannot use the 'ProactorEventLoop' to run in async mode``
    on every connection attempt, so Postgres-backed checkpoints never
    persist on Windows.
  - Some older ``aio*`` libraries have the same limitation.

``set_windows_selector_policy()`` switches to ``WindowsSelectorEventLoopPolicy``
on Windows. It MUST be called before uvicorn creates its event loop (i.e. at
the very top of ``_run_serve``, before any async work). It is a no-op on
non-Windows platforms, so calling it unconditionally is safe.

Trade-off: the SelectorEventLoop does not support subprocesses from within
async code. Kazma's subprocess-spawning paths (code_exec, MCP stdio) use
``asyncio.create_subprocess_exec`` which IS supported on the Selector loop
on Python 3.8+; the only thing lost is ``asyncio.subprocess`` on the
Proactor's overlapped IO, which Kazma does not use.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

__all__ = ["set_windows_selector_policy"]


def set_windows_selector_policy() -> None:
    """On Windows, switch asyncio to ``WindowsSelectorEventLoopPolicy``.

    No-op on macOS/Linux. Safe to call multiple times. Call this as early as
    possible in the boot path — before uvicorn.run and before any async
    driver imports its loop.
    """
    if sys.platform != "win32":
        return
    try:
        import asyncio

        # Idempotent: setting the same policy twice is fine.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]
        logger.debug("[eventloop] Windows: set WindowsSelectorEventLoopPolicy (psycopg/async compat)")
    except Exception as exc:  # noqa: BLE001 — never block boot over the loop policy
        logger.warning("[eventloop] could not set WindowsSelectorEventLoopPolicy: %s", exc)
