"""Stage 3 review, L1 to L5: link codes and the links in memory.

* **L1** -- a linked person's hyphenated word ("next-week", "make-sure") is
  their message: handed on, never taken for a code nor counted as a guess;
  from a sender with no link a code alone is still a code.
* **L2** -- a live code anywhere in a group's message, beside a mention
  ("@kazmabot ABCD-2345"), is burned and never handed to the agent; and the
  account is told at its next page load which chat-app account was linked
  to it, with Unlink.
* **L3** -- links that could not be read again are not trusted: no stale
  admin.
* **L4** -- before the links were read, a reply waits in the outbox; it is
  never sent to a chat that may be unlinked.
* **L5** -- an unlinked sender of a Discord native command is told how to
  link, never to use the allowlist.

Through the real Telegram and Discord adapters, the real gateway manager
and outbox, the People record. Each has its control: the rule of before.
"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import chat_apps, chat_links, identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
from kazma_gateway import send_report
from kazma_gateway.chat_outbox import ChatOutbox
from kazma_gateway.gateway import BaseAdapter, GatewayManager, OutboundMessage

PASSWORD = "a-long-enough-password"
GROUP = -100661


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    monkeypatch.setattr(scope, "_enforced", True)
    store = get_config_store()
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    chat_links._forget_attempts_for_tests()
    chat_links._forget_links_for_tests()
    dana = people.create_person(f"dana-{uuid4().hex[:6]}", PASSWORD, by=OWNER_ACCOUNT_ID)
    uid = f"8{uuid4().int % 10**8}"
    code = chat_links.issue_code(dana["id"], "telegram")
    assert chat_links.redeem("telegram", uid, code["code"], direct=True,
                             target=f"telegram:{uid}").outcome == "linked"
    yield SimpleNamespace(dana=dana, uid=uid, chat=f"telegram:{uid}", store=store)
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    chat_links._forget_links_for_tests()


def _telegram_message(uid: str, text: str, *, group: bool) -> list[Any]:
    """What the real Telegram adapter hands on for *text* from *uid*."""
    from kazma_gateway.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter(token="123:abc")
    adapter._running = True
    sent: list[Any] = []

    async def send(out):
        sent.append(out)
        return True

    adapter.send = send  # type: ignore[method-assign]

    class _Http:
        async def post(self, path, json=None, **_kw):
            return MagicMock(status_code=200, json=lambda: {"ok": True, "result": True})

        async def get(self, path, params=None, **_kw):
            return MagicMock(status_code=200, json=lambda: {"ok": True, "result": []})

    adapter._http = _Http()
    adapter._ensure_http = lambda: adapter._http
    queue: asyncio.Queue = asyncio.Queue()
    adapter._queue = queue

    async def run() -> list[Any]:
        chat = {"id": GROUP, "type": "supergroup"} if group else {"id": int(uid), "type": "private"}
        await adapter._process_update({"update_id": 1, "message": {
            "message_id": 5, "date": 1, "chat": chat, "from": {"id": int(uid), "is_bot": False},
            "text": text}}, queue)
        for _ in range(30):
            await asyncio.sleep(0.01)
        return [queue.get_nowait() for _ in range(queue.qsize())]

    return asyncio.run(run())


# ── L1 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("word", ["next-week", "make-sure", "type-safe"])
def test_a_linked_person_s_hyphenated_word_is_their_message(world, word) -> None:
    handed = _telegram_message(world.uid, word, group=False)
    assert [m.text for m in handed] == [word], f"{word!r} was taken for a link code"
    assert chat_links.take_attempt("telegram", world.uid), "it was counted as a guess"


def test_from_a_sender_with_no_link_a_code_alone_is_a_code(world) -> None:
    assert _telegram_message("5550001", "next-week", group=False) == []


# ── L2 ──────────────────────────────────────────────────────────────────


def test_a_live_code_beside_a_mention_in_a_group_is_burned(world) -> None:
    code = chat_links.issue_code(world.dana["id"], "telegram")["code"]
    handed = _telegram_message(world.uid, f"@kazmabot {code}", group=True)
    assert handed == [], "the code reached the agent"
    assert not chat_links.is_live_code(code), "the code was not burned"
    assert chat_links.redeem("telegram", "5550002", code, direct=True).outcome == "used"


def test_the_account_is_told_which_chat_was_linked(world) -> None:
    told = [n for n in people.notices_for(world.dana["id"]) if n["kind"] == "chat_linked"]
    assert [(n["platform"], n["chat_user"]) for n in told] == [("telegram", world.uid)], (
        "the account was not told a chat was linked")


def test_an_adoption_at_the_switch_is_not_told(world) -> None:
    world.store.set("guard.telegram.chat_id", "7072", category="guard")
    try:
        chat_links.adopt_owner_ids(by=OWNER_ACCOUNT_ID)
        assert [n for n in people.notices_for(OWNER_ACCOUNT_ID) if n["kind"] == "chat_linked"] == []
    finally:
        world.store.delete("guard.telegram.chat_id")


# ── L3 ──────────────────────────────────────────────────────────────────


def test_links_that_cannot_be_read_again_make_no_admin(world, monkeypatch) -> None:
    def owner_link(reg: dict) -> None:
        reg["links"].append({"platform": "telegram", "user_id": "7073", "account": OWNER_ACCOUNT_ID})

    identity.update_registry(owner_link)
    assert chat_links.linked_admin("telegram", "7073")

    def unreadable():
        raise identity.IdentityUnavailable("the database is away")

    monkeypatch.setattr(identity, "read_registry", unreadable)
    chat_links.refresh_links()
    assert not chat_links.linked_admin("telegram", "7073"), "a stale snapshot made an admin"


# ── L4 ──────────────────────────────────────────────────────────────────


class _Telegram(BaseAdapter):
    name = "telegram"

    def __init__(self) -> None:
        super().__init__()
        self.delivered: list[str] = []

    async def listen(self, queue, shutdown_event) -> None:  # pragma: no cover
        return None

    async def send(self, outbound: OutboundMessage) -> bool:
        send_report.part_delivered()
        self.delivered.append(outbound.target_id)
        return True


def test_before_the_links_are_read_a_reply_waits(world, monkeypatch, tmp_path) -> None:
    assert chat_links.unlink(world.dana["id"], "telegram", world.uid)
    chat_links._forget_links_for_tests()  # a start whose first read failed

    def unreadable():
        raise identity.IdentityUnavailable("the database is away")

    monkeypatch.setattr(identity, "read_registry", unreadable)
    monkeypatch.setattr(chat_links, "_refresh_soon", lambda: None)
    telegram = _Telegram()
    manager = GatewayManager()
    manager.add_adapter(telegram)
    outbox = ChatOutbox(tmp_path / "chat_outbox.db")
    manager.set_outbox(outbox)
    asyncio.run(manager.send(OutboundMessage(target_id=world.chat, text="your report",
                                             context_metadata={"chat_id": int(world.uid)})))
    assert telegram.delivered == [], "a reply went to a chat that may be unlinked"
    conn = sqlite3.connect(outbox.path)
    try:
        assert [r[0] for r in conn.execute("SELECT state FROM outbox")] == ["pending"]
    finally:
        conn.close()


# ── L5 ──────────────────────────────────────────────────────────────────


def test_an_unlinked_native_command_sender_is_told_how_to_link(world) -> None:
    from kazma_gateway.adapters import discord_commands
    from kazma_gateway.adapters.discord import DiscordAdapter

    adapter = DiscordAdapter(token="fake")
    said: list[dict] = []

    async def post(url, json=None, **_kw):
        said.append(json or {})
        return MagicMock(status_code=200, raise_for_status=lambda: None)

    adapter._http = MagicMock()
    adapter._http.post = AsyncMock(side_effect=post)
    asyncio.run(discord_commands.handle(adapter, {
        "id": "1", "token": "t", "application_id": "2", "channel_id": "33", "guild_id": "44",
        "member": {"user": {"id": "990001"}}, "data": {"name": "kazma", "options": []}}))
    content = ((said[0] if said else {}).get("data") or {}).get("content", "")
    assert "not linked" in content and "My account" in content, content


# ── negative controls ───────────────────────────────────────────────────


def test_negative_control_l1_every_bare_word_a_code(world, monkeypatch) -> None:
    """Planted: a code alone taken from anyone. A linked person's word is
    swallowed: the check above fails on its own assertion."""
    monkeypatch.setattr(chat_links, "account_linked", lambda *a, **k: False)
    with pytest.raises(AssertionError, match="was taken for a link code"):
        test_a_linked_person_s_hyphenated_word_is_their_message(world, "next-week")


def test_negative_control_l2_a_code_only_alone(world, monkeypatch) -> None:
    """Planted: a code counted only as the whole message. The mention's code
    reaches the agent: the check above fails on its own assertion."""
    monkeypatch.setattr(chat_links, "live_codes_in", lambda text: [])
    with pytest.raises(AssertionError, match="the code reached the agent"):
        test_a_live_code_beside_a_mention_in_a_group_is_burned(world)


def test_negative_control_l2_a_silent_link(world, monkeypatch) -> None:
    """Planted: the notices of before, which never told a link."""
    monkeypatch.setattr(people, "_TOLD", ("password_reset", "member_added", "member_removed",
                                          "sessions_ended"))
    with pytest.raises(AssertionError, match="was not told a chat was linked"):
        test_the_account_is_told_which_chat_was_linked(world)


def test_negative_control_l3_the_stale_snapshot(world, monkeypatch) -> None:
    """Planted: a failed read keeps what was read before."""
    monkeypatch.setattr(chat_links, "_forget_snapshot", lambda: None)
    with pytest.raises(AssertionError, match="a stale snapshot made an admin"):
        test_links_that_cannot_be_read_again_make_no_admin(world, monkeypatch)


def test_negative_control_l4_sent_on_a_guess(world, monkeypatch, tmp_path) -> None:
    """Planted: a reply sent before the links are read."""
    from kazma_gateway import gateway

    monkeypatch.setattr(gateway, "_links_unknown", lambda: False)
    with pytest.raises(AssertionError, match="may be unlinked"):
        test_before_the_links_are_read_a_reply_waits(world, monkeypatch, tmp_path)
