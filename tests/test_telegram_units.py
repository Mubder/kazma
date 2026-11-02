"""Telegram messages are split by Telegram's own count (FA-140).

Telegram's 4096 limit counts UTF-16 code units; the splitter counted code
points. A character outside the Basic Multilingual Plane -- most emoji -- is
two units and one code point, so a long reply with emoji came out as chunks of
4203 and 3597 units: Telegram refused the first ("message is too long"), the
plain-text retry resent the same length, and the second half of the answer
never arrived.

A property test over random text heavy in astral characters, Arabic and
markup: every chunk, plain and HTML, is at most 4096 units and the chunks
rejoin to the text. A "too long" answer from Telegram splits the chunk again
instead of resending it. The code-point splitter is the negative control.
"""

from __future__ import annotations

import random
from typing import Any

import httpx
import pytest
from kazma_gateway.adapters import telegram_send
from kazma_gateway.adapters.telegram_send import (
    TELEGRAM_MAX_MESSAGE_LEN,
    chunk_message,
    telegram_cut,
    _telegram_units,
)

_ALPHABET = ["a", " ", "\n", "ك", "ظ", "é", "😀", "🚀", "𝒜", "<b>", "</b>", "\n\n"]


def _text(seed: int, size: int) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice(_ALPHABET) for _ in range(size))


def test_the_units_are_telegrams():
    assert _telegram_units("abc") == 3 and _telegram_units("😀") == 2 and _telegram_units("ك") == 1
    assert telegram_cut("a😀b", 2) == 1 and telegram_cut("a😀b", 3) == 2


@pytest.mark.parametrize("seed", range(25))
def test_every_plain_chunk_fits_and_the_chunks_rejoin(seed):
    text = _text(seed, 9000)
    chunks = chunk_message(text)
    assert "".join(chunks) == text
    assert all(0 < _telegram_units(c) <= TELEGRAM_MAX_MESSAGE_LEN for c in chunks), [_telegram_units(c) for c in chunks]


@pytest.mark.parametrize("seed", range(25))
def test_every_html_chunk_fits(seed):
    text = _text(seed, 9000).replace("<b>", "").replace("</b>", "")
    text = "<blockquote>" + text + "</blockquote>"
    chunks = chunk_message(text, parse_mode="HTML")
    assert all(_telegram_units(c) <= TELEGRAM_MAX_MESSAGE_LEN for c in chunks), [_telegram_units(c) for c in chunks]
    stripped = "".join(c.replace("<blockquote>", "").replace("</blockquote>", "") for c in chunks)
    assert stripped == text.replace("<blockquote>", "").replace("</blockquote>", "")


def test_the_code_point_splitter_overran_the_limit():
    """Negative control: the splitter before FA-140."""
    text = "😀" * 3000 + "a" * 3000
    old = [text[i:i + TELEGRAM_MAX_MESSAGE_LEN] for i in range(0, len(text), TELEGRAM_MAX_MESSAGE_LEN)]
    assert max(_telegram_units(c) for c in old) > TELEGRAM_MAX_MESSAGE_LEN


class _TooLongOnce:
    """Telegram refusing any text over *limit* units with its own answer."""

    def __init__(self, limit: int) -> None:
        self.limit, self.sent = limit, []

    async def post(self, url: str, json: dict[str, Any] | None = None, **_kw: Any) -> httpx.Response:
        request = httpx.Request("POST", "https://api.telegram.org" + url)
        text = (json or {}).get("text", "")
        if _telegram_units(text) > self.limit:
            return httpx.Response(400, request=request, json={
                "ok": False, "error_code": 400, "description": "Bad Request: message is too long"})
        self.sent.append(text)
        return httpx.Response(200, request=request, json={"ok": True, "result": {"message_id": len(self.sent)}})


async def test_a_too_long_answer_splits_the_chunk_again():
    http = _TooLongOnce(limit=10)

    async def no_wait() -> None:
        return None

    ok = await telegram_send.send_chunks_with_retry(
        http=http, chat_id=1, chunks=["😀😀😀😀😀😀😀😀 twelve"], parse_mode=None, reply_markup=None,
        rate_acquire=no_wait)
    assert ok is True and len(http.sent) >= 2
    assert "".join(http.sent) == "😀😀😀😀😀😀😀😀 twelve"
