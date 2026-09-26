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


# ── a peer that never answers the close (Slack Socket Mode's behaviour) ───


async def _mute_server():
    """Completes the websocket upgrade, then never reads or answers a frame."""
    import base64
    import hashlib
    import re

    writers = []

    async def handle(reader, writer):
        request = await reader.readuntil(b"\r\n\r\n")
        key = re.search(rb"Sec-WebSocket-Key: (\S+)", request, re.IGNORECASE).group(1)
        accept = base64.b64encode(
            hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest()  # noqa: S324 - RFC 6455
        )
        writer.write(
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
            b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n"
        )
        await writer.drain()
        writers.append(writer)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, writers, f"ws://127.0.0.1:{port}"


async def _stop_seconds(close_timeout: float, budget: float) -> float | None:
    """Seconds from shutdown until the reader returned, or None past *budget*."""
    import time

    from kazma_gateway.adapters.ws_shutdown import closes_on_shutdown

    server, writers, url = await _mute_server()
    shutdown = asyncio.Event()

    async def read() -> None:
        async with websockets.connect(url, close_timeout=close_timeout) as ws:
            async with closes_on_shutdown(ws, shutdown):
                async for _ in ws:
                    pass

    reader = asyncio.ensure_future(read())
    try:
        await asyncio.sleep(0.3)  # upgraded and waiting in the read
        started = time.monotonic()
        shutdown.set()
        try:
            await asyncio.wait_for(asyncio.shield(reader), timeout=budget)
        except TimeoutError:
            return None
        return time.monotonic() - started
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, websockets.ConnectionClosed, OSError):
            await reader
        for writer in writers:
            writer.transport.abort()
        server.close()


def test_a_peer_that_never_answers_the_close_costs_only_the_close_timeout() -> None:
    from kazma_gateway.adapters.ws_shutdown import CLOSE_TIMEOUT_S

    took = asyncio.run(_stop_seconds(CLOSE_TIMEOUT_S, budget=CLOSE_TIMEOUT_S + 2.5))
    assert took is not None, "the reader outlived the close timeout"


def test_negative_control_websockets_default_waits_far_longer() -> None:
    """With websockets' default close_timeout (10 s) the same peer holds the
    reader past the budget the adapters' setting stays within."""
    from kazma_gateway.adapters.ws_shutdown import CLOSE_TIMEOUT_S

    assert asyncio.run(_stop_seconds(10.0, budget=CLOSE_TIMEOUT_S + 2.5)) is None


def test_every_platform_socket_bounds_its_close() -> None:
    """Each websockets.connect in the adapters passes close_timeout."""
    import ast
    from pathlib import Path

    adapters = Path(__file__).resolve().parents[1] / "kazma-gateway" / "kazma_gateway" / "adapters"
    calls = []
    for path in sorted(adapters.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "connect"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "websockets"
            ):
                calls.append((path.name, node.lineno, {k.arg for k in node.keywords}))
    assert len(calls) >= 2, calls  # Discord and Slack
    unbounded = [(name, line) for name, line, kw in calls if "close_timeout" not in kw]
    assert not unbounded, f"websockets.connect without close_timeout: {unbounded}"


def test_a_drop_outside_shutdown_still_raises_for_the_reconnect() -> None:
    """Only a close WE made for shutdown is swallowed: the platform dropping
    the connection must still reach the adapter's reconnect loop."""
    from kazma_gateway.adapters.ws_shutdown import CLOSE_TIMEOUT_S, closes_on_shutdown

    async def run() -> BaseException | None:
        server, writers, url = await _mute_server()
        shutdown = asyncio.Event()
        try:
            async with websockets.connect(url, close_timeout=CLOSE_TIMEOUT_S) as ws:
                await asyncio.sleep(0.3)
                for writer in writers:
                    writer.transport.abort()  # the platform goes away
                try:
                    async with closes_on_shutdown(ws, shutdown):
                        async for _ in ws:
                            pass
                except websockets.ConnectionClosed as exc:
                    return exc
            return None
        finally:
            server.close()

    assert isinstance(asyncio.run(run()), websockets.ConnectionClosed)
