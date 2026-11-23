"""Spaces S3-01: a person links their chat-app account with a short code.

A signed-in person asks My account for a code (``chat_links.issue_code``,
``POST /api/account/chat-links/code``) and sends it to the bot in a direct
chat. Every adapter's message accept path hands a code to the link flow
before the sender rule (``SenderGate.link_attempted``), so the sender, not
linked yet, is not refused first. Through the real adapters:

* a code links the sender's account to the account that asked for it, the
  sender is told whose account they are now, and the code never reaches the
  agent;
* a used code, an old one and a wrong one link nothing;
* a live code typed in a group, a server channel or a Slack channel is
  refused and burned there; one sent on another chat app is burned too;
* a chat-app account linked to someone is not moved by another's code;
* a sender who keeps sending codes is not answered past five in ten
  minutes, counted as they arrive (a flood at once too);
* an install that does not enforce Spaces issues no code and every message,
  code-shaped or not, goes where it always went.

Negative control: a code that can be used twice links a second sender.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.config_store import get_config_store
from kazma_core.spaces import chat_links, identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from kazma_gateway import sender_rule
from kazma_gateway.sender_rule import LINK_CODE

PASSWORD = "a-long-enough-password"
DANA_TG = 555001
OMAR_TG = 555002


@pytest.fixture
def clean_store():
    store = get_config_store()
    store.delete(identity.REGISTRY_KEY)
    store.delete(chat_links.CODES_KEY)
    chat_links._forget_attempts_for_tests()
    sender_rule._forget_told_for_tests()
    yield store
    store.delete(identity.REGISTRY_KEY)
    store.delete(chat_links.CODES_KEY)
    chat_links._forget_attempts_for_tests()
    sender_rule._forget_told_for_tests()
    from kazma_core.security.vault import reset_posture_cache

    reset_posture_cache()


@pytest.fixture
def enforced(monkeypatch, clean_store):
    monkeypatch.setattr(scope, "_enforced", True)
    return clean_store


@pytest.fixture
def dana(enforced) -> dict[str, Any]:
    return people.create_person("dana-links", PASSWORD, by=OWNER_ACCOUNT_ID)


@pytest.fixture
def omar(enforced) -> dict[str, Any]:
    return people.create_person("omar-links", PASSWORD, by=OWNER_ACCOUNT_ID)


# ── adapters, as the private-by-default test drives them ────────────────


def _recording(adapter: Any, sent: list[Any]) -> Any:
    async def record(outbound: Any) -> bool:
        sent.append(outbound)
        return True

    adapter.send = record  # type: ignore[method-assign]
    adapter._running = True
    return adapter


def _telegram(sent: list[Any]) -> Any:
    from kazma_gateway.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter(token="123:abc")

    async def nothing(*args: Any, **kwargs: Any) -> None:
        return None

    adapter._announce_if_added_to_group = nothing  # type: ignore[method-assign]
    adapter._set_reaction = nothing  # type: ignore[method-assign]
    return _recording(adapter, sent)


def _tg_update(uid: int, user: int, text: str, *, chat: dict | None = None,
               forwarded: bool = False) -> dict:
    message: dict[str, Any] = {
        "message_id": uid, "chat": chat or {"id": user, "type": "private"},
        "from": {"id": user, "is_bot": False, "first_name": "B"}, "date": 1, "text": text,
    }
    if forwarded:
        message["forward_origin"] = {"type": "user", "sender_user": {"id": 1, "first_name": "X"}}
    return {"update_id": uid, "message": message}


async def _settle() -> None:
    """Let the link flow's background tasks (a thread, then the answer) end."""
    for _ in range(400):
        await asyncio.sleep(0.02)
        if not any(t.get_name().endswith(("-chat-link", "-private-notice"))
                   for t in asyncio.all_tasks() if not t.done()):
            return
    raise AssertionError("the link flow never finished")


