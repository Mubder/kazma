"""Discord voice STT/TTS helpers — Telegram-depth path via voice_helpers."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from kazma_gateway.gateway import IncomingMessage

logger = logging.getLogger(__name__)

__all__ = ["send_voice_reply", "transcribe_message"]


async def transcribe_message(
    msg: IncomingMessage,
    *,
    http: httpx.AsyncClient | None = None,
) -> IncomingMessage:
    """Run the shared deep STT pipeline on a Discord inbound message."""
    from kazma_gateway.adapters.voice_helpers import transcribe_inbound_message

    return await transcribe_inbound_message(msg, http=http)


async def send_voice_reply(
    *,
    http: httpx.AsyncClient,
    channel_id: str,
    text: str,
    rate_limiter: Any = None,
) -> bool:
    """Synthesize *text* and upload as an audio attachment to Discord."""
    from kazma_gateway.adapters.voice_helpers import (
        live_voice_settings,
        synthesize_speech,
    )

    if not live_voice_settings().get("enabled") or not text:
        return False
    try:
        audio = await synthesize_speech(text)
        if not audio:
            return False
        if rate_limiter is not None:
            await rate_limiter.acquire()
        import json

        fmt = str(live_voice_settings().get("tts_output_format") or "mp3")
        ext = "mp3" if fmt in ("mp3", "mpeg") else fmt
        mime = "audio/mpeg" if ext == "mp3" else f"audio/{ext}"
        resp = await http.post(
            f"/channels/{channel_id}/messages",
            data={"payload_json": json.dumps({"content": ""})},
            files={"files[0]": (f"reply.{ext}", audio, mime)},
        )
        resp.raise_for_status()
        logger.info(
            "[discord] voice reply sent to %s (%d bytes)", channel_id, len(audio)
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[discord] voice reply failed: %s", type(exc).__name__)
        return False
