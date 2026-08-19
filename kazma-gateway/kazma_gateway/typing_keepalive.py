"""Cross-platform typing keepalive while the agent is working.

Telegram's ``sendChatAction(typing)`` expires after ~5 seconds. Without a
refresh loop, users only see a 👀 reaction for the entire graph run.
This coordinator starts a per-target task at handler entry and cancels it
when the turn finishes (success, error, or early return).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

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

    def __init__(self, interval: float = _DEFAULT_INTERVAL) -> None:
        self._interval = interval
        self._tasks: dict[str, asyncio.Task[None]] = {}
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
            task = asyncio.create_task(
                self._loop(target_id, typing_fn, period),
                name=f"typing-keepalive:{target_id}",
            )
            self._tasks[target_id] = task

    async def stop(self, target_id: str) -> None:
        """Stop keepalive for *target_id* when the last holder stops (idempotent)."""
        async with self._lock:
            refs = self._refs.get(target_id, 0)
            if refs > 1:
                self._refs[target_id] = refs - 1
                return  # another turn still needs the indicator
            self._refs.pop(target_id, None)
            await self._cancel_unlocked(target_id)

    async def stop_all(self) -> None:
        async with self._lock:
            self._refs.clear()
            for tid in list(self._tasks.keys()):
                await self._cancel_unlocked(tid)

    async def _cancel_unlocked(self, target_id: str) -> None:
        task = self._tasks.pop(target_id, None)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("[typing] cancel wait failed for %s", target_id, exc_info=True)

    async def _loop(
        self,
        target_id: str,
        typing_fn: TypingFn,
        period: float,
    ) -> None:
        try:
            while True:
                try:
                    await typing_fn(target_id)
                except Exception as exc:
                    logger.debug(
                        "[typing] keepalive fire failed target=%s: %s",
                        target_id,
                        exc,
                    )
                await asyncio.sleep(period)
        except asyncio.CancelledError:
            return


_singleton: TypingKeepalive | None = None


def get_typing_keepalive() -> TypingKeepalive:
    global _singleton
    if _singleton is None:
        _singleton = TypingKeepalive()
    return _singleton
