"""Unit tests for telegram_send helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kazma_gateway.adapters.telegram_send import (
    chunk_message,
    resolve_chat_id,
    send_chunks_with_retry,
)


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


@pytest.mark.asyncio
async def test_send_chunks_success():
    http = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"ok": True}
    resp.raise_for_status = MagicMock()
    http.post = AsyncMock(return_value=resp)
    ok = await send_chunks_with_retry(
        http=http,
        chat_id=1,
        chunks=["hi"],
        parse_mode="Markdown",
        reply_markup=None,
        rate_acquire=AsyncMock(),
    )
    assert ok is True
    http.post.assert_awaited()
