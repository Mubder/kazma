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
    "SPEECH_HOSTS",
    "SPEECH_PATH_ENDS",
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


#: The last part of a speech endpoint's address: OpenAI's (and every
#: OpenAI-shaped server's) ``/audio/transcriptions``, ``/audio/translations``
#: and ``/audio/speech``, Cohere's ``/v2/audio/transcriptions``, Deepgram's
#: ``/listen`` and ``/speak``, ElevenLabs' ``/text-to-speech`` and
#: ``/speech-to-text``, AssemblyAI's ``/transcript``. No chat API's address
#: ends with one of them.
SPEECH_PATH_ENDS: frozenset[str] = frozenset({
    "transcriptions", "translations", "speech", "transcript", "transcripts",
    "listen", "speak", "text-to-speech", "speech-to-text", "asr", "stt", "tts",
})

#: Services that serve speech only, at any path of these hosts.
SPEECH_HOSTS: frozenset[str] = frozenset({"api.deepgram.com", "api.elevenlabs.io", "api.assemblyai.com"})


def _is_speech_address(url: str | None) -> bool:
    """True when *url* is a speech endpoint: a path with an ``audio`` part, or
    ending with a speech endpoint's name (:data:`SPEECH_PATH_ENDS`), or a
    speech-only host (:data:`SPEECH_HOSTS`). Such an address cannot answer a
    chat message, whatever the row says it is."""
    from urllib.parse import urlsplit

    text = str(url or "").strip()
    if not text:
        return False
    try:
        parts = urlsplit(text if "://" in text else "https://" + text)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if host in SPEECH_HOSTS:
        return True
    segments = [s for s in parts.path.lower().split("/") if s]
    return "audio" in segments or bool(segments and segments[-1] in SPEECH_PATH_ENDS)


def row_is_speech(entry: dict | None, discovered: Iterable[str] | None = ()) -> bool:
    """True for a provider row that serves speech, not chat (providers
    stage 2): its address is a speech endpoint (:func:`_is_speech_address`),
    it says so (``kind: speech``), or every model it lists or found is a
    speech model. Rows like "NVIDIA ASR" and "Cohere Transcriptions" were
    added as providers because speech had nowhere else to go, and sat in
    every chat model list.

    The address comes first: live's "Cohere Transcriptions" row
    (``https://api.cohere.com/v2/audio/transcriptions``) lists no model and
    says no kind, so the model rule could not see it (2026-11-08), and no
    ``kind: chat`` makes such an address answer a chat message."""
    if not isinstance(entry, dict):
        return False
    if _is_speech_address(entry.get("base_url")):
        return True
    kind = str(entry.get("kind") or "").strip().lower()
    if kind:
        return kind == "speech"
    ids = [str(m).strip() for m in [*(entry.get("models") or []), *(discovered or ())] if str(m).strip()]
    return bool(ids) and all(is_speech_model(m) for m in ids)
