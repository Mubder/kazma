"""Is this thread the caller's? One answer for every route that takes a thread.

A thread belongs to a tenant when a web session of that tenant is bound to it
(``SessionManager.get_by_thread_id``). Gateway threads get such a session too
(``sessions.directory._ensure_web_row``), so Telegram and Discord threads count
for the operator who owns them.

The HITL approval route has checked this since audit M7, failing CLOSED when the
session store errors. The routes around it did not: the replay reads and
deletes, and the chat stop/steer/abort controls, acted on any thread id they
were handed (audit 2026-09-22) — abort even wrote an abort marker into the
graph state of a thread that was not the caller's. Each route used to resolve
and check (or not check) ownership in its own copy of the code; this module is
the copy. ``tests/test_thread_route_policy.py`` makes every thread-taking route
declare which rule it follows.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Any

from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

__all__ = [
    "owned_threads",
    "owned_threads_async",
    "require_thread_owned",
    "resolve_caller_thread",
]


def _store(store: Any = None) -> Any:
    if store is not None:
        return store
    from kazma_ui.session_manager import get_session_manager

    return get_session_manager()


def owned_threads(thread_ids: Iterable[str], *, store: Any = None) -> list[str] | None:
    """The given threads the current tenant owns, in order; ``None`` if unknown.

    ``None`` means the check itself failed. Every caller denies on it.
    """
    try:
        sessions = _store(store)
        return [t for t in thread_ids if t and sessions.get_by_thread_id(t) is not None]
    except Exception:
        logger.warning("[ownership] thread ownership check failed — denying", exc_info=True)
        return None


async def owned_threads_async(
    thread_ids: Iterable[str], *, store: Any = None
) -> list[str] | None:
    """:func:`owned_threads` off the event loop (it may query the session DB).

    ``asyncio.to_thread`` copies the context, so the request's tenant travels.
    """
    return await asyncio.to_thread(owned_threads, list(thread_ids), store=store)


async def require_thread_owned(thread_id: str, *, store: Any = None) -> JSONResponse | None:
    """``None`` if the caller owns *thread_id*; else the response to return.

    404 for a thread that is not the caller's (the same answer as one that does
    not exist), 403 when ownership cannot be established.
    """
    owned = await owned_threads_async([thread_id], store=store)
    if owned is None:
        return JSONResponse({"error": "ownership check failed"}, status_code=403)
    if not owned:
        logger.warning("[ownership] thread not owned by current tenant: %s", thread_id)
        return JSONResponse({"error": "not found"}, status_code=404)
    return None


async def resolve_caller_thread(session_id: str, thread_id: str, *, store: Any = None) -> str:
    """The thread a chat control request may act on, or ``""`` if none is the caller's.

    An explicit ``thread_id`` must be owned. Otherwise ``session_id`` is looked
    up in the caller's tenant. A session the tenant does not have used to fall
    back to treating the raw id as a thread id — unchecked — which is how
    ``/api/chat/abort`` could write into any thread it was handed. That
    fallback remains only for an id that is itself an owned thread (a session
    whose thread id doubles as its session id).
    """
    thread_id = (thread_id or "").strip()
    session_id = (session_id or "").strip()
    if thread_id:
        owned = await owned_threads_async([thread_id], store=store)
        return thread_id if owned else ""
    if not session_id:
        return ""

    def _from_session() -> str:
        session = _store(store).get(session_id)
        return (session.thread_id or session_id) if session is not None else ""

    try:
        resolved = await asyncio.to_thread(_from_session)
    except Exception:
        logger.warning("[ownership] session lookup failed — denying", exc_info=True)
        return ""
    if resolved:
        return resolved
    owned = await owned_threads_async([session_id], store=store)
    return session_id if owned else ""
