"""An ops alert reaches someone, or says it did not.

Live 2026-10-02 the boot check's alert (``install.requirements_unmet``) was
raised from a worker thread. The dispatcher ran the delivery on a new event
loop of its own, the Telegram sender's HTTP client belonged to the server's
loop and raised RuntimeError, the sender swallowed it, and ``_deliver``
counted the finished ``gather`` as a delivery: the alert reached nobody and
nothing said so.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kazma_core.observability import ops_alerts
from kazma_core.swarm import bus as bus_mod
from kazma_core.swarm.bus import BusAdapter, BusMessage, FanOutBusAdapter, NullBusAdapter


class _Sender(BusAdapter):
    """A chat-app sender that records where it ran and answers as told."""

    def __init__(self, answer: Any = True) -> None:
        self.answer = answer
        self.loops: list[asyncio.AbstractEventLoop] = []
        self.sent: list[str] = []

    async def send(self, message: BusMessage) -> bool:
        self.loops.append(asyncio.get_running_loop())
        self.sent.append(message.content)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer

    async def send_report(self, report: Any) -> None:
        return None

    async def request_approval(self, approval: Any, timeout: float = 60.0) -> bool:
        return False


class _Bus:
    def __init__(self, adapter: BusAdapter) -> None:
        self.adapter = adapter


@pytest.fixture
def sender(monkeypatch: pytest.MonkeyPatch) -> _Sender:
    adapter = _Sender()
    monkeypatch.setattr(bus_mod, "get_message_bus", lambda: _Bus(adapter))
    monkeypatch.setattr(ops_alerts, "_ops_channels", lambda: [])
    monkeypatch.delenv("KAZMA_OPS_ALERTS", raising=False)
    ops_alerts.reset_alert_state()
    yield adapter
    ops_alerts.bind_server_loop(None)
    ops_alerts.drain_alerts(timeout=5.0)
    ops_alerts.reset_alert_state()


async def _alert_from_a_worker_thread(adapter: _Sender, *, bind: bool) -> asyncio.AbstractEventLoop:
    loop = asyncio.get_running_loop()
    if bind:
        ops_alerts.bind_server_loop(loop)
    await asyncio.to_thread(ops_alerts.alert, "test.delivery", "Something is off", "detail")
    for _ in range(300):
        if adapter.sent:
            break
        await asyncio.sleep(0.01)
    await asyncio.to_thread(ops_alerts.drain_alerts, 5.0)
    return loop


def test_a_worker_thread_alert_is_delivered_on_the_server_loop(sender: _Sender) -> None:
    async def main() -> None:
        loop = await _alert_from_a_worker_thread(sender, bind=True)
        assert sender.sent and sender.loops == [loop]

    asyncio.run(main())


def test_without_the_server_loop_it_ran_on_a_loop_of_its_own(sender: _Sender) -> None:
    """Negative control: the old path -- the sender ran on a new loop, where
    a real sender's HTTP client (made on the server's loop) fails."""
    async def main() -> None:
        loop = await _alert_from_a_worker_thread(sender, bind=False)
        assert sender.sent and sender.loops and sender.loops[0] is not loop

    asyncio.run(main())


def test_a_stopped_server_loop_is_not_used(sender: _Sender) -> None:
    stale = asyncio.new_event_loop()
    stale.close()
    ops_alerts.bind_server_loop(stale)

    async def main() -> None:
        await _alert_from_a_worker_thread(sender, bind=False)
        assert sender.sent and sender.loops[0] is not stale

    asyncio.run(main())


# ── _deliver counts only what a platform took ──────────────────────────


@pytest.mark.parametrize(("answer", "direct_called"), [(True, False), (False, True), (RuntimeError("bound to another loop"), True)])
def test_a_bus_that_took_nothing_falls_back_to_direct(
    sender: _Sender, monkeypatch: pytest.MonkeyPatch, answer: Any, direct_called: bool,
) -> None:
    sender.answer = answer
    direct: list[str] = []
    monkeypatch.setattr(ops_alerts, "_telegram_direct", lambda text, **_: direct.append(text) or True)
    assert asyncio.run(ops_alerts._deliver("hello")) is True
    assert bool(direct) is direct_called


def test_chosen_channels_that_refuse_are_a_failed_delivery(
    sender: _Sender, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not the "routing choice" log line: the chosen channel was tried."""
    sender.answer = False
    monkeypatch.setattr(ops_alerts, "_ops_channels", lambda: ["discord"])
    monkeypatch.setattr(ops_alerts, "bus_send_targets", lambda adapter, channels: [adapter])
    direct: list[str] = []
    monkeypatch.setattr(ops_alerts, "_telegram_direct", lambda text, **_: direct.append(text) or True)
    assert asyncio.run(ops_alerts._deliver("hello")) is False
    assert direct == []


# ── every sender reports delivery ──────────────────────────────────────


def test_the_fan_out_reports_whether_any_platform_took_it() -> None:
    message = BusMessage(worker_name="Kazma", worker_role="ops", content="x", level="warn")
    assert asyncio.run(FanOutBusAdapter([_Sender(False), _Sender(True)]).send(message)) is True
    assert asyncio.run(FanOutBusAdapter([_Sender(False), _Sender(RuntimeError())]).send(message)) is False
    assert asyncio.run(NullBusAdapter().send(message)) is False


@pytest.mark.parametrize(("platform", "helper", "accepted", "refused"), [
    ("telegram", "_post", {"ok": True, "result": {}}, {"ok": False, "description": "chat not found"}),
    ("discord", "_post_message", {"id": "1"}, None),
    ("slack", "_post_message", {"ok": True}, None),
])
def test_each_platform_sender_reports_delivery(
    monkeypatch: pytest.MonkeyPatch, platform: str, helper: str, accepted: Any, refused: Any,
) -> None:
    from kazma_gateway.adapters import discord_bus, slack_bus, telegram_bus

    cls = {
        "telegram": telegram_bus.TelegramBusAdapter,
        "discord": discord_bus.DiscordBusAdapter,
        "slack": slack_bus.SlackBusAdapter,
    }[platform]
    adapter = cls.__new__(cls)
    adapter._chat_id = adapter._channel_id = "c1"
    message = BusMessage(worker_name="Kazma", worker_role="ops", content="x", level="warn")

    for answer, expected in ((accepted, True), (refused, False), (None, False)):
        async def reply(*_a: Any, _answer: Any = answer, **_k: Any) -> Any:
            return _answer

        monkeypatch.setattr(adapter, helper, reply)
        assert asyncio.run(adapter.send(message)) is expected, (platform, answer)


def test_the_app_binds_and_unbinds_its_loop() -> None:
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parents[1] / "kazma-ui/kazma_ui/app.py").read_text(encoding="utf-8"))
    hooks = {
        n.name: [ast.unparse(c) for c in ast.walk(n) if isinstance(c, ast.Call) and ast.unparse(c.func) == "bind_server_loop"]
        for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name in ("_on_startup", "_on_shutdown")
    }
    assert hooks["_on_startup"] == ["bind_server_loop(asyncio.get_running_loop())"]
    assert hooks["_on_shutdown"] and set(hooks["_on_shutdown"]) == {"bind_server_loop(None)"}
