"""Cross-platform typing keepalive while the agent is working.

Telegram's ``sendChatAction(typing)`` expires after ~5 seconds. Without a
refresh loop, users only see a 👀 reaction for the entire graph run.
This coordinator starts a per-target task at handler entry and cancels it
when the turn finishes (success, error, or early return).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

__all__ = ["TypingKeepalive", "get_typing_keepalive"]

logger = logging.getLogger(__name__)

# Telegram expires ~5s; Discord ~10s. Refresh under the shortest window.
_DEFAULT_INTERVAL = 4.0

TypingFn = Callable[[str], Awaitable[None]]


class TypingKeepalive:
    """Manage per-target typing refresh tasks.

    Refcounted per target (deep-audit 2026-08-19, finding #15): the gateway
    consumer dispatches messages concurrently, so two turns in the same chat
    (e.g. a HITL approve arriving mid-turn, or two users in one Telegram
    group) can hold the indicator at once. The second ``start()`` previously
    cancelled the first turn's task, and the first turn's ``stop()`` then
    cancelled the second's. Now ``start()``/``stop()`` form a bracket pair
    and the indicator dies only when the last holder stops.
    """

    def __init__(self, interval: float = _DEFAULT_INTERVAL, *, stop_timeout: float = 5.0) -> None:
        self._interval = interval
        self._stop_timeout = stop_timeout
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stops: dict[str, asyncio.Event] = {}
        self._refs: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        target_id: str,
        typing_fn: TypingFn,
        *,
        interval: float | None = None,
    ) -> None:
        """Begin (or join) typing keepalive for *target_id*."""
        if not target_id or typing_fn is None:
            return
        period = interval if interval is not None else self._interval
        async with self._lock:
            refs = self._refs.get(target_id, 0) + 1
            self._refs[target_id] = refs
            existing = self._tasks.get(target_id)
            if refs > 1 and existing is not None and not existing.done():
                return  # another turn already holds a live indicator
            stop = asyncio.Event()
            task = asyncio.create_task(
                self._loop(target_id, typing_fn, period, stop),
                name=f"typing-keepalive:{target_id}",
            )
            self._tasks[target_id] = task
            self._stops[target_id] = stop

    async def stop(self, target_id: str) -> None:
        """Stop keepalive for *target_id* when the last holder stops (idempotent).

        The loop is taken off the books under the lock and waited for OUTSIDE
        it, bounded: a typing request whose cancel was absorbed (2026-10-23,
        ``kazma_core.bounded_cancel``) held this lock for good, and every
        later message on every chat app waited on it before its turn began.
        """
        async with self._lock:
            refs = self._refs.get(target_id, 0)
            if refs > 1:
                self._refs[target_id] = refs - 1
                return  # another turn still needs the indicator
            self._refs.pop(target_id, None)
            task = self._detach(target_id)
        await self._end(target_id, task)

    async def stop_all(self) -> None:
        async with self._lock:
            self._refs.clear()
            detached = [(tid, self._detach(tid)) for tid in list(self._tasks.keys())]
        for tid, task in detached:
            await self._end(tid, task)

    def _detach(self, target_id: str) -> asyncio.Task[None] | None:
        """Forget *target_id*'s loop and tell it to stop (call under the lock)."""
        stop = self._stops.pop(target_id, None)
        if stop is not None:
            stop.set()
        return self._tasks.pop(target_id, None)

    async def _end(self, target_id: str, task: asyncio.Task[None] | None) -> None:
        from kazma_core.bounded_cancel import cancel_and_wait

        await cancel_and_wait(
            task, what=f"typing indicator for {target_id}", timeout=self._stop_timeout,
        )

    async def _loop(
        self,
        target_id: str,
        typing_fn: TypingFn,
        period: float,
        stop: asyncio.Event,
    ) -> None:
        # The stop flag ends a loop whose cancel was absorbed by the request it
        # was in, at its next pass: it kept the chat "typing..." for hours.
        try:
            while not stop.is_set():
                try:
                    await typing_fn(target_id)
                except Exception as exc:
                    logger.debug(
                        "[typing] keepalive fire failed target=%s: %s",
                        target_id,
                        exc,
                    )
                if stop.is_set():
                    return
                try:
                    await asyncio.wait_for(stop.wait(), timeout=period)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            return


_singleton: TypingKeepalive | None = None


def get_typing_keepalive() -> TypingKeepalive:
    global _singleton
    if _singleton is None:
        _singleton = TypingKeepalive()
    return _singleton
