"""Concurrent dispatch + shutdown drain for GatewayManager._consume.

The gateway consumer previously ``await``ed the handler serially, so one long
turn blocked every platform and /steer /abort queued behind it. These tests
pin the concurrent-dispatch behavior and the stop() drain of in-flight tasks.
"""

from __future__ import annotations

import asyncio

import pytest

from kazma_gateway.gateway import GatewayManager, IncomingMessage


def _msg(text: str, sender: str = "test:1") -> IncomingMessage:
    return IncomingMessage(platform="test", sender_id=sender, text=text)


@pytest.mark.asyncio
async def test_consumer_dispatches_concurrently() -> None:
    gm = GatewayManager(max_queue_size=10)
    done: list[str] = []
    concurrent = 0
    max_concurrent = 0
    lock = asyncio.Lock()

    async def slow_handler(msg: IncomingMessage) -> None:
        nonlocal concurrent, max_concurrent
        async with lock:
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
        await asyncio.sleep(0.25)
        done.append(msg.text)
        async with lock:
            concurrent -= 1

    gm.on_message(slow_handler)
    await gm.start()
    try:
        gm.queue.put_nowait(_msg("one"))
        gm.queue.put_nowait(_msg("two"))
        for _ in range(200):
            if len(done) == 2:
                break
            await asyncio.sleep(0.02)
        assert sorted(done) == ["one", "two"]
        # A serial consumer would peak at 1; concurrent dispatch must overlap.
        assert max_concurrent >= 2
    finally:
        await gm.stop()


@pytest.mark.asyncio
async def test_stop_drains_inflight_handler_tasks() -> None:
    gm = GatewayManager(max_queue_size=10)
    finished = asyncio.Event()

    async def slow_handler(msg: IncomingMessage) -> None:
        await asyncio.sleep(0.4)
        finished.set()

    gm.on_message(slow_handler)
    await gm.start()
    gm.queue.put_nowait(_msg("one"))
    # Let the consumer pick the message up before shutting down.
    await asyncio.sleep(0.05)
    await gm.stop()
    # The in-flight handler must have been drained, not abandoned.
    assert finished.is_set()
