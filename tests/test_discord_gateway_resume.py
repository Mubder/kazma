"""The Discord adapter resumes its session instead of starting a new one.

Discord asks every bot to reconnect every hour or two (op 7) and expects a
Resume on the ``resume_gateway_url`` it sent in READY; a Resume on any other
URL is answered with op 9 "invalid session", and the bot must start over.
The adapter always resumed on the default gateway URL, so on the live install
every one of those reconnects failed (seven of seven on 2026-09-25, each
"Sending op 6 Resume" followed 0.3 s later by "Invalid session
(resumable=False)"), and whatever was said to the bot between the reconnect
request and the new READY was never delivered -- a new session does not
replay the old one's events.

``_FakeDiscord`` below applies Discord's rule, so the tests are statements
about what Discord accepts.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

pytest.importorskip("websockets")

DEFAULT_URL = "wss://gateway.discord.gg/?v=10&encoding=json"
RESUME_BASE = "wss://gateway-us-east1-b.discord.gg"


class _Socket:
    """One gateway connection: frames are produced as the client speaks."""

    def __init__(self, server: _FakeDiscord, url: str) -> None:
        self.server = server
        self.url = url
        self.sent: list[dict[str, Any]] = []
        self._frames: asyncio.Queue[str | None] = asyncio.Queue()

    async def recv(self) -> str:
        return json.dumps({"op": 10, "d": {"heartbeat_interval": 3_600_000}})

    async def send(self, data: str) -> None:
        msg = json.loads(data)
        self.sent.append(msg)
        for frame in self.server.answer(self, msg):
            self._frames.put_nowait(frame)

    async def close(self) -> None:
        self._frames.put_nowait(None)

    def push(self, frame: dict[str, Any] | None) -> None:
        self._frames.put_nowait(json.dumps(frame) if frame is not None else None)

    def __aiter__(self) -> _Socket:
        return self

    async def __anext__(self) -> str:
        frame = await self._frames.get()
        if frame is None:
            raise StopAsyncIteration
        return frame


class _FakeDiscord:
    """Discord's session rules, as far as these tests need them."""

    def __init__(self) -> None:
        self.sockets: list[_Socket] = []
        self.sessions = 0
        self.seq = 0
        self.after_ready: list[dict[str, Any] | None] = []

    def connect(self, url: str, **_kw: Any) -> Any:
        sock = _Socket(self, url)
        self.sockets.append(sock)

        class _Ctx:
            async def __aenter__(self_inner) -> _Socket:
                return sock

            async def __aexit__(self_inner, *exc: object) -> None:
                return None

        return _Ctx()

    def _dispatch(self, t: str, d: dict[str, Any]) -> str:
        self.seq += 1
        return json.dumps({"op": 0, "t": t, "s": self.seq, "d": d})

    def answer(self, sock: _Socket, msg: dict[str, Any]) -> list[str | None]:
        op = msg.get("op")
        if op == 2:  # Identify -> a new session
            self.sessions += 1
            out = [
                self._dispatch(
                    "READY",
                    {"session_id": f"s{self.sessions}", "resume_gateway_url": RESUME_BASE},
                )
            ]
            out += [json.dumps(f) if f is not None else None for f in self.after_ready]
            self.after_ready = []
            return out
        if op == 6:  # Resume -> only on the URL READY named
            if sock.url.startswith(RESUME_BASE):
                return [self._dispatch("RESUMED", {}), None]
            return [json.dumps({"op": 9, "d": False}), None]
        return []


@pytest.fixture
def discord(monkeypatch):
    import websockets

    server = _FakeDiscord()
    monkeypatch.setattr(websockets, "connect", server.connect)
    return server


def _adapter():
    from kazma_gateway.adapters.discord import DiscordAdapter

    return DiscordAdapter(token="fake:token", allow_all=True)


async def _run(adapter, queue=None) -> Any:
    try:
        return await adapter._connect_gateway(queue or asyncio.Queue(), asyncio.Event())
    finally:
        if adapter._heartbeat_task is not None:
            adapter._heartbeat_task.cancel()


async def test_a_reconnect_request_resumes_the_same_session(discord):
    adapter = _adapter()
    discord.after_ready = [{"op": 7, "d": None}]
    await _run(adapter)  # identify, READY, then Discord asks for a reconnect
    assert adapter._session_id == "s1"

    await _run(adapter)  # the resume

    resume = discord.sockets[1]
    assert resume.url == f"{RESUME_BASE}/?v=10&encoding=json"
    assert resume.sent[0]["op"] == 6
    assert resume.sent[0]["d"]["session_id"] == "s1"
    assert discord.sessions == 1, "the resume must not have ended in a new session"
    assert adapter._session_id == "s1"


async def test_the_old_url_is_what_discord_refuses(discord):
    """Negative control: resuming on the default URL is the live failure."""
    adapter = _adapter()
    discord.after_ready = [{"op": 7, "d": None}]
    await _run(adapter)
    adapter._resume_url = None  # what the adapter used to do: no resume URL
    await _run(adapter)
    assert discord.sockets[1].url == DEFAULT_URL
    assert adapter._session_id is None, "Discord answered op 9 and the session is gone"


async def test_a_non_resumable_invalid_session_waits_then_identifies(discord):
    """Discord: wait 1-5 s after op 9 before a fresh Identify."""
    adapter = _adapter()
    adapter._session_id = "stale"
    adapter._sequence = 7
    adapter._resume_url = "wss://elsewhere.discord.gg"
    wait = await _run(adapter)  # resume on the wrong URL -> op 9
    assert adapter._session_id is None and adapter._sequence is None
    assert adapter._resume_url is None
    assert wait is not None and 1.0 <= wait <= 5.0

    discord.after_ready = [None]
    await _run(adapter)
    assert discord.sockets[-1].url == DEFAULT_URL
    assert discord.sockets[-1].sent[0]["op"] == 2


async def test_a_heartbeat_request_is_answered_at_once(discord):
    adapter = _adapter()
    discord.after_ready = [{"op": 1, "d": None}, None]
    await _run(adapter)
    beats = [m for m in discord.sockets[0].sent if m.get("op") == 1]
    assert beats == [{"op": 1, "d": 1}], discord.sockets[0].sent


async def test_the_listen_loop_honours_the_wait(monkeypatch):
    """The 1-5 s wait happens with the socket closed, and shutdown cuts it short.

    No timing bet: the first connect asks for a 4 s wait. A loop that
    ignored it would run the second connect straight away -- before this
    test resumes -- so ``calls`` would be 2. A loop that waits is parked
    when shutdown is set, and returns without connecting again.
    """
    import httpx

    adapter = _adapter()
    calls = 0
    first = asyncio.Event()

    async def fake_connect(queue, shutdown_event):
        nonlocal calls
        calls += 1
        if calls == 1:
            first.set()
            return 4.0
        shutdown_event.set()
        return None

    monkeypatch.setattr(adapter, "_connect_gateway", fake_connect)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _NullHttp())
    shutdown = asyncio.Event()
    task = asyncio.create_task(adapter.listen(asyncio.Queue(), shutdown))
    await first.wait()
    shutdown.set()
    await asyncio.wait_for(task, timeout=2)  # the 4 s wait was cut short
    assert calls == 1, "the loop reconnected without waiting"


class _NullHttp:
    async def aclose(self) -> None:
        return None
