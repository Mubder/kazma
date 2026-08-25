"""Realtime / Live APIs as *audio codecs only* — never the conversation brain.

OpenAI Realtime and Gemini Live own a tool loop if you open a session.
Kazma cannot separate that loop from STT/TTS without skipping HITL /
``turn_failed`` / commitment, so those providers are **skipped**.

When ``KAZMA_REALTIME_CODEC=1``, listen/speak still uses the existing REST
STT + TTS mouths. Tokens of meaning always go through ``invoke_llm_chat``
/ the LangGraph supervisor.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "codec_backend",
    "realtime_codec_enabled",
    "realtime_providers_skipped",
    "speak_codec",
    "transcribe_codec",
]


def realtime_codec_enabled() -> bool:
    return os.environ.get("KAZMA_REALTIME_CODEC", "0").strip().lower() in (
        "1",
        "true",
        "on",
        "yes",
    )


def realtime_providers_skipped() -> tuple[str, ...]:
    """Providers that cannot stay codec-only (conversation/tool loop)."""
    return ("openai_realtime", "gemini_live")


def codec_backend() -> str:
    """``stt_tts_rest`` when the kill-switch is on, else ``none``."""
    if not realtime_codec_enabled():
        return "none"
    return "stt_tts_rest"


async def transcribe_codec(
    audio_bytes: bytes,
    *,
    provider: str = "openai",
    language: str = "auto",
    audio_format: str = "webm",
) -> str | None:
    """STT only. Does not open a Realtime/Live session."""
    if codec_backend() == "none":
        return None
    from kazma_core.voice.stt import transcribe

    return await transcribe(
        audio_bytes,
        provider=provider,
        language=language,
        audio_format=audio_format,
    )


async def speak_codec(
    text: str,
    *,
    provider: str = "edgetts",
    voice: str = "default",
    output_format: str = "mp3",
) -> bytes | None:
    """TTS only. Does not open a Realtime/Live session."""
    if codec_backend() == "none":
        return None
    from kazma_core.voice.tts import synthesize

    return await synthesize(
        text,
        provider=provider,
        voice=voice,
        output_format=output_format,
    )


def codec_status() -> dict[str, Any]:
    return {
        "enabled": realtime_codec_enabled(),
        "backend": codec_backend(),
        "skipped_providers": list(realtime_providers_skipped()),
        "brain": "langgraph",
    }
