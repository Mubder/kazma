"""LiveKit duplex tokens + mode (no LiveKit server)."""

from __future__ import annotations

import pytest

from kazma_core.voice.livekit import (
    get_livekit_config,
    mint_livekit_token,
    sanitize_room_name,
    voice_duplex_enabled,
)
from kazma_core.voice.mode import realtime_available, voice_product_mode


def test_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("KAZMA_VOICE_DUPLEX", raising=False)
    monkeypatch.setattr(
        "kazma_core.voice.livekit._store_get", lambda *a, **k: ""
    )
    assert voice_duplex_enabled() is False
    assert voice_product_mode() == "turn_based"
    assert realtime_available() is False


def test_enabled_with_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_URL", "http://127.0.0.1:7880")
    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret-secret-secret-32bytes-min")
    monkeypatch.delenv("KAZMA_VOICE_DUPLEX", raising=False)
    assert voice_duplex_enabled() is True
    assert voice_product_mode() == "duplex"


def test_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_URL", "http://127.0.0.1:7880")
    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret-secret-secret-32bytes-min")
    monkeypatch.setenv("KAZMA_VOICE_DUPLEX", "0")
    assert voice_duplex_enabled() is False


def test_sanitize_room() -> None:
    assert sanitize_room_name("cli-abc 1!") == "cli-abc-1"
    assert sanitize_room_name("") == "default"


def test_mint_token_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "APIxxx")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "super-secret-value-32bytes-min!!")
    token = mint_livekit_token(identity="web-user", room="kazma-web")
    import jwt

    claims = jwt.decode(
        token,
        "super-secret-value-32bytes-min!!",
        algorithms=["HS256"],
        options={"require": ["iss", "sub", "exp"]},
    )
    assert claims["iss"] == "APIxxx"
    assert claims["sub"] == "web-user"
    assert claims["video"]["roomJoin"] is True
    assert claims["video"]["room"] == "kazma-web"


def test_mint_without_config_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIVEKIT_URL", raising=False)
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    monkeypatch.setattr(
        "kazma_core.voice.livekit._store_get", lambda *a, **k: ""
    )
    with pytest.raises(ValueError, match="not configured"):
        mint_livekit_token(identity="x", room="y")


def test_status_hides_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_URL", "http://127.0.0.1:7880")
    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "do-not-leak-this-secret-32b-min")
    from kazma_core.voice.livekit import livekit_status

    st = livekit_status()
    assert "do-not-leak" not in str(st)
    assert st["enabled"] is True
    assert st["brain"] == "langgraph"
    assert st["tts_in_room"] is True


def test_voice_js_publishes_tts_track() -> None:
    from pathlib import Path

    js = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui"
        / "kazma_ui"
        / "static"
        / "js"
        / "voice.js"
    ).read_text(encoding="utf-8")
    assert "publishTrack" in js
    assert "_publishTtsToLiveKit" in js
