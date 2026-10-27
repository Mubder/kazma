"""The Discord adapter stops on a close code it cannot recover from, and backs off.

Audit 2026-10-26 AUD-034. Only close codes 4013 and 4014 stopped the listen
loop. A revoked token (4004) -- or a shard/version refusal (4010-4012) --
re-identified every 1-3 s forever, against Discord's identify limit, while the
connector Test showed only "connecting"; and a clean close reconnected with no
delay at all.

The fakes below speak Discord's gateway as far as these rules need; the loop
under test is the adapter's real ``listen``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

pytest.importorskip("websockets")

from websockets.exceptions import ConnectionClosedError  # noqa: E402
from websockets.frames import Close  # noqa: E402


class _Socket:
    def __init__(self, close_code: int | None) -> None:
        self.close_code = close_code
        self.sent: list[dict[str, Any]] = []

    async def recv(self) -> str:
        return json.dumps({"op": 10, "d": {"heartbeat_interval": 3_600_000}})

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        return None

    def __aiter__(self) -> _Socket:
        return self

    async def __anext__(self) -> str:
        if self.close_code is None:
            raise StopAsyncIteration  # a clean close, before any READY
        raise ConnectionClosedError(Close(self.close_code, "refused"), None)


class _Gateway:
    """Every connection is closed by Discord with *close_code* before READY."""

    def __init__(self, close_code: int | None, stop_after: int, shutdown: asyncio.Event) -> None:
        self.close_code = close_code
        self.stop_after = stop_after
        self.shutdown = shutdown
        self.connects = 0

    def connect(self, url: str, **_kw: Any) -> Any:
        self.connects += 1
        if self.connects >= self.stop_after:
            self.shutdown.set()  # a loop that never stops is cut here
        sock = _Socket(self.close_code)

        class _Ctx:
            async def __aenter__(self_inner) -> _Socket:
                return sock

            async def __aexit__(self_inner, *exc: object) -> None:
                return None

        return _Ctx()


class _NullHttp:
    async def aclose(self) -> None:
        return None


async def _listen(monkeypatch, close_code: int | None, *, stop_after: int = 4) -> tuple[Any, _Gateway]:
    import httpx
    import websockets

    from kazma_gateway.adapters.discord import DiscordAdapter

    adapter = DiscordAdapter(token="fake:token", allow_all=True)
    shutdown = asyncio.Event()
    gateway = _Gateway(close_code, stop_after, shutdown)
    monkeypatch.setattr(websockets, "connect", gateway.connect)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _NullHttp())

    async def no_jitter(_shutdown: asyncio.Event) -> bool:
        return _shutdown.is_set()

    monkeypatch.setattr(adapter, "jitter_sleep", no_jitter)
    try:
        await asyncio.wait_for(adapter.listen(asyncio.Queue(), shutdown), timeout=10)
    finally:
        if adapter._heartbeat_task is not None:
            adapter._heartbeat_task.cancel()
    return adapter, gateway


@pytest.fixture
def fast_backoff(monkeypatch):
    """Record the backoff asked for, and wait none of it."""
    from kazma_gateway.adapters import discord

    asked: list[int] = []

    def backoff(failures: int) -> float:
        asked.append(failures)
        return 0.0

    monkeypatch.setattr(discord, "_reconnect_backoff", backoff)
    return asked


@pytest.mark.parametrize("code", [4004, 4010, 4011, 4012, 4013, 4014])
async def test_a_fatal_close_code_stops_and_says_why(monkeypatch, fast_backoff, code) -> None:
    adapter, gateway = await _listen(monkeypatch, code)
    assert gateway.connects == 1, f"reconnected {gateway.connects - 1} time(s) after close code {code}"
    state = adapter._receive.last_problem or {}
    assert str(code) in state.get("what", ""), state
    assert adapter._receive.connected is False


def test_a_refused_token_says_what_to_fix() -> None:
    from kazma_gateway.adapters.discord import _FATAL_CLOSE_CODES

    assert "token" in _FATAL_CLOSE_CODES[4004]


async def test_a_recoverable_close_before_ready_backs_off(monkeypatch, fast_backoff) -> None:
    """4000 (unknown error) is retried -- with a growing wait, not every 1-3 s."""
    _adapter, gateway = await _listen(monkeypatch, 4000, stop_after=4)
    assert gateway.connects == 4
    assert fast_backoff[:3] == [1, 2, 3]


async def test_a_clean_close_before_ready_backs_off(monkeypatch, fast_backoff) -> None:
    _adapter, gateway = await _listen(monkeypatch, None, stop_after=3)
    assert gateway.connects == 3
    assert fast_backoff[:2] == [1, 2]


def test_the_backoff_grows_and_is_capped() -> None:
    from kazma_gateway.adapters.discord import _RECONNECT_BACKOFF_MAX_S, _reconnect_backoff

    assert 1.5 <= _reconnect_backoff(1) <= 2.5
    assert 6.0 <= _reconnect_backoff(3) <= 10.0
    for _ in range(20):
        assert _reconnect_backoff(30) <= _RECONNECT_BACKOFF_MAX_S * 1.25


async def test_a_clean_close_after_ready_waits_a_moment(monkeypatch) -> None:
    """A session that was up and then closed cleanly reconnects after a
    minimum wait, never at once (it returned None, reconnect now)."""
    import websockets

    from kazma_gateway.adapters.discord import _CLEAN_CLOSE_MIN_S, DiscordAdapter

    frames = [
        json.dumps({"op": 0, "t": "READY", "s": 1, "d": {"session_id": "s1", "resume_gateway_url": "wss://r"}}),
    ]

    class _ReadyThenClose(_Socket):
        def __init__(self) -> None:
            super().__init__(None)
            self.frames = list(frames)

        async def __anext__(self) -> str:
            if self.frames:
                return self.frames.pop(0)
            raise StopAsyncIteration

    def connect(url: str, **_kw: Any) -> Any:
        sock = _ReadyThenClose()

        class _Ctx:
            async def __aenter__(self_inner) -> _Socket:
                return sock

            async def __aexit__(self_inner, *exc: object) -> None:
                return None

        return _Ctx()

    monkeypatch.setattr(websockets, "connect", connect)
    from kazma_gateway.adapters import discord_commands

    async def no_register(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(discord_commands, "register", no_register)
    adapter = DiscordAdapter(token="fake:token", allow_all=True)
    try:
        wait = await adapter._connect_gateway(asyncio.Queue(), asyncio.Event())
    finally:
        if adapter._heartbeat_task is not None:
            adapter._heartbeat_task.cancel()
    assert adapter._reached_ready is True
    assert wait is not None and wait >= _CLEAN_CLOSE_MIN_S >= 1.0
