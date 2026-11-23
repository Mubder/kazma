"""Spaces S3-02: once Spaces is enforced, a chat app answers a linked,
enabled account and no one else.

The private-by-default test (``test_chat_apps_private_by_default.py``)
re-pointed: every accept path of every adapter -- Telegram's updates and
buttons, Discord's messages and buttons, Slack's socket and polling paths and
buttons, WhatsApp's messages -- asks ``SenderGate.sender_refusal``, which asks
the link (``chat_links.link_refusal``, from memory) once Spaces is on:

* a linked, enabled account passes; a stranger is refused (``not_linked``)
  and told, in a direct chat, how to link; a disabled account is refused
  (``account_disabled``) and told so, at once; a deleted one is a stranger;
* before the links are first read a sender waits (``links_unknown``), never
  let in, and is not told;
* the allowlists retire: a sender who is only on an allowlist is refused;
* the owner's chat-app accounts Kazma already knows (the guard's Telegram
  chat, ``KAZMA_GATEWAY_ADMINS`` entries) become the owner's links when
  Spaces is switched on, with no step from the owner -- never an account
  someone else linked; a listed sender who is not linked is named first.

An install that does not enforce Spaces keeps the allowlist rule (the
private-by-default test). Negative control: the allowlist rule lets a listed
stranger act as the owner.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import chat_links, identity, people, preconditions, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from kazma_gateway import sender_rule
from kazma_gateway.sender_rule import ACCOUNT_OFF, LINKS_UNKNOWN, NOT_LINKED, SenderGate

PASSWORD = "a-long-enough-password"
DANA_TG = 777101
STRANGER_TG = 777102
OWNER_TG = 777100


@pytest.fixture
def store(monkeypatch):
    cs = get_config_store()
    for key in (identity.REGISTRY_KEY, chat_links.CODES_KEY, "guard.telegram.chat_id",
                "connectors.telegram.allowed_users", identity.ENFORCED_KEY):
        cs.delete(key)
    monkeypatch.delenv("KAZMA_GATEWAY_ADMINS", raising=False)
    monkeypatch.delenv("KAZMA_GATEWAY_OPEN_TO_EVERYONE", raising=False)
    chat_links._forget_attempts_for_tests()
    chat_links._forget_links_for_tests()
    sender_rule._forget_told_for_tests()
    yield cs
    for key in (identity.REGISTRY_KEY, chat_links.CODES_KEY, "guard.telegram.chat_id",
                "connectors.telegram.allowed_users", identity.ENFORCED_KEY):
        cs.delete(key)
    chat_links._forget_links_for_tests()
    sender_rule._forget_told_for_tests()
    from kazma_core.security.vault import reset_posture_cache

    reset_posture_cache()


@pytest.fixture
def enforced(monkeypatch, store):
    monkeypatch.setattr(scope, "_enforced", True)
    return store


@pytest.fixture
def dana(enforced) -> dict[str, Any]:
    """A person whose Telegram account is linked (through a real code)."""
    person = people.create_person("dana-senders", PASSWORD, by=OWNER_ACCOUNT_ID)
    code = chat_links.issue_code(person["id"], "telegram")
    assert chat_links.redeem("telegram", DANA_TG, code["code"], direct=True).outcome == "linked"
    return person


def _link(person_id: str, platform: str, user_id: str) -> None:
    code = chat_links.issue_code(person_id, platform)
    assert chat_links.redeem(platform, user_id, code["code"], direct=True).outcome == "linked"


# ── adapters ────────────────────────────────────────────────────────────


def _recording(adapter: Any, sent: list[Any]) -> Any:
    async def record(outbound: Any) -> bool:
        sent.append(outbound)
        return True

    adapter.send = record  # type: ignore[method-assign]
    adapter._running = True
    return adapter


def _telegram(sent: list[Any]) -> Any:
    from kazma_gateway.adapters.telegram import TelegramAdapter
    from kazma_gateway.allowlists import apply_adapter_allowlists

    adapter = TelegramAdapter(token="123:abc")
    apply_adapter_allowlists(adapter, get_config_store())

    async def nothing(*args: Any, **kwargs: Any) -> None:
        return None

    adapter._announce_if_added_to_group = nothing  # type: ignore[method-assign]
    adapter._set_reaction = nothing  # type: ignore[method-assign]
    return _recording(adapter, sent)


def _tg(uid: int, user: int, text: str = "hello there") -> dict:
    return {"update_id": uid, "message": {
        "message_id": uid, "chat": {"id": user, "type": "private"},
        "from": {"id": user, "is_bot": False, "first_name": "B"}, "date": 1, "text": text,
    }}


async def _settle() -> None:
    for _ in range(400):
        await asyncio.sleep(0.02)
        if not any(t.get_name().endswith(("-private-notice", "-chat-link"))
                   for t in asyncio.all_tasks() if not t.done()):
            return
    raise AssertionError("a background notice never finished")


def _telegram_run(adapter: Any, *updates: dict) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()

    async def run() -> None:
        for update in updates:
            await adapter._process_update(update, queue)
        await _settle()

    asyncio.run(run())
    return queue


# ── Telegram: messages and buttons ──────────────────────────────────────


def test_a_linked_account_passes_and_a_stranger_is_told_how_to_link(dana) -> None:
    sent: list[Any] = []
    adapter = _telegram(sent)
    queue = _telegram_run(adapter, _tg(1, DANA_TG), _tg(2, STRANGER_TG))
    assert queue.qsize() == 1 and queue.get_nowait().context_metadata["user_id"] == DANA_TG
    assert adapter.diagnostics()["dropped"] == {NOT_LINKED: 1}
    assert [s.target_id for s in sent] == [f"telegram:{STRANGER_TG}"]
    assert "Link Telegram" in sent[0].text and "/link" in sent[0].text
    assert adapter.actor_allowed(DANA_TG) and not adapter.actor_allowed(STRANGER_TG)
    assert adapter.admission()["linked"] == 1


def test_a_stranger_is_told_in_their_language(dana) -> None:
    sent: list[Any] = []
    _telegram_run(_telegram(sent), _tg(1, STRANGER_TG, "مرحبا، كيف حالك؟"))
    assert "تطبيقات الدردشة" in sent[0].text and "Link Telegram" not in sent[0].text


def test_a_disabled_account_is_refused_at_once_and_told(dana) -> None:
    people.set_enabled(dana["id"], False, by=OWNER_ACCOUNT_ID)
    sent: list[Any] = []
    adapter = _telegram(sent)
    queue = _telegram_run(adapter, _tg(1, DANA_TG))
    assert queue.qsize() == 0 and adapter.diagnostics()["dropped"] == {ACCOUNT_OFF: 1}
    assert "disabled" in sent[0].text
    assert not adapter.actor_allowed(DANA_TG), "a button of theirs is refused too"
    people.set_enabled(dana["id"], True, by=OWNER_ACCOUNT_ID)
    assert adapter.actor_allowed(DANA_TG), "enabled again: theirs again, at once"


def test_a_deleted_account_is_a_stranger(dana) -> None:
    people.delete_person(dana["id"], by=OWNER_ACCOUNT_ID)
    assert chat_links.link_refusal("telegram", DANA_TG) == NOT_LINKED


def test_before_the_links_are_read_a_sender_waits_and_is_not_told(dana) -> None:
    chat_links._forget_links_for_tests()
    sent: list[Any] = []
    adapter = _telegram(sent)
    queue = _telegram_run(adapter, _tg(1, DANA_TG))
    assert queue.qsize() == 0 and adapter.diagnostics()["dropped"] == {LINKS_UNKNOWN: 1}
    assert sent == [], "Kazma's own wait is not the sender's to hear about"
    assert chat_links._drain_links_refresh(), "the read it started, in a thread"
    assert chat_links.link_refusal("telegram", DANA_TG) == ""


def test_the_links_are_read_again_when_they_are_old(dana, monkeypatch) -> None:
    """Another process changed the registry: the links in memory are read
    again in a thread past their age, never on the adapter's loop."""
    started: list[bool] = []
    monkeypatch.setattr(chat_links, "_refresh_soon", lambda: started.append(True))
    assert chat_links.link_refusal("telegram", DANA_TG) == "" and not started
    real = chat_links._now
    monkeypatch.setattr(chat_links, "_now", lambda: real() + chat_links.LINKS_TTL_S + 1)
    assert chat_links.link_refusal("telegram", DANA_TG) == "" and started == [True]


