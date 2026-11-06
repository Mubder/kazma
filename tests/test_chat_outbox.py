"""Chat-app replies go through an outbox (resilience plan F3).

Live: 57 Slack timeouts, about 65 Discord drops and 15 Telegram failures each
lost a reply -- ``GatewayManager.send`` tried once. Now a durable reply is kept
(``chat_outbox.db``) until the platform takes it. Held here:

* a reply nothing of which arrived is tried again, for up to two hours, then
  is undeliverable: one alert, never a silent loss;
* a reply whose answer was lost (a read timeout after it left) or that partly
  arrived is never sent again; the next reply in that chat says an earlier
  message may not have reached them, in their language;
* the adapters say what each part did (``send_report``), so a read timeout
  is told from a refused connection;
* a real process killed at each stage of a send leaves exactly one delivery,
  or one honest state, never two deliveries;
* the agent's sends and scheduled deliveries are not kept here (their caller
  keeps its own retries): two outboxes would deliver twice.

Each rule has a negative control: the manager without an outbox loses the
reply.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import httpx
import pytest

from kazma_gateway import chat_outbox, send_report
from kazma_gateway.chat_outbox import GIVE_UP_AFTER_S, ChatOutbox
from kazma_gateway.gateway import BaseAdapter, GatewayManager, OutboundMessage

ROOT = Path(__file__).resolve().parents[1]


class _Fake(BaseAdapter):
    """A chat app that records what it was given and answers as scripted."""

    name = "telegram"

    def __init__(self, answers=None) -> None:
        super().__init__()
        self.answers = list(answers or [])
        self.delivered: list[str] = []
        self.calls = 0

    async def listen(self, queue, shutdown_event) -> None:  # pragma: no cover
        return None

    async def send(self, outbound: OutboundMessage) -> bool:
        self.calls += 1
        answer = self.answers.pop(0) if self.answers else "ok"
        if answer == "ok":
            send_report.part_delivered()
            self.delivered.append(outbound.text)
            return True
        if answer == "refused":
            return False
        if answer == "read_timeout":
            send_report.part_failed(httpx.ReadTimeout("lost"))
            return False
        if answer == "connect_error":
            send_report.part_failed(httpx.ConnectError("refused"))
            return False
        if answer == "partial":
            send_report.part_delivered()
            self.delivered.append(outbound.text + " [first part]")
            return False
        raise AssertionError(answer)


def _manager(tmp_path, adapter, *, outbox=True):
    manager = GatewayManager()
    manager.add_adapter(adapter)
    store = ChatOutbox(tmp_path / "chat_outbox.db") if outbox else None
    if store is not None:
        manager.set_outbox(store)
    return manager, store


def _reply(text="Your report is ready.", **kw) -> OutboundMessage:
    return OutboundMessage(target_id="telegram:42", text=text, context_metadata={"chat_id": 42}, **kw)


def _due(store: ChatOutbox, seconds: float) -> None:
    """Move every pending row's next try *seconds* earlier."""
    import sqlite3

    conn = sqlite3.connect(store.path)
    try:
        with conn:
            conn.execute("UPDATE outbox SET next_attempt_at = next_attempt_at - ?, "
                         "created_at = created_at - ? WHERE state = 'pending'", (seconds, seconds))
    finally:
        conn.close()


def _states(store: ChatOutbox) -> dict[str, int]:
    return store.counts()


# -- the outbox ------------------------------------------------------------------------


def test_a_refused_reply_is_tried_again_and_delivered_once(tmp_path):
    adapter = _Fake(["refused", "ok"])
    manager, store = _manager(tmp_path, adapter)
    assert asyncio.run(manager.send(_reply())) is False
    assert _states(store) == {"pending": 1}
    assert asyncio.run(manager._drain_outbox_once()) == 0     # not due yet
    _due(store, 31)
    assert asyncio.run(manager._drain_outbox_once()) == 1
    assert adapter.delivered == ["Your report is ready."]
    assert _states(store) == {"sent": 1}
    assert asyncio.run(manager._drain_outbox_once()) == 0


def test_without_the_outbox_a_refused_reply_is_lost(tmp_path):
    # The negative control: the manager before F3.
    adapter = _Fake(["refused", "ok"])
    manager, _store = _manager(tmp_path, adapter, outbox=False)
    assert asyncio.run(manager.send(_reply())) is False
    assert asyncio.run(manager._drain_outbox_once()) == 0
    assert adapter.delivered == []


def test_a_reply_whose_answer_was_lost_is_never_sent_twice(tmp_path):
    adapter = _Fake(["read_timeout"])
    manager, store = _manager(tmp_path, adapter)
    assert asyncio.run(manager.send(_reply())) is False
    assert _states(store) == {"unknown": 1}
    _due(store, 10_000)
    assert asyncio.run(manager._drain_outbox_once()) == 0
    assert adapter.calls == 1
    # The next reply to that chat says so, once.
    asyncio.run(manager.send(_reply("Here is the next answer.")))
    asyncio.run(manager.send(_reply("And another.")))
    assert adapter.delivered[0].startswith("(An earlier message may not have reached you in full.)")
    assert adapter.delivered[1] == "And another."


