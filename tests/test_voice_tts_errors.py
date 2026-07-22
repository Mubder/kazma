"""TTS provider error handling (503-config vs 502-runtime).

Validates the improvement to /api/voice/tts: a missing dependency or API key
(misconfiguration) is surfaced as a 503 with an actionable install/config hint,
while a transient runtime failure stays a 502. Both still return None from
``synthesize`` (back-compat for WS/telegram callers).
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_missing_dependency_records_config_error() -> None:
    """edge-tts missing → synthesize returns None + a config TTSError with hint."""
    from kazma_core.voice import tts

    # Simulate edge-tts not being importable
    import builtins

    real_import = builtins.__import__

    def _block_edge_tts(name, *args, **kwargs):
        if name == "edge_tts":
            raise ImportError("simulated: no module named 'edge_tts'")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = _block_edge_tts
    try:
        audio = await tts.synthesize("hello world", provider="edgetts")
    finally:
        builtins.__import__ = real_import

    assert audio is None  # back-compat contract preserved
    err = tts.get_last_error()
    assert err is not None
    assert err.is_config is True
    assert "pip install edge-tts" in err.hint


@pytest.mark.asyncio
async def test_missing_api_key_records_config_error() -> None:
    """OpenAI provider with no key → config error, not a bare runtime failure."""
    from kazma_core.voice import tts

    audio = await tts.synthesize("hello", provider="openai")
    assert audio is None
    err = tts.get_last_error()
    assert err is not None
    assert err.is_config is True
    assert "OPENAI_API_KEY" in err.hint or "openai" in err.hint.lower()


@pytest.mark.asyncio
async def test_runtime_failure_is_not_config() -> None:
    """A provider whose underlying call throws (not a dep/key issue) is runtime."""
    from kazma_core.voice import tts

    # Register a provider that raises a non-config error mid-synthesis
    async def _broken(text, *, voice="default", api_key=None, output_format="mp3"):
        raise RuntimeError("network exploded")

    original = tts.get_tts_provider("edgetts")
    tts.register_tts_provider("edgetts", _broken)
    try:
        audio = await tts.synthesize("hello", provider="edgetts")
    finally:
        tts.register_tts_provider("edgetts", original)

    assert audio is None
    err = tts.get_last_error()
    assert err is not None
    assert err.is_config is False
    assert "network exploded" in str(err)


@pytest.mark.asyncio
async def test_last_error_resets_on_success() -> None:
    """A successful call clears the last error so it doesn't leak to a later 502."""
    from kazma_core.voice import tts

    # First, force a config error
    import builtins

    real_import = builtins.__import__

    def _block(name, *args, **kwargs):
        if name == "edge_tts":
            raise ImportError("nope")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = _block
    try:
        await tts.synthesize("fail", provider="edgetts")
    finally:
        builtins.__import__ = real_import
    assert tts.get_last_error() is not None

    # Now register a provider that succeeds
    async def _ok(text, *, voice="default", api_key=None, output_format="mp3"):
        return b"FAKE_AUDIO_BYTES"

    tts.register_tts_provider("edgetts", _ok)
    audio = await tts.synthesize("ok now", provider="edgetts")
    assert audio == b"FAKE_AUDIO_BYTES"
    # last_error must be cleared after a successful call
    assert tts.get_last_error() is None


@pytest.mark.asyncio
async def test_unknown_provider_falls_back_then_errors() -> None:
    """A completely unknown provider falls back to edgetts; if that also has
    no providers, records a config error."""
    from kazma_core.voice import tts

    # Temporarily clear the registry
    saved = dict(tts._providers)
    tts._providers.clear()
    try:
        audio = await tts.synthesize("hello", provider="nonexistent")
        assert audio is None
        err = tts.get_last_error()
        assert err is not None
        assert err.is_config is True
    finally:
        tts._providers.update(saved)