# ── the allowlists retire ───────────────────────────────────────────────


def _listed_stranger_is_refused() -> None:
    store = get_config_store()
    store.set("connectors.telegram.allowed_users", f"{STRANGER_TG}", "connectors")
    sent: list[Any] = []
    adapter = _telegram(sent)
    queue = _telegram_run(adapter, _tg(9, STRANGER_TG))
    assert queue.qsize() == 0, "an allowlisted stranger never acts as the owner"
    assert not adapter.actor_allowed(STRANGER_TG)


def test_a_sender_on_an_allowlist_only_is_refused(dana) -> None:
    _listed_stranger_is_refused()


def test_negative_control_the_allowlist_lets_a_listed_stranger_act_as_the_owner(dana, monkeypatch) -> None:
    """Planted: the sender rule as it was before S3-02, whatever Spaces says."""

    def before(self: SenderGate, user_id: Any) -> str:
        return sender_rule.refusal(user_id, allowed=self._allowed_users or (),
                                   owners=self._owner_ids, open_to_everyone=self._allow_all)

    monkeypatch.setattr(SenderGate, "sender_refusal", before)
    with pytest.raises(AssertionError, match="never acts as the owner"):
        _listed_stranger_is_refused()


# ── Discord, Slack and WhatsApp ─────────────────────────────────────────


