"""The gateway stops its platform adapters promptly and together.

Live 2026-09-26: on every reload, after everything else had stopped in under
a second, the gateway waited for its adapters one after another -- Discord
+5.3 s, then Slack +5.0 s. Their readers sat in ``recv()`` and saw the shutdown
event only when the platform next sent a frame, so each ran out its 5 s grace
before being cancelled. Now the socket is closed the moment shutdown is set
(adapters/ws_shutdown.closes_on_shutdown) and the adapters stop concurrently.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

websockets = pytest.importorskip("websockets")


async def _silent_server():
    """A websocket server that accepts and never says anything."""

    async def handler(ws):
        await ws.wait_closed()  # hold the connection open, silently

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = next(iter(server.sockets)).getsockname()[1]
    return server, f"ws://127.0.0.1:{port}"


async def _read_until_closed(url: str, shutdown: asyncio.Event, *, guarded: bool) -> None:
    from kazma_gateway.adapters.ws_shutdown import closes_on_shutdown

    async with websockets.connect(url) as ws:
        if guarded:
            async with closes_on_shutdown(ws, shutdown):
                async for _ in ws:  # Discord's reader shape
                    pass
        else:
            async for _ in ws:
                pass


async def _stops_within(seconds: float, *, guarded: bool) -> bool:
    server, url = await _silent_server()
    shutdown = asyncio.Event()
    reader = asyncio.ensure_future(_read_until_closed(url, shutdown, guarded=guarded))
    try:
        await asyncio.sleep(0.2)  # connected and waiting in the read
        shutdown.set()
        try:
            await asyncio.wait_for(asyncio.shield(reader), timeout=seconds)
            return True
        except TimeoutError:
            return False
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, websockets.ConnectionClosed):
            await reader
        server.close()
        await asyncio.wait_for(server.wait_closed(), timeout=5)


def test_a_silent_socket_is_closed_when_shutdown_is_set() -> None:
    assert asyncio.run(_stops_within(2.0, guarded=True)), "the reader outlived the shutdown"


def test_negative_control_without_the_guard_the_read_waits() -> None:
    assert not asyncio.run(_stops_within(1.0, guarded=False)), (
        "an unguarded reader returned on its own -- the test would prove nothing"
    )


def test_adapters_stop_together() -> None:
    """A's stop can only finish once B's has started: stopping one after the
    other would never finish (the wait_for fails the test instead)."""
    from kazma_gateway.gateway import GatewayManager

    b_started = asyncio.Event()

    class _Adapter:
        def __init__(self, name: str) -> None:
            self.name = name

        async def stop(self) -> None:
            if self.name == "a":
                await asyncio.wait_for(b_started.wait(), timeout=3)
            else:
                b_started.set()

    class _Failing:
        name = "broken"

        async def stop(self) -> None:
            raise RuntimeError("adapter blew up")

    async def run() -> GatewayManager:
        manager = GatewayManager()
        manager.adapters = [_Adapter("a"), _Failing(), _Adapter("b")]
        manager._started = True
        await asyncio.wait_for(manager.stop(), timeout=5)
        return manager

    manager = asyncio.run(run())
    # One adapter failing did not stop the rest of the shutdown.
    assert manager._started is False
