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
    from kazma_core.voice.stt import transcribe

    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")

    # Detect format from filename/Content-Type
    ext = "ogg"
    if file.filename:
        ext = file.filename.rsplit(".", 1)[-1] if "." in file.filename else "ogg"
    elif file.content_type:
        ext = file.content_type.split("/")[-1].split(";")[0]

    text = await transcribe(
        audio_bytes,
        provider=provider,
        language=language,
        audio_format=ext,
    )
    if text is None:
        raise HTTPException(status_code=502, detail=f"STT provider '{provider}' failed")
    return {"text": text, "provider": provider}


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
    from kazma_core.voice.tts import synthesize

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