def test_a_refused_connection_is_tried_again(tmp_path):
    # Control for the one above: the request never left, so it is not
    # "unknown" -- it is tried again.
    adapter = _Fake(["connect_error", "ok"])
    manager, store = _manager(tmp_path, adapter)
    asyncio.run(manager.send(_reply()))
    assert _states(store) == {"pending": 1}
    _due(store, 31)
    asyncio.run(manager._drain_outbox_once())
    assert adapter.delivered == ["Your report is ready."]


def test_a_partly_delivered_reply_is_not_sent_again(tmp_path):
    adapter = _Fake(["partial"])
    manager, store = _manager(tmp_path, adapter)
    asyncio.run(manager.send(_reply()))
    assert _states(store) == {"partial": 1}
    _due(store, 10_000)
    assert asyncio.run(manager._drain_outbox_once()) == 0
    assert adapter.calls == 1


def test_the_notice_is_in_their_language(tmp_path):
    adapter = _Fake(["read_timeout", "ok"])
    manager, _store = _manager(tmp_path, adapter)
    asyncio.run(manager.send(_reply()))
    asyncio.run(manager.send(_reply("تقريرك جاهز الآن.")))
    assert adapter.delivered[0].startswith("(ربما لم تصلك إحدى الرسائل السابقة كاملة.)")
    assert adapter.delivered[0].endswith("تقريرك جاهز الآن.")


def test_a_late_reply_says_it_is_late(tmp_path):
    adapter = _Fake(["refused", "ok"])
    manager, store = _manager(tmp_path, adapter)
    asyncio.run(manager.send(_reply()))
    _due(store, 120)
    asyncio.run(manager._drain_outbox_once())
    assert adapter.delivered[0].startswith("(Sent late: Kazma could not reach the chat app earlier.)")


def test_after_two_hours_a_reply_is_undeliverable_with_one_alert(tmp_path, monkeypatch):
    alerts = []
    from kazma_core.observability import ops_alerts

    monkeypatch.setattr(ops_alerts, "alert", lambda key, title, detail="", **kw: alerts.append((key, title, detail)))
    adapter = _Fake(["refused"] * 20)
    manager, store = _manager(tmp_path, adapter)
    asyncio.run(manager.send(_reply("secret plans for the weekend")))
    _due(store, GIVE_UP_AFTER_S + 1)
    asyncio.run(manager._drain_outbox_once())
    assert _states(store) == {"undeliverable": 1}
    assert len(alerts) == 1 and alerts[0][0] == "chat.reply_undeliverable.telegram"
    assert "secret plans" not in json.dumps(alerts)       # never the reply's text
    assert adapter.calls == 1                              # given up, not sent again


def test_the_waits_double_to_fifteen_minutes():
    waits = [chat_outbox._retry_wait(n) for n in range(1, 9)]
    assert waits == [30, 60, 120, 240, 480, 900, 900, 900]


def test_a_settled_row_keeps_no_text_and_is_purged_after_a_week(tmp_path):
    adapter = _Fake(["ok"])
    manager, store = _manager(tmp_path, adapter)
    asyncio.run(manager.send(_reply("words")))
    import sqlite3

    conn = sqlite3.connect(store.path)
    try:
        assert conn.execute("SELECT payload FROM outbox").fetchone()[0] is None
    finally:
        conn.close()
    import time

    assert store.prune(time.time() + 8 * 86400) == 1


def test_the_agents_sends_are_not_kept_here(tmp_path):
    # make_gateway_send_handler's sends: the caller reads the answer and
    # keeps its own retries (the scheduled jobs' outbox).
    adapter = _Fake(["refused"])
    manager, store = _manager(tmp_path, adapter)
    asyncio.run(manager.send(_reply(durable=False)))
    assert _states(store) == {}

    from kazma_gateway.agent_handler.graph import make_gateway_send_handler

    class _Store:
        async def get(self, _key):
            return None

    sent = []

    class _Manager:
        async def send(self, outbound):
            sent.append(outbound)
            return True

    asyncio.run(make_gateway_send_handler(_Manager(), _Store())("discord:9", "hello"))
    assert sent and sent[0].durable is False


def test_an_interaction_reply_is_not_kept(tmp_path):
    adapter = _Fake(["refused"])
    manager, store = _manager(tmp_path, adapter)
    asyncio.run(manager.send(OutboundMessage(target_id="telegram:42", text="x",
                                             context_metadata={"native_reply_id": "abc"})))
    assert _states(store) == {}


# -- the adapters' reports ---------------------------------------------------------------


class _Http:
    def __init__(self, answers) -> None:
        self.answers = list(answers)

    async def post(self, url, json=None, **kw):
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(200, json=answer, request=httpx.Request("POST", "https://api.example" + url))


async def _rate():
    return None


