"""A file sent in a chat app reaches the agent, with the words sent with it.

Live 2026-11-01 the owner sent a .docx on Telegram with the caption "Keep
this file as reference". Kazma answered as if no file had come and searched
the disk for it through five approval cards: Telegram puts the words sent
with a file in its ``caption``, and the adapter read the caption as a plain
text message before it looked for a file, so a captioned document or photo
reached the agent without its file. Only a file sent with no words arrived.

Slack had the same gap from the other side: a file shared there is a
message with subtype ``file_share``, which Kazma left as "not a new message"
on both the Socket Mode and the polling path.

Each Telegram case runs the real update path against a fake Bot API; the
parse-first order is the negative control. Slack's is the old subtype rule.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import MethodType
from typing import Any

import httpx
import pytest

REPO = Path(__file__).resolve().parents[1]
ADAPTERS = REPO / "kazma-gateway" / "kazma_gateway" / "adapters"
DOCX = b"PK\x03\x04 a docx body"
NAME = "HypertFit-Brand-Name-Research-2026-11-01.docx"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


# ── Telegram ────────────────────────────────────────────────────────────


def _tg():
    from kazma_gateway.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter(token="123:abc", allow_all=True)

    async def no_announce(update: dict) -> None:
        return None

    adapter._announce_if_added_to_group = no_announce  # type: ignore[method-assign]
    files = {"doc-1": DOCX, "photo-big": b"\xff\xd8 jpeg", "audio-1": b"ID3 mp3"}

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            file_id = request.url.params["file_id"]
            if file_id not in files:
                return httpx.Response(200, json={"ok": False})
            return httpx.Response(200, json={"ok": True, "result": {"file_path": f"f/{file_id}"}})
        file_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, content=files[file_id])

    adapter._http = httpx.AsyncClient(
        base_url=adapter._api_base, transport=httpx.MockTransport(answer)
    )
    return adapter


def _update(uid: int = 1, *, key: str = "message", **fields: Any) -> dict:
    message: dict[str, Any] = {
        "message_id": 10, "date": 1,
        "chat": {"id": 7, "type": "private"},
        "from": {"id": 7, "is_bot": False, "first_name": "B"},
    }
    message.update(fields)
    return {"update_id": uid, key: message}


def _document(**fields: Any) -> dict:
    return _update(document={"file_id": "doc-1", "file_name": NAME, "mime_type": DOCX_MIME}, **fields)


async def _parse_first(self, update: dict, message: dict | None):
    """The old order: the text parser first, files only when it found no text."""
    msg = self._parse_update(update)
    if msg is not None:
        return msg
    if message and self._detect_media_message(message):
        return await self._handle_media_message(message)
    return None


def _run(adapter, *updates: dict) -> list:
    queue: asyncio.Queue = asyncio.Queue()

    async def go() -> None:
        for update in updates:
            await adapter._process_update(update, queue)
        await adapter._http.aclose()

    asyncio.run(go())
    return [queue.get_nowait() for _ in range(queue.qsize())]


def test_a_file_sent_with_words_reaches_the_agent_with_both() -> None:
    adapter = _tg()
    [msg] = _run(adapter, _document(caption="Keep this file as reference"))
    assert msg.text == "Keep this file as reference"
    [att] = msg.attachments
    assert (att.kind, att.filename, att.mime, att.data) == ("file", NAME, DOCX_MIME, DOCX)
    assert msg.context_metadata["media"] is True
    assert adapter.diagnostics()["passed_on"] == 1


def test_the_old_order_lost_the_file() -> None:
    """Negative control: parsing the caption first answers with no file."""
    adapter = _tg()
    adapter._read_message = MethodType(  # type: ignore[method-assign]
        lambda self, update, message: _parse_first(self, update, message), adapter
    )
    [msg] = _run(adapter, _document(caption="Keep this file as reference"))
    assert msg.text == "Keep this file as reference"
    assert msg.attachments == []


def test_a_photo_sent_with_words_keeps_the_photo() -> None:
    photo = [{"file_id": "photo-small", "file_unique_id": "s"},
             {"file_id": "photo-big", "file_unique_id": "b"}]
    [msg] = _run(_tg(), _update(photo=photo, caption="What is in this picture?"))
    assert msg.text == "What is in this picture?"
    [att] = msg.attachments
    assert (att.kind, att.data) == ("image", b"\xff\xd8 jpeg")


def test_a_file_with_no_words_still_arrives() -> None:
    [msg] = _run(_tg(), _document())
    assert msg.text == "[file]" and msg.attachments[0].filename == NAME


def test_a_file_in_a_forum_topic_is_answered_in_that_topic() -> None:
    """The file path built its own metadata without the topic."""
    [msg] = _run(_tg(), _document(uid=42, message_thread_id=55, caption="keep it"))
    meta = msg.context_metadata
    assert meta["message_thread_id"] == 55
    assert meta["thread_hint"] == "gw-telegram-7-topic-55"
    assert meta["update_id"] == 42


def test_a_file_that_cannot_be_downloaded_is_said() -> None:
    adapter = _tg()
    update = _update(document={"file_id": "gone", "file_name": NAME}, caption="Keep this")
    assert _run(adapter, update) == []
    assert adapter.diagnostics()["dropped"] == {"media_failed": 1}


def test_an_edit_is_not_a_new_turn() -> None:
    adapter = _tg()
    edit = _update(key="edited_message", text="hi again", caption=None)
    edited_file = _document(caption="new words")
    edited_file["edited_message"] = edited_file.pop("message")
    assert _run(adapter, edit, edited_file) == []
    assert adapter.diagnostics()["dropped"] == {"duplicate": 2}


def test_a_voice_note_sent_with_words_keeps_both() -> None:
    adapter = _tg()

    async def transcribe(message: dict) -> str:
        return "the transcript"

    adapter._handle_voice_message = transcribe  # type: ignore[method-assign]
    [msg] = _run(adapter, _update(voice={"file_id": "v"}, caption="Summarise this"))
    assert msg.text == "Summarise this\n\nthe transcript"
    assert msg.context_metadata["voice_transcribed"] is True


def test_an_audio_file_that_cannot_be_transcribed_arrives_as_a_file() -> None:
    adapter = _tg()

    async def no_transcript(message: dict) -> None:
        return None

    adapter._handle_voice_message = no_transcript  # type: ignore[method-assign]
    audio = {"file_id": "audio-1", "file_name": "call.mp3", "mime_type": "audio/mpeg"}
    voice_only = _update(uid=2, voice={"file_id": "v"}, caption="Note this")
    voice_only["message"]["message_id"] = 11
    [file_msg, voice_msg] = _run(adapter, _update(audio=audio, caption="Keep this call"), voice_only)
    assert file_msg.text == "Keep this call"
    assert file_msg.attachments[0].filename == "call.mp3"
    assert voice_msg.text.startswith("Note this\n\n[A voice note was sent")


def test_a_voice_note_alone_that_cannot_be_transcribed_is_said() -> None:
    adapter = _tg()

    async def no_transcript(message: dict) -> None:
        return None

    adapter._handle_voice_message = no_transcript  # type: ignore[method-assign]
    assert _run(adapter, _update(voice={"file_id": "v"})) == []
    assert adapter.diagnostics()["dropped"] == {"voice_failed": 1}


def test_text_is_unchanged() -> None:
    [msg] = _run(_tg(), _update(text="hello"))
    assert msg.text == "hello" and msg.attachments == []


# ── Slack ───────────────────────────────────────────────────────────────


def _file_share(**extra: Any) -> dict:
    event = {
        "type": "message", "subtype": "file_share", "user": "U1", "channel": "D1",
        "ts": "1.1", "text": "Keep this file as reference",
        "files": [{"id": "F1", "name": NAME, "mimetype": DOCX_MIME,
                   "url_private_download": "https://files.slack.com/F1"}],
    }
    event.update(extra)
    return event


def _slack():
    from kazma_gateway.adapters.slack import SlackAdapter

    return SlackAdapter(bot_token="xoxb-demo-token", app_token="xapp-demo-token", allow_all=True)


def test_a_file_shared_on_slack_reaches_the_agent() -> None:
    msg = _slack()._accept_event(_file_share())
    assert msg is not None and msg.text == "Keep this file as reference"
    [att] = msg.attachments
    assert (att.filename, att.url) == (NAME, "https://files.slack.com/F1")


def test_slack_left_every_shared_file_before(monkeypatch) -> None:
    """Negative control: the old subtype rule read only "" and bot_message."""
    import kazma_gateway.adapters.slack_parse as slack_parse

    monkeypatch.setattr(slack_parse, "_NEW_MESSAGE_SUBTYPES", frozenset({"", "bot_message"}))
    adapter = _slack()
    assert adapter._accept_event(_file_share()) is None
    assert adapter.diagnostics()["dropped"] == {"not_a_new_message": 1}


def test_slack_still_leaves_edits_and_notices() -> None:
    adapter = _slack()
    for subtype in ("message_changed", "message_deleted", "channel_join"):
        assert adapter._accept_event(_file_share(subtype=subtype, ts=subtype)) is None


def _subtype_reads(source: str) -> list[str]:
    """The functions of *source* that read a message's ``subtype`` field."""
    found = []
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            reads = (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == "subtype"
            ) or (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
                  and node.slice.value == "subtype")
            if reads:
                found.append(fn.name)
    return found


