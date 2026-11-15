"""The path every gate judges is the path the router matches (2026-11-15).

The sign-in gate, the CSRF check and the role check read
``request.url.path``. Starlette builds ``request.url`` by parsing
``root_path + scope["path"]`` as a URL, and ``scope["path"]`` is already
percent-decoded: a request for ``/api/x%3Fy/z`` has the path ``/api/x?y/z``,
whose URL reading stops at the ``?`` (``/api/x``) -- and ``%23`` the same at
``#``. The router matches ``scope["path"]`` itself. So the gates judged one
path and the router served another; a route such as ``/api/mcp/{x}`` would
have been judged as ``/api/mcp`` and its role rule read for the wrong path.

:func:`judged_path` is the one answer every gate asks: the path the router
matches (Starlette's own ``get_route_path``: ``scope["path"]`` without the
``root_path``). A decoded ``?`` or ``#`` in a path is refused outright
(:class:`AmbiguousPathGuard`, and the sign-in gate itself): no route has one,
and a path that holds one reads differently to every layer that parses it.
``tests/test_gates_judge_the_routed_path.py`` holds the three gates to it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from starlette._utils import get_route_path

logger = logging.getLogger(__name__)

__all__ = ["AmbiguousPathGuard", "ambiguous_path_response", "judged_path", "path_is_ambiguous"]

#: Characters a decoded path never holds: each ends the path when the URL is
#: read again (query, fragment).
_AMBIGUOUS = ("?", "#")


def _scope_of(conn: Any) -> dict[str, Any]:
    scope = getattr(conn, "scope", None)
    return scope if isinstance(scope, dict) else conn


def judged_path(conn: Any) -> str:
    """The path the router matches for *conn* (a request, a websocket or a
    scope): never ``request.url.path``, which stops at a decoded ``?``/``#``."""
    return get_route_path(_scope_of(conn)) or "/"


def path_is_ambiguous(conn: Any) -> bool:
    """True when the decoded path holds a ``?`` or ``#``."""
    path = str(_scope_of(conn).get("path") or "")
    return any(mark in path for mark in _AMBIGUOUS)


_MESSAGE = "The request path holds an encoded '?' or '#', which no route takes."


def ambiguous_path_response() -> Any:
    """The 400 answered for an ambiguous path (a JSONResponse)."""
    from fastapi.responses import JSONResponse

    return JSONResponse({"ok": False, "error": _MESSAGE, "code": "ambiguous_path"}, status_code=400)


class AmbiguousPathGuard:
    """Refuses a request whose decoded path holds ``?`` or ``#``, before any
    gate or route sees it: HTTP answers 400, a WebSocket handshake is closed."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        kind = scope.get("type")
        if kind not in ("http", "websocket") or not path_is_ambiguous(scope):
            await self.app(scope, receive, send)
            return
        logger.info("[path] refused an encoded '?' or '#' in a %s path", kind)
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"ok": False, "error": _MESSAGE, "code": "ambiguous_path"}).encode("utf-8")
        await send({"type": "http.response.start", "status": 400, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]})
        await send({"type": "http.response.body", "body": body})
