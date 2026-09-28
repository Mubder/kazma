"""What synthesized audio actually is, read from its first bytes.

Every place that hands speech to someone -- the web read-aloud route, and the
Telegram, Discord and Slack voice replies -- labelled the bytes with the
format Settings ASKED for (``voice.tts_output_format``). Providers do not all
honour it: edge-tts (the default voice) always returns MP3. On the live
install (2026-09-28) Settings said ``opus``, so the web answered MP3 as
``audio/opus`` and Telegram uploaded it as ``reply.ogg`` / ``audio/ogg``.
Chrome sniffs and played it anyway; a stricter client (Safari, a chat
platform's player) is entitled not to. The label now follows the bytes.
"""

from __future__ import annotations

__all__ = ["audio_label"]

# Requested format -> (file extension, MIME type), for bytes nothing below
# recognises. "opus" is Opus in an Ogg file, which is what the name means on
# disk; "audio/opus" is not a file container type.
_BY_REQUEST: dict[str, tuple[str, str]] = {
    "mp3": ("mp3", "audio/mpeg"),
    "mpeg": ("mp3", "audio/mpeg"),
    "opus": ("ogg", "audio/ogg"),
    "ogg": ("ogg", "audio/ogg"),
    "wav": ("wav", "audio/wav"),
    "flac": ("flac", "audio/flac"),
    "aac": ("aac", "audio/aac"),
    "m4a": ("m4a", "audio/mp4"),
    "webm": ("webm", "audio/webm"),
}


def _sniff_audio(data: bytes | None) -> tuple[str, str] | None:
    """``(extension, mime)`` of the container *data* starts with, or None."""
    head = bytes(data or b"")[:12]
    if len(head) < 4:
        return None
    if head.startswith(b"ID3"):
        return ("mp3", "audio/mpeg")
    if head.startswith(b"OggS"):
        return ("ogg", "audio/ogg")
    if head.startswith(b"fLaC"):
        return ("flac", "audio/flac")
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return ("wav", "audio/wav")
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return ("webm", "audio/webm")
    if head[4:8] == b"ftyp":
        return ("m4a", "audio/mp4")
    if head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        # A frame sync: layer bits 00 are AAC in ADTS, anything else MPEG
        # audio (MP3 is layer III).
        return ("aac", "audio/aac") if (head[1] & 0x06) == 0 else ("mp3", "audio/mpeg")
    return None


def audio_label(data: bytes | None, requested: str | None = None) -> tuple[str, str]:
    """``(extension, mime)`` to send *data* under: what it is, else what was
    asked for, else MP3 (the default every provider can make)."""
    sniffed = _sniff_audio(data)
    if sniffed:
        return sniffed
    return _BY_REQUEST.get(str(requested or "").strip().lower(), ("mp3", "audio/mpeg"))