def test_discord_asks_the_link_for_messages_and_buttons(dana) -> None:
    from kazma_gateway.adapters.discord import DiscordAdapter

    _link(dana["id"], "discord", "4242")
    sent: list[Any] = []
    adapter = _recording(DiscordAdapter(token="fake:token"), sent)

    def msg(mid: str, author: str) -> dict[str, Any]:
        return {"id": mid, "channel_id": f"dm-{author}", "content": "hello",
                "author": {"id": author, "username": f"n-{author}", "bot": False}}

    async def run() -> list[Any]:
        got = [adapter._accept_message(msg("m1", "4242")), adapter._accept_message(msg("m2", "5555"))]
        await _settle()
        return got

    linked, stranger = asyncio.run(run())
    assert linked is not None and stranger is None
    assert adapter.diagnostics()["dropped"] == {NOT_LINKED: 1}
    assert [s.target_id for s in sent] == ["discord:dm-5555"]
    assert adapter.actor_allowed("4242") and not adapter.actor_allowed("5555")


def test_slack_asks_the_link_on_both_paths(dana) -> None:
    from kazma_gateway.adapters.slack import SlackAdapter

    _link(dana["id"], "slack", "U5")
    sent: list[Any] = []
    adapter = _recording(SlackAdapter(bot_token="xoxb-t", app_token="xapp-t"), sent)
    queue: asyncio.Queue = asyncio.Queue()
    adapter._queue = queue

    async def run() -> Any:
        got = adapter._accept_event({"type": "message", "ts": "1.0", "user": "U5", "channel": "D5",
                                     "text": "hi"})
        await adapter._handle_message("C1", {"ts": "2.0", "user": "U6", "text": "hi"})
        await adapter._handle_message("C1", {"ts": "3.0", "user": "U5", "text": "hi"})
        await _settle()
        return got

    assert asyncio.run(run()) is not None
    assert adapter.diagnostics()["dropped"] == {NOT_LINKED: 1}
    assert queue.qsize() == 1, "the linked sender's polled message is handed on"
    assert adapter.actor_allowed("U5") and not adapter.actor_allowed("U6")


