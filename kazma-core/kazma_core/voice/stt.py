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
    "list_nvidia_stt_models",
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


def _get_provider_base_url_from_db(provider_name: str) -> str | None:
    """Fallback helper to fetch base URL from unified providers database."""
    try:
        from kazma_core.config_store import get_config_store
        from kazma_core.model_registry import ModelRegistry
        registry = ModelRegistry(get_config_store())
        entry = registry.get_provider(provider_name)
        if entry and entry.get("base_url"):
            return str(entry["base_url"])
    except Exception:
        pass
    return None


def _get_configured_stt_model(provider_name: str) -> str | None:
    """Get the custom STT model configured in the database, if any."""
    try:
        from kazma_core.config_store import get_config_store
        cs = get_config_store()
        stored_provider = cs.get("voice.stt_provider")
        if stored_provider == provider_name:
            model = cs.get("voice.stt_model")
            if model and model != "default":
                return str(model)
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
                        "model": _get_configured_stt_model("openai") or "whisper-1",
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
                        "model": _get_configured_stt_model("groq") or "whisper-large-v3",
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


def _nvidia_asr_base_url() -> str | None:
    """Resolve ASR base URL — never the LLM chat integrate URL.

    NVIDIA's ``integrate.api.nvidia.com/v1`` hosts *chat* models only.
    Whisper/Riva ASR is a separate OpenAI-compatible NIM you self-host
    (or set explicitly via ``voice.stt_base_url`` / ``NVIDIA_ASR_URL``).
    """
    try:
        from kazma_core.config_store import get_config_store

        cs = get_config_store()
        explicit = cs.get("voice.stt_base_url")
        if explicit and str(explicit).strip():
            return str(explicit).strip().rstrip("/")
    except Exception:
        pass
    env = os.environ.get("NVIDIA_ASR_URL") or os.environ.get("NVIDIA_STT_URL")
    if env:
        return env.strip().rstrip("/")
    return None


def _is_llm_only_nvidia_url(url: str) -> bool:
    """True if URL is the chat integrate API (no /audio/transcriptions)."""
    low = url.lower()
    return "integrate.api.nvidia.com" in low and "/asr" not in low


def list_nvidia_stt_models() -> list[dict[str, str]]:
    """Catalog of NVIDIA ASR models (not LLM chat models).

    Dynamic: includes any model set in ConfigStore + known NIM ids.
    """
    models: list[dict[str, str]] = [
        {
            "id": "openai/whisper-large-v3",
            "label": "Whisper Large v3 (NIM)",
            "note": "Self-hosted NVIDIA Speech NIM — set voice.stt_base_url",
        },
        {
            "id": "whisper-large-v3",
            "label": "Whisper Large v3 (short id)",
            "note": "Alias used by some NIM containers",
        },
        {
            "id": "nvidia/parakeet-ctc-1.1b-en-us",
            "label": "Parakeet CTC 1.1B (EN)",
            "note": "Riva/NIM English ASR",
        },
        {
            "id": "nvidia/canary-1b",
            "label": "Canary 1B",
            "note": "Multilingual NIM ASR when deployed",
        },
    ]
    try:
        configured = _get_configured_stt_model("nvidia")
        if configured and not any(m["id"] == configured for m in models):
            models.insert(0, {
                "id": configured,
                "label": f"{configured} (configured)",
                "note": "From voice.stt_model",
            })
    except Exception:
        pass
    return models


def _nvidia_stt() -> STTProvider:
    """NVIDIA Speech NIM ASR (OpenAI-compatible /v1/audio/transcriptions).

    Requires a **Speech NIM** base URL (local or remote), e.g.
    ``http://localhost:9000/v1`` — **not** the LLM integrate.api.nvidia.com
    endpoint. Reuses the NVIDIA API key from the LLM providers registry
    when the NIM requires auth.
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
        ext = audio_format or "ogg"
        mime = {
            "ogg": "audio/ogg",
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "flac": "audio/flac",
            "m4a": "audio/mp4",
            "webm": "audio/webm",
        }.get(ext, f"audio/{ext}")

        base_url = _nvidia_asr_base_url()
        # Legacy mistake: using LLM provider base_url (integrate.api…) → 404
        if not base_url:
            llm_url = _get_provider_base_url_from_db("nvidia")
            if llm_url and not _is_llm_only_nvidia_url(llm_url):
                base_url = llm_url.rstrip("/")
            elif llm_url and _is_llm_only_nvidia_url(llm_url):
                logger.error(
                    "[STT/nvidia] LLM base URL %s has no ASR. "
                    "Deploy a Speech NIM and set voice.stt_base_url "
                    "(e.g. http://localhost:9000/v1) or use STT provider "
                    "openai/groq for cloud Whisper.",
                    llm_url,
                )
                return None

        if not base_url:
            logger.error(
                "[STT/nvidia] No ASR endpoint configured. "
                "Set voice.stt_base_url to your Speech NIM root "
                "(OpenAI-compatible, e.g. http://127.0.0.1:9000/v1). "
                "Cloud integrate.api.nvidia.com does not host Whisper. "
                "For set-and-forget cloud STT use provider=groq or openai."
            )
            return None

        # Build /v1/audio/transcriptions from base
        target_url = base_url
        if target_url.endswith("/transcriptions"):
            pass
        elif target_url.endswith("/audio"):
            target_url = f"{target_url}/transcriptions"
        elif target_url.rstrip("/").endswith("/v1"):
            target_url = f"{target_url.rstrip('/')}/audio/transcriptions"
        else:
            target_url = f"{target_url.rstrip('/')}/v1/audio/transcriptions"

        model = (
            _get_configured_stt_model("nvidia")
            or "openai/whisper-large-v3"
        )
        headers: dict[str, str] = {"Accept": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(
                    target_url,
                    headers=headers,
                    files={"file": (f"audio.{ext}", audio_bytes, mime)},
                    data={
                        "model": model,
                        **({} if language == "auto" else {"language": language}),
                    },
                )
                if resp.status_code == 404:
                    logger.error(
                        "[STT/nvidia] 404 at %s — this is not a Speech NIM. "
                        "Model=%s. Configure voice.stt_base_url to a real ASR "
                        "NIM, or switch STT provider to groq/openai.",
                        target_url,
                        model,
                    )
                    return None
                resp.raise_for_status()
                result = resp.json()
                text = result.get("text", "")
                if not text and "transcriptions" in result:
                    transcriptions = result["transcriptions"]
                    if transcriptions:
                        text = transcriptions[0].get("text", "")
                text = (text or "").strip()
                if text:
                    logger.info("[STT/nvidia] Transcribed via %s: %.100s", model, text)
                return text or None
        except Exception:
            logger.exception("[STT/nvidia] Failed url=%s model=%s", target_url, model)
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

        model_size = _get_configured_stt_model("faster-whisper") or os.environ.get("WHISPER_MODEL", "base")
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
