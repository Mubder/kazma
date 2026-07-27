"""Telegram voice STT helpers — extracted from telegram adapter (S5).

Low-level provider calls remain here for direct tests; runtime Telegram
path prefers ``kazma_core.voice.stt.transcribe`` (same as Discord/Slack via
:mod:`voice_helpers`). Shared depth: size caps, language, ConfigStore keys.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from kazma_gateway.adapters.voice_helpers import (
    MAX_VOICE_BYTES,
    live_voice_settings,
    prepare_tts_text,
    synthesize_speech,
    transcribe_audio,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_VOICE_BYTES",
    "detect_voice_message",
    "live_voice_settings",
    "prepare_tts_text",
    "synthesize_speech",
    "transcribe_audio",
    "transcribe_groq",
    "transcribe_openai",
]


def detect_voice_message(message: dict[str, Any]) -> bool:
    """Return True if the Telegram message contains voice or audio."""
    return "voice" in message or "audio" in message


async def transcribe_openai(audio_bytes: bytes, api_key: str | None = None) -> str | None:
    """Transcribe via OpenAI Whisper API (legacy direct helper)."""
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        logger.error("[telegram] No OpenAI API key for STT")
        return None
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": ("voice.ogg", audio_bytes, "audio/ogg")},
                data={"model": "whisper-1"},
            )
            resp.raise_for_status()
            result = resp.json()
            text = result.get("text", "").strip()
            if text:
                logger.info("[telegram] OpenAI STT transcription: %.100s", text)
            return text or None
    except Exception:
        logger.exception("[telegram] OpenAI STT transcription failed")
        return None


async def transcribe_groq(audio_bytes: bytes, api_key: str | None = None) -> str | None:
    """Transcribe via Groq Whisper API (legacy direct helper)."""
    key = api_key or os.environ.get("GROQ_API_KEY")
    if not key:
        logger.error("[telegram] No Groq API key for STT")
        return None
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": ("voice.ogg", audio_bytes, "audio/ogg")},
                data={"model": "whisper-large-v3"},
            )
            resp.raise_for_status()
            result = resp.json()
            text = result.get("text", "").strip()
            if text:
                logger.info("[telegram] Groq STT transcription: %.100s", text)
            return text or None
    except Exception:
        logger.exception("[telegram] Groq STT transcription failed")
        return None
