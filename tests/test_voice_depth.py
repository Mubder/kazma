"""Telegram-depth shared voice pipeline for Discord/Slack."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kazma_gateway.adapters.voice_helpers import (
    MAX_VOICE_BYTES,
    download_audio_bytes,
    find_audio_attachment,
    prepare_tts_text,
    transcribe_inbound_message,
)
from kazma_gateway.gateway import Attachment, IncomingMessage


def test_max_voice_bytes_matches_telegram() -> None:
    assert MAX_VOICE_BYTES == 10 * 1024 * 1024


def test_prepare_tts_text_strips_markdown() -> None:
    raw = "Hello **world** with `code` and [link](http://x.com)"
    clean = prepare_tts_text(raw)
    assert "**" not in clean
    assert "`" not in clean
    assert "http" not in clean
    assert "world" in clean
    assert "link" in clean


def test_find_audio_by_mime() -> None:
    msg = IncomingMessage(
        platform="discord",
        sender_id="d:1",
        text="[file]",
        attachments=[
            Attachment(
                kind="file",
                mime="audio/ogg",
                filename="v.ogg",
                url="http://example/v.ogg",
            )
        ],
    )
    att = find_audio_attachment(msg)
    assert att is not None
    assert att.filename == "v.ogg"


@pytest.mark.asyncio
async def test_download_rejects_oversized() -> None:
    big = b"x" * (MAX_VOICE_BYTES + 1)
    out = await download_audio_bytes(data=big)
    assert out is None


@pytest.mark.asyncio
async def test_transcribe_inbound_tags_metadata() -> None:
    msg = IncomingMessage(
        platform="discord",
        sender_id="discord:ch",
        text="[audio]",
        attachments=[
            Attachment(
                kind="audio",
                mime="audio/ogg",
                filename="voice.ogg",
                data=b"fake-audio-bytes",
            )
        ],
        context_metadata={"channel_id": "ch"},
    )
    with patch(
        "kazma_gateway.adapters.voice_helpers.live_voice_settings",
        return_value={
            "enabled": True,
            "stt_provider": "openai",
            "stt_language": "en",
            "stt_api_key": "",
            "tts_provider": "edgetts",
            "tts_voice": "default",
            "tts_output_format": "mp3",
        },
    ), patch(
        "kazma_gateway.adapters.voice_helpers.transcribe_audio",
        new_callable=AsyncMock,
        return_value="hello from voice",
    ):
        out = await transcribe_inbound_message(msg)
    assert out.text == "hello from voice"
    assert out.context_metadata.get("voice_transcribed") is True
    assert out.context_metadata.get("stt_provider") == "openai"
    assert out.context_metadata.get("stt_language") == "en"
    assert out.context_metadata.get("voice_bytes") == len(b"fake-audio-bytes")


@pytest.mark.asyncio
async def test_transcribe_skips_when_disabled() -> None:
    msg = IncomingMessage(
        platform="slack",
        sender_id="slack:u",
        text="[audio]",
        attachments=[
            Attachment(kind="audio", mime="audio/mpeg", filename="a.mp3", data=b"x")
        ],
    )
    with patch(
        "kazma_gateway.adapters.voice_helpers.live_voice_settings",
        return_value={"enabled": False},
    ):
        out = await transcribe_inbound_message(msg)
    assert out.text == "[audio]"
    assert not out.context_metadata.get("voice_transcribed")
