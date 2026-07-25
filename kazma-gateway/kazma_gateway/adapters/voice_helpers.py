"""Shared voice (STT/TTS) pipeline for all gateway platforms.

Telegram historically had a deeper path (size caps, ConfigStore live settings,
markdown strip for TTS, language-aware STT). Discord/Slack used a thin wrapper.
This module is the **single SoT** for that depth so every platform behaves the
same when ``voice.enabled`` is on.

Env / ConfigStore keys (shared with Settings UI)::

    voice.enabled
    voice.stt_provider / voice.stt_language / voice.stt_api_key / voice.stt_model
    voice.tts_provider / voice.tts_voice / voice.tts_output_format
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from kazma_gateway.gateway import Attachment, IncomingMessage

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_VOICE_BYTES",
    "download_audio_bytes",
    "find_audio_attachment",
    "live_voice_settings",
    "prepare_tts_text",
    "synthesize_speech",
    "transcribe_audio",
    "transcribe_inbound_message",
]

#: Same cap as Telegram (``telegram.MAX_VOICE_BYTES``).
MAX_VOICE_BYTES = 10 * 1024 * 1024  # 10 MB


def live_voice_settings() -> dict[str, str | bool]:
    """Read voice settings from ConfigStore with sensible defaults.

    Mirrors ``TelegramAdapter._live_voice_settings``. Returns a dict with
    keys: ``enabled``, ``stt_provider``, ``stt_language``, ``stt_api_key``,
    ``stt_model``, ``tts_provider``, ``tts_voice``, ``tts_output_format``.
    """
    out: dict[str, str | bool] = {
        "enabled": False,
        "stt_provider": "openai",
        "stt_language": "auto",
        "stt_api_key": "",
        "stt_model": "default",
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
            ("voice.stt_model", "stt_model"),
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


def find_audio_attachment(msg: IncomingMessage) -> Attachment | None:
    """Return the first audio attachment (or video treated as voice note)."""
    for a in msg.attachments:
        if a.kind == "audio":
            return a
    # Discord sometimes labels voice messages as files with audio mime
    for a in msg.attachments:
        mime = (a.mime or "").lower()
        if mime.startswith("audio/"):
            return a
    return None


async def download_audio_bytes(
    *,
    data: bytes | None = None,
    url: str | None = None,
    http: httpx.AsyncClient | None = None,
    headers: dict[str, str] | None = None,
    max_bytes: int = MAX_VOICE_BYTES,
) -> bytes | None:
    """Load audio bytes from memory or URL with Telegram-style size caps."""
    if data is not None:
        if len(data) > max_bytes:
            logger.warning(
                "[voice] audio too large in-memory: %d > %d", len(data), max_bytes
            )
            return None
        return data
    if not url:
        return None
    client = http or httpx.AsyncClient(timeout=60.0)
    own_client = http is None
    try:
        resp = await client.get(url, headers=headers or {}, timeout=60.0)
        resp.raise_for_status()
        cl = int(resp.headers.get("content-length") or 0)
        if cl > max_bytes:
            logger.warning(
                "[voice] audio Content-Length too large: %d > %d", cl, max_bytes
            )
            return None
        body = resp.content
        if len(body) > max_bytes:
            logger.warning(
                "[voice] audio download too large: %d > %d", len(body), max_bytes
            )
            return None
        return body
    except Exception as exc:
        logger.warning("[voice] audio download failed: %s", type(exc).__name__)
        return None
    finally:
        if own_client:
            await client.aclose()


async def transcribe_audio(
    audio_bytes: bytes,
    *,
    api_key: str | None = None,
    filename: str = "voice.ogg",
) -> str | None:
    """Transcribe audio via the configured STT provider (full core pipeline).

    Uses ``kazma_core.voice.stt.transcribe`` so Discord/Slack get the same
    providers as Telegram (openai, groq, local faster-whisper, nvidia, …)
    plus language and model from ConfigStore.
    """
    if not audio_bytes:
        return None
    from kazma_core.voice.stt import transcribe, transcribe_with_fallback

    cfg = live_voice_settings()
    provider = str(cfg["stt_provider"])
    language = str(cfg["stt_language"])
    key = api_key or str(cfg.get("stt_api_key") or "") or None

    # Infer format from filename extension for the provider contract
    audio_format = "ogg"
    if "." in filename:
        audio_format = filename.rsplit(".", 1)[-1].lower()
        if audio_format == "mp3":
            audio_format = "mp3"
        elif audio_format in ("m4a", "mp4"):
            audio_format = "m4a"
        elif audio_format == "wav":
            audio_format = "wav"
        elif audio_format in ("webm",):
            audio_format = "webm"
        else:
            audio_format = "ogg"

    try:
        text = await transcribe(
            audio_bytes,
            provider=provider,
            language=language,
            api_key=key,
            audio_format=audio_format,
        )
        if text:
            logger.info(
                "[voice] STT ok provider=%s lang=%s: %.100s",
                provider,
                language,
                text,
            )
            return text.strip() or None
        # Fallback chain if primary returns empty
        text = await transcribe_with_fallback(
            audio_bytes,
            providers=[provider, "openai", "groq"],
            language=language,
            api_key=key,
            audio_format=audio_format,
        )
        return (text or "").strip() or None
    except TypeError:
        # Older transcribe signature without audio_format
        try:
            text = await transcribe(
                audio_bytes,
                provider=provider,
                language=language,
                api_key=key,
            )
            return (text or "").strip() or None
        except Exception:
            logger.exception("[voice] STT failed provider=%s", provider)
            return None
    except Exception:
        logger.exception("[voice] STT failed provider=%s", provider)
        return None


async def transcribe_inbound_message(
    msg: IncomingMessage,
    *,
    http: httpx.AsyncClient | None = None,
    auth_headers: dict[str, str] | None = None,
    api_key: str | None = None,
) -> IncomingMessage:
    """Telegram-depth STT path for Discord/Slack (and any attachment-based platform).

    - Respects ``voice.enabled``
    - Downloads with size caps
    - Transcribes with language + provider from ConfigStore
    - Tags ``voice_transcribed``, ``stt_provider``, ``stt_language``, byte size
    - Never drops the turn on STT failure
    """
    cfg = live_voice_settings()
    if not cfg.get("enabled"):
        return msg

    audio = find_audio_attachment(msg)
    if audio is None:
        return msg

    try:
        data = await download_audio_bytes(
            data=audio.data,
            url=audio.url,
            http=http,
            headers=auth_headers,
        )
        if not data:
            return msg

        filename = audio.filename or "voice.ogg"
        transcript = await transcribe_audio(
            data, api_key=api_key, filename=filename
        )
        if transcript:
            # Prefer transcript; keep original text as caption if non-empty
            caption = (msg.text or "").strip()
            if caption and not caption.startswith("["):
                msg.text = f"{transcript}\n\n(caption: {caption})"
            else:
                msg.text = transcript
            meta = dict(msg.context_metadata or {})
            meta["voice_transcribed"] = True
            meta["stt_provider"] = str(cfg["stt_provider"])
            meta["stt_language"] = str(cfg["stt_language"])
            meta["voice_bytes"] = len(data)
            meta["voice_filename"] = filename
            msg.context_metadata = meta
            logger.info(
                "[voice] transcribed %d bytes platform=%s channel=%s",
                len(data),
                msg.platform,
                meta.get("channel_id"),
            )
    except Exception as exc:  # noqa: BLE001 — never drop a turn over STT
        logger.warning(
            "[voice] transcription failed platform=%s: %s",
            msg.platform,
            type(exc).__name__,
        )
    return msg


def prepare_tts_text(text: str) -> str:
    """Strip markdown/HTML for clean TTS input (Telegram parity)."""
    if not text:
        return ""
    clean = re.sub(r"```.*?```", "", text, flags=re.DOTALL)  # code blocks
    clean = re.sub(r"`[^`]*`", "", clean)  # inline code
    clean = re.sub(r"[*_~]+", "", clean)  # bold/italic/strike
    clean = re.sub(r"<[^>]+>", "", clean)  # HTML tags
    clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean)  # markdown links
    clean = re.sub(r"\s+", " ", clean).strip()
    # Cap spoken length for UX
    if len(clean) > 2500:
        clean = clean[:2500] + "…"
    return clean


async def synthesize_speech(text: str) -> bytes | None:
    """Synthesize text to audio bytes via the configured TTS provider."""
    from kazma_core.voice.tts import synthesize

    cfg = live_voice_settings()
    if not cfg.get("enabled"):
        return None
    clean = prepare_tts_text(text)
    if not clean:
        return None
    try:
        audio = await synthesize(
            clean,
            provider=str(cfg["tts_provider"]),
            voice=str(cfg["tts_voice"]),
            output_format=str(cfg["tts_output_format"]),
        )
        if audio:
            logger.info(
                "[voice] TTS ok provider=%s format=%s (%d bytes)",
                cfg["tts_provider"],
                cfg["tts_output_format"],
                len(audio),
            )
        return audio
    except Exception:
        logger.exception("[voice] TTS failed")
        return None
