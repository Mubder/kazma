"""Spaces S3-07: unlinking -- and a disabled or deleted account -- stops
everything at once.

With Spaces enforced and Dana's Telegram linked (S3-01) from her direct
chat, she unlinks it in My account (``chat_links.unlink``):

* **the sender is refused at once**: her next message and her next button
  press are refused at the adapter (the links are read again in the writer's
  thread), and a message already queued is not run (``chat_place``);
* **what waits for her chat is dropped** (``chat_links.address_revoked``,
  asked by ``GatewayManager.send`` and the outbox's retry pass): a reply
  waiting in the chat outbox is settled ``dropped`` and never sent, and an
  approval card's expiry, delivered by the watchdog's resume, never reaches
  her chat;
* the same for a person disabled (until enabled again) or deleted;
* linked again, her chat is answered again; an owner's known id the owner
  unlinked is not linked again on its own.

Negative control: a waiting reply still sent -- the outbox sends to the
chat whatever happened to its person.
"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import chat_apps, chat_links, identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from kazma_gateway import send_report
from kazma_gateway.chat_outbox import ChatOutbox
from kazma_gateway.gateway import BaseAdapter, GatewayManager, IncomingMessage, OutboundMessage

PASSWORD = "a-long-enough-password"


class _Telegram(BaseAdapter):
    """A Telegram that records what it was handed."""

    name = "telegram"

    def __init__(self) -> None:
        super().__init__()
        self.delivered: list[tuple[str, str]] = []

    async def listen(self, queue, shutdown_event) -> None:  # pragma: no cover
        return None

    async def send(self, outbound: OutboundMessage) -> bool:
        send_report.part_delivered()
        self.delivered.append((outbound.target_id, outbound.text))
        return True


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    monkeypatch.setattr(scope, "_enforced", True)
    store = get_config_store()
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    chat_links._forget_attempts_for_tests()
    dana = people.create_person(f"dana-{uuid4().hex[:6]}", PASSWORD, by=OWNER_ACCOUNT_ID)
    uid = f"9{uuid4().int % 10**8}"
    code = chat_links.issue_code(dana["id"], "telegram")
    assert chat_links.redeem("telegram", uid, code["code"], direct=True,
                             target=f"telegram:{uid}").outcome == "linked"
    telegram = _Telegram()
    manager = GatewayManager()
    manager.add_adapter(telegram)
    outbox = ChatOutbox(tmp_path / "chat_outbox.db")
    manager.set_outbox(outbox)
    yield SimpleNamespace(dana=dana, uid=uid, chat=f"telegram:{uid}", telegram=telegram,
                          manager=manager, outbox=outbox)
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    chat_links._forget_links_for_tests()


def _unlink(w: Any) -> None:
    assert chat_links.unlink(w.dana["id"], "telegram", w.uid, by=w.dana["id"])


def _waiting_reply(w: Any) -> str:
    """A reply to Dana's chat the platform did not take, due another try."""
    row = w.outbox.begin(OutboundMessage(target_id=w.chat, text="Your report is ready.",
                                         context_metadata={"chat_id": int(w.uid)}))
    assert w.outbox.finish(row, "not_sent") == "pending"
    conn = sqlite3.connect(w.outbox.path)
    try:
        with conn:
            conn.execute("UPDATE outbox SET next_attempt_at = 0 WHERE id = ?", (row,))
    finally:
        conn.close()
    return row


def _state(w: Any, row: str) -> str:
    conn = sqlite3.connect(w.outbox.path)
    try:
        return str(conn.execute("SELECT state FROM outbox WHERE id = ?", (row,)).fetchone()[0])
    finally:
        conn.close()


# ── the outbox ──────────────────────────────────────────────────────────


def test_a_reply_waiting_in_the_outbox_is_dropped(world) -> None:
    row = _waiting_reply(world)
    _unlink(world)
    asyncio.run(world.manager._drain_outbox_once())
    assert world.telegram.delivered == [], "nothing reached her chat"
    assert _state(world, row) == "dropped"


def test_a_new_reply_to_her_chat_is_dropped(world) -> None:
    _unlink(world)
    sent = asyncio.run(world.manager.send(OutboundMessage(target_id=world.chat, text="hello",
                                                          context_metadata={"chat_id": int(world.uid)})))
    assert sent is False and world.telegram.delivered == []


def test_a_waiting_reply_is_sent_while_she_is_linked(world) -> None:
    _waiting_reply(world)
    asyncio.run(world.manager._drain_outbox_once())
    assert [t for t, _ in world.telegram.delivered] == [world.chat]


# ── the inbox and callbacks ─────────────────────────────────────────────


