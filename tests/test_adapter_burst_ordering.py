"""Every chat adapter delivers a burst of messages once each, in arrival order.

Regression for the 2026-09-22 audit finding in the Discord adapter: the
per-channel chain closure was defined inside the gateway receive loop and read
``_prev`` / ``parsed`` only when its task first ran. ``websockets`` hands out
already-buffered frames without suspending, so a burst (two quick messages, or
the replay Discord sends after a RESUME) let the loop rebind both names first.
The first message was lost and the second was enqueued twice — two agent turns
for one message, and every tool in it ran twice.

The test is parametrized over all three adapters on purpose. Slack and
Telegram had the correct shape (values bound at call time); Discord had the
same idea inlined, and nothing compared the siblings. A new adapter belongs in
``ADAPTERS`` below.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

N_MESSAGES = 4


def _discord_event(i: int) -> dict[str, Any]:
    return {
        "id": f"m{i}",
        "channel_id": "222",
        "guild_id": "333",
        "content": f"message {i}",
        "author": {"id": "444", "username": "alice", "bot": False},
    }


class _BufferedGatewaySocket:
    """A Discord gateway socket whose event frames are all already buffered.

    ``__anext__`` returns without suspending while frames remain — the same
    thing ``websockets`` does when its frame queue is non-empty.
    """

    def __init__(self, frames: list[str]) -> None:
        self._frames = list(frames)
        self.sent: list[str] = []

    async def recv(self) -> str:
        return json.dumps({"op": 10, "d": {"heartbeat_interval": 45_000}})

    async def send(self, data: str) -> None:
        self.sent.append(data)

    def __aiter__(self) -> _BufferedGatewaySocket:
        return self

    async def __anext__(self) -> str:
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)


class _Connect:
    def __init__(self, ws: _BufferedGatewaySocket) -> None:
        self._ws = ws

    async def __aenter__(self) -> _BufferedGatewaySocket:
        return self._ws

    async def __aexit__(self, *exc: object) -> None:
        return None


async def _slow_first(text: str) -> None:
    # The first message is the slow one (a voice note being transcribed). The
    # chain exists so a later, faster message cannot overtake it.
    await asyncio.sleep(0.05 if text.endswith("0") else 0)


async def _burst_discord(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    import websockets
    from kazma_gateway.adapters.discord import DiscordAdapter

    adapter = DiscordAdapter(token="fake:token", allow_all=True)
    frames = [
        json.dumps({"op": 0, "t": "MESSAGE_CREATE", "s": i + 1, "d": _discord_event(i)})
        for i in range(N_MESSAGES)
    ]
    monkeypatch.setattr(
        websockets, "connect", lambda *_a, **_k: _Connect(_BufferedGatewaySocket(frames))
    )

    async def _no_stt(msg: Any) -> Any:
        await _slow_first(msg.text)
        return msg

    monkeypatch.setattr(adapter, "_maybe_transcribe_audio", _no_stt)
    queue: asyncio.Queue = asyncio.Queue()
    try:
        await adapter._connect_gateway(queue, asyncio.Event())
        return await _drain(queue, lambda m: m.text)
    finally:
        if adapter._heartbeat_task is not None:
            adapter._heartbeat_task.cancel()


async def _burst_telegram(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from kazma_gateway.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter(token="123:fake", allow_all=True)

    async def _process(update: dict, queue: asyncio.Queue) -> None:
        text = update["message"]["text"]
        await _slow_first(text)
        queue.put_nowait(text)

    monkeypatch.setattr(adapter, "_process_update", _process)
    queue: asyncio.Queue = asyncio.Queue()
    for i in range(N_MESSAGES):
        adapter._dispatch_update_to_chain(
            {
                "update_id": 1000 + i,
                "message": {"message_id": i, "chat": {"id": 42}, "text": f"message {i}"},
            },
            queue,
        )
    return await _drain(queue, lambda m: m)


async def _burst_slack(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from kazma_gateway.adapters.slack import SlackAdapter
    from kazma_gateway.gateway import IncomingMessage

    adapter = SlackAdapter(bot_token="xoxb-fake", app_token="xapp-fake", allow_all=True)
    queue: asyncio.Queue = asyncio.Queue()
    adapter._queue = queue

    async def _passthrough(msg: Any) -> Any:
        return msg

    async def _stt(msg: Any) -> Any:
        await _slow_first(msg.text)
        return msg

    monkeypatch.setattr(adapter, "_prefetch_private_files", _passthrough)
    monkeypatch.setattr(adapter, "_maybe_transcribe_audio", _stt)
    for i in range(N_MESSAGES):
        incoming = IncomingMessage(
            platform="slack",
            sender_id="slack:U1:C1",
            text=f"message {i}",
            context_metadata={"channel_id": "C1", "user_id": "U1"},
        )
        adapter._chain_channel_work("C1", adapter._finalize_event(incoming, {"type": "message"}))
    return await _drain(queue, lambda m: m.text)


async def _drain(queue: asyncio.Queue, text_of: Any) -> list[str]:
    """Collect everything the adapter enqueues, including late duplicates."""
    got: list[str] = []
    deadline = asyncio.get_running_loop().time() + 2.0
    while len(got) < N_MESSAGES and asyncio.get_running_loop().time() < deadline:
        try:
            got.append(text_of(await asyncio.wait_for(queue.get(), timeout=0.2)))
        except TimeoutError:
            continue
    # A duplicate arrives after the expected count; give it the chance to.
    await asyncio.sleep(0.1)
    while not queue.empty():
        got.append(text_of(queue.get_nowait()))
    return got


ADAPTERS = {
    "discord": _burst_discord,
    "telegram": _burst_telegram,
    "slack": _burst_slack,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", sorted(ADAPTERS))
async def test_burst_is_delivered_once_each_in_order(
    platform: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    got = await ADAPTERS[platform](monkeypatch)
    assert got == [f"message {i}" for i in range(N_MESSAGES)], (
        f"{platform}: a burst of {N_MESSAGES} messages was delivered as {got}"
    )
