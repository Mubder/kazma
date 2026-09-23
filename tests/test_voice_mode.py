"""Voice is turn-based; Silero is opt-in."""

from __future__ import annotations

import pytest

from kazma_core.voice.mode import get_vad, realtime_available, voice_product_mode
from kazma_core.voice.vad import EnergyVAD


def test_product_is_turn_based(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(
        "kazma_core.voice.livekit._store_get", lambda *a, **k: ""
    )
    assert voice_product_mode() == "turn_based"


def test_realtime_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAZMA_VOICE_REALTIME", raising=False)
    for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(
        "kazma_core.voice.livekit._store_get", lambda *a, **k: ""
    )
    assert realtime_available() is False


def test_default_vad_is_energy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAZMA_SILERO_VAD", raising=False)
    vad = get_vad()
    assert isinstance(vad, EnergyVAD)
