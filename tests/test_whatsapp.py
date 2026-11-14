"""WhatsApp (Meta's WhatsApp Cloud API) as a Kazma chat app (2026-11-14).

Everything runs against a stand-in for Meta (``FakeMeta``: an
``httpx.MockTransport`` answering the Graph API the way Meta does) and the
real webhook route, adapter, builder and Test. No request leaves the machine.
Each rule has its negative control: an unsigned call, a number spelled with
"+" and spaces against the old exact-text admin rule, a stranger's message, a
resend.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
from typing import Any

import httpx
import pytest
from kazma_gateway.adapters.whatsapp import WEBHOOK_PATH, WhatsAppAdapter, whatsapp_webhook_router
from kazma_gateway.adapters.whatsapp_api import WhatsAppClient, WhatsAppError, explain
from kazma_gateway.adapters.whatsapp_format import MAX_TEXT, md_to_whatsapp, split_for_whatsapp
from kazma_gateway.adapters.whatsapp_parse import count_statuses, inbound_messages, signature_ok, wa_digits
from kazma_gateway.gateway import Attachment, OutboundMessage

PHONE_ID = "109876543210"
SECRET = "app-secret-for-tests"
VERIFY = "verify-token-for-tests"
OWNER = "96599991234"
STRANGER = "96588887777"


class FakeMeta:
    """Meta's Graph API as Kazma calls it, recording each request."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.read: list[str] = []
        self.requests: list[httpx.Request] = []
        self.fail_send: dict[str, Any] | None = None
        self.token_ok = True
        self.uploads = 0
        self.media = {"m-img": (b"\x89PNG fake image", "image/png"), "m-voice": (b"OggS fake", "audio/ogg; codecs=opus")}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.url.host == "lookaside.fbsbx.com":
            mid = request.url.params.get("mid", "")
            data, mime = self.media[mid]
            return httpx.Response(200, content=data, headers={"content-type": mime})
        if not self.token_ok:
            return httpx.Response(401, json={"error": {"code": 190, "message": "Error validating access token"}})
        if request.method == "GET" and path == f"/v23.0/{PHONE_ID}":
            return httpx.Response(200, json={"display_phone_number": "+965 2222 3333", "verified_name": "Kazma Test",
                                             "quality_rating": "GREEN", "id": PHONE_ID})
        if request.method == "GET" and path.startswith("/v23.0/m-"):
            mid = path.rsplit("/", 1)[1]
            data, mime = self.media[mid]
            return httpx.Response(200, json={"url": f"https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid={mid}",
                                             "mime_type": mime, "file_size": len(data), "id": mid})
        if request.method == "POST" and path == f"/v23.0/{PHONE_ID}/media":
            self.uploads += 1
            return httpx.Response(200, json={"id": f"up-{self.uploads}"})
        if request.method == "POST" and path == f"/v23.0/{PHONE_ID}/messages":
            body = json.loads(request.content)
            if body.get("status") == "read":
                self.read.append(body["message_id"])
                return httpx.Response(200, json={"success": True})
            if self.fail_send is not None:
                return httpx.Response(self.fail_send["status"], json={"error": self.fail_send["error"]})
            self.sent.append(body)
            return httpx.Response(200, json={"messages": [{"id": f"wamid.out.{len(self.sent)}"}]})
        return httpx.Response(404, json={"error": {"code": 100, "message": f"no route {path}"}})

    def client(self) -> WhatsAppClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handler), follow_redirects=False)
        return WhatsAppClient("EAAG-test-token", PHONE_ID, http=http)

    def texts(self) -> list[str]:
        return [b["text"]["body"] for b in self.sent if b.get("type") == "text"]


def _payload(*messages: dict[str, Any], statuses: int = 0, phone_id: str = PHONE_ID) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "96522223333", "phone_number_id": phone_id},
            "contacts": [{"profile": {"name": "Owner"}, "wa_id": OWNER}],
            "messages": list(messages),
            "statuses": [{"id": f"s{i}", "status": "delivered"} for i in range(statuses)],
        }}]}],
    }


