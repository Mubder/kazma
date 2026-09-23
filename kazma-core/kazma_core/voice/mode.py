"""Voice product mode — turn-based notes, not LiveKit/Realtime duplex.

Energy VAD is the implementation. ``KAZMA_SILERO_VAD=1`` opts into a better
frame classifier: WebRTC VAD when ``webrtcvad`` is installed. Silero needs a
vendored model file (an ONNX artifact shipped with Kazma) — the old path
downloaded ``snakers4/silero-vad`` from GitHub at runtime and then fed an
inner EnergyVAD without ever scoring the model (a placebo). It was removed;
when no real classifier is available, ``get_vad()`` logs once and returns
:class:`EnergyVAD`. Kill-switch for the upgrade: omit the env flag.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from kazma_core.voice.vad import EnergyVAD

logger = logging.getLogger(__name__)

__all__ = [
    "get_vad",
    "realtime_available",
    "voice_product_mode",
]

TURN_BASED = "turn_based"
DUPLEX = "duplex"

# webrtcvad only accepts these frame durations / sample rates.
_WEBRTC_FRAME_DURATIONS = (0.01, 0.02, 0.03)
_WEBRTC_SAMPLE_RATES = (8000, 16000, 32000, 48000)

_silero_noop_warned = False


def voice_product_mode() -> str:
    """``duplex`` when LiveKit credentials are set; else turn-based notes."""
    try:
        from kazma_core.voice.livekit import voice_duplex_enabled

        if voice_duplex_enabled():
            return DUPLEX
    except Exception:
        pass
    return TURN_BASED


def realtime_available() -> bool:
    """True when LiveKit duplex is configured (credentials + kill-switch).

    OpenAI Realtime and Gemini Live are never used: they own a tool loop and
    cannot stay codec-only without bypassing HITL, so voice always runs on the
    REST STT/TTS mouths. There is no switch for this (a ``KAZMA_REALTIME_CODEC``
    flag was documented but never read; removed 2026-09-23).
    ``tests/test_static_gates.py`` keeps those APIs out of product code.
    """
    try:
        from kazma_core.voice.livekit import voice_duplex_enabled

        return voice_duplex_enabled()
    except Exception:
        return False


def get_vad(**kwargs: Any) -> Any:
    """Return WebRTC VAD when opted in and installed, else :class:`EnergyVAD`.

    ``KAZMA_SILERO_VAD=1`` is the historical opt-in flag name; it selects the
    best available frame classifier. Silero itself requires a vendored model
    artifact that Kazma does not ship yet — a missing classifier is logged
    ONCE and Energy VAD is returned honestly (never a wrapper that loads a
    model and then ignores it).
    """
    flag = os.environ.get("KAZMA_SILERO_VAD", "").strip().lower()
    if flag in ("1", "true", "on", "yes"):
        webrtc = _try_webrtc(**kwargs)
        if webrtc is not None:
            return webrtc
        global _silero_noop_warned
        if not _silero_noop_warned:
            _silero_noop_warned = True
            logger.info(
                "[voice] KAZMA_SILERO_VAD is set but no classifier is available "
                "(pip install webrtcvad). Using energy VAD — the flag does "
                "nothing until a Silero model ships vendored with Kazma."
            )
    return EnergyVAD(**kwargs)


def _try_webrtc(**kwargs: Any) -> Any | None:
    """Real ``webrtcvad`` adapter, or None when unusable.

    None (not a fake) when the package is missing, the frame duration is not
    a webrtcvad-supported size, or the sample rate is outside its 8/16/32/48
    kHz support — e.g. the 24 kHz client rate is legal for Kazma but not for
    webrtcvad, so that combination falls back to EnergyVAD.
    """
    try:
        import webrtcvad  # noqa: F401
    except ImportError:
        return None
    kwargs = dict(kwargs)
    sample_rate = int(kwargs.pop("sample_rate", 16000) or 16000)
    frame_duration = float(kwargs.get("frame_duration", 0.03) or 0.03)
    if sample_rate not in _WEBRTC_SAMPLE_RATES:
        return None
    if frame_duration not in _WEBRTC_FRAME_DURATIONS:
        kwargs["frame_duration"] = 0.03
    return _WebRtcVAD(sample_rate=sample_rate, **kwargs)


class _WebRtcVAD(EnergyVAD):
    """EnergyVAD's segment state machine with webrtcvad frame classification."""

    def __init__(self, sample_rate: int = 16000, **kwargs: Any) -> None:
        import webrtcvad

        super().__init__(sample_rate=sample_rate, **kwargs)
        # Aggressiveness 3 = most aggressive filtering (best for speech over
        # background noise); the segment thresholds still come from EnergyVAD.
        self._vad = webrtcvad.Vad(3)

    def _frame_is_speech(self, frame: bytes) -> bool:
        try:
            return self._vad.is_speech(bytes(frame), self._sample_rate)
        except Exception:
            # Malformed frame for webrtcvad — fall back to energy, never
            # drop the frame silently.
            return super()._frame_is_speech(frame)
