"""Shared blackboard primitives for swarm task groups."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

ValueT = TypeVar("ValueT")


class BlackboardStore:
    """Async-safe shared key-value store scoped to a task group."""

    def __init__(self, initial: Mapping[str, Any] | None = None) -> None:
        self._values: dict[str, Any] = dict(initial or {})
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        """Return the stored value for *key*, or None if it is missing."""
        async with self._lock:
            return self._values.get(key)

    async def set(self, key: str, value: Any) -> None:
        """Store *value* under *key*."""
        async with self._lock:
            self._values[key] = value

    async def update(
        self,
        key: str,
        updater: Callable[[Any | None], ValueT | Awaitable[ValueT]],
    ) -> ValueT:
        """Atomically update *key* using the current value."""
        async with self._lock:
            updated = updater(self._values.get(key))
            if inspect.isawaitable(updated):
                updated = await updated
            self._values[key] = updated
            return updated

    async def keys(self) -> list[str]:
        """Return the stored keys."""
        async with self._lock:
            return list(self._values.keys())

    async def snapshot(self) -> dict[str, Any]:
        """Return a shallow copy of the current blackboard state."""
        async with self._lock:
            return dict(self._values)

    async def clear(self) -> None:
        """Remove all stored keys."""
        async with self._lock:
            self._values.clear()

    @classmethod
    def from_snapshot(cls, data: Mapping[str, Any]) -> BlackboardStore:
        """Create a new store pre-populated from a snapshot dict."""
        return cls(initial=data)


class SwarmDispatchContext(str):
    """String-compatible worker context carrying shared task-group state."""

    blackboard: BlackboardStore | None
    metadata: dict[str, Any]
    task_id: str | None
    task_type: str | None
    system_prompt: str | None

    def __new__(
        cls,
        text: str = "",
        *,
        blackboard: BlackboardStore | None = None,
        metadata: Mapping[str, Any] | None = None,
        task_id: str | None = None,
        task_type: str | None = None,
        system_prompt: str | None = None,
    ) -> SwarmDispatchContext:
        context = str.__new__(cls, text)
        context.blackboard = blackboard
        context.metadata = dict(metadata or {})
        context.task_id = task_id
        context.task_type = task_type
        context.system_prompt = system_prompt
        return context

    @property
    def text(self) -> str:
        """Return the plain text form of the context."""
        return str(self)


def context_text(context: str | SwarmDispatchContext) -> str:
    """Return the text portion of a dispatch context."""
    return str(context)