def test_slack_decides_new_messages_in_one_place() -> None:
    """Every Slack module asks ``is_new_message``; none reads ``subtype`` itself.

    The receive record, the polling path and the connector Test each kept
    their own copy of the rule, and each left shared files out.
    """
    found = {
        path.name: reads
        for path in sorted(ADAPTERS.glob("slack*.py"))
        if (reads := _subtype_reads(path.read_text(encoding="utf-8")))
    }
    assert found == {"slack_parse.py": ["is_new_message"]}, found


@pytest.mark.parametrize("planted", ['msg.get("subtype")', 'msg["subtype"]'])
def test_the_gate_sees_a_second_copy(planted: str) -> None:
    """Control for the gate: a planted subtype read is found."""
    assert _subtype_reads(f"def poll(msg):\n    return {planted}\n") == ["poll"]


# ── Discord: already read both, kept that way ───────────────────────────


def test_a_file_sent_on_discord_reaches_the_agent() -> None:
    from kazma_gateway.adapters.discord_parse import parse_message_create

    msg = parse_message_create({
        "id": "1", "channel_id": "2", "author": {"id": "3", "username": "b"},
        "content": "Keep this file as reference",
        "attachments": [{"id": "4", "filename": NAME, "content_type": DOCX_MIME,
                         "url": "https://cdn.discordapp.com/4", "size": 10}],
    })
    assert msg is not None and msg.text == "Keep this file as reference"
    assert [a.filename for a in msg.attachments] == [NAME]
