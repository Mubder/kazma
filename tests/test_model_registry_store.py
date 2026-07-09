"""Unit tests for model_registry_store helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from kazma_core.model_registry_store import (
    default_provider_entries,
    normalize_models,
    normalize_provider_entry,
    parse_providers_raw,
    save_providers,
    seed_missing_presets,
)


def test_normalize_models_dedupes_and_sorts():
    assert normalize_models(["b", "a", "a", "  "]) == ["a", "b"]
    assert normalize_models("not-a-list") == []


def test_normalize_provider_entry_defaults():
    e = normalize_provider_entry({"name": " openai ", "models": ["m1"]})
    assert e["name"] == "openai"
    assert e["display_name"] == "openai"
    assert e["enabled"] is True
    assert e["location"] == "us-central1"
    assert e["models"] == ["m1"]


def test_parse_providers_raw_list():
    raw = [{"name": "x", "models": ["a"]}]
    out = parse_providers_raw(raw)
    assert len(out) == 1
    assert out[0]["name"] == "x"


def test_parse_providers_raw_legacy_string():
    import json

    raw = json.dumps([{"name": "legacy"}])
    out = parse_providers_raw(raw)
    assert out[0]["name"] == "legacy"


def test_parse_providers_raw_bad_string():
    assert parse_providers_raw("{not-json") == []


def test_save_providers_uses_category():
    store = MagicMock()
    save_providers(store, [{"name": "a"}])
    store.set.assert_called_once_with(
        "providers.list", [{"name": "a"}], category="providers"
    )


def test_default_provider_entries_has_openai():
    entries = default_provider_entries()
    names = {e["name"] for e in entries}
    assert "openai" in names
    assert "custom" not in names


def test_seed_missing_presets_adds_new():
    stored, changed = seed_missing_presets([])
    assert changed is True
    assert any(p["name"] == "openai" for p in stored)
    stored2, changed2 = seed_missing_presets(list(stored))
    assert changed2 is False
