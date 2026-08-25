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


def test_realtime_codec_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.voice.realtime_codec import (
        codec_backend,
        realtime_codec_enabled,
        realtime_providers_skipped,
    )

    monkeypatch.delenv("KAZMA_REALTIME_CODEC", raising=False)
    assert realtime_codec_enabled() is False
    assert codec_backend() == "none"
    assert "openai_realtime" in realtime_providers_skipped()
    assert "gemini_live" in realtime_providers_skipped()


def test_realtime_codec_rest_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.voice.realtime_codec import codec_backend, codec_status

    monkeypatch.setenv("KAZMA_REALTIME_CODEC", "1")
    assert codec_backend() == "stt_tts_rest"
    st = codec_status()
    assert st["brain"] == "langgraph"
    assert "openai_realtime" in st["skipped_providers"]


def test_default_vad_is_energy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAZMA_SILERO_VAD", raising=False)
    vad = get_vad()
    assert isinstance(vad, EnergyVAD)