def _send_telegram(adapter: Any, *updates: dict) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()

    async def run() -> None:
        for update in updates:
            await adapter._process_update(update, queue)
        await _settle()

    asyncio.run(run())
    return queue


# ── the code's shape ────────────────────────────────────────────────────


@pytest.mark.parametrize("text, expected", [
    ("/link ABCD-2345", "ABCD2345"),
    ("/link@kazma_bot abcd2345", "ABCD2345"),
    ("link abcd-2345", "ABCD2345"),
    ("ربط ABCD-2345", "ABCD2345"),
    ("ABCD-2345", "ABCD2345"),
    ("  abcd-2345  ", "ABCD2345"),
    ("/link 1234-5678", "12345678"),  # named /link: an attempt, though no code has a 1
    ("2026-1123", None),              # a date is never a code
    ("THANKYOU", None),               # a word alone is never a code
    ("ABCD2345", None),               # a code alone needs its dash
    ("please link ABCD-2345 now", None),
    ("", None),
    (None, None),
])
def test_code_in_reads_only_link_codes(text, expected) -> None:
    assert chat_links.code_in(text) == expected


# ── issuing ─────────────────────────────────────────────────────────────


def test_a_code_is_kept_as_its_hash_and_replaces_the_last_one(dana, enforced) -> None:
    first = chat_links.issue_code(dana["id"], "telegram")
    second = chat_links.issue_code(dana["id"], "telegram")
    stored = enforced.get(chat_links.CODES_KEY, {})
    text = json.dumps(stored)
    assert first["code"].replace("-", "") not in text and second["code"].replace("-", "") not in text
    assert len(stored) == 1, "a new code for the same chat app replaces the earlier one"
    assert second["send"] == f"/link {second['code']}" and second["ttl_s"] == 600
    slack = chat_links.issue_code(dana["id"], "slack")
    assert slack["send"] == slack["code"], "Slack takes / as its own commands: the code alone"


def test_codes_are_issued_only_where_spaces_is_enforced(clean_store) -> None:
    with pytest.raises(chat_links.LinkRefused) as refused:
        chat_links.issue_code(OWNER_ACCOUNT_ID, "telegram")
    assert refused.value.code == "spaces_off" and refused.value.status == 409


def test_an_unknown_chat_app_and_a_disabled_account_get_no_code(dana, enforced) -> None:
    with pytest.raises(chat_links.LinkRefused) as bad:
        chat_links.issue_code(dana["id"], "signal")
    assert bad.value.code == "bad_platform"
    people.set_enabled(dana["id"], False, by=OWNER_ACCOUNT_ID)
    with pytest.raises(chat_links.LinkRefused) as off:
        chat_links.issue_code(dana["id"], "telegram")
    assert off.value.code == "no_account"


# ── Telegram: the real update path ──────────────────────────────────────


def test_a_code_links_the_sender_and_never_reaches_the_agent(dana, enforced) -> None:
    code = chat_links.issue_code(dana["id"], "telegram", language="en")
    sent: list[Any] = []
    adapter = _telegram(sent)
    queue = _send_telegram(adapter, _tg_update(1, DANA_TG, code["send"]))

    assert queue.qsize() == 0, "a link code is never handed to the agent"
    assert chat_links._link_of("telegram", DANA_TG) == dana["id"]
    assert adapter.diagnostics()["dropped"] == {LINK_CODE: 1}
    assert [s.target_id for s in sent] == [f"telegram:{DANA_TG}"]
    assert "Linked" in sent[0].text and "dana-links" in sent[0].text
    event = identity.read_registry()["events"][-1]
    assert (event["kind"], event["account"], event.get("detail")) == ("chat_linked", dana["id"], "telegram")


def test_the_answer_is_in_the_language_the_code_was_asked_for_in(dana) -> None:
    code = chat_links.issue_code(dana["id"], "telegram", language="ar")
    sent: list[Any] = []
    _send_telegram(_telegram(sent), _tg_update(1, DANA_TG, code["send"]))
    assert "تمّ الربط" in sent[0].text and "Linked" not in sent[0].text


