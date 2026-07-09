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


def test_parse_skips_empty():
    assert parse_text_update({"message": {"chat": {"id": 1}, "text": "  "}}) is None


def test_advance_offset():
    assert advance_offset([], 3) == 3
    assert advance_offset([{"update_id": 1}, {"update_id": 5}], 0) == 6
