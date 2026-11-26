"""A client that went away is not a server error (2026-11-25).

A page closed while it is still sending its request ends that request's body
with ``http.disconnect``; whoever reads the body gets Starlette's
``ClientDisconnect``. Nothing caught it, so it reached the app's catch-all:
a WARNING, an ERROR with a traceback under a reference, uvicorn's "Exception
in ASGI application" (Starlette raises it again after the 500), and a 500
written to nobody.

:class:`ClientGoneMiddleware` ends such a request quietly, one DEBUG line.
It believes the connection, never the exception alone: only a request whose
own ``http.disconnect`` arrived, before its answer was complete, is ended
this way, and only by ``ClientDisconnect`` and nothing else
(:func:`_only_client_disconnect`). Everything else is raised on as before: a
real failure, a group that also holds one, a ``ClientDisconnect`` raised
while a real failure was being handled or with the client still there, and
every ``CancelledError``.

A request whose body was cut off is answered nothing, whatever the route
made of it (FastAPI's own 400 for a body it could not read, a handler that
took the cut for malformed JSON). :func:`body_cut_off` tells the recorders
inside, which note it as :data:`CLIENT_GONE_STATUS`, not as a failure.

``tests/test_client_gone.py`` is its gate.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from anyio import EndOfStream
from starlette.requests import ClientDisconnect

logger = logging.getLogger(__name__)

__all__ = ["CLIENT_GONE_STATUS", "ClientGoneMiddleware", "body_cut_off"]

#: What a recorder notes for a request whose client left before sending all
#: of it (nginx's "client closed request"); nobody is ever answered it.
CLIENT_GONE_STATUS = 499

_SCOPE_KEY = "kazma_client_connection"


class _Connection:
    """What one request's connection has said so far."""

    def __init__(self) -> None:
        self.body_complete = False
        self.answer_complete = False
        self.gone = False

    @property
    def cut_off(self) -> bool:
        """The client left before sending all of its request."""
        return self.gone and not self.body_complete


def body_cut_off(scope: Mapping[str, Any]) -> bool:
    """Whether the client of *scope*'s request left before sending all of it."""
    connection = scope.get(_SCOPE_KEY)
    return isinstance(connection, _Connection) and connection.cut_off


def _only_client_disconnect(exc: BaseException) -> bool:
    """True when *exc* is ``ClientDisconnect`` and nothing else: no other
    member of a group (however deep), and no failure it was raised over.
    ``EndOfStream`` is how Starlette's middleware passes an error on."""
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    found = False
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        elif isinstance(current, ClientDisconnect):
            found = True
        elif not isinstance(current, EndOfStream):
            return False
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None and not current.__suppress_context__:
            pending.append(current.__context__)
    return found


class ClientGoneMiddleware:
    """Ends a request quietly when its client went away (see the module)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        connection = _Connection()
        scope[_SCOPE_KEY] = connection

        async def watched_receive() -> dict[str, Any]:
            message = await receive()
            if message["type"] == "http.request":
                if not message.get("more_body", False):
                    connection.body_complete = True
            elif message["type"] == "http.disconnect" and not connection.answer_complete:
                # A server says the same once the answer is complete: that
                # client was served.
                connection.gone = True
            return message

        async def watched_send(message: dict[str, Any]) -> None:
            if connection.cut_off:
                return  # nobody is there, and nothing was asked in full
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                connection.answer_complete = True

        try:
            await self.app(scope, watched_receive, watched_send)
        except (ClientDisconnect, ExceptionGroup) as exc:
            if not (connection.gone and _only_client_disconnect(exc)):
                raise
            logger.debug("[app] the client of %s %s went away", scope.get("method"), scope.get("path"))
