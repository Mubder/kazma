"""Close a platform websocket the moment the gateway is told to stop.

The Discord and Slack readers wait inside ``recv()`` and look at the shutdown
event only between frames: Discord's ``async for`` wakes when Discord next
sends something (a heartbeat ACK, ~41 s apart), Slack's ``recv`` waits up to
30 s. ``Gateway.stop`` gives each adapter 5 s before cancelling it, so every
reload spent those 5 s per adapter, one after the other, on a socket with
nothing more to say (live 2026-09-26: Discord +5.3 s, then Slack +5.0 s, on
every deploy). Closing the socket ends the read at once, with a proper close
frame to the platform.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

#: How long a close waits for the platform's half of the closing handshake.
#: websockets waits 10 s by default, and Slack's Socket Mode does not answer
#: promptly: with the socket closed at once, the Slack adapter still took the
#: full 10 s to stop (live, 2026-09-26). Pass it to ``websockets.connect``.
CLOSE_TIMEOUT_S = 2.0


@contextlib.asynccontextmanager
async def closes_on_shutdown(ws: Any, shutdown_event: asyncio.Event) -> AsyncIterator[Any]:
    """Yield *ws*, closing it as soon as *shutdown_event* is set.

    A read that ends because of that close is the point, not a failure: a
    peer that never answers the close makes it end with ``ConnectionClosed``
    ("no close frame received"), which the Discord loop would otherwise log
    as a dropped connection it is about to reconnect. Swallowed only while
    shutdown is set; any other close still raises.
    """
    from websockets.exceptions import ConnectionClosed, WebSocketException

    async def _close_when_told() -> None:
        await shutdown_event.wait()
        try:
            await ws.close()
        except (OSError, WebSocketException):
            # Already gone: the read ends either way.
            logger.debug("[gateway] websocket close on shutdown failed", exc_info=True)

    watcher = asyncio.ensure_future(_close_when_told())
    try:
        yield ws
    except ConnectionClosed:
        if not shutdown_event.is_set():
            raise
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
