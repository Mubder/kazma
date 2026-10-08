"""Short-lived reply credentials stay in adapter memory, never session state."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class NativeReply:
    credential: str = field(repr=False)
    application: str
    user: str
    channel: str
    expires: float
    sent: int = 0
    original: bool = True
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class NativeReplies:
    """Bounded, expiring continuation credentials and delivery deduplication."""

    def __init__(self, ttl: float, limit: int = 256) -> None:
        self.ttl, self.limit = ttl, limit
        self._entries: dict[str, NativeReply] = {}

    def get(self, key: str) -> NativeReply | None:
        entry = self._entries.get(key)
        if entry and entry.expires > time.monotonic():
            return entry
        self._entries.pop(key, None)
        return None

    def put(
        self, key: str, credential: str, application: str, user: str, channel: str, *, original: bool = True
    ) -> NativeReply:
        for old in list(self._entries):
            self.get(old)
        while len(self._entries) >= self.limit:
            self._entries.pop(next(iter(self._entries)))
        entry = NativeReply(credential, application, user, channel, time.monotonic() + self.ttl)
        entry.original = original
        self._entries[key] = entry
        return entry
