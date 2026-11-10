"""A Telegram sender is checked before Kazma downloads or transcribes.

Security audit 2026-11-10. ``_process_update`` ran ``_read_message`` -- which
downloads a file or photo and sends a voice note to the speech-to-text
provider (a paid call) -- and checked the allowlist only after. Anyone who
could message the bot made Kazma download and transcribe for them. The
sender (``message["from"]["id"]``) is now checked first, with the same drop
reasons, so every message stays accounted for (AGENTS §42).
"""

from __future__ import annotations

import asyncio

import pytest
from kazma_gateway.adapters.telegram import TelegramAdapter

ALLOWED, STRANGER = 111, 999


def _voice_update(sender: int, update_id: int = 1) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 10 + update_id,
            "from": {"id": sender, "is_bot": False, "username": f"u{sender}"},
            "chat": {"id": sender, "type": "private"},
            "date": 1,
            "voice": {"file_id": "VOICE", "duration": 3, "mime_type": "audio/ogg"},
        },
    }


@pytest.fixture()
def adapter(monkeypatch: pytest.MonkeyPatch) -> tuple[TelegramAdapter, list[int]]:
    adapter = TelegramAdapter(token="123:fake", allow_all=False, allowed_users=[ALLOWED])
    read: list[int] = []

    async def _read(update, message):  # stands in for download + STT
        read.append(message["from"]["id"])
        return None

    monkeypatch.setattr(adapter, "_read_message", _read)
    monkeypatch.setattr(adapter, "_announce_if_added_to_group", lambda update: asyncio.sleep(0))
    return adapter, read


async def test_a_strangers_voice_note_is_not_downloaded(adapter) -> None:
    tg, read = adapter
    await tg._process_update(_voice_update(STRANGER), asyncio.Queue())
    assert read == []


async def test_the_refusal_is_recorded(adapter, monkeypatch) -> None:
    tg, _ = adapter
    seen: list[str] = []
    monkeypatch.setattr(tg, "_note_drop", lambda reason, message: seen.append(reason))
    await tg._process_update(_voice_update(STRANGER), asyncio.Queue())
    assert seen == ["user_not_allowed"]


async def test_an_allowed_sender_is_read(adapter) -> None:
    tg, read = adapter
    await tg._process_update(_voice_update(ALLOWED), asyncio.Queue())
    assert read == [ALLOWED]


async def test_no_allowlist_reads_nothing(monkeypatch) -> None:
    tg = TelegramAdapter(token="123:fake", allow_all=False, allowed_users=[])
    read: list[int] = []

    async def _read(update, message):
        read.append(1)
        return None

    monkeypatch.setattr(tg, "_read_message", _read)
    monkeypatch.setattr(tg, "_announce_if_added_to_group", lambda update: asyncio.sleep(0))
    seen: list[str] = []
    monkeypatch.setattr(tg, "_note_drop", lambda reason, message: seen.append(reason))
    await tg._process_update(_voice_update(STRANGER), asyncio.Queue())
    assert read == [] and seen == ["no_allowlist"]


async def test_allow_all_still_reads_everyone(monkeypatch) -> None:
    tg = TelegramAdapter(token="123:fake", allow_all=True, allowed_users=[])
    read: list[int] = []

    async def _read(update, message):
        read.append(message["from"]["id"])
        return None

    monkeypatch.setattr(tg, "_read_message", _read)
    monkeypatch.setattr(tg, "_announce_if_added_to_group", lambda update: asyncio.sleep(0))
    await tg._process_update(_voice_update(STRANGER), asyncio.Queue())
    assert read == [STRANGER]


async def test_control_without_the_early_check_the_stranger_was_read(adapter, monkeypatch) -> None:
    """Negative control: the order before the fix (read, then check)."""
    tg, read = adapter
    monkeypatch.setattr(tg, "_sender_refusal", lambda message: "")
    await tg._process_update(_voice_update(STRANGER), asyncio.Queue())
    assert read == [STRANGER]
