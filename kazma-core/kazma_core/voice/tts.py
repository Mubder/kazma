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
    "TTSError",
    "TTSProvider",
    "get_tts_provider",
    "get_last_error",
    "list_tts_providers",
    "register_tts_provider",
    "synthesize",
]

logger = logging.getLogger(__name__)


class TTSError(Exception):
    """Raised by a TTS provider when synthesis cannot proceed.

    Carries a ``hint`` (install/fix guidance shown to the operator) and an
    ``is_config`` flag distinguishing a misconfiguration / missing dependency
    (HTTP callers should map this to **503 Service Unavailable**) from a
    transient runtime failure (map to **502 Bad Gateway**).
    """

    def __init__(self, message: str, *, hint: str = "", is_config: bool = False) -> None:
        super().__init__(message)
        self.hint = hint
        self.is_config = is_config


# Last error recorded by ``synthesize`` for the current thread/task. HTTP/WS
# handlers inspect this (when synthesize returns None) to surface an accurate
# status code + install hint instead of a generic 502. ContextVar propagates
# across ``await`` points within one asyncio task and is copied into child
# tasks created in the same context.
from contextvars import ContextVar  # noqa: E402

_last_error: ContextVar[TTSError | None] = ContextVar("kazma_tts_last_error", default=None)


def get_last_error() -> TTSError | None:
    """Return the most recent TTS error recorded for this task, if any."""
    return _last_error.get()


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

    Returns ``None`` on failure (back-compat). When a provider raises
    :class:`TTSError`, the reason is recorded and retrievable via
    :func:`get_last_error` so HTTP/WS handlers can emit an accurate status
    code + install hint (503 config vs 502 runtime) instead of a bare 502.
    """
    # Reset per-call so a stale error from a previous call doesn't leak.
    _last_error.set(None)
    p = get_tts_provider(provider)
    if p is None:
        logger.warning("[TTS] Unknown provider '%s' — falling back to edgetts", provider)
        p = get_tts_provider("edgetts")
    if p is None:
        err = TTSError(
            f"No TTS providers registered (provider '{provider}' unknown)",
            hint="Register a TTS provider, e.g. pip install edge-tts",
            is_config=True,
        )
        _last_error.set(err)
        logger.error("[TTS] %s", err)
        return None
    try:
        return await p(text, voice=voice, api_key=api_key, output_format=output_format)
    except TTSError as err:
        _last_error.set(err)
        if err.is_config:
            logger.error("[TTS/%s] %s", provider, err)
        else:
            logger.exception("[TTS/%s] %s", provider, err)
        return None
    except Exception as err:  # provider didn't raise TTSError
        wrapped = TTSError(str(err) or "TTS synthesis failed")
        _last_error.set(wrapped)
        logger.exception("[TTS/%s] Failed", provider)
        return None


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
            raise TTSError(
                "edge-tts is not installed",
                hint="pip install edge-tts",
                is_config=True,
            )
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
        except Exception as err:
            raise TTSError(str(err) or "edge-tts synthesis failed") from err

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
        key = api_key or os.environ.get("OPENAI_API_KEY") or _get_provider_api_key_from_db("openai")
        if not key:
            raise TTSError(
                "OpenAI API key not configured",
                hint="Set OPENAI_API_KEY or configure the openai provider in Settings",
                is_config=True,
            )
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
        except Exception as err:
            raise TTSError(str(err) or "OpenAI TTS request failed") from err

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
        key = (
            api_key
            or os.environ.get("NVIDIA_API_KEY")
            or os.environ.get("NGC_API_KEY")
            or _get_provider_api_key_from_db("nvidia")
        )
        if not key:
            raise TTSError(
                "NVIDIA/NGC API key not configured",
                hint="Set NVIDIA_API_KEY (or NGC_API_KEY)",
                is_config=True,
            )
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
        except Exception as err:
            raise TTSError(str(err) or "NVIDIA TTS request failed") from err

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
            raise TTSError(
                "kokoro / soundfile not installed",
                hint="pip install 'kokoro>=0.8' soundfile",
                is_config=True,
            )
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
        except Exception as err:
            raise TTSError(str(err) or "kokoro synthesis failed") from err

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
            raise TTSError(
                "Coqui TTS / soundfile not installed",
                hint="pip install TTS soundfile",
                is_config=True,
            )
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
        except Exception as err:
            raise TTSError(str(err) or "Coqui TTS failed") from err

    return _synthesize


# ── Auto-register built-in providers ───────────────────────────────────

register_tts_provider("edgetts", _edgetts_provider())
register_tts_provider("openai", _openai_tts_provider())
register_tts_provider("nvidia", _nvidia_tts_provider())
register_tts_provider("kokoro", _kokoro_provider())
register_tts_provider("coqui", _coqui_provider())
