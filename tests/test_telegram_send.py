"""Unit tests for telegram_send helpers."""

from __future__ import annotations

from kazma_gateway.adapters.telegram_send import chunk_message, resolve_chat_id


def test_resolve_chat_id_from_metadata():
    assert resolve_chat_id({"chat_id": -100}, "telegram:1") == -100


def test_resolve_chat_id_from_target():
    assert resolve_chat_id({}, "telegram:12345") == 12345


def test_resolve_chat_id_missing():
    assert resolve_chat_id({}, "nope") is None


def test_chunk_message():
    assert chunk_message("") == [""]
    text = "a" * 5000
    chunks = chunk_message(text, max_len=4096)
    assert len(chunks) == 2
    assert len(chunks[0]) == 4096
    assert len(chunks[1]) == 5000 - 4096
