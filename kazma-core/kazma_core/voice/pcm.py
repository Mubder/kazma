"""PCM → WAV container helper.

The VAD yields raw signed 16-bit little-endian PCM. STT providers expect a
real container: ``audio_format="wav"`` sends the bytes as a multipart
``audio.wav`` / ``audio/wav`` part, and a strict provider rejects (or
mis-sniffs) bare PCM bytes with no RIFF header. Never label PCM as WAV
without wrapping it here first.
"""

from __future__ import annotations

import io
import wave

__all__ = ["MIN_SPEECH_SECONDS", "pcm16le_duration_seconds", "pcm16le_to_wav"]

#: Below this, a VAD segment is a click / mic-open pop, not speech.
MIN_SPEECH_SECONDS = 0.4


def pcm16le_to_wav(pcm: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """Wrap raw 16-bit little-endian PCM in a canonical RIFF/WAVE header.

    Args:
        pcm: Raw PCM bytes (signed 16-bit LE, interleaved when stereo).
        sample_rate: Samples per second (validated to a positive int).
        channels: Channel count (validated to a positive int).

    Returns:
        A complete WAV file: 44-byte PCM-format header + ``pcm``.
    """
    try:
        rate = int(sample_rate)
    except (TypeError, ValueError):
        rate = 16000
    if rate <= 0:
        rate = 16000
    try:
        ch = int(channels)
    except (TypeError, ValueError):
        ch = 1
    if ch <= 0:
        ch = 1

    # stdlib `wave` writes the canonical PCM header providers sniff.
    # A hand-rolled RIFF that disagreed with the payload (odd sizes, a
    # 48 kHz stream labelled 16 kHz) is what made live STT 400 while
    # hold-to-record WebM succeeded.
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(ch)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def pcm16le_duration_seconds(
    pcm: bytes, sample_rate: int = 16000, channels: int = 1
) -> float:
    """Duration of raw 16-bit PCM in seconds (0.0 on bad inputs)."""
    try:
        rate = int(sample_rate)
        ch = int(channels)
    except (TypeError, ValueError):
        return 0.0
    if rate <= 0 or ch <= 0:
        return 0.0
    bytes_per_sec = rate * ch * 2
    if bytes_per_sec <= 0:
        return 0.0
    return len(pcm) / float(bytes_per_sec)
