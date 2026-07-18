"""Voice subsystem — STT and TTS providers for Kazma.

Provides a unified abstraction over cloud and local speech providers:

- **STT** (speech-to-text): OpenAI Whisper, Groq Whisper, Cohere Transcribe,
  NVIDIA NIM ASR, faster-whisper (local), whisper.cpp (local).
- **TTS** (text-to-speech): EdgeTTS (free), OpenAI TTS, NVIDIA NIM TTS,
  Kokoro (local neural).

Usage::

    from kazma_core.voice.stt import transcribe, list_stt_providers
    from kazma_core.voice.tts import synthesize, list_tts_providers
"""

from __future__ import annotations

__all__ = ["list_stt_providers", "list_tts_providers"]