def _text(mid: str, body: str, sender: str = OWNER) -> dict[str, Any]:
    return {"from": sender, "id": mid, "timestamp": "1763100000", "type": "text", "text": {"body": body}}


def _signed(body: bytes, secret: str = SECRET) -> dict[str, str]:
    return {"X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
            "Content-Type": "application/json"}


# ── Pure pieces ─────────────────────────────────────────────────────────


def test_a_signature_matches_only_the_body_under_the_app_secret() -> None:
    body = b'{"object":"whatsapp_business_account"}'
    good = _signed(body)["X-Hub-Signature-256"]
    assert signature_ok(SECRET, body, good)
    assert signature_ok(SECRET, body, good.upper().replace("SHA256=", "sha256="))
    # Negative controls: another secret, another body, no header, no secret, another scheme.
    assert not signature_ok("other-secret", body, good)
    assert not signature_ok(SECRET, body + b" ", good)
    assert not signature_ok(SECRET, body, None)
    assert not signature_ok("", body, good)
    assert not signature_ok(SECRET, body, good.replace("sha256=", "sha1="))


def test_every_kind_of_message_is_read_and_statuses_are_counted() -> None:
    payload = _payload(
        _text("w1", "hello"),
        {"from": OWNER, "id": "w2", "timestamp": "1", "type": "image",
         "image": {"id": "m-img", "mime_type": "image/png", "caption": "look"}},
        {"from": OWNER, "id": "w3", "timestamp": "1", "type": "audio",
         "audio": {"id": "m-voice", "mime_type": "audio/ogg; codecs=opus", "voice": True}},
        {"from": OWNER, "id": "w4", "timestamp": "1", "type": "interactive",
         "interactive": {"type": "button_reply", "button_reply": {"id": "hitl:approve:r1", "title": "Approve"}}},
        {"from": OWNER, "id": "w5", "timestamp": "1", "type": "interactive",
         "interactive": {"type": "list_reply", "list_reply": {"id": "hitl:opt:a:r2", "title": "A"}}},
        {"from": OWNER, "id": "w6", "timestamp": "1", "type": "sticker", "sticker": {"id": "s"}},
        {"from": "+965 9999 1234", "id": "w7", "timestamp": "1", "type": "document",
         "document": {"id": "m-img", "filename": "a.pdf", "mime_type": "application/pdf"}},
        statuses=2,
    )
    msgs = inbound_messages(payload)
    assert [m.kind for m in msgs] == ["text", "image", "audio", "interactive", "interactive", "sticker", "document"]
    assert msgs[0].text == "hello" and msgs[0].name == "Owner"
    assert msgs[1].media_id == "m-img" and msgs[1].text == "look"
    assert msgs[2].voice is True
    assert msgs[3].button_id == "hitl:approve:r1" and msgs[4].button_id == "hitl:opt:a:r2"
    assert not msgs[5].readable
    assert msgs[6].from_id == OWNER and msgs[6].filename == "a.pdf"
    assert count_statuses(payload) == 2
    assert inbound_messages({"object": "page", "entry": []}) == []
    assert wa_digits("+965 9999-1234") == OWNER


def test_markdown_becomes_whatsapp_marks_and_code_is_kept() -> None:
    md = "# Title\n\n**bold** and *it* and ~~gone~~ [site](https://k.ai)\n\n* item\n\n`a**b`\n\n```\nx = **y**\n```"
    out = md_to_whatsapp(md)
    assert out.startswith("*Title*")
    assert "*bold* and _it_ and ~gone~ site (https://k.ai)" in out
    assert "- item" in out
    assert "`a**b`" in out and "x = **y**" in out


