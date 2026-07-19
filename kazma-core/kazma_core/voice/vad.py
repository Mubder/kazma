"""Voice Activity Detection (VAD) — energy-based speech segmenter.

Detects speech segments in raw audio chunks using simple energy
thresholding. When the energy drops below the threshold for a
configured silence duration, the segment is considered complete
and can be sent to STT.

This is a lightweight, dependency-free VAD. For higher accuracy,
consider Silero VAD or web-vad (browser-side).

Usage::

    vad = EnergyVAD(sample_rate=16000, silence_threshold=0.01,
                    silence_duration=1.5)
    for chunk in audio_chunks:
        segment = vad.feed(chunk)
        if segment:
            # complete speech segment ready for STT
            transcribe(segment)
"""

from __future__ import annotations

import logging
from collections import deque

__all__ = ["EnergyVAD", "AudioBuffer"]

logger = logging.getLogger(__name__)


class AudioBuffer:
    """Accumulates raw audio bytes until a complete segment is detected."""

    def __init__(self, sample_rate: int = 16000, max_duration: float = 30.0) -> None:
        self._sample_rate = sample_rate
        self._max_bytes = int(sample_rate * 2 * max_duration)  # 16-bit mono
        self._buf = bytearray()

    def append(self, audio: bytes) -> None:
        """Append audio bytes, respecting the max buffer size."""
        space = self._max_bytes - len(self._buf)
        if space > 0:
            self._buf.extend(audio[:space])

    def drain(self) -> bytes:
        """Return accumulated audio and clear the buffer."""
        data = bytes(self._buf)
        self._buf.clear()
        return data

    @property
    def duration(self) -> float:
        """Current buffered duration in seconds."""
        return len(self._buf) / (self._sample_rate * 2)

    @property
    def is_full(self) -> bool:
        """True when the buffer has reached max duration."""
        return len(self._buf) >= self._max_bytes


class EnergyVAD:
    """Energy-based Voice Activity Detector.

    Computes short-term energy of incoming PCM 16-bit mono audio.
    When energy exceeds ``speech_threshold``, speech is considered
    active. When it drops below ``silence_threshold`` for
    ``silence_duration`` seconds, the segment is complete.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_duration: float = 0.03,  # 30ms frames
        silence_threshold: float = 0.01,  # RMS energy threshold
        speech_threshold: float = 0.015,
        silence_duration: float = 1.5,  # seconds of silence to end segment
        min_speech_duration: float = 0.5,  # ignore segments shorter than this
        pre_speech_buffer: float = 0.3,  # keep 300ms before speech starts
    ) -> None:
        self._sample_rate = sample_rate
        self._frame_size = int(sample_rate * frame_duration)
        self._silence_threshold = silence_threshold
        self._speech_threshold = speech_threshold
        self._silence_frames = int(silence_duration / frame_duration)
        self._min_speech_frames = int(min_speech_duration / frame_duration)
        self._pre_speech_frames = int(pre_speech_buffer / frame_duration)

        self._speech_buffer = bytearray()
        self._pre_buffer: deque[bytes] = deque(maxlen=self._pre_speech_frames)
        self._is_speaking = False
        self._silence_count = 0
        self._speech_frame_count = 0

    @staticmethod
    def _compute_rms(samples: bytes) -> float:
        """Compute RMS energy of 16-bit signed PCM samples."""
        import struct

        if len(samples) < 2:
            return 0.0
        # Unpack 16-bit signed integers
        count = len(samples) // 2
        total = 0
        for i in range(count):
            val = struct.unpack_from("<h", samples, i * 2)[0]
            total += val * val
        # Normalize to [0, 1] (32768 = max for 16-bit)
        rms = (total / count) ** 0.5 / 32768.0
        return rms

    def feed(self, audio_chunk: bytes) -> bytes | None:
        """Feed a chunk of audio bytes and return a complete segment if detected.

        Args:
            audio_chunk: Raw 16-bit PCM mono audio bytes.

        Returns:
            Complete speech segment bytes when silence detected, otherwise None.
        """
        offset = 0
        while offset + self._frame_size * 2 <= len(audio_chunk):
            frame = audio_chunk[offset:offset + self._frame_size * 2]
            offset += self._frame_size * 2

            rms = self._compute_rms(frame)

            if rms > self._speech_threshold:
                if not self._is_speaking:
                    # Speech started — flush pre-buffer into speech buffer
                    self._is_speaking = True
                    self._speech_frame_count = 0
                    self._silence_count = 0
                    self._speech_buffer.clear()
                    for pre_frame in self._pre_buffer:
                        self._speech_buffer.extend(pre_frame)
                self._speech_buffer.extend(frame)
                self._speech_frame_count += 1
                self._silence_count = 0
            elif self._is_speaking:
                # In speech but this frame is silent
                self._speech_buffer.extend(frame)
                self._silence_count += 1
                if self._silence_count >= self._silence_frames:
                    # Silence long enough — segment complete
                    self._is_speaking = False
                    if self._speech_frame_count >= self._min_speech_frames:
                        segment = bytes(self._speech_buffer)
                        self._speech_buffer.clear()
                        self._speech_frame_count = 0
                        self._silence_count = 0
                        return segment
                    # Segment too short — discard
                    self._speech_buffer.clear()
                    self._speech_frame_count = 0
                    self._silence_count = 0
            else:
                # Not speaking and frame is silent — add to pre-buffer
                self._pre_buffer.append(frame)

        return None

    @property
    def is_speaking(self) -> bool:
        """True when speech is currently being detected."""
        return self._is_speaking

    def reset(self) -> None:
        """Reset all state."""
        self._speech_buffer.clear()
        self._pre_buffer.clear()
        self._is_speaking = False
        self._silence_count = 0
        self._speech_frame_count = 0