def test_a_used_code_links_nothing(dana) -> None:
    code = chat_links.issue_code(dana["id"], "telegram", language="en")
    sent: list[Any] = []
    adapter = _telegram(sent)
    _send_telegram(adapter, _tg_update(1, DANA_TG, code["send"]))
    _send_telegram(adapter, _tg_update(2, OMAR_TG, code["send"]))
    assert chat_links._link_of("telegram", OMAR_TG) is None, "a code links once"
    assert chat_links._link_of("telegram", DANA_TG) == dana["id"]
    assert "does not work" in sent[-1].text and sent[-1].target_id == f"telegram:{OMAR_TG}"
    assert chat_links.redeem("telegram", OMAR_TG, code["code"], direct=True).outcome == "used"

    # Two at once: one of them links, the other is told the code is used.
    both = chat_links.issue_code(dana["id"], "telegram", language="en")
    _send_telegram(adapter, _tg_update(3, 555003, both["send"]), _tg_update(4, 555004, both["send"]))
    linked = [uid for uid in (555003, 555004) if chat_links._link_of("telegram", uid)]
    assert len(linked) == 1, linked


def test_an_old_code_links_nothing(dana, monkeypatch) -> None:
    code = chat_links.issue_code(dana["id"], "telegram", language="en")
    real = chat_links._now
    monkeypatch.setattr(chat_links, "_now", lambda: real() + chat_links.CODE_TTL_S + 1)
    sent: list[Any] = []
    _send_telegram(_telegram(sent), _tg_update(1, DANA_TG, code["send"]))
    assert chat_links._link_of("telegram", DANA_TG) is None
    assert "does not work" in sent[0].text
    assert chat_links.redeem("telegram", OMAR_TG, code["code"], direct=True).outcome == "expired"


def test_a_wrong_code_links_nothing(dana) -> None:
    chat_links.issue_code(dana["id"], "telegram")
    sent: list[Any] = []
    _send_telegram(_telegram(sent), _tg_update(1, DANA_TG, "/link ZZZZ-9999"))
    assert chat_links._link_of("telegram", DANA_TG) is None
    assert len(sent) == 1 and sent[0].target_id == f"telegram:{DANA_TG}"


def test_a_code_typed_in_a_group_is_refused_and_burned(dana) -> None:
    code = chat_links.issue_code(dana["id"], "telegram", language="en")
    sent: list[Any] = []
    adapter = _telegram(sent)
    group = {"id": -100555, "type": "supergroup"}
    queue = _send_telegram(adapter, _tg_update(1, DANA_TG, code["send"], chat=group))
    assert queue.qsize() == 0 and chat_links._link_of("telegram", DANA_TG) is None
    assert [s.target_id for s in sent] == ["telegram:-100555"]
    assert "direct message" in sent[0].text
    # Seen in the group, it is spent: the same code in a direct chat links nothing.
    _send_telegram(adapter, _tg_update(2, DANA_TG, code["send"]))
    assert chat_links._link_of("telegram", DANA_TG) is None


def test_a_group_hears_nothing_of_a_code_that_was_never_live(dana) -> None:
    sent: list[Any] = []
    _send_telegram(_telegram(sent), _tg_update(1, DANA_TG, "/link ZZZZ-9999",
                                               chat={"id": -100555, "type": "supergroup"}))
    assert sent == []


def test_a_code_for_another_chat_app_is_burned(dana) -> None:
    code = chat_links.issue_code(dana["id"], "discord", language="en")
    sent: list[Any] = []
    _send_telegram(_telegram(sent), _tg_update(1, DANA_TG, f"/link {code['code']}"))
    assert chat_links._link_of("telegram", DANA_TG) is None
    assert "made for Discord" in sent[0].text
    assert chat_links.redeem("discord", "7001", code["code"], direct=True).outcome == "used"


