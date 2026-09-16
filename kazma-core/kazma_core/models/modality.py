"""Chat vs speech modality.

Speech models (Whisper, transcribe, TTS) share API *keys* with
Settings → Providers. They must never be the chat brain, appear in
chat model pickers, or be sent to ``/chat/completions`` (the Test
alert when a voice model is selected).

Voice STT/TTS models are chosen on Settings → Voice.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable

__all__ = [
    "SPEECH_PATTERNS",
    "chat_models",
    "is_speech_model",
    "speech_models",
]

# Lowercased model-id globs. Deny-list: if it matches, it is speech, not chat.
SPEECH_PATTERNS: tuple[str, ...] = (
    "*whisper*",
    "*transcribe*",
    "*speech-to-text*",
    "*text-to-speech*",
    "tts-1",
    "tts-1-*",
    "tts-*",
    "*-tts",
    "gpt-4o-mini-tts",
    "gpt-4o-audio*",
    "gpt-4o-*-transcribe*",
    "canary-*",
    "parakeet*",
    "kokoro*",
    "*speech*",
)


def is_speech_model(model_id: str | None) -> bool:
    """True when *model_id* is STT/TTS/audio, not a chat completion model."""
    raw = (model_id or "").strip().lower()
    if not raw:
        return False
    return any(fnmatch.fnmatch(raw, pat) for pat in SPEECH_PATTERNS)


def chat_models(ids: Iterable[str] | None) -> list[str]:
    """Keep ids that are safe to send to ``/chat/completions``."""
    out: list[str] = []
    seen: set[str] = set()
    for item in ids or ():
        text = str(item).strip()
        if not text or text in seen or is_speech_model(text):
            continue
        seen.add(text)
        out.append(text)
    return out


def speech_models(ids: Iterable[str] | None) -> list[str]:
    """Keep ids that belong on Settings → Voice, not the chat picker."""
    out: list[str] = []
    seen: set[str] = set()
    for item in ids or ():
        text = str(item).strip()
        if not text or text in seen or not is_speech_model(text):
            continue
        seen.add(text)
        out.append(text)
    return out
