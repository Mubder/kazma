"""Shared voice (STT/TTS) helpers for non-Telegram adapters.

Telegram has its own inline implementation; Discord and Slack reuse these
to transcribe inbound audio and synthesize outbound voice replies. The
helpers read the same ConfigStore keys as Telegram's ``_live_voice_settings``
so a single setting controls voice across every platform.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def live_voice_settings() -> dict[str, str | bool]:
    """Read voice settings from ConfigStore with sensible defaults.

    Mirrors ``TelegramAdapter._live_voice_settings``. Returns a dict with
    keys: ``enabled``, ``stt_provider``, ``stt_language``, ``stt_api_key``,
    ``tts_provider``, ``tts_voice``, ``tts_output_format``.
    """
    out: dict[str, str | bool] = {
        "enabled": False,
        "stt_provider": "openai",
        "stt_language": "auto",
        "stt_api_key": "",
        "tts_provider": "edgetts",
        "tts_voice": "default",
        "tts_output_format": "mp3",
    }
    try:
        from kazma_core.config_store import get_config_store

        cs = get_config_store()
        mapping: tuple[tuple[str, str], ...] = (
            ("voice.enabled", "enabled"),
            ("voice.stt_provider", "stt_provider"),
            ("voice.stt_language", "stt_language"),
            ("voice.stt_api_key", "stt_api_key"),
            ("voice.tts_provider", "tts_provider"),
            ("voice.tts_voice", "tts_voice"),
            ("voice.tts_output_format", "tts_output_format"),
        )
        for key, attr in mapping:
            val = cs.get(key)
            if val is None:
                continue
            sval = str(val).strip()
            if not sval or sval.lower() == "none":
                continue
            if attr == "enabled":
                out[attr] = sval.lower() in ("true", "1", "yes", "on")
            else:
                out[attr] = sval
    except Exception:
        logger.debug("[voice] live settings unavailable", exc_info=True)
    return out


async def transcribe_audio(audio_bytes: bytes, api_key: str | None = None) -> str | None:
    """Transcribe audio bytes via the configured STT provider."""
    from kazma_core.voice.stt import transcribe

    cfg = live_voice_settings()
    return await transcribe(
        audio_bytes,
        provider=str(cfg["stt_provider"]),
        language=str(cfg["stt_language"]),
        api_key=api_key or str(cfg["stt_api_key"]) or None,
    )


async def synthesize_speech(text: str) -> bytes | None:
    """Synthesize text to audio bytes via the configured TTS provider."""
    from kazma_core.voice.tts import synthesize

    cfg = live_voice_settings()
    return await synthesize(
        text,
        provider=str(cfg["tts_provider"]),
        voice=str(cfg["tts_voice"]),
        output_format=str(cfg["tts_output_format"]),
    )