def test_a_linked_chat_account_is_not_moved_by_another_s_code(dana, omar) -> None:
    sent: list[Any] = []
    adapter = _telegram(sent)
    _send_telegram(adapter, _tg_update(1, DANA_TG, chat_links.issue_code(dana["id"], "telegram")["send"]))
    _send_telegram(adapter, _tg_update(2, DANA_TG, chat_links.issue_code(omar["id"], "telegram")["send"]))
    assert chat_links._link_of("telegram", DANA_TG) == dana["id"]
    assert chat_links.links_of(omar["id"]) == []
    again = chat_links.issue_code(dana["id"], "telegram")
    assert chat_links.redeem("telegram", DANA_TG, again["code"], direct=True).outcome == "already_linked"


def test_a_sender_who_keeps_sending_codes_is_not_answered(dana) -> None:
    """A flood at once: counted as the codes arrive, not after they are read."""
    good = chat_links.issue_code(dana["id"], "telegram")
    sent: list[Any] = []
    adapter = _telegram(sent)
    wrong = [_tg_update(i, DANA_TG, f"/link ZZZZ-{i + 2:04d}".replace("0", "2").replace("1", "3"))
             for i in range(1, chat_links.MAX_ATTEMPTS + 1)]
    _send_telegram(adapter, *wrong, _tg_update(99, DANA_TG, good["send"]))
    assert len(sent) == chat_links.MAX_ATTEMPTS, "past its codes, a sender hears nothing"
    assert chat_links._link_of("telegram", DANA_TG) is None, "not even a right code is looked at"
    assert adapter.diagnostics()["dropped"] == {LINK_CODE: chat_links.MAX_ATTEMPTS + 1}
    assert chat_links.redeem("telegram", OMAR_TG, good["code"], direct=True).outcome == "linked"


def test_a_forwarded_code_is_someone_else_s_words(dana, enforced) -> None:
    code = chat_links.issue_code(dana["id"], "telegram")
    enforced.set("guard.telegram.chat_id", str(DANA_TG), "guard")
    try:
        from kazma_gateway.allowlists import apply_adapter_allowlists

        sent: list[Any] = []
        adapter = _telegram(sent)
        apply_adapter_allowlists(adapter, enforced)
        _send_telegram(adapter, _tg_update(1, DANA_TG, code["send"], forwarded=True))
    finally:
        enforced.delete("guard.telegram.chat_id")
    assert chat_links._link_of("telegram", DANA_TG) is None


def test_without_spaces_a_code_shaped_message_goes_where_it_always_went(clean_store) -> None:
    clean_store.set("guard.telegram.chat_id", str(DANA_TG), "guard")
    try:
        from kazma_gateway.allowlists import apply_adapter_allowlists

        sent: list[Any] = []
        adapter = _telegram(sent)
        apply_adapter_allowlists(adapter, clean_store)
        queue = _send_telegram(adapter, _tg_update(1, DANA_TG, "/link ABCD-2345"))
        stranger = _send_telegram(adapter, _tg_update(2, OMAR_TG, "/link ABCD-2345"))
    finally:
        clean_store.delete("guard.telegram.chat_id")
    assert queue.qsize() == 1, "the owner's message reaches the agent, as before"
    assert stranger.qsize() == 0 and adapter.diagnostics()["dropped"] == {"no_allowlist": 1}
    assert "private" in sent[0].text, "a stranger is told the bot is private, as before"


# ── Discord, Slack and WhatsApp: the real accept paths ──────────────────


