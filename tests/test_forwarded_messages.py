"""A forwarded message is someone else's words (2026-11, the review of the
memory framework audit's F3).

Telegram and WhatsApp tell a bot when a message was forwarded, and the
adapters ignored it: "My name is Dana and I live in Oslo", forwarded from
Dana, reached the agent as the person's own message, and the quick pass
filed Dana's name and home as the user's at the user's own trust. The text
now reaches the agent inside a data fence under "[Forwarded message from
Dana]" (``kazma_gateway/forwarded.py``); ``memory.own_words`` leaves both out
of the user's words, and the model reads the text as data. A pasted mail's
"---------- Forwarded message ---------" header ends the user's words too
(own_words).

Through the real Telegram adapter (its update path) and the real WhatsApp
parser; the adapters as they were are the negative controls.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from kazma_core.memory.belief_extractor import extract_beliefs_heuristic
from kazma_core.memory.own_words import own_words
from kazma_core.safety.prompt_fence import outside_fences

DANA_EN = "My name is Dana and I live in Oslo."
DANA_AR = "اسمي دانة وساكنة في جدة."


def _facts(text: str) -> set[tuple[str, str]]:
    return {(f["predicate"], f["object"]) for f in extract_beliefs_heuristic(text)}


# ── Telegram ────────────────────────────────────────────────────────────


def _tg():
    from kazma_gateway.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter(token="123:abc", allow_all=True)

    async def no_announce(update: dict) -> None:
        return None

    adapter._announce_if_added_to_group = no_announce  # type: ignore[method-assign]
    adapter._http = httpx.AsyncClient(base_url=adapter._api_base,
                                      transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    return adapter


def _update(**fields: Any) -> dict:
    message: dict[str, Any] = {
        "message_id": 10, "date": 1,
        "chat": {"id": 7, "type": "private"},
        "from": {"id": 7, "is_bot": False, "first_name": "B"},
    }
    message.update(fields)
    return {"update_id": 1, "message": message}


def _run(adapter, update: dict) -> list:
    queue: asyncio.Queue = asyncio.Queue()

    async def go() -> None:
        await adapter._process_update(update, queue)
        await adapter._http.aclose()

    asyncio.run(go())
    return [queue.get_nowait() for _ in range(queue.qsize())]


ORIGINS = {
    "a user": {"forward_origin": {"type": "user", "date": 1, "sender_user": {"id": 9, "first_name": "Dana"}}},
    "a hidden user": {"forward_origin": {"type": "hidden_user", "date": 1, "sender_user_name": "Dana"}},
    "a channel": {"forward_origin": {"type": "channel", "date": 1, "chat": {"id": -100, "title": "News"}}},
    "the older fields": {"forward_from": {"id": 9, "first_name": "Dana"}, "forward_date": 1},
}


@pytest.mark.parametrize("origin", sorted(ORIGINS))
@pytest.mark.parametrize("said", [DANA_EN, DANA_AR])
def test_a_forwarded_telegram_message_is_not_the_users(origin, said):
    [msg] = _run(_tg(), _update(text=said, **ORIGINS[origin]))
    assert msg.text.startswith("[Forwarded message")
    assert said in msg.text and said not in outside_fences(msg.text)
    assert 'source="forwarded_message"' in msg.text
    assert own_words(msg.text) == ""
    assert _facts(msg.text) == set()


def test_the_origin_is_named_when_telegram_names_it():
    [msg] = _run(_tg(), _update(text=DANA_EN, **ORIGINS["a user"]))
    assert msg.text.startswith("[Forwarded message from Dana]\n")
    [msg] = _run(_tg(), _update(text=DANA_EN, forward_date=1))
    assert msg.text.startswith("[Forwarded message]\n")


def test_a_message_the_person_wrote_is_unchanged():
    [msg] = _run(_tg(), _update(text=DANA_EN))
    assert msg.text == DANA_EN
    assert ("name_is", "Dana") in _facts(msg.text)


def test_control_the_adapter_that_ignored_forwards_gave_dana_the_users_name(monkeypatch):
    import kazma_gateway.forwarded as forwarded

    monkeypatch.setattr(forwarded, "telegram_forward_origin", lambda message: None)
    [msg] = _run(_tg(), _update(text=DANA_EN, **ORIGINS["a user"]))
    assert msg.text == DANA_EN
    assert {("name_is", "Dana"), ("lives_in", "Oslo")} <= _facts(msg.text)


# ── WhatsApp ────────────────────────────────────────────────────────────


def _wa_payload(text: str, *, context: dict | None = None) -> dict:
    message: dict[str, Any] = {"from": "96599991234", "id": "wamid.1", "timestamp": "1", "type": "text",
                               "text": {"body": text}}
    if context is not None:
        message["context"] = context
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{
        "field": "messages",
        "value": {"metadata": {"phone_number_id": "1"}, "contacts": [], "messages": [message]},
    }]}]}


@pytest.mark.parametrize("context", [{"forwarded": True}, {"frequently_forwarded": True}])
@pytest.mark.parametrize("said", [DANA_EN, DANA_AR])
def test_a_forwarded_whatsapp_message_is_not_the_users(context, said):
    from kazma_gateway.adapters.whatsapp_parse import inbound_messages

    [m] = inbound_messages(_wa_payload(said, context=context))
    assert m.text.startswith("[Forwarded message]") and said not in outside_fences(m.text)
    assert own_words(m.text) == "" and _facts(m.text) == set()


def test_control_whatsapp_without_the_mark_gave_dana_the_users_name(monkeypatch):
    import kazma_gateway.forwarded as forwarded
    from kazma_gateway.adapters.whatsapp_parse import inbound_messages

    [m] = inbound_messages(_wa_payload(DANA_EN))  # not forwarded: the person's own
    assert m.text == DANA_EN
    monkeypatch.setattr(forwarded, "whatsapp_forwarded", lambda raw: False)
    [m] = inbound_messages(_wa_payload(DANA_EN, context={"forwarded": True}))
    assert ("name_is", "Dana") in _facts(m.text)


# ── a pasted forward ────────────────────────────────────────────────────


@pytest.mark.parametrize("message", [
    f"---------- Forwarded message ---------\nFrom: Dana\n{DANA_EN}",
    f"Forwarded from Dana\n{DANA_EN}",
    f"-----Original Message-----\n{DANA_EN}",
    f"رسالة محولة\n{DANA_AR}",
])
def test_a_pasted_forward_ends_the_users_words(message):
    assert _facts(message) == set()
    assert _facts(f"I live in Lisbon.\n{message}") == {("lives_in", "Lisbon")}
