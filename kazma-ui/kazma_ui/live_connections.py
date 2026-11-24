"""A person's sockets and streams in a space, closed when they leave it (the
S2-12 review, finding 7).

Each WebSocket a space binding opened (chat, voice, the dashboard feed) and
each attached chat stream is registered here for the life of the
connection, under its person and space. When the identity store writes a
departure (``kazma_core.spaces.departures``), that person's sockets in that
space are closed (4403, ``not_a_member``) and their streams woken, which
end once they see the departure. Their next request is refused by the
membership check every request passes.

A chat a person moves into another space (Spaces S2-12) is a deleted chat in
the space it left: every socket and stream of it there is closed or ended
(:func:`chat_gone`), so a tab left open on it says so at its next message.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

__all__ = ["chat_gone", "is_gone", "socket_watched", "stream_watched"]

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_SOCKETS: dict[int, tuple[str, str, Any, asyncio.AbstractEventLoop, str]] = {}
_STREAMS: dict[int, tuple[str, str, Any, Any, asyncio.AbstractEventLoop, str]] = {}
_TOKENS = itertools.count(1)
_INSTALLED: list[Any] = []

#: The threads of the chats that went (:func:`chat_gone`), newest last.
_GONE: OrderedDict[str, None] = OrderedDict()
_GONE_KEPT = 1_000

#: The close code a socket gets when its person left the space.
CLOSE_CODE = 4403


def _install() -> None:
    with _LOCK:
        if _INSTALLED:
            return
        from kazma_core.spaces.departures import on_departure

        _INSTALLED.append(on_departure(_on_departure))


@contextmanager
def socket_watched(asker: str | None, space: str | None, websocket: Any, *,
                   chat: str = "") -> Iterator[None]:
    """Register *websocket* as *asker*'s in *space* while the block runs;
    *chat* is the chat it shows (a session or thread id), when it shows one."""
    if not asker or not space:
        yield
        return
    _install()
    token = next(_TOKENS)
    with _LOCK:
        _SOCKETS[token] = (str(asker), str(space), websocket, asyncio.get_running_loop(), str(chat or ""))
    try:
        yield
    finally:
        with _LOCK:
            _SOCKETS.pop(token, None)


@contextmanager
def stream_watched(asker: str | None, space: str | None, queue: Any, wake: Any, *,
                   chat: str = "") -> Iterator[None]:
    """Register a stream's *queue* as *asker*'s in *space*: a departure puts
    *wake* on it, and the stream ends once it sees the departure. *chat* is
    the thread it streams."""
    if not asker or not space:
        yield
        return
    _install()
    token = next(_TOKENS)
    with _LOCK:
        _STREAMS[token] = (str(asker), str(space), queue, wake, asyncio.get_running_loop(), str(chat or ""))
    try:
        yield
    finally:
        with _LOCK:
            _STREAMS.pop(token, None)


def _on_departure(account: str, space: str) -> None:
    """Close *account*'s sockets in *space* and wake their streams; called
    in the thread that wrote the change."""
    with _LOCK:
        sockets = [(ws, loop) for a, s, ws, loop, _c in _SOCKETS.values() if (a, s) == (account, space)]
        streams = [(q, wake, loop) for a, s, q, wake, loop, _c in _STREAMS.values() if (a, s) == (account, space)]
    for websocket, loop in sockets:
        _on_loop(loop, _close, websocket)
    for queue, wake, loop in streams:
        _on_loop(loop, queue.put_nowait, wake)
    if sockets or streams:
        logger.info("[spaces] %s left %s: %d socket(s) closed, %d stream(s) ended",
                    account, space, len(sockets), len(streams))


def is_gone(thread_id: str) -> bool:
    """Whether the chat of *thread_id* went (:func:`chat_gone`)."""
    with _LOCK:
        return thread_id in _GONE


def chat_gone(*, session_id: str, thread_id: str, space: str) -> int:
    """The chat left *space* for another: close its sockets there and wake
    its streams, which end once they see it (:func:`is_gone`). Returns how
    many. Called from any thread."""
    chats = {str(c) for c in (session_id, thread_id) if c}
    with _LOCK:
        _GONE[thread_id] = None
        while len(_GONE) > _GONE_KEPT:
            _GONE.popitem(last=False)
        sockets = [(ws, loop) for _a, s, ws, loop, c in _SOCKETS.values() if c in chats and s == space]
        streams = [(q, wake, loop) for _a, s, q, wake, loop, c in _STREAMS.values() if c in chats and s == space]
    for websocket, loop in sockets:
        _on_loop(loop, _close, websocket, "moved")
    for queue, wake, loop in streams:
        _on_loop(loop, queue.put_nowait, wake)
    if sockets or streams:
        logger.info("[spaces] a chat left %s: %d socket(s) closed, %d stream(s) ended",
                    space, len(sockets), len(streams))
    return len(sockets) + len(streams)


def _on_loop(loop: asyncio.AbstractEventLoop, fn: Any, *args: Any) -> None:
    try:
        loop.call_soon_threadsafe(fn, *args)
    except RuntimeError:  # the loop is closed: the connection went with it
        pass


def _close(websocket: Any, reason: str = "not_a_member") -> None:
    from kazma_core.background import spawn_background

    async def close() -> None:
        try:
            await websocket.close(code=CLOSE_CODE, reason=reason)
        except (RuntimeError, OSError):  # already closed
            pass

    spawn_background(close(), name="spaces:close-departed-socket")
