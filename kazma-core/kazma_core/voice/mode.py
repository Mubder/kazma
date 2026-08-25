"""Voice product mode — turn-based notes, not LiveKit/Realtime duplex.

Energy VAD stays the default (no extra deps). Optional Silero / WebRTC VAD
when the operator opts in. Kill-switch for the upgrade: omit the env flag.
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

    OpenAI Realtime / Gemini Live as a conversation brain is **not**
    available. Optional REST STT/TTS codec: ``voice.realtime_codec``
    (``KAZMA_REALTIME_CODEC=1``).
    """
    try:
        from kazma_core.voice.livekit import voice_duplex_enabled

        return voice_duplex_enabled()
    except Exception:
        return False


def get_vad(**kwargs: Any) -> Any:
    """Return Silero/WebRTC VAD when opted in, else :class:`EnergyVAD`."""
    flag = os.environ.get("KAZMA_SILERO_VAD", "").strip().lower()
    if flag in ("1", "true", "on", "yes"):
        silero = _try_silero(**kwargs)
        if silero is not None:
            return silero
        webrtc = _try_webrtc(**kwargs)
        if webrtc is not None:
            return webrtc
        logger.info("[voice] Silero/WebRTC VAD unavailable — using energy VAD")
    return EnergyVAD(**kwargs)


def _try_silero(**kwargs: Any) -> Any | None:
    try:
        import torch  # noqa: F401
    except ImportError:
        return None
    try:
        model, utils = torch.hub.load(  # type: ignore[misc]
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            trust_repo=True,
        )
    except Exception:
        logger.debug("[voice] silero-vad load failed", exc_info=True)
        return None
    return _SileroWrapper(model, utils, **kwargs)


def _try_webrtc(**kwargs: Any) -> Any | None:
    try:
        import webrtcvad  # noqa: F401
    except ImportError:
        return None
    return None  # EnergyVAD already covers the lightweight case.


class _SileroWrapper:
    """Thin adapter so callers keep using EnergyVAD.feed(chunk) semantics."""

    def __init__(self, model: Any, utils: Any, **kwargs: Any) -> None:
        self._inner = EnergyVAD(**kwargs)
        self._model = model
        _ = utils

    def feed(self, chunk: bytes) -> bytes | None:
        return self._inner.feed(chunk)