def test_a_long_answer_is_split_under_the_limit_and_a_code_block_kept_whole() -> None:
    para = ("word " * 300).strip()
    fence = "```\n" + ("line\n" * 50) + "```"
    text = "\n\n".join([para] * 6 + [fence] + [para] * 3)
    parts = split_for_whatsapp(text)
    assert len(parts) > 1 and all(len(p) <= MAX_TEXT for p in parts)
    assert any(fence in p for p in parts)
    assert "".join(parts).replace("\n", "").replace(" ", "") == text.replace("\n", "").replace(" ", "")
    huge = "x" * (MAX_TEXT * 2 + 5)
    assert [len(p) for p in split_for_whatsapp(huge)] == [MAX_TEXT, MAX_TEXT, 5]


def test_meta_errors_say_what_to_do() -> None:
    assert "24 hours" in explain(131047)
    assert "test number" in explain(131030)
    assert "access token" in explain(190)
    assert explain(999, "odd", 400) == "Meta answered HTTP 400: odd (error 999)"


# ── The client against the stand-in Meta ────────────────────────────────


def test_the_client_sends_with_the_token_in_a_header_never_the_url() -> None:
    meta = FakeMeta()

    async def run() -> None:
        api = meta.client()
        await api.send_text(OWNER, "hi")
        await api.send_buttons(OWNER, "Approve?", [("hitl:approve:r", "Approve"), ("hitl:deny:r", "Deny")])
        await api.mark_read("wamid.1")
        data, mime = await api.download_media("m-img")
        assert data.startswith(b"\x89PNG") and mime == "image/png"
        await api.aclose()

    asyncio.run(run())
    assert meta.sent[0] == {"messaging_product": "whatsapp", "recipient_type": "individual", "to": OWNER,
                            "type": "text", "text": {"body": "hi", "preview_url": False}}
    buttons = meta.sent[1]["interactive"]["action"]["buttons"]
    assert [b["reply"]["id"] for b in buttons] == ["hitl:approve:r", "hitl:deny:r"]
    assert meta.read == ["wamid.1"]
    for req in meta.requests:
        assert "EAAG-test-token" not in str(req.url)
        assert req.headers["authorization"] == "Bearer EAAG-test-token"


