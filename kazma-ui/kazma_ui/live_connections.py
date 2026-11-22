"""A person's sockets and streams in a space, closed when they leave it (the
S2-12 review, finding 7).

Each WebSocket a space binding opened (chat, voice, the dashboard feed) and
each attached chat stream is registered here for the life of the
connection, under its person and space. When the identity store writes a
departure (``kazma_core.spaces.departures``), that person's sockets in that
space are closed (4403, ``not_a_member``) and their streams woken, which
end once they see the departure. Their next request is refused by the
membership check every request passes.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

__all__ = ["socket_watched", "stream_watched"]

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_SOCKETS: dict[int, tuple[str, str, Any, asyncio.AbstractEventLoop]] = {}
_STREAMS: dict[int, tuple[str, str, Any, Any, asyncio.AbstractEventLoop]] = {}
_TOKENS = itertools.count(1)
_INSTALLED: list[Any] = []

#: The close code a socket gets when its person left the space.
CLOSE_CODE = 4403


def _install() -> None:
    with _LOCK:
        if _INSTALLED:
            return
        from kazma_core.spaces.departures import on_departure

        _INSTALLED.append(on_departure(_on_departure))


@contextmanager
def socket_watched(asker: str | None, space: str | None, websocket: Any) -> Iterator[None]:
    """Register *websocket* as *asker*'s in *space* while the block runs."""
    if not asker or not space:
        yield
        return
    _install()
    token = next(_TOKENS)
    with _LOCK:
        _SOCKETS[token] = (str(asker), str(space), websocket, asyncio.get_running_loop())
    try:
        yield
    finally:
        with _LOCK:
            _SOCKETS.pop(token, None)


@contextmanager
def stream_watched(asker: str | None, space: str | None, queue: Any, wake: Any) -> Iterator[None]:
    """Register a stream's *queue* as *asker*'s in *space*: a departure puts
    *wake* on it, and the stream ends once it sees the departure."""
    if not asker or not space:
        yield
        return
    _install()
    token = next(_TOKENS)
    with _LOCK:
        _STREAMS[token] = (str(asker), str(space), queue, wake, asyncio.get_running_loop())
    try:
        yield
    finally:
        with _LOCK:
            _STREAMS.pop(token, None)


def _on_departure(account: str, space: str) -> None:
    """Close *account*'s sockets in *space* and wake their streams; called
    in the thread that wrote the change."""
    with _LOCK:
        sockets = [(ws, loop) for a, s, ws, loop in _SOCKETS.values() if (a, s) == (account, space)]
        streams = [(q, wake, loop) for a, s, q, wake, loop in _STREAMS.values() if (a, s) == (account, space)]
    for websocket, loop in sockets:
        _on_loop(loop, _close, websocket)
    for queue, wake, loop in streams:
        _on_loop(loop, queue.put_nowait, wake)
    if sockets or streams:
        logger.info("[spaces] %s left %s: %d socket(s) closed, %d stream(s) ended",
                    account, space, len(sockets), len(streams))


def _on_loop(loop: asyncio.AbstractEventLoop, fn: Any, *args: Any) -> None:
    try:
        loop.call_soon_threadsafe(fn, *args)
    except RuntimeError:  # the loop is closed: the connection went with it
        pass


def _close(websocket: Any) -> None:
    from kazma_core.background import spawn_background

    async def close() -> None:
        try:
            await websocket.close(code=CLOSE_CODE, reason="not_a_member")
        except (RuntimeError, OSError):  # already closed
            pass

    spawn_background(close(), name="spaces:close-departed-socket")
