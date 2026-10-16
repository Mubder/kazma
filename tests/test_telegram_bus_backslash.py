"""A Telegram alert shows a backslash as a backslash.

Live 2026-10-16 the ops alert "The document worker image is older than this
build" named the command ``.venv\\Scripts\\python.exe scripts\\document_worker_image.py
build``, and Telegram showed ".venvScriptspython.exe scriptsdocument_worker_image.py
build": the bus escaped MarkdownV2's markup characters but not the backslash,
which is MarkdownV2's escape character, so each one swallowed the letter after
it. Every Windows path in a Telegram alert lost its separators.
"""

from __future__ import annotations

import asyncio

from kazma_gateway.adapters import telegram_bus
from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter, _escape_md

_ALERT = (
    "\U0001f7e1 [Ops] The document worker image is older than this build\n"
    "Documents run in containers. Run this in the install folder:\n"
    ".venv\\Scripts\\python.exe scripts\\document_worker_image.py build\n"
    "C:\\Users\\balfa\\kazma (a_b*c) [d] 1.5!"
)


def _as_telegram_shows(markdown_v2: str) -> str:
    """What Telegram displays for MarkdownV2 text with no formatting marks:
    ``\\X`` shows X; an unescaped markup character would be a parse error
    (or formatting), so the reader fails on one."""
    out: list[str] = []
    i = 0
    while i < len(markdown_v2):
        ch = markdown_v2[i]
        if ch == "\\":
            assert i + 1 < len(markdown_v2), "a trailing escape character"
            out.append(markdown_v2[i + 1])
            i += 2
            continue
        assert ch not in "_*[]()~`>#+-=|{}.!", f"unescaped {ch!r} at {i}"
        out.append(ch)
        i += 1
    return "".join(out)


def test_escaped_text_reads_back_as_written():
    assert _as_telegram_shows(_escape_md(_ALERT)) == _ALERT


def test_the_old_escaper_loses_the_backslashes():
    """Negative control: the escaper before the fix."""

    def old(text: str) -> str:
        for c in r"_*[]()~`>#+-=|{}.!":
            text = text.replace(c, f"\\{c}")
        return text

    shown = _as_telegram_shows(old(_ALERT))
    assert shown != _ALERT
    assert ".venvScriptspython.exe scriptsdocument_worker_image.py build" in shown


def test_an_operator_card_keeps_its_command(monkeypatch):
    """Through the real send path (header bolded, body escaped)."""
    sent: list[dict] = []

    async def fake_post(self, payload, method="sendMessage"):
        sent.append(payload)
        return {"ok": True}

    monkeypatch.setattr(TelegramBusAdapter, "_post", fake_post)
    msg = type("M", (), {"content": _ALERT})()
    assert asyncio.run(TelegramBusAdapter("t", 1).send(msg)) is True
    text = sent[0]["text"]
    assert sent[0]["parse_mode"] == "MarkdownV2"
    body = text.split("\n", 1)[1]  # line 1 is the bolded header
    assert _as_telegram_shows(body) == _ALERT.split("\n", 1)[1]


def test_the_plain_text_fallback_undoes_every_escape():
    """When Telegram refuses the markup, the fallback strips the escapes:
    a doubled backslash goes back to one."""
    import re

    escaped = _escape_md(_ALERT)
    fallback = re.sub(r"\\([\\_*\[\]()~`>#+\-=|{}.!])", r"\1", escaped)
    assert fallback == _ALERT
    src = open(telegram_bus.__file__, encoding="utf-8").read()
    assert r're.sub(r"\\([\\_*' in src