def test_a_send_is_retried_after_slow_down_but_never_after_a_server_error(monkeypatch) -> None:
    import kazma_gateway.adapters.whatsapp_api as api_mod

    async def no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(api_mod.asyncio, "sleep", no_sleep)
    calls = {"n": 0}

    def busy(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": {"code": 130429, "message": "slow"}}, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"messages": [{"id": "ok"}]})

    def broken(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"error": {"code": 2, "message": "boom"}})

    async def run(handler: Any) -> Any:
        api = WhatsAppClient("t", PHONE_ID, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            return await api.send_text(OWNER, "x")
        finally:
            await api.aclose()

    assert asyncio.run(run(busy)) == "ok" and calls["n"] == 2
    calls["n"] = 0
    with pytest.raises(WhatsAppError):
        asyncio.run(run(broken))
    assert calls["n"] == 1  # a 500 on a send may have delivered it: not sent twice


def test_a_download_past_the_limit_is_refused() -> None:
    meta = FakeMeta()
    meta.media["m-big"] = (b"x" * 2048, "image/png")

    async def run() -> None:
        api = meta.client()
        with pytest.raises(WhatsAppError):
            await api.download_media("m-big", max_bytes=1024)
        await api.aclose()

    asyncio.run(run())


# ── The adapter behind the real webhook route ───────────────────────────


class _Rig:
    """The real router and adapter, a stand-in Meta, one event loop."""

    def __init__(self, meta: FakeMeta, *, allowed: list[str] | None = None, app_secret: str = SECRET,
                 allow_all: bool = False) -> None:
        from fastapi import FastAPI

        self.meta = meta
        self.adapter = WhatsAppAdapter("EAAG-test-token", PHONE_ID, app_secret=app_secret, verify_token=VERIFY,
                                       allowed_users=allowed if allowed is not None else ["+965 9999 1234"],
                                       allow_all=allow_all, client=meta.client())
        app = FastAPI()
        app.include_router(whatsapp_webhook_router(lambda: self.adapter), prefix=WEBHOOK_PATH)
        self.app = app
        self.queue: asyncio.Queue = asyncio.Queue()

    async def __aenter__(self) -> _Rig:
        self.shutdown = asyncio.Event()
        await self.adapter.start(self.queue, self.shutdown)
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://kazma")
        for _ in range(100):
            if self.adapter._receive.connected:
                break
            await asyncio.sleep(0.01)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.adapter.settled()
        await self.http.aclose()
        self.shutdown.set()
        await self.adapter.stop()

    async def post(self, payload: dict[str, Any], *, sign: bool = True, secret: str = SECRET) -> httpx.Response:
        body = json.dumps(payload).encode()
        headers = _signed(body, secret) if sign else {"Content-Type": "application/json"}
        resp = await self.http.post(WEBHOOK_PATH, content=body, headers=headers)
        await self.adapter.settled()
        return resp

    def taken(self) -> list[Any]:
        out = []
        while not self.queue.empty():
            out.append(self.queue.get_nowait())
        return out


@pytest.fixture(autouse=True)
def _no_open_switch(monkeypatch) -> None:
    for name in ("KAZMA_GATEWAY_ADMINS", "KAZMA_GATEWAY_OPEN_TO_EVERYONE", "KAZMA_GATEWAY_STRICT_ALLOWLIST"):
        monkeypatch.delenv(name, raising=False)
    from kazma_core.config_store import get_config_store
    from kazma_gateway.sender_rule import NOTICE_KEY, _forget_told_for_tests

    _forget_told_for_tests()
    get_config_store().delete(NOTICE_KEY)


async def _until(check: Any, seconds: float = 5.0) -> None:
    """Wait for something done in the background (a notice is sent that way)."""
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    while not check() and loop.time() < end:
        await asyncio.sleep(0.02)


def test_meta_verifies_the_webhook_with_the_verify_token_only() -> None:
    async def run() -> None:
        async with _Rig(FakeMeta()) as rig:
            ok = await rig.http.get(WEBHOOK_PATH, params={"hub.mode": "subscribe", "hub.verify_token": VERIFY,
                                                          "hub.challenge": "1158201444"})
            assert ok.status_code == 200 and ok.text == "1158201444"
            bad = await rig.http.get(WEBHOOK_PATH, params={"hub.mode": "subscribe", "hub.verify_token": "guess",
                                                           "hub.challenge": "x"})
            assert bad.status_code == 403
            assert rig.adapter.diagnostics()["events"]["webhook_verified"] == 1

    asyncio.run(run())


def test_a_signed_message_reaches_kazma_and_an_unsigned_one_does_not() -> None:
    meta = FakeMeta()

    async def run() -> None:
        async with _Rig(meta) as rig:
            assert rig.adapter._receive.connected
            # Negative controls first: no signature, another secret's signature.
            assert (await rig.post(_payload(_text("w0", "forged")), sign=False)).status_code == 401
            assert (await rig.post(_payload(_text("w0", "forged")), secret="guess")).status_code == 401
            assert rig.taken() == []
            resp = await rig.post(_payload(_text("w1", "hello Kazma")))
            assert resp.status_code == 200
            (msg,) = rig.taken()
            assert msg.platform == "whatsapp" and msg.sender_id == f"whatsapp:{OWNER}" and msg.text == "hello Kazma"
            assert msg.context_metadata["chat_id"] == OWNER and msg.context_metadata["receive_key"] == "w1"
            assert meta.read == ["w1"]

    asyncio.run(run())


def test_without_an_app_secret_no_call_is_answered() -> None:
    async def run() -> None:
        async with _Rig(FakeMeta(), app_secret="") as rig:
            assert (await rig.post(_payload(_text("w1", "hi")), secret="")).status_code == 503
            assert rig.taken() == []

    asyncio.run(run())


def test_a_resend_is_answered_once_and_one_senders_messages_keep_their_order() -> None:
    async def run() -> None:
        async with _Rig(FakeMeta()) as rig:
            await rig.post(_payload(_text("w1", "one"), _text("w2", "two"), _text("w3", "three")))
            await rig.post(_payload(_text("w2", "two")))  # Meta resending
            assert [m.text for m in rig.taken()] == ["one", "two", "three"]
            snap = rig.adapter.diagnostics()
            assert snap["dropped"] == {"duplicate": 1}
            # The first copy's fate stands under its id.
            assert snap["recent"]["w2"] == "passed_on"

    asyncio.run(run())


def test_a_stranger_is_told_once_that_the_number_is_private() -> None:
    meta = FakeMeta()

    async def run() -> None:
        async with _Rig(meta) as rig:
            await rig.post(_payload(_text("w1", "hello?", sender=STRANGER)))
            await _until(lambda: any(b["to"] == STRANGER for b in meta.sent))
            await rig.post(_payload(_text("w2", "hello again", sender=STRANGER)))
            # A second notice would be sent in the background: give it the chance to show.
            await _until(lambda: sum(b["to"] == STRANGER for b in meta.sent) > 1, 0.5)
            assert rig.taken() == []
            snap = rig.adapter.diagnostics()
            assert snap["dropped"]["user_not_allowed"] == 2
            assert "Allowed numbers" in snap["reasons"]["user_not_allowed"]

    asyncio.run(run())
    # One notice in a day, to the stranger, naming their number and the card's field.
    notices = [b for b in meta.sent if b["to"] == STRANGER]
    assert len(notices) == 1
    assert f"+{STRANGER}" in notices[0]["text"]["body"] and "Allowed numbers" in notices[0]["text"]["body"]


def test_no_message_is_unaccounted_for() -> None:
    async def run() -> None:
        async with _Rig(FakeMeta()) as rig:
            await rig.post(_payload(
                _text("w1", "hi"),
                _text("w2", "x", sender=STRANGER),
                {"from": OWNER, "id": "w3", "timestamp": "1", "type": "sticker", "sticker": {"id": "s"}},
                statuses=3,
            ))
            await rig.post(_payload(_text("w4", "other number"), phone_id="555"))
            snap = rig.adapter.diagnostics()
            assert snap["messages"] == 4
            assert snap["passed_on"] + sum(snap["dropped"].values()) == snap["messages"]
            assert snap["passed_on"] == 1
            assert snap["dropped"] == {"user_not_allowed": 1, "unsupported_type": 1, "not_this_number": 1}
            assert snap["events"]["status"] == 3

    asyncio.run(run())


def test_a_photo_is_downloaded_and_handed_on_with_its_caption() -> None:
    async def run() -> None:
        async with _Rig(FakeMeta()) as rig:
            await rig.post(_payload({"from": OWNER, "id": "w1", "timestamp": "1", "type": "image",
                                     "image": {"id": "m-img", "mime_type": "image/png", "caption": "what is this?"}}))
            (msg,) = rig.taken()
            assert msg.text == "what is this?"
            (att,) = msg.attachments
            assert att.kind == "image" and att.data.startswith(b"\x89PNG") and att.mime == "image/png"

    asyncio.run(run())


def test_a_voice_note_goes_through_the_shared_speech_to_text(monkeypatch) -> None:
    from kazma_gateway.adapters import voice_helpers

    seen: list[Any] = []

    async def fake_transcribe(msg: Any, **_kw: Any) -> Any:
        seen.append(msg.attachments[0])
        msg.text = "transcribed words"
        msg.context_metadata = {**msg.context_metadata, "voice_transcribed": True}
        return msg

    monkeypatch.setattr(voice_helpers, "transcribe_inbound_message", fake_transcribe)

    async def run() -> None:
        async with _Rig(FakeMeta()) as rig:
            await rig.post(_payload({"from": OWNER, "id": "w1", "timestamp": "1", "type": "audio",
                                     "audio": {"id": "m-voice", "mime_type": "audio/ogg; codecs=opus", "voice": True}}))
            (msg,) = rig.taken()
            assert msg.text == "transcribed words"

    asyncio.run(run())
    assert seen[0].kind == "audio" and seen[0].data.startswith(b"OggS")


def test_an_approval_button_from_the_owner_becomes_the_hitl_command() -> None:
    from kazma_core.config_store import get_config_store

    get_config_store().set("connectors.whatsapp.allowed_users", "+965 9999 1234", category="connectors")
    markup = WhatsAppAdapter.build_approval_keyboard("req-1")
    ids = [b[0] for b in markup["buttons"]]
    assert ids == ["hitl:approve:req-1", "hitl:deny:req-1", "hitl:approve_task:req-1"]

    async def run() -> None:
        async with _Rig(FakeMeta()) as rig:
            await rig.post(_payload({"from": OWNER, "id": "w1", "timestamp": "1", "type": "interactive",
                                     "interactive": {"type": "button_reply",
                                                     "button_reply": {"id": ids[0], "title": "Approve"}}}))
            (msg,) = rig.taken()
            assert msg.text == "/hitl approve req-1" and msg.sender_id == f"whatsapp:{OWNER}"

    asyncio.run(run())


def test_an_approval_button_from_someone_not_the_owner_decides_nothing() -> None:
    from kazma_core.config_store import get_config_store

    get_config_store().set("connectors.whatsapp.allowed_users", "", category="connectors")
    meta = FakeMeta()

    async def run() -> None:
        # The open switch lets the stranger chat; it never makes them the owner.
        async with _Rig(meta, allowed=[], allow_all=True) as rig:
            await rig.post(_payload({"from": STRANGER, "id": "w1", "timestamp": "1", "type": "interactive",
                                     "interactive": {"type": "button_reply",
                                                     "button_reply": {"id": "hitl:approve:req-1", "title": "Approve"}}}))
            await _until(lambda: any("Not authorized" in t for t in meta.texts()))
            assert rig.taken() == []

    asyncio.run(run())
    assert any("Not authorized" in t for t in meta.texts())


def test_a_public_action_names_itself_and_has_no_task_approval() -> None:
    from kazma_gateway.adapters.markup import approval_markup, attach_markup

    markup = approval_markup("whatsapp", "req-2", approve_label="Post now", task=False)
    assert [b[1] for b in markup["buttons"]] == ["Post now", "Deny"]
    ctx = attach_markup({}, "whatsapp", markup)
    assert ctx == {"whatsapp_buttons": markup}
    # Every chat app's buttons go in that app's own field, and each parses back.
    from kazma_gateway.adapters.platform_callbacks import parse_callback_data

    assert parse_callback_data(markup["buttons"][0][0]).text == "/hitl approve req-2"
    for platform, key in (("telegram", "reply_markup"), ("discord", "components"), ("slack", "blocks")):
        assert key in attach_markup({}, platform, approval_markup(platform, "r"))


# ── Sending ─────────────────────────────────────────────────────────────


def test_a_reply_is_sent_in_whatsapp_marks_split_with_files_and_buttons() -> None:
    meta = FakeMeta()
    adapter = WhatsAppAdapter("EAAG-test-token", PHONE_ID, app_secret=SECRET, client=meta.client())
    long = "**Answer**\n\n" + "\n\n".join(("word " * 200).strip() for _ in range(6))
    out = OutboundMessage(
        target_id=f"whatsapp:{OWNER}", text=long,
        attachments=[Attachment(kind="file", mime="application/pdf", filename="r.pdf", data=b"%PDF-1.7")],
        context_metadata={"chat_id": OWNER, "whatsapp_buttons": {
            "prompt": "Choose", "buttons": [(f"id{i}", f"Option {i}") for i in range(5)]}},
    )
    assert asyncio.run(adapter.send(out)) is True
    texts = meta.texts()
    assert len(texts) >= 2 and texts[0].startswith("*Answer*") and all(len(t) <= MAX_TEXT for t in texts)
    doc = next(b for b in meta.sent if b["type"] == "document")
    assert doc["document"] == {"id": "up-1", "filename": "r.pdf"}
    interactive = meta.sent[-1]["interactive"]
    assert interactive["type"] == "list" and len(interactive["action"]["sections"][0]["rows"]) == 5


def test_a_refused_send_reports_failure_with_metas_reason(caplog) -> None:
    meta = FakeMeta()
    meta.fail_send = {"status": 400, "error": {"code": 131047, "message": "Re-engagement message"}}
    adapter = WhatsAppAdapter("EAAG-test-token", PHONE_ID, client=meta.client())
    with caplog.at_level("WARNING", logger="kazma_gateway.adapters.whatsapp"):
        ok = asyncio.run(adapter.send(OutboundMessage(target_id=f"whatsapp:{OWNER}", text="late")))
    assert ok is False
    assert "24 hours" in caplog.text


# ── Settings, builder, allowlists, the Test ─────────────────────────────


def test_the_builder_builds_whatsapp_from_saved_settings_and_watches_its_keys() -> None:
    from kazma_gateway.chat_adapters import CHAT_PLATFORMS, _build_chat_adapter, _read_platform_settings, _watched_key

    class Store:
        def __init__(self, values: dict[str, Any]) -> None:
            self.values = values

        def get(self, key: str, default: Any = None) -> Any:
            return self.values.get(key, default)

    assert "whatsapp" in CHAT_PLATFORMS
    for field in ("token", "phone_number_id", "app_secret", "verify_token", "allowed_users", "enabled"):
        assert _watched_key(f"connectors.whatsapp.{field}")
    store = Store({"connectors.whatsapp.token": "EAAG", "connectors.whatsapp.phone_number_id": PHONE_ID,
                   "connectors.whatsapp.app_secret": SECRET, "connectors.whatsapp.verify_token": VERIFY,
                   "connectors.whatsapp.allowed_users": "+965 9999 1234"})
    settings = _read_platform_settings(store, "whatsapp")
    assert SECRET not in repr(settings) and VERIFY not in repr(settings)
    adapter = _build_chat_adapter(settings, store)
    assert isinstance(adapter, WhatsAppAdapter)
    assert adapter.sender_refusal(OWNER) == "" and adapter.sender_refusal(STRANGER)
    # No phone number id: not started.
    store.values.pop("connectors.whatsapp.phone_number_id")
    assert _build_chat_adapter(_read_platform_settings(store, "whatsapp"), store) is None


def test_a_number_in_any_spelling_makes_its_owner_admin() -> None:
    from kazma_core.config_store import get_config_store
    from kazma_gateway.allowlists import is_gateway_admin

    get_config_store().set("connectors.whatsapp.allowed_users", "+965 9999 1234", category="connectors")
    assert is_gateway_admin(f"whatsapp:{OWNER}", "whatsapp")
    assert not is_gateway_admin(f"whatsapp:{STRANGER}", "whatsapp")
    # Negative control: the exact-text rule other platforms keep would not match.
    from kazma_gateway.allowlists import _candidate_tokens

    assert not (_candidate_tokens(f"whatsapp:{OWNER}") & _candidate_tokens("+965 9999 1234"))


def test_the_webhook_is_open_to_meta_and_its_secrets_are_install_scoped() -> None:
    from kazma_core.config_store import is_sensitive_config_key
    from kazma_core.security import vault as vault_mod
    from kazma_ui.auth import is_always_open

    assert is_always_open(WEBHOOK_PATH)
    for field in ("token", "app_secret", "verify_token"):
        key = f"connectors.whatsapp.{field}"
        assert is_sensitive_config_key(key), key
        assert vault_mod.is_install_scoped_secret("cfg:" + key), key
    assert not is_sensitive_config_key("connectors.whatsapp.phone_number_id")


def test_the_test_names_each_problem_with_its_fix() -> None:
    from kazma_gateway.adapters.whatsapp_diagnose import diagnose

    meta = FakeMeta()
    live = {"connected": True, "connected_since": "2026-11-14T10:00:00+00:00", "messages": 1, "passed_on": 1,
            "events": {"text": 1}, "dropped": {}, "recent": {"w1": "passed_on"},
            "last_person": {"id": "w1", "author_id": OWNER, "at": "2026-11-14T10:05:00+00:00"},
            "reasons": {}, "allow_all": False}

    def run(**kw: Any) -> dict[str, Any]:
        base = {"phone_number_id": PHONE_ID, "app_secret": SECRET, "verify_token": VERIFY,
                "public_url": "https://my.kazma.example", "allowed_users": ["+965 9999 1234"], "live": live,
                "client": meta.client()}
        return asyncio.run(diagnose("EAAG", **{**base, **kw}))

    good = run()
    by = {c["key"]: c for c in good["checks"]}
    assert good["success"] and good["bot_name"] == "Kazma Test"
    assert by["webhook"]["ok"] and "https://my.kazma.example/api/webhooks/whatsapp" in by["webhook"]["detail"]
    assert by["latest"]["ok"] and by["window"]["ok"] is None and by["allowed"]["ok"]
    assert not run(app_secret="")["success"]
    assert "KAZMA_PUBLIC_URL" in {c["key"]: c for c in run(public_url="")["checks"]}["webhook"]["detail"]
    meta.token_ok = False
    bad = run()
    assert not bad["success"] and "access token" in bad["error"]
    meta.token_ok = True
    empty = {c["key"]: c for c in run(allowed_users=[])["checks"]}["allowed"]
    assert empty["ok"] is False and "Allowed numbers" in empty["detail"]


def test_the_connector_route_runs_the_whatsapp_test_with_the_saved_settings(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.config_store import get_config_store
    from kazma_gateway.adapters import whatsapp_diagnose
    from kazma_ui import providers

    store = get_config_store()
    for key, value in {"connectors.whatsapp.token": "EAAG", "connectors.whatsapp.phone_number_id": PHONE_ID,
                       "connectors.whatsapp.app_secret": SECRET, "connectors.whatsapp.verify_token": VERIFY,
                       "connectors.whatsapp.allowed_users": "+965 9999 1234"}.items():
        store.set(key, value, category="connectors")
    monkeypatch.setenv("KAZMA_PUBLIC_URL", "https://my.kazma.example")
    got: dict[str, Any] = {}

    async def fake(token: str, **kw: Any) -> dict[str, Any]:
        got.update(token=token, **kw)
        return {"success": True, "bot_name": "b", "error": None, "checks": []}

    monkeypatch.setattr(whatsapp_diagnose, "diagnose", fake)
    monkeypatch.setattr(providers, "_live_adapter_diagnostics", lambda platform: {"live": platform})

    async def no_rebuild(_platform: str) -> None:
        return None

    monkeypatch.setattr(providers, "_adapter_from_saved_settings", no_rebuild)
    app = FastAPI()
    app.include_router(providers.create_providers_router(store))
    client = TestClient(app)
    assert client.post("/api/connectors/whatsapp/test").json()["success"]
    assert got["token"] == "EAAG" and got["phone_number_id"] == PHONE_ID and got["app_secret"] == SECRET
    assert got["verify_token"] == VERIFY and got["public_url"] == "https://my.kazma.example"
    assert got["allowed_users"] == ["+965 9999 1234"] and got["live"] == {"live": "whatsapp"}
    listed = {c["name"]: c for c in client.get("/api/connectors").json()}["whatsapp"]
    assert listed["endpoint"] == "https://my.kazma.example/api/webhooks/whatsapp"
    assert SECRET not in json.dumps(listed) and VERIFY not in json.dumps(listed)


def test_no_whatsapp_source_names_a_token_in_a_url() -> None:
    from pathlib import Path

    adapters = Path(__file__).resolve().parents[1] / "kazma-gateway" / "kazma_gateway" / "adapters"
    for path in adapters.glob("whatsapp*.py"):
        src = path.read_text(encoding="utf-8")
        assert not re.search(r"access_token=|\?token=", src), path.name