def test_discord_links_in_a_dm_and_burns_a_code_in_a_server(dana, omar) -> None:
    from kazma_gateway.adapters.discord import DiscordAdapter

    sent: list[Any] = []
    adapter = _recording(DiscordAdapter(token="fake:token"), sent)
    good = chat_links.issue_code(dana["id"], "discord", language="en")
    seen = chat_links.issue_code(omar["id"], "discord", language="en")

    def msg(mid: str, author: str, text: str, guild: str | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {"id": mid, "channel_id": f"dm-{author}", "content": text,
                                "author": {"id": author, "username": f"n-{author}", "bot": False}}
        if guild:
            data["guild_id"] = guild
        return data

    async def run() -> list[Any]:
        got = [adapter._accept_message(msg("m1", "4242", good["code"])),
               adapter._accept_message(msg("m2", "5151", seen["code"], guild="g1"))]
        await _settle()
        return got

    assert asyncio.run(run()) == [None, None]
    assert chat_links._link_of("discord", "4242") == dana["id"]
    assert chat_links._link_of("discord", "5151") is None
    assert adapter.diagnostics()["dropped"] == {LINK_CODE: 2}
    assert chat_links.redeem("discord", "5151", seen["code"], direct=True).outcome == "used"


def test_slack_links_on_both_paths_and_burns_a_code_in_a_channel(dana, omar) -> None:
    from kazma_gateway.adapters.slack import SlackAdapter

    sent: list[Any] = []
    adapter = _recording(SlackAdapter(bot_token="xoxb-t", app_token="xapp-t"), sent)
    socket = chat_links.issue_code(dana["id"], "slack")
    polled = chat_links.issue_code(omar["id"], "slack")

    async def run() -> Any:
        got = adapter._accept_event(
            {"type": "message", "ts": "1.0", "user": "U5", "channel": "D5", "text": socket["send"]})
        await adapter._handle_message("C1", {"ts": "2.0", "user": "U6", "text": polled["send"]})
        await _settle()
        return got

    assert asyncio.run(run()) is None
    assert chat_links._link_of("slack", "U5") == dana["id"]
    assert chat_links._link_of("slack", "U6") is None, "a code in a channel is refused"
    assert sorted(s.target_id for s in sent) == ["slack:C1", "slack:D5"]


def test_whatsapp_links_a_number_in_any_spelling(dana) -> None:
    from kazma_gateway.adapters.whatsapp import WhatsAppAdapter
    from kazma_gateway.adapters.whatsapp_parse import WaInbound

    sent: list[Any] = []
    adapter = _recording(WhatsAppAdapter("EAAG-test-token", "PHONE1"), sent)
    code = chat_links.issue_code(dana["id"], "whatsapp")

    async def run() -> None:
        await adapter._process(WaInbound(message_id="w1", from_id="96599991234", name="D",
                                         phone_number_id="PHONE1", timestamp=1.0, kind="text",
                                         text=code["send"]))
        await _settle()

    asyncio.run(run())
    assert chat_links._link_of("whatsapp", "+965 9999-1234") == dana["id"]
    assert sent[0].target_id == "whatsapp:96599991234"


# ── a deleted person's links go with them ───────────────────────────────


def test_deleting_a_person_takes_their_links(dana) -> None:
    code = chat_links.issue_code(dana["id"], "telegram")
    assert chat_links.redeem("telegram", DANA_TG, code["code"], direct=True).outcome == "linked"
    people.delete_person(dana["id"], by=OWNER_ACCOUNT_ID)
    assert chat_links._link_of("telegram", DANA_TG) is None
    assert identity.read_registry()["links"] == []


def test_the_registry_refuses_a_chat_account_linked_twice(dana, omar) -> None:
    def twice(reg: dict[str, Any]) -> None:
        for who in (dana["id"], omar["id"]):
            reg["links"].append({"platform": "telegram", "user_id": "1", "account": who})

    with pytest.raises(identity.IdentityError) as refused:
        identity.update_registry(twice)
    assert refused.value.code == "linked_elsewhere"


# ── My account's routes ─────────────────────────────────────────────────


def _client(monkeypatch, account: str | None) -> TestClient:
    from kazma_ui import chat_links_api

    monkeypatch.setattr(chat_links_api, "_caller", lambda request: account)
    app = FastAPI()
    app.include_router(chat_links_api.create_chat_links_router())
    return TestClient(app)


def test_my_account_issues_a_code_and_lists_the_links(dana, monkeypatch, enforced) -> None:
    client = _client(monkeypatch, dana["id"])
    issued = client.post("/api/account/chat-links/code", json={"platform": "telegram"})
    assert issued.status_code == 200 and issued.headers["cache-control"] == "no-store"
    body = issued.json()
    assert body["send"] == f"/link {body['code']}"
    assert body["code"].replace("-", "") not in json.dumps(enforced.get(chat_links.CODES_KEY))
    assert chat_links.redeem("telegram", DANA_TG, body["code"], direct=True).outcome == "linked"
    listed = client.get("/api/account/chat-links").json()
    assert [(link["platform"], link["user_id"], link["label"]) for link in listed["links"]] == [
        ("telegram", str(DANA_TG), "Telegram")]
    assert client.post("/api/account/chat-links/code", json={"platform": "x"}).json()["error"] == "bad_platform"
    assert client.post("/api/account/chat-links/code", content=b"{").status_code == 400


def test_my_account_routes_without_spaces(clean_store, monkeypatch) -> None:
    client = _client(monkeypatch, OWNER_ACCOUNT_ID)
    assert client.get("/api/account/chat-links").json() == {
        "ok": True, "enforced": False, "links": [], "platforms": []}
    refused = client.post("/api/account/chat-links/code", json={"platform": "telegram"})
    assert refused.status_code == 409 and refused.json()["error"] == "spaces_off"
    assert _client(monkeypatch, None).get("/api/account/chat-links").status_code == 403


def test_the_running_platforms_come_from_the_gateway(monkeypatch) -> None:
    from kazma_core.service_container import get_container

    from kazma_gateway.adapters.telegram import TelegramAdapter
    from kazma_gateway.gateway import GatewayManager
    from kazma_ui.chat_links_api import _running_platforms

    gateway = GatewayManager()
    adapter = TelegramAdapter(token="123:abc")
    adapter._bot_username = "kazma_test_bot"
    gateway.add_adapter(adapter)
    container = get_container()
    token = container.register(GatewayManager, gateway)
    try:
        assert _running_platforms() == [
            {"id": "telegram", "label": "Telegram", "bot": "https://t.me/kazma_test_bot"}]
    finally:
        container.release(GatewayManager, token)


# ── negative control ────────────────────────────────────────────────────


def _two_senders_one_code(account: str) -> None:
    code = chat_links.issue_code(account, "telegram")
    sent: list[Any] = []
    adapter = _telegram(sent)
    _send_telegram(adapter, _tg_update(1, DANA_TG, code["send"]))
    _send_telegram(adapter, _tg_update(2, OMAR_TG, code["send"]))
    assert chat_links._link_of("telegram", OMAR_TG) is None, "a code links once"


def test_negative_control_a_code_that_can_be_used_twice(dana, monkeypatch) -> None:
    """Planted: the redemption reads the code and never marks it used. The
    check above fails on its own assertion."""
    _two_senders_one_code(dana["id"])  # today's code passes
    enforced = get_config_store()
    enforced.delete(identity.REGISTRY_KEY)
    person = people.create_person("dana-twice", PASSWORD, by=OWNER_ACCOUNT_ID)
    real_take = chat_links._take

    def take_twice(code: str, platform: str, user_id: str, *, direct: bool) -> Any:
        found = real_take(code, platform, user_id, direct=direct)

        def unmark(current: Any) -> Any:
            for entry in (current or {}).values():
                entry.pop("used_at", None)
            return current

        enforced.atomic_update(chat_links.CODES_KEY, unmark, category="auth")
        return found

    monkeypatch.setattr(chat_links, "_take", take_twice)
    with pytest.raises(AssertionError, match="a code links once"):
        _two_senders_one_code(person["id"])
