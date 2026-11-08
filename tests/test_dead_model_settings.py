"""Model settings that something reads, or that are gone (providers stage 1,
2026-11-08).

The live install held, beside the model in use (DeepSeek), kazma.yaml's
``llm.model = gpt-4o-mini`` at OpenAI with gpt-4o-mini's prices
(``llm.input_cost_per_1m`` / ``output``), ``models.default`` and
``models.fallback`` = gpt-4o-mini and ``models.router``: the prices, the
fallback and the router were read by nothing; ``models.default`` only added
gpt-4o-mini to the model lists; and ``llm.model`` was the last-resort model
for ANY provider -- gpt-4o-mini sent to DeepSeek. Saved Profiles' "Load"
filled fields no part of the page showed and said "Loaded".
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
GONE = ("llm.input_cost_per_1m", "llm.output_cost_per_1m", "models.default", "models.fallback", "models.router")


def _flat(tree, prefix=""):
    out = {}
    for key, value in (tree or {}).items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flat(value, path + "."))
        else:
            out[path] = value
    return out


def test_the_dead_keys_are_gone_from_kazma_yaml_and_declared_retired():
    from kazma_core.config_defaults import RETIRED_DEFAULTS

    shipped = _flat(yaml.safe_load((ROOT / "kazma.yaml").read_text(encoding="utf-8")))
    assert not [k for k in GONE if k in shipped]
    declared = {entry.key for entry in RETIRED_DEFAULTS}
    assert set(GONE) <= declared


def test_an_install_drops_its_stored_copies_and_keeps_its_own_values(tmp_path):
    from kazma_core.config_store import ConfigStore

    store = ConfigStore(db_path=str(tmp_path / "settings.db"))
    try:
        store.batch_set([("models.fallback", "gpt-4o-mini", "models"), ("models.router", "litellm", "models"),
                         ("llm.input_cost_per_1m", 0.15, "llm"), ("models.default", "my-own-model", "models")])
        shipped = _flat(yaml.safe_load((ROOT / "kazma.yaml").read_text(encoding="utf-8")))
        store.apply_retired_defaults(shipped)
        assert store.get("models.fallback") is None and store.get("models.router") is None
        assert store.get("llm.input_cost_per_1m") is None
        assert store.get("models.default") == "my-own-model"  # a value the owner chose stays
    finally:
        store.close()


def test_llm_model_is_never_another_providers_model(tmp_path):
    from kazma_core.config_store import ConfigStore
    from kazma_core.model_registry import ModelRegistry
    from kazma_core.providers import default_model_for

    store = ConfigStore(db_path=str(tmp_path / "settings.db"))
    try:
        store.set("providers.list", [{"name": "deepseek", "base_url": "https://api.deepseek.com/v1",
                                      "api_key": "sk-dead-settings-test", "models": [], "enabled": True}],
                  category="providers")
        store.batch_set([("registry.active_provider", "deepseek", "registry"),
                         ("registry.active_model", "", "registry"), ("llm.model", "gpt-4o-mini", "llm")])
        client = ModelRegistry(store).get_client()
        assert client.config.model != "gpt-4o-mini"
        assert client.config.model == default_model_for("deepseek")
    finally:
        store.close()


def test_the_model_lists_carry_no_models_default(tmp_path):
    from kazma_core.config_store import ConfigStore
    from kazma_core.model_registry import ModelRegistry

    store = ConfigStore(db_path=str(tmp_path / "settings.db"))
    try:
        store.set("models.default", "stale-default-model", category="models")
        registry = ModelRegistry(store)
        assert "stale-default-model" not in registry.list_unified_options()["models"]
    finally:
        store.close()


def test_a_saved_profile_is_used_not_loaded_into_nothing():
    js = (ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "settings_hub.js").read_text(encoding="utf-8")
    start = js.index("async loadHubProfile(name)")
    body = js[start:js.index("async saveHubProfile()", start)]
    assert "/api/settings/active_model" in body and "this.currentModel" not in body
    assert "loadModelProfile" not in js  # the copy no page called
