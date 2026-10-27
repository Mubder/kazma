"""A rate limit of 0 is "off", and one message never ends the consumer.

Audit 2026-10-26 AUD-033. An operator who set ``gateway.rate_limits.<platform>``
to 0 meant "no limit"; the flood guard's bucket refilled at 0 a second and
``get_reset_seconds`` divided by it. The ``ZeroDivisionError`` escaped the
gateway's ``_consume`` loop (it caught only ``TimeoutError`` and
``CancelledError``), so ``gateway-consumer`` died and every chat app stopped
answering, said only by asyncio's "Task exception was never retrieved".
"""

from __future__ import annotations

import asyncio
from typing import Any

from kazma_gateway.gateway import BaseAdapter, GatewayManager, IncomingMessage
from kazma_gateway.rate_feedback import RateFeedbackManager
from kazma_gateway.receive_log import ReceiveLog


class _RecordingAdapter(BaseAdapter):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self._receive = ReceiveLog()
        self.sent: list[str] = []

    async def listen(self, queue: asyncio.Queue, shutdown_event: asyncio.Event) -> None:
        await shutdown_event.wait()

    async def send(self, outbound: Any) -> bool:
        self.sent.append(outbound.text)
        return True


async def _until(check, timeout: float = 5.0) -> Any:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        value = check()
        if value:
            return value
        await asyncio.sleep(0.02)
    raise AssertionError("the gateway never handled the messages")


def _run(rate_feedback: Any, texts: list[str]) -> tuple[list[str], _RecordingAdapter, bool]:
    handled: list[str] = []

    async def scenario() -> tuple[_RecordingAdapter, bool]:
        gateway = GatewayManager()
        adapter = _RecordingAdapter("slack")
        gateway.add_adapter(adapter)
        gateway.set_rate_feedback(rate_feedback)

        async def handler(msg: IncomingMessage) -> None:
            handled.append(msg.text)

        gateway.on_message(handler)
        await gateway.start()
        try:
            for n, text in enumerate(texts):
                key = f"D1:{n}.000100"
                adapter._receive.note_passed_on(key)
                await gateway.queue.put(IncomingMessage(
                    platform="slack", sender_id="slack:U1", text=text,
                    context_metadata={"receive_key": key, "user_id": "U1", "channel_id": "D1"},
                ))
            await _until(lambda: gateway.queue.empty() and len(adapter._receive.recent) == len(texts))
            await asyncio.sleep(0.1)
            alive = gateway._consumer_task is not None and not gateway._consumer_task.done()
        finally:
            await gateway.stop()
        return adapter, alive

    adapter, alive = asyncio.run(scenario())
    return handled, adapter, alive


def test_a_limit_of_zero_is_no_limit() -> None:
    rfm = RateFeedbackManager(limit={"slack": 0, "telegram": 2}, window_seconds=60)
    for _ in range(50):
        assert rfm.is_limited("slack:U1") is False
    assert rfm.get_reset_seconds("slack:U1") == 0
    # The message never raises, whatever is asked of a disabled limit.
    assert "Slow down" in rfm.get_feedback_message("slack:U1")
    # Control: a positive limit on another platform still limits.
    assert rfm.is_limited("telegram:1") is False
    assert rfm.is_limited("telegram:1") is False
    assert rfm.is_limited("telegram:1") is True


def test_a_gateway_with_a_zero_limit_answers_every_message() -> None:
    handled, adapter, alive = _run(
        RateFeedbackManager(limit={"slack": 0}, window_seconds=60), ["one", "two", "three"],
    )
    assert handled == ["one", "two", "three"]
    assert alive
    assert "rate_limited" not in adapter._receive.dropped


class _BreaksOn:
    """A flood guard that raises on the first message (any failure inside
    the loop)."""

    def __init__(self) -> None:
        self.calls = 0

    def is_limited(self, user_id: str) -> bool:
        self.calls += 1
        if self.calls == 1:
            raise ZeroDivisionError("float division by zero")
        return False

    def should_send_feedback(self, user_id: str) -> bool:
        return False

    def get_feedback_message(self, user_id: str) -> str:
        return ""


def test_one_message_that_raises_does_not_end_the_consumer() -> None:
    handled, adapter, alive = _run(_BreaksOn(), ["first", "second", "third"])
    assert alive, "the consumer task ended on one message"
    assert handled == ["second", "third"]
    recent = adapter._receive.recent
    assert recent["D1:0.000100"] == "dispatch_failed"
    assert adapter._receive.dropped["dispatch_failed"] == 1
