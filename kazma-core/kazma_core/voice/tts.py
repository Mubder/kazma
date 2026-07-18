"""Text-to-Speech (TTS) provider abstraction and implementations.

All providers follow the same contract::

    async def synthesize(text: str, *, voice: str = "default",
                         api_key: str | None = None,
                         output_format: str = "mp3") -> bytes | None

Providers are registered via ``register_tts_provider()`` and discovered
via ``get_tts_provider()`` / ``list_tts_providers()``.

Supported providers:
- ``edgetts``   — EdgeTTS / Microsoft Neural TTS (free, no API key)
- ``openai``    — OpenAI TTS API (cloud)
- ``nvidia``    — NVIDIA NIM TTS (cloud API)
- ``kokoro``    — Kokoro neural TTS (local)
- ``coqui``     — Coqui TTS (local)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol, runtime_checkable

import httpx

__all__ = [
    "TTSProvider",
    "get_tts_provider",
    "list_tts_providers",
    "register_tts_provider",
    "synthesize",
]

logger = logging.getLogger(__name__)


# ── Provider protocol ──────────────────────────────────────────────────


@runtime_checkable
class TTSProvider(Protocol):
    """Any callable that synthesizes text to audio bytes."""

    async def __call__(
        self,
        text: str,
        *,
        voice: str = "default",
        api_key: str | None = None,
        output_format: str = "mp3",
    ) -> bytes | None: ...


# ── Provider registry ──────────────────────────────────────────────────

_providers: dict[str, TTSProvider] = {}


def register_tts_provider(name: str, provider: TTSProvider) -> None:
    """Register a TTS provider by name."""
    _providers[name] = provider
    logger.debug("[TTS] Registered provider: %s", name)


def get_tts_provider(name: str) -> TTSProvider | None:
    """Return the named provider, or None."""
    return _providers.get(name)


def list_tts_providers() -> list[str]:
    """Return sorted list of registered provider names."""
    return sorted(_providers.keys())


# ── High-level API ─────────────────────────────────────────────────────


async def synthesize(
    text: str,
    *,
    provider: str = "edgetts",
    voice: str = "default",
    api_key: str | None = None,
    output_format: str = "mp3",
) -> bytes | None:
    """Synthesize text to audio using the named provider.

    Falls back to ``edgetts`` if the named provider is not registered.
    """
    p = get_tts_provider(provider)
    if p is None:
        logger.warning("[TTS] Unknown provider '%s' — falling back to edgetts", provider)
        p = get_tts_provider("edgetts")
    if p is None:
        logger.error("[TTS] No providers registered")
        return None
    return await p(text, voice=voice, api_key=api_key, output_format=output_format)


# ── Built-in providers ─────────────────────────────────────────────────


def _edgetts_provider() -> TTSProvider:
    """EdgeTTS provider — free Microsoft Neural TTS.

    No API key required. Supports many languages and voices.
    Install: ``pip install edge-tts``
    """

    async def _synthesize(
        text: str,
        *,
        voice: str = "default",
        api_key: str | None = None,
        output_format: str = "mp3",
    ) -> bytes | None:
        try:
            import edge_tts  # type: ignore[import-untyped]
        except ImportError:
            logger.error("[TTS/edgetts] pip install edge-tts required")
            return None
        if voice == "default":
            voice = "en-US-AriaNeural"
        try:
            communicate = edge_tts.Communicate(text, voice)
            audio_data = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_data += chunk["data"]
            if audio_data:
                logger.info(
                    "[TTS/edgetts] Synthesized %d bytes (voice=%s)",
                    len(audio_data),
                    voice,
                )
            return audio_data or None
        except Exception:
            logger.exception("[TTS/edgetts] Failed")
            return None

    return _synthesize


def _openai_tts_provider() -> TTSProvider:
    """OpenAI TTS API provider.

    Supports: alloy, echo, fable, onyx, nova, shimmer voices.
    """

    async def _synthesize(
        text: str,
        *,
        voice: str = "default",
        api_key: str | None = None,
        output_format: str = "mp3",
    ) -> bytes | None:
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            logger.error("[TTS/openai] No API key")
            return None
        if voice == "default":
            voice = "alloy"
        # OpenAI TTS output formats: mp3, opus, aac, flac, wav, pcm
        resp_format = output_format if output_format in ("mp3", "opus", "aac", "flac", "wav", "pcm") else "mp3"
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": "tts-1",
                        "input": text,
                        "voice": voice,
                        "response_format": resp_format,
                    },
                )
                resp.raise_for_status()
                audio = resp.content
                if audio:
                    logger.info("[TTS/openai] Synthesized %d bytes (voice=%s)", len(audio), voice)
                return audio or None
        except Exception:
            logger.exception("[TTS/openai] Failed")
            return None

    return _synthesize


def _nvidia_tts_provider() -> TTSProvider:
    """NVIDIA NIM TTS cloud API provider.

    Uses the hosted NVIDIA TTS endpoint (Magpie TTS Multilingual).
    Requires an NGC API key.
    """

    async def _synthesize(
        text: str,
        *,
        voice: str = "default",
        api_key: str | None = None,
        output_format: str = "mp3",
    ) -> bytes | None:
        key = api_key or os.environ.get("NVIDIA_API_KEY") or os.environ.get("NGC_API_KEY")
        if not key:
            logger.error("[TTS/nvidia] No API key (set NVIDIA_API_KEY)")
            return None
        if voice == "default":
            voice = "Magpie-Multilingual.EN-US.Aria"
        base_url = os.environ.get(
            "NVIDIA_TTS_URL",
            "https://ai.api.nvidia.com/v1/tts",
        )
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{base_url}/speech",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                        "Accept": "audio/wav",
                    },
                    json={
                        "model": "magpie-tts-multilingual",
                        "text": text,
                        "voice": voice,
                    },
                )
                resp.raise_for_status()
                audio = resp.content
                if audio:
                    logger.info("[TTS/nvidia] Synthesized %d bytes (voice=%s)", len(audio), voice)
                return audio or None
        except Exception:
            logger.exception("[TTS/nvidia] Failed")
            return None

    return _synthesize


def _kokoro_provider() -> TTSProvider:
    """Kokoro neural TTS provider — local, high quality.

    Requires ``pip install kokoro>=0.8`` and ``soundfile``.
    Runs the model in-process (no server needed).
    """

    async def _synthesize(
        text: str,
        *,
        voice: str = "default",
        api_key: str | None = None,
        output_format: str = "mp3",
    ) -> bytes | None:
        try:
            import io

            import kokoro  # type: ignore[import-untyped]
            import soundfile as sf  # type: ignore[import-untyped]
        except ImportError:
            logger.error("[TTS/kokoro] pip install 'kokoro>=0.8' soundfile required")
            return None
        if voice == "default":
            voice = "af_sarah"
        try:
            # Kokoro returns (audio_numpy, sample_rate)
            audio, sr = kokoro.create(text, voice=voice)
            # Write to WAV buffer
            buf = io.BytesIO()
            sf.write(buf, audio, sr, format="WAV")
            buf.seek(0)
            audio_bytes = buf.read()
            if audio_bytes:
                logger.info("[TTS/kokoro] Synthesized %d bytes (voice=%s)", len(audio_bytes), voice)
            return audio_bytes or None
        except Exception:
            logger.exception("[TTS/kokoro] Failed")
            return None

    return _synthesize


def _coqui_provider() -> TTSProvider:
    """Coqui TTS provider — local neural TTS.

    Requires ``pip install TTS``.
    """

    async def _synthesize(
        text: str,
        *,
        voice: str = "default",
        api_key: str | None = None,
        output_format: str = "mp3",
    ) -> bytes | None:
        try:
            import io

            import TTS.api.TTS as coqui_tts  # type: ignore[import-untyped]
            import soundfile as sf  # type: ignore[import-untyped]
        except ImportError:
            logger.error("[TTS/coqui] pip install TTS soundfile required")
            return None
        try:
            tts_model = coqui_tts.TTS()
            wav = tts_model.tts(text)
            buf = io.BytesIO()
            sf.write(buf, wav, 22050, format="WAV")
            buf.seek(0)
            audio_bytes = buf.read()
            if audio_bytes:
                logger.info("[TTS/coqui] Synthesized %d bytes", len(audio_bytes))
            return audio_bytes or None
        except Exception:
            logger.exception("[TTS/coqui] Failed")
            return None

    return _synthesize


# ── Auto-register built-in providers ───────────────────────────────────

register_tts_provider("edgetts", _edgetts_provider())
register_tts_provider("openai", _openai_tts_provider())
register_tts_provider("nvidia", _nvidia_tts_provider())
register_tts_provider("kokoro", _kokoro_provider())
register_tts_provider("coqui", _coqui_provider())
