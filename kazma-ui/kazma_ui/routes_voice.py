"""Voice API — STT and TTS endpoints for the Web UI.

Provides:
  POST /api/voice/stt  — Transcribe audio bytes to text
  POST /api/voice/tts  — Synthesize text to audio bytes
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.post("/stt")
async def speech_to_text(
    file: UploadFile = File(...),
    provider: str = Form("openai"),
    language: str = Form("auto"),
) -> dict[str, Any]:
    """Transcribe an audio file to text.

    Accepts any audio format (ogg, mp3, wav, flac, webm, m4a).
    Returns ``{"text": "..."}`` on success.
    """
    from kazma_core.config_store import get_config_store
    from kazma_core.voice.stt import transcribe
    import traceback
    import os
    from pathlib import Path

    cs = get_config_store()
    db_provider = cs.get("voice.stt_provider")
    if db_provider and str(db_provider).strip() and str(db_provider).strip().lower() != "none":
        provider = str(db_provider)

    db_language = cs.get("voice.stt_language")
    if db_language and str(db_language).strip() and str(db_language).strip().lower() != "none":
        language = str(db_language)

    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")

    # Detect format from filename/Content-Type
    ext = "ogg"
    if file.filename:
        ext = file.filename.rsplit(".", 1)[-1] if "." in file.filename else "ogg"
    elif file.content_type:
        ext = file.content_type.split("/")[-1].split(";")[0]

    try:
        text = await transcribe(
            audio_bytes,
            provider=provider,
            language=language,
            audio_format=ext,
        )
        if text is None:
            log_dir = Path("kazma-data")
            log_dir.mkdir(exist_ok=True)
            with open(log_dir / "stt_error.log", "a", encoding="utf-8") as f:
                f.write(f"\n--- STT FAILURE IN ENDPOINT: provider={provider}, format={ext}, bytes={len(audio_bytes)}, filename={file.filename}, content_type={file.content_type} ---\n")
                f.write(f"OPENAI_API_KEY set in env: {bool(os.environ.get('OPENAI_API_KEY'))}\n")
                f.write(f"GROQ_API_KEY set in env: {bool(os.environ.get('GROQ_API_KEY'))}\n")
                f.write(f"NVIDIA_API_KEY set in env: {bool(os.environ.get('NVIDIA_API_KEY'))}\n")
                from kazma_core.config_store import get_config_store
                cs = get_config_store()
                f.write(f"voice.stt_provider in DB: {cs.get('voice.stt_provider')}\n")
                f.write(f"voice.stt_model in DB: {cs.get('voice.stt_model')}\n")
                f.write("transcribe returned None (check API keys or logs above)\n")
            raise HTTPException(status_code=502, detail=f"STT provider '{provider}' failed")
        return {"text": text, "provider": provider}
    except Exception as e:
        if not isinstance(e, HTTPException):
            log_dir = Path("kazma-data")
            log_dir.mkdir(exist_ok=True)
            with open(log_dir / "stt_error.log", "a", encoding="utf-8") as f:
                f.write(f"\n--- STT EXCEPTION IN ENDPOINT: provider={provider}, format={ext}, bytes={len(audio_bytes)}, filename={file.filename}, content_type={file.content_type} ---\n")
                f.write(f"OPENAI_API_KEY set in env: {bool(os.environ.get('OPENAI_API_KEY'))}\n")
                f.write(f"GROQ_API_KEY set in env: {bool(os.environ.get('GROQ_API_KEY'))}\n")
                f.write(f"NVIDIA_API_KEY set in env: {bool(os.environ.get('NVIDIA_API_KEY'))}\n")
                from kazma_core.config_store import get_config_store
                cs = get_config_store()
                f.write(f"voice.stt_provider in DB: {cs.get('voice.stt_provider')}\n")
                f.write(f"voice.stt_model in DB: {cs.get('voice.stt_model')}\n")
                f.write(traceback.format_exc())
            raise HTTPException(status_code=500, detail=str(e))
        raise e


@router.post("/tts")
async def text_to_speech(
    text: str = Form(...),
    provider: str = Form("edgetts"),
    voice: str = Form("default"),
    output_format: str = Form("mp3"),
) -> Response:
    """Synthesize text to audio.

    Returns raw audio bytes with the appropriate content type.
    """
    from kazma_core.config_store import get_config_store
    from kazma_core.voice.tts import synthesize

    cs = get_config_store()
    db_provider = cs.get("voice.tts_provider")
    if db_provider and str(db_provider).strip() and str(db_provider).strip().lower() != "none":
        provider = str(db_provider)

    db_voice = cs.get("voice.tts_voice")
    if db_voice and str(db_voice).strip() and str(db_voice).strip().lower() != "none":
        voice = str(db_voice)

    db_output_format = cs.get("voice.tts_output_format")
    if db_output_format and str(db_output_format).strip() and str(db_output_format).strip().lower() != "none":
        output_format = str(db_output_format)

    if not text.strip():
        raise HTTPException(status_code=400, detail="Empty text")

    audio = await synthesize(
        text,
        provider=provider,
        voice=voice,
        output_format=output_format,
    )
    if audio is None:
        raise HTTPException(status_code=502, detail=f"TTS provider '{provider}' failed")

    content_type = {
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "opus": "audio/opus",
        "flac": "audio/flac",
        "ogg": "audio/ogg",
    }.get(output_format, "audio/mpeg")

    return Response(content=audio, media_type=content_type)


@router.get("/providers")
async def list_providers() -> dict[str, list[str]]:
    """List available STT and TTS providers."""
    from kazma_core.voice.stt import list_stt_providers
    from kazma_core.voice.tts import list_tts_providers

    return {
        "stt": list_stt_providers(),
        "tts": list_tts_providers(),
    }


@router.get("/status")
async def voice_status() -> dict[str, Any]:
    """Voice readiness — separate from LLM providers (set-and-forget check).

    Returns current STT/TTS settings, whether keys look present, and
    recommended models. Does **not** list chat/LLM model IDs.
    """
    import os

    from kazma_core.config_store import get_config_store
    from kazma_core.voice.stt import list_stt_providers
    from kazma_core.voice.tts import list_tts_providers

    cs = get_config_store()

    def _g(key: str, default: str = "") -> str:
        val = cs.get(key)
        if val is None or str(val).strip().lower() in ("", "none"):
            return default
        return str(val).strip()

    stt = _g("voice.stt_provider", "openai")
    tts = _g("voice.tts_provider", "edgetts")
    enabled_raw = cs.get("voice.enabled")
    enabled = True if enabled_raw is None else str(enabled_raw).lower() in (
        "1", "true", "yes", "on",
    )

    def _key_present(provider: str) -> bool:
        env_map = {
            "openai": ("OPENAI_API_KEY",),
            "groq": ("GROQ_API_KEY",),
            "cohere": ("COHERE_API_KEY",),
            "nvidia": ("NVIDIA_API_KEY", "NGC_API_KEY"),
        }
        for env in env_map.get(provider, ()):
            if os.environ.get(env):
                return True
        try:
            from kazma_core.model_registry import ModelRegistry

            entry = ModelRegistry(cs).get_provider(provider)
            if entry and entry.get("api_key"):
                return True
        except Exception:
            pass
        # Free / local providers need no key
        if provider in ("edgetts", "faster-whisper", "kokoro", "coqui"):
            return True
        return False

    return {
        "enabled": enabled,
        "note": (
            "Voice (STT/TTS) is independent of chat LLM providers. "
            "Use Speech-to-Text models (e.g. whisper), not chat models "
            "(e.g. llama / gpt). NVIDIA ASR/TTS use modality-specific endpoints."
        ),
        "stt": {
            "provider": stt,
            "model": _g("voice.stt_model", "default"),
            "language": _g("voice.stt_language", "auto"),
            "key_present": _key_present(stt),
            "available_providers": list_stt_providers(),
            "recommended_models": {
                "openai": ["whisper-1"],
                "groq": ["whisper-large-v3"],
                "nvidia": ["nvidia/whisper-large-v3", "default"],
                "faster-whisper": ["base", "small", "medium"],
            }.get(stt, ["default"]),
        },
        "tts": {
            "provider": tts,
            "voice": _g("voice.tts_voice", "default"),
            "output_format": _g("voice.tts_output_format", "mp3"),
            "key_present": _key_present(tts),
            "available_providers": list_tts_providers(),
        },
        "ready": enabled and _key_present(stt) and _key_present(tts),
    }


@router.get("/voices")
async def list_voices(provider: str = "edgetts") -> list[str]:
    """Get available voice models/ShortNames for a specific TTS provider."""
    p_lower = provider.strip().lower()
    if p_lower == "openai":
        return ["default", "alloy", "echo", "fable", "onyx", "nova", "shimmer"]
    elif p_lower == "nvidia":
        return [
            "default",
            "Magpie-Multilingual.EN-US.Aria",
            "Magpie-Multilingual.EN-US.Benjamin",
            "Magpie-Multilingual.ES-ES.Alba",
            "Magpie-Multilingual.FR-FR.Denise",
            "Magpie-Multilingual.ZH-CN.Xiaoxiao",
        ]
    elif p_lower == "edgetts":
        try:
            import edge_tts  # type: ignore
            voices = await edge_tts.VoicesManager.create()
            return ["default"] + sorted([v["ShortName"] for v in voices.voices])
        except Exception:
            return [
                "default",
                "en-US-AriaNeural",
                "en-US-GuyNeural",
                "en-GB-SoniaNeural",
                "en-GB-RyanNeural",
                "es-ES-ElviraNeural",
                "fr-FR-DeniseNeural",
                "ar-EG-SalmaNeural",
            ]
    return ["default"]


@router.get("/stt-models")
async def list_stt_models(provider: str = "openai") -> list[str]:
    """Get available STT model IDs for a specific STT provider."""
    p_lower = provider.strip().lower()
    if p_lower == "openai":
        return ["default", "whisper-1"]
    elif p_lower == "groq":
        return ["default", "whisper-large-v3", "distil-whisper-large-v3-en"]
    elif p_lower == "nvidia":
        return ["default", "nvidia/whisper-large-v3"]
    elif p_lower == "faster-whisper":
        return ["default", "tiny", "base", "small", "medium", "large-v3"]
    return ["default"]