def test_whatsapp_asks_the_link(dana) -> None:
    from kazma_gateway.adapters.whatsapp import WhatsAppAdapter
    from kazma_gateway.adapters.whatsapp_parse import WaInbound

    _link(dana["id"], "whatsapp", "96599990000")
    sent: list[Any] = []
    adapter = _recording(WhatsAppAdapter("EAAG-test-token", "PHONE1"), sent)

    async def nothing(*args: Any, **kwargs: Any) -> None:
        return None

    adapter._mark_read = nothing  # type: ignore[method-assign]
    queue: asyncio.Queue = asyncio.Queue()
    adapter._queue = queue

    def inbound(mid: str, number: str) -> WaInbound:
        return WaInbound(message_id=mid, from_id=number, name="N", phone_number_id="PHONE1",
                         timestamp=1.0, kind="text", text="hello")

    async def run() -> None:
        await adapter._process(inbound("w2", "96500000001"))
        await _settle()

    asyncio.run(run())
    assert adapter.diagnostics()["dropped"] == {NOT_LINKED: 1}
    assert adapter.actor_allowed("+965 9999 0000") and not adapter.actor_allowed("96500000001")


# ── the owner's known ids become the owner's links ──────────────────────


def test_the_owner_s_known_ids_become_the_owner_s_links_at_the_switch(store, monkeypatch) -> None:
    store.set("guard.telegram.chat_id", str(OWNER_TG), "guard")
    # The environment list is the admin rule of before: the owner's Telegram
    # is in it, as it was the owner (the stage 3 review, M1: only what was
    # the owner before the switch is adopted).
    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", f"telegram:{OWNER_TG}, discord:4242, slack:U77, 31337")
    planned = next(s for s in preconditions.plan() if s["step"] == "chat_links")
    assert len(planned["changes"]) == 3, planned
    monkeypatch.setattr(scope, "_enforced", False)
    try:
        preconditions.switch_on(by=OWNER_ACCOUNT_ID)
        assert scope.spaces_enforced()
        assert chat_links.link_refusal("telegram", OWNER_TG) == "", "the owner's chat keeps working"
        assert chat_links._linked_account("discord", "4242") == OWNER_ACCOUNT_ID
        assert chat_links._linked_account("slack", "U77") == OWNER_ACCOUNT_ID
        assert chat_links.link_refusal("telegram", "31337") == NOT_LINKED, "an unqualified entry names no app"
        assert chat_links.adopt_owner_ids() == [], "a second run changes nothing"
        events = [e for e in identity.read_registry()["events"] if e["kind"] == "chat_linked"]
        assert [e.get("detail") for e in events] == ["telegram", "discord", "slack"]
    finally:
        scope.set_enforced(False)


def test_a_known_id_someone_linked_stays_theirs(dana, enforced) -> None:
    enforced.set("guard.telegram.chat_id", str(DANA_TG), "guard")
    assert chat_links.owner_links_to_adopt() == []
    assert chat_links.adopt_owner_ids() == []
    assert chat_links._linked_account("telegram", DANA_TG) == dana["id"]


def test_the_check_names_a_listed_sender_who_is_not_linked(dana, enforced) -> None:
    enforced.set("connectors.telegram.allowed_users", f"{DANA_TG}, {STRANGER_TG}", "connectors")
    notes = [n for n in preconditions.notes() if "allowlist" in n]
    assert len(notes) == 1 and f"Telegram {STRANGER_TG}" in notes[0]
    assert str(DANA_TG) not in notes[0], "a linked sender is not named"


# ── what the owner sees ─────────────────────────────────────────────────


def test_the_test_says_the_allowlist_is_not_read(dana) -> None:
    from kazma_gateway.connector_test import allowlist_check

    ok, said = allowlist_check([str(STRANGER_TG)], platform="Telegram", find_id="x")
    assert ok is True and "Spaces is on" in said and "1 Telegram" in said
    ok, said = allowlist_check([], platform="Discord", find_id="x")
    assert ok is False and "Link Discord" in said


def test_people_lists_each_person_s_links(dana) -> None:
    listed = next(p for p in people.list_people() if p["id"] == dana["id"])
    assert listed["links"] == [{"platform": "telegram", "user_id": str(DANA_TG)}]


def test_without_spaces_the_allowlist_rule_stands(store) -> None:
    store.set("connectors.telegram.allowed_users", f"{STRANGER_TG}", "connectors")
    adapter = _telegram([])
    assert adapter.actor_allowed(STRANGER_TG), "one account: the allowlist, as before"
    assert not adapter.actor_allowed(DANA_TG)
