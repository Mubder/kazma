"""Unit tests for provider presets."""

from __future__ import annotations

from kazma_core.providers import (
    GEMINI_MODELS,
    PROVIDER_PRESETS,
    get_base_url,
    get_preset,
    list_providers,
)


def test_openai_preset():
    p = get_preset("openai")
    assert p is not None
    assert "openai.com" in p["base_url"]
    assert get_base_url("openai") == p["base_url"]


def test_unknown_preset():
    assert get_preset("nope") is None
    assert get_base_url("nope") == ""


def test_list_providers_has_local_and_cloud():
    keys = {p["key"] for p in list_providers()}
    assert "openai" in keys
    assert "ollama" in keys
    assert "lm-studio" in keys
    assert "google" in keys


def test_gemini_models_nonempty():
    assert len(GEMINI_MODELS) >= 1
    assert all(m.startswith("gemini") for m in GEMINI_MODELS)


def test_all_presets_have_name():
    for key, val in PROVIDER_PRESETS.items():
        assert "name" in val, key
        assert "base_url" in val, key