@pytest.mark.parametrize("exc, uncertain", [
    (httpx.ReadTimeout("lost"), True),
    (httpx.RemoteProtocolError("dropped"), True),
    (httpx.ConnectError("refused"), False),
    (httpx.ConnectTimeout("refused"), False),
])
def test_telegram_says_whether_a_failed_part_may_have_arrived(exc, uncertain):
    from kazma_gateway.adapters.telegram_send import send_chunks_with_retry

    async def run():
        with send_report.reporting() as report:
            ok = await send_chunks_with_retry(http=_Http([{"ok": True, "result": {}}, exc]), chat_id=1,
                                              chunks=["one", "two"], parse_mode=None, reply_markup=None,
                                              rate_acquire=_rate)
        return ok, report

    ok, report = asyncio.run(run())
    assert ok is False
    assert report.parts == 1
    assert report.uncertain is uncertain


def test_discord_and_slack_report_their_parts():
    from kazma_gateway.adapters.discord import DiscordAdapter
    from kazma_gateway.adapters.slack import SlackAdapter

    discord = DiscordAdapter.__new__(DiscordAdapter)
    BaseAdapter.__init__(discord)
    discord._http = _Http([httpx.ReadTimeout("lost")])

    class _Limiter:
        async def acquire(self):
            return None

    discord._rate_limiter = _Limiter()

    async def no_typing(*a, **k):
        return None

    discord._trigger_typing = no_typing

    async def run_discord():
        with send_report.reporting() as report:
            ok = await discord.send(OutboundMessage(target_id="discord:7", text="hi",
                                                    context_metadata={"channel_id": "7"}))
        return ok, report

    ok, report = asyncio.run(run_discord())
    assert ok is False and report.uncertain is True

    slack = SlackAdapter.__new__(SlackAdapter)
    BaseAdapter.__init__(slack)
    slack._http = _Http([{"ok": True, "ts": "1"}])
    slack._trigger_typing = no_typing
    slack._headers = lambda: {}

    async def run_slack():
        with send_report.reporting() as report:
            ok = await slack.send(OutboundMessage(target_id="slack:C1", text="hi",
                                                  context_metadata={"channel_id": "C1"}))
        return ok, report

    ok, report = asyncio.run(run_slack())
    assert ok is True and report.parts == 1


# -- a real process killed at each stage ------------------------------------------------------

_CHILD = textwrap.dedent("""
    import asyncio, os, sys
    from kazma_gateway import send_report
    from kazma_gateway.chat_outbox import ChatOutbox
    from kazma_gateway.gateway import BaseAdapter, GatewayManager, OutboundMessage

    stage = os.environ["STAGE"]
    log = os.environ["DELIVERED"]

    class Fake(BaseAdapter):
        name = "telegram"
        async def listen(self, queue, shutdown_event):
            return None
        async def send(self, outbound):
            if stage == "before_send":
                os._exit(9)                       # the row is 'sending', nothing left
            if stage == "refused":
                return False
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(outbound.text + "\\n")
            send_report.part_delivered()
            if stage == "after_delivery":
                os._exit(9)                       # it arrived; nothing recorded it
            return True

    manager = GatewayManager()
    manager.add_adapter(Fake())
    if os.environ.get("OUTBOX") == "1":
        manager.set_outbox(ChatOutbox(os.environ["DB"]))
    asyncio.run(manager.send(OutboundMessage(target_id="telegram:42", text="the reply",
                                             context_metadata={"chat_id": 42})))
    os._exit(9)                                   # killed after the send returned
""")


def _run_child(tmp_path, stage, *, outbox=True):
    env = {**os.environ, "STAGE": stage, "DELIVERED": str(tmp_path / "delivered.txt"),
           "DB": str(tmp_path / "chat_outbox.db"), "OUTBOX": "1" if outbox else "0",
           "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    proc = subprocess.run([sys.executable, "-c", _CHILD], env=env, capture_output=True, text=True,
                          timeout=120, check=False)
    assert proc.returncode == 9, proc.stderr[-2000:]


def _recover_and_drain(tmp_path) -> tuple[list[str], dict[str, int]]:
    adapter = _Fake(["ok"] * 5)
    manager, store = _manager(tmp_path, adapter)
    store.recover()
    _due(store, 100)
    asyncio.run(manager._drain_outbox_once())
    log = tmp_path / "delivered.txt"
    before = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return before + adapter.delivered, store.counts()


@pytest.mark.parametrize("stage, deliveries, state", [
    ("before_send", 0, "unknown"),      # may have left: an honest "unknown", never resent
    ("after_delivery", 1, "unknown"),   # arrived once; never twice
    ("refused", 1, "sent"),             # not taken: the next start's loop delivers it
    ("sent", 1, "sent"),                # done: nothing more
])
def test_a_process_killed_at_each_stage_never_delivers_twice(tmp_path, stage, deliveries, state):
    _run_child(tmp_path, stage)
    delivered, counts = _recover_and_drain(tmp_path)
    assert len(delivered) == deliveries, delivered
    assert counts == {state: 1}


def test_without_the_outbox_a_killed_process_loses_the_refused_reply(tmp_path):
    # The negative control for the "refused" stage.
    _run_child(tmp_path, "refused", outbox=False)
    assert not (tmp_path / "delivered.txt").exists()
    assert not (tmp_path / "chat_outbox.db").exists()