def test_her_next_message_and_press_are_refused_at_once(world) -> None:
    from kazma_gateway.adapters.telegram import TelegramAdapter

    _unlink(world)
    assert chat_links.link_refusal("telegram", world.uid) == chat_links.NOT_LINKED
    adapter = TelegramAdapter(token="123:abc")
    adapter._running = True
    answered: list[str] = []

    async def answer(_cb_id: str, text: str = "") -> None:
        answered.append(text)

    adapter._answer_callback_query = answer
    queue: asyncio.Queue = asyncio.Queue()
    adapter._queue = queue

    async def press() -> int:
        await adapter._handle_callback_query({
            "id": "cb-1", "data": "hitl:approve:gw-x~iid-1",
            "message": {"message_id": 7, "chat": {"id": int(world.uid), "type": "private"}},
            "from": {"id": int(world.uid)},
        })
        for _ in range(20):
            await asyncio.sleep(0.01)
        return queue.qsize()

    assert asyncio.run(press()) == 0, "the press never reached the handler"
    assert answered and "Not authorized" in answered[0]


def test_a_message_already_queued_is_not_run(world) -> None:
    from kazma_gateway.agent_handler.graph import create_graph_handler
    from kazma_gateway.agent_handler.store import _InMemoryStore

    ran: list[str] = []

    class _Graph:
        async def aget_state(self, config):
            return SimpleNamespace(values={}, next=(), tasks=())

        async def ainvoke(self, state, config):
            ran.append(config["configurable"]["thread_id"])
            return {**state, "messages": [*state["messages"], {"role": "assistant", "content": "ok"}]}

    handler = create_graph_handler(graph=_Graph(), manager=world.manager, store=_InMemoryStore())
    queued = IncomingMessage("telegram", f"telegram:{world.uid}", "are you there?",
                             context_metadata={"chat_id": int(world.uid), "user_id": int(world.uid),
                                               "chat_type": "private"})
    _unlink(world)
    asyncio.run(handler(queued))
    assert ran == [] and world.telegram.delivered == []


# ── a pending card ──────────────────────────────────────────────────────


def test_a_pending_card_s_expiry_never_reaches_her_chat(world, monkeypatch) -> None:
    from kazma_gateway.agent_handler import hitl

    class _Graph:
        async def aget_state(self, config):
            return SimpleNamespace(values={"messages": []}, next=(), tasks=())

    async def resumed(graph, cmd, config, thread_id):
        return {"messages": [{"role": "assistant", "content": "the rest of the turn"}],
                hitl._SAID_BEFORE: []}, None

    _unlink(world)
    resumer = hitl.make_timeout_resumer(_Graph(), world.manager, None)
    monkeypatch.setattr(hitl, "_run_resume", resumed)
    asyncio.run(resumer(thread_id="gw-dana", resume_cmd=None,
                        resume_config={"configurable": {"thread_id": "gw-dana"}},
                        platform="telegram", target_id=world.chat, tool="file_write", timeout_s=300))
    assert world.telegram.delivered == [], "nothing of the card reached her chat"


# ── disabled, deleted, linked again ─────────────────────────────────────


def test_a_disabled_person_s_chat_is_dropped_until_enabled(world) -> None:
    people.set_enabled(world.dana["id"], False, by=OWNER_ACCOUNT_ID)
    assert chat_links.address_revoked(world.chat)
    assert chat_links.link_refusal("telegram", world.uid) == chat_links.ACCOUNT_OFF
    people.set_enabled(world.dana["id"], True, by=OWNER_ACCOUNT_ID)
    assert not chat_links.address_revoked(world.chat)


def test_a_deleted_person_s_chat_is_dropped(world) -> None:
    people.delete_person(world.dana["id"], by=OWNER_ACCOUNT_ID)
    assert chat_links.address_revoked(world.chat)
    assert chat_links.link_refusal("telegram", world.uid) == chat_links.NOT_LINKED


def test_linked_again_her_chat_is_answered_again(world) -> None:
    _unlink(world)
    code = chat_links.issue_code(world.dana["id"], "telegram")
    assert chat_links.redeem("telegram", world.uid, code["code"], direct=True,
                             target=world.chat).outcome == "linked"
    assert not chat_links.address_revoked(world.chat)
    assert chat_links.link_refusal("telegram", world.uid) == ""


def test_an_owner_s_known_id_the_owner_unlinked_is_not_linked_again(world, monkeypatch) -> None:
    store = get_config_store()
    store.set("guard.telegram.chat_id", "7071", category="guard")
    try:
        assert [m["user_id"] for m in chat_links.adopt_owner_ids()] == ["7071"]
        assert chat_links.unlink(OWNER_ACCOUNT_ID, "telegram", "7071")
        assert chat_links.owner_links_to_adopt() == [], "the owner's word stands"
        assert chat_links.adopt_owner_ids() == []
    finally:
        store.delete("guard.telegram.chat_id")


def test_before_spaces_nothing_is_dropped(world, monkeypatch) -> None:
    _unlink(world)
    monkeypatch.setattr(scope, "_enforced", False)
    assert not chat_links.address_revoked(world.chat)


# ── negative control ────────────────────────────────────────────────────


def test_negative_control_a_waiting_reply_still_sent(world, monkeypatch) -> None:
    """Planted: the outbox of before, which sends to a chat whatever
    happened to its person. The reply waiting for Dana reaches her chat
    after she unlinked: the check above fails on its own assertion."""
    monkeypatch.setattr(chat_links, "address_revoked", lambda _target: False)
    with pytest.raises(AssertionError, match="nothing reached her chat"):
        test_a_reply_waiting_in_the_outbox_is_dropped(world)
