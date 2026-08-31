"""Unit tests for telegram_parse helpers."""

from __future__ import annotations

from kazma_gateway.adapters.telegram_parse import (
    advance_offset,
    extract_message,
    parse_text_update,
)


def test_extract_message_shapes():
    assert extract_message({"message": {"text": "hi"}})["text"] == "hi"
    assert extract_message({"channel_post": {"text": "c"}})["text"] == "c"
    assert extract_message({"edited_message": {"text": "e"}})["text"] == "e"
    assert extract_message({}) is None


def test_parse_text_update():
    update = {
        "update_id": 10,
        "message": {
            "message_id": 5,
            "text": "hello",
            "chat": {"id": 99, "type": "private"},
            "from": {"id": 7, "username": "alice"},
        },
    }
    msg = parse_text_update(update)
    assert msg is not None
    assert msg.platform == "telegram"
    assert msg.sender_id == "telegram:7"
    assert msg.text == "hello"
    assert msg.context_metadata["chat_id"] == 99
    assert msg.context_metadata["update_id"] == 10
    assert "message_thread_id" not in msg.context_metadata
    assert "thread_hint" not in msg.context_metadata


def test_parse_forum_topic_stamps_thread_hint():
    update = {
        "update_id": 12,
        "message": {
            "message_id": 6,
            "message_thread_id": 77,
            "text": "in topic",
            "chat": {"id": 99, "type": "supergroup"},
            "from": {"id": 7, "username": "alice"},
        },
    }
    msg = parse_text_update(update)
    assert msg is not None
    assert msg.context_metadata["message_thread_id"] == 77
    assert msg.context_metadata["thread_hint"] == "gw-telegram-7-topic-77"


def test_parse_skips_empty():
    assert parse_text_update({"message": {"chat": {"id": 1}, "text": "  "}}) is None


def test_parse_skips_edited_message_as_new_turn():
    """Edits must not start a second graph turn / HITL card."""
    update = {
        "update_id": 11,
        "edited_message": {
            "message_id": 5,
            "text": "hello edited",
            "chat": {"id": 99, "type": "private"},
            "from": {"id": 7, "username": "alice"},
        },
    }
    assert parse_text_update(update) is None


def test_telegram_adapter_dedupes_same_message_id() -> None:
    from kazma_gateway.adapters.telegram import TelegramAdapter
    from kazma_gateway.gateway import IncomingMessage

    adapter = TelegramAdapter(token="0:test", allow_all=True)
    msg = IncomingMessage(
        platform="telegram",
        sender_id="telegram:1",
        text="hi",
        context_metadata={"chat_id": 9, "message_id": 44},
    )
    assert adapter._already_seen_message(msg) is False
    assert adapter._already_seen_message(msg) is True


def test_advance_offset():
    assert advance_offset([], 3) == 3
    assert advance_offset([{"update_id": 1}, {"update_id": 5}], 0) == 6
