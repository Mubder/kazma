"""Every action under the person's name, and the page that shows a space's
(Spaces S2-09).

:class:`ActivityRecorder` is an ASGI middleware outside the tenant
middleware: every request that changes something (``POST``, ``PUT``,
``PATCH``, ``DELETE``) and reached a route is written to the space's
activity (``kazma_core.spaces.activity``) under the account that made it --
the asker the tenant middleware bound (``kazma_asker`` in the request), never
a session label, a body or a header the client chose. A request no person
made (sign-in, a platform's webhook) has no asker and writes nothing. Only
where Spaces is enforced.

The Activity page (``/activity``, ``GET /api/activity``) shows the space's
actions to its members: the membership check every request passes is the
only door, the admin's included (S1-25).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from kazma_ui.client_gone import CLIENT_GONE_STATUS, body_cut_off

logger = logging.getLogger(__name__)

__all__ = ["ActivityRecorder", "create_activity_router"]

_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
#: Rows the page reads at a time.
_PAGE = 100

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
ASGIApp = Callable[[Scope, Callable[[], Awaitable[Message]], Callable[[Message], Awaitable[None]]], Awaitable[None]]


def _who(scope: Scope) -> str:
    """The account the request acted as: the tenant middleware's asker."""
    return str(scope.get("kazma_asker") or "")


def _action(scope: Scope) -> tuple[str, list[str]] | None:
    """The route's method and template, and the ids in its path; None when
    no route answered (nothing was done)."""
    route = scope.get("route")
    template = getattr(route, "path", None)
    if not template:
        return None
    ids = [str(v) for v in (scope.get("path_params") or {}).values()]
    return f"{scope['method']} {template}", ids


def _write(space: str, account: str, action: str, ids: list[str], status: int) -> None:
    from kazma_core.spaces.activity import record

    try:
        record(space, account, action, ids, status)
    except (sqlite3.Error, OSError):
        logger.warning("[activity] %s by %s in %s was not recorded", action, account, space, exc_info=True)


class ActivityRecorder:
    """ASGI middleware: writes each change a person made to their space's
    activity (see the module docstring)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Callable[[], Awaitable[Message]],
                       send: Callable[[Message], Awaitable[None]]) -> None:
        from kazma_core.spaces.scope import spaces_enforced

        if scope.get("type") != "http" or scope.get("method") not in _MUTATING or not spaces_enforced():
            await self.app(scope, receive, send)
            return
        held: dict[str, int] = {}

        async def watching(message: Message) -> None:
            if message["type"] == "http.response.start":
                held["status"] = int(message["status"])
            await send(message)

        answered = False
        try:
            await self.app(scope, receive, watching)
            answered = True
        finally:
            done = _action(scope)
            account, space = _who(scope), str(scope.get("kazma_space") or "")
            if done is not None and account and space:
                # A client that left before sending all of its request is no
                # failure of the server's, whatever the route made of it.
                status = CLIENT_GONE_STATUS if body_cut_off(scope) else held.get("status", 500)
                if answered:
                    await asyncio.to_thread(_write, space, account, done[0], done[1], status)
                else:
                    # Ended by an error or a cancel: written without waiting.
                    from kazma_core.background import spawn_background

                    spawn_background(asyncio.to_thread(_write, space, account, done[0], done[1], status),
                                     name="activity-record")


def _rows_here(space: str, before: int | None) -> dict[str, Any]:
    from kazma_core.spaces import identity
    from kazma_core.spaces.activity import recent

    rows = recent(space, limit=_PAGE, before=before)
    accounts = identity.read_registry().get("accounts") or {}
    names = {account_id: str((account or {}).get("name") or "") for account_id, account in accounts.items()}
    return {
        "ok": True,
        "actions": [{"id": r.id, "at": r.at, "who": r.account, "name": names.get(r.account, ""),
                     "action": r.action, "ids": list(r.ids), "status": r.status} for r in rows],
        "more": len(rows) == _PAGE,
    }


def create_activity_router(templates: Any) -> APIRouter:
    router = APIRouter()

    @router.get("/activity", response_class=HTMLResponse)
    def activity_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "activity.html", {"active_page": "activity"})

    @router.get("/api/activity")
    def activity(request: Request, before: int | None = None) -> JSONResponse:
        """The bound space's actions, newest first (its members only: the
        tenant middleware checked membership)."""
        from kazma_core.spaces.identity import IdentityUnavailable
        from kazma_core.spaces.scope import NoSpaceBound, require_space_id, spaces_enforced

        if not spaces_enforced():
            return JSONResponse({"ok": False, "code": "spaces_off"}, status_code=409)
        try:
            space = require_space_id()
            return JSONResponse(_rows_here(space, before))
        except NoSpaceBound:
            return JSONResponse({"ok": False, "code": "no_space"}, status_code=409)
        except IdentityUnavailable:
            return JSONResponse({"ok": False, "code": "identity_unavailable"}, status_code=503,
                                headers={"Retry-After": "5"})

    return router
