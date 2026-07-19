"""Speech-to-Text (STT) provider abstraction and implementations.

All providers follow the same contract::

    async def transcribe(audio_bytes: bytes, language: str = "auto",
                         api_key: str | None = None) -> str | None

Providers are registered via ``register_stt_provider()`` and discovered
via ``get_stt_provider()`` / ``list_stt_providers()``.

Supported providers:
- ``openai``   — OpenAI Whisper API (cloud)
- ``groq``     — Groq Whisper API (cloud, free tier)
- ``cohere``   — Cohere Transcribe (cloud)
- ``nvidia``   — NVIDIA NIM ASR (cloud API)
- ``faster-whisper`` — faster-whisper (local GPU/CPU)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol, runtime_checkable

import httpx

__all__ = [
    "STTProvider",
    "get_stt_provider",
    "list_stt_providers",
    "register_stt_provider",
    "transcribe",
    "transcribe_with_fallback",
]

logger = logging.getLogger(__name__)


def _get_provider_api_key_from_db(provider_name: str) -> str | None:
    """Fallback helper to fetch API key from unified providers database."""
    try:
        from kazma_core.config_store import get_config_store
        from kazma_core.model_registry import ModelRegistry
        registry = ModelRegistry(get_config_store())
        entry = registry.get_provider(provider_name)
        if entry and entry.get("api_key"):
            return str(entry["api_key"])
    except Exception:
        pass
    return None



# ── Provider protocol ──────────────────────────────────────────────────


@runtime_checkable
class STTProvider(Protocol):
    """Any callable that transcribes audio bytes to text."""

    async def __call__(
        self,
        audio_bytes: bytes,
        *,
        language: str = "auto",
        api_key: str | None = None,
        audio_format: str = "ogg",
    ) -> str | None: ...


# ── Provider registry ──────────────────────────────────────────────────

_providers: dict[str, STTProvider] = {}


def register_stt_provider(name: str, provider: STTProvider) -> None:
    """Register an STT provider by name."""
    _providers[name] = provider
    logger.debug("[STT] Registered provider: %s", name)


def get_stt_provider(name: str) -> STTProvider | None:
    """Return the named provider, or None."""
    return _providers.get(name)


def list_stt_providers() -> list[str]:
    """Return sorted list of registered provider names."""
    return sorted(_providers.keys())


# ── High-level API ─────────────────────────────────────────────────────


async def transcribe(
    audio_bytes: bytes,
    *,
    provider: str = "openai",
    language: str = "auto",
    api_key: str | None = None,
    audio_format: str = "ogg",
) -> str | None:
    """Transcribe audio using the named provider.

    Falls back to ``openai`` if the named provider is not registered.
    """
    p = get_stt_provider(provider)
    if p is None:
        logger.warning("[STT] Unknown provider '%s' — falling back to openai", provider)
        p = get_stt_provider("openai")
    if p is None:
        logger.error("[STT] No providers registered")
        return None
    return await p(audio_bytes, language=language, api_key=api_key, audio_format=audio_format)


async def transcribe_with_fallback(
    audio_bytes: bytes,
    *,
    providers: list[str] | None = None,
    language: str = "auto",
    api_key: str | None = None,
    audio_format: str = "ogg",
) -> str | None:
    """Try multiple STT providers in order until one succeeds.

    Args:
        audio_bytes: Raw audio data.
        providers: Ordered list of provider names to try.
            Defaults to ``["openai", "groq", "cohere"]``.
        language: ISO-639-1 code or ``"auto"``.
        api_key: Optional API key override (provider-specific).
        audio_format: Audio container format hint.

    Returns:
        Transcribed text or None if all providers fail.
    """
    if providers is None:
        providers = ["openai", "groq", "cohere"]
    for name in providers:
        p = get_stt_provider(name)
        if p is None:
            continue
        try:
            result = await p(audio_bytes, language=language, api_key=api_key, audio_format=audio_format)
            if result:
                return result
        except Exception as exc:
            logger.warning("[STT] Provider '%s' failed: %s", name, exc)
    return None


# ── Built-in providers ─────────────────────────────────────────────────


def _openai_stt() -> STTProvider:
    """OpenAI Whisper API STT provider."""

    async def _transcribe(
        audio_bytes: bytes,
        *,
        language: str = "auto",
        api_key: str | None = None,
        audio_format: str = "ogg",
    ) -> str | None:
        key = api_key or os.environ.get("OPENAI_API_KEY") or _get_provider_api_key_from_db("openai")
        if not key:
            logger.error("[STT/openai] No API key")
            return None
        ext = audio_format or "ogg"
        mime = {
            "ogg": "audio/ogg",
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "flac": "audio/flac",
            "m4a": "audio/mp4",
            "webm": "audio/webm",
        }.get(ext, f"audio/{ext}")
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {key}"},
                    files={"file": (f"audio.{ext}", audio_bytes, mime)},
                    data={
                        "model": "whisper-1",
                        **({} if language == "auto" else {"language": language}),
                    },
                )
                resp.raise_for_status()
                text = resp.json().get("text", "").strip()
                if text:
                    logger.info("[STT/openai] Transcribed: %.100s", text)
                return text or None
        except Exception:
            logger.exception("[STT/openai] Failed")
            return None

    return _transcribe


def _groq_stt() -> STTProvider:
    """Groq Whisper API STT provider (free tier)."""

    async def _transcribe(
        audio_bytes: bytes,
        *,
        language: str = "auto",
        api_key: str | None = None,
        audio_format: str = "ogg",
    ) -> str | None:
        key = api_key or os.environ.get("GROQ_API_KEY") or _get_provider_api_key_from_db("groq")
        if not key:
            logger.error("[STT/groq] No API key")
            return None
        ext = audio_format or "ogg"
        mime = {
            "ogg": "audio/ogg",
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "flac": "audio/flac",
            "m4a": "audio/mp4",
            "webm": "audio/webm",
        }.get(ext, f"audio/{ext}")
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {key}"},
                    files={"file": (f"audio.{ext}", audio_bytes, mime)},
                    data={
                        "model": "whisper-large-v3",
                        **({} if language == "auto" else {"language": language}),
                    },
                )
                resp.raise_for_status()
                text = resp.json().get("text", "").strip()
                if text:
                    logger.info("[STT/groq] Transcribed: %.100s", text)
                return text or None
        except Exception:
            logger.exception("[STT/groq] Failed")
            return None

    return _transcribe


def _cohere_stt() -> STTProvider:
    """Cohere Transcribe STT provider.

    Supports 14 languages. Model: ``cohere-transcribe-03-2026``.
    For Arabic, use ``cohere-transcribe-arabic-07-2026``.
    """

    async def _transcribe(
        audio_bytes: bytes,
        *,
        language: str = "auto",
        api_key: str | None = None,
        audio_format: str = "ogg",
    ) -> str | None:
        key = api_key or os.environ.get("COHERE_API_KEY") or _get_provider_api_key_from_db("cohere")
        if not key:
            logger.error("[STT/cohere] No API key")
            return None
        # Cohere supports: flac, mp3, mpeg, mpga, ogg, wav
        ext = audio_format or "ogg"
        mime = {
            "ogg": "audio/ogg",
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "flac": "audio/flac",
            "m4a": "audio/mp4",
            "webm": "audio/webm",
        }.get(ext, f"audio/{ext}")
        # Select model based on language
        if language == "ar":
            model = "cohere-transcribe-arabic-07-2026"
        else:
            model = "cohere-transcribe-03-2026"
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                data: dict[str, Any] = {"model": model}
                if language != "auto":
                    data["language"] = language
                resp = await client.post(
                    "https://api.cohere.com/v2/audio/transcriptions",
                    headers={"Authorization": f"Bearer {key}"},
                    files={"file": (f"audio.{ext}", audio_bytes, mime)},
                    data=data,
                )
                resp.raise_for_status()
                text = resp.json().get("text", "").strip()
                if text:
                    logger.info("[STT/cohere] Transcribed: %.100s", text)
                return text or None
        except Exception:
            logger.exception("[STT/cohere] Failed")
            return None

    return _transcribe


def _nvidia_stt() -> STTProvider:
    """NVIDIA NIM ASR cloud API STT provider.

    Uses the NVIDIA hosted ASR endpoint (Whisper Large v3 or Parakeet).
    Requires an NGC API key.
    """

    async def _transcribe(
        audio_bytes: bytes,
        *,
        language: str = "auto",
        api_key: str | None = None,
        audio_format: str = "ogg",
    ) -> str | None:
        key = (
            api_key
            or os.environ.get("NVIDIA_API_KEY")
            or os.environ.get("NGC_API_KEY")
            or _get_provider_api_key_from_db("nvidia")
        )
        if not key:
            logger.error("[STT/nvidia] No API key (set NVIDIA_API_KEY)")
            return None
        ext = audio_format or "ogg"
        mime = {
            "ogg": "audio/ogg",
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "flac": "audio/flac",
            "m4a": "audio/mp4",
            "webm": "audio/webm",
        }.get(ext, f"audio/{ext}")
        # Use the cloud-hosted NVIDIA ASR endpoint
        base_url = os.environ.get(
            "NVIDIA_ASR_URL",
            "https://ai.api.nvidia.com/v1/asr",
        )
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{base_url}/transcriptions",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Accept": "application/json",
                    },
                    files={"file": (f"audio.{ext}", audio_bytes, mime)},
                    data={
                        "model": "nvidia/whisper-large-v3",
                        **({} if language == "auto" else {"language": language}),
                    },
                )
                resp.raise_for_status()
                result = resp.json()
                # NVIDIA ASR returns {"text": "..."} or {"transcriptions": [...]}
                text = result.get("text", "")
                if not text and "transcriptions" in result:
                    transcriptions = result["transcriptions"]
                    if transcriptions:
                        text = transcriptions[0].get("text", "")
                text = text.strip()
                if text:
                    logger.info("[STT/nvidia] Transcribed: %.100s", text)
                return text or None
        except Exception:
            logger.exception("[STT/nvidia] Failed")
            return None

    return _transcribe


def _faster_whisper_stt() -> STTProvider:
    """Local faster-whisper STT provider.

    Requires ``pip install faster-whisper``. Uses the CTranslate2 backend
    for GPU/CPU accelerated Whisper inference.
    """

    async def _transcribe(
        audio_bytes: bytes,
        *,
        language: str = "auto",
        api_key: str | None = None,
        audio_format: str = "ogg",
    ) -> str | None:
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]
        except ImportError:
            logger.error("[STT/faster-whisper] pip install faster-whisper required")
            return None
        import tempfile
        from pathlib import Path

        model_size = os.environ.get("WHISPER_MODEL", "base")
        try:
            model = WhisperModel(model_size, device="auto", compute_type="float16")
            # Write audio to temp file (faster-whisper needs a file path)
            ext = audio_format or "ogg"
            with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name
            try:
                lang = None if language == "auto" else language
                segments, info = model.transcribe(tmp_path, language=lang)
                text = " ".join(seg.text for seg in segments).strip()
                if text:
                    logger.info(
                        "[STT/faster-whisper] Transcribed (%s, %.1fs): %.100s",
                        info.language,
                        info.duration,
                        text,
                    )
                return text or None
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            logger.exception("[STT/faster-whisper] Failed")
            return None

    return _transcribe


# ── Auto-register built-in providers ───────────────────────────────────

register_stt_provider("openai", _openai_stt())
register_stt_provider("groq", _groq_stt())
register_stt_provider("cohere", _cohere_stt())
register_stt_provider("nvidia", _nvidia_stt())
register_stt_provider("faster-whisper", _faster_whisper_stt())
