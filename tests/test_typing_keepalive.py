"""Typing keepalive coordinator tests."""

from __future__ import annotations

import asyncio

import pytest

from kazma_gateway.typing_keepalive import TypingKeepalive


@pytest.mark.asyncio
async def test_typing_fires_and_stops() -> None:
    calls: list[str] = []

    async def typing_fn(target: str) -> None:
        calls.append(target)

    ka = TypingKeepalive(interval=0.05)
    await ka.start("telegram:1", typing_fn)
    await asyncio.sleep(0.12)
    await ka.stop("telegram:1")
    await asyncio.sleep(0.08)

    assert len(calls) >= 2
    assert all(c == "telegram:1" for c in calls)
    # No further fires after stop
    n = len(calls)
    await asyncio.sleep(0.1)
    assert len(calls) == n
