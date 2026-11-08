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
    "row_is_speech",
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
    # The id with its vendor prefix, and without it: "nvidia/parakeet-ctc"
    # and "nvidia/canary-1b" are speech too (a glob must match the whole id,
    # so "parakeet*" missed them; providers stage 2).
    tail = raw.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(raw, pat) or fnmatch.fnmatch(tail, pat) for pat in SPEECH_PATTERNS)


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


def row_is_speech(entry: dict | None, discovered: Iterable[str] | None = ()) -> bool:
    """True for a provider row that serves speech, not chat (providers
    stage 2): it says so (``kind: speech``), or every model it lists or
    found is a speech model. Rows like "NVIDIA ASR" and "Cohere
    Transcriptions" were added as providers because speech had nowhere else
    to go, and sat in every chat model list."""
    if not isinstance(entry, dict):
        return False
    kind = str(entry.get("kind") or "").strip().lower()
    if kind:
        return kind == "speech"
    ids = [str(m).strip() for m in [*(entry.get("models") or []), *(discovered or ())] if str(m).strip()]
    return bool(ids) and all(is_speech_model(m) for m in ids)
