"""The health probe must send a model the provider actually serves.

Reported from a live install: Test reached the provider (model list answered in
358 ms) and then said *"Reachable, but chat is failing — no model selected"*.
The provider was fine. The probe read `provider["model"]`, which is empty for
any provider configured the normal way — Discover, then tick the models you
want — so it sent nothing and reported the resulting failure as a verdict about
the provider.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def registry(tmp_path):
    from kazma_core.config_store import ConfigStore
    from kazma_core.model_registry import ModelRegistry

    store = ConfigStore(
        db_path=str(tmp_path / "pm.db"), yaml_path=str(tmp_path / "none.yaml")
    )
    reg = ModelRegistry(store)
    reg.upsert_provider(
        {"name": "deepseek", "base_url": "https://api.deepseek.com/v1", "api_key": "k"}
    )
    return reg


class TestProbeModelSelection:
    def test_the_row_does_not_even_store_a_model_field(self, registry):
        """Why the fallbacks are load-bearing rather than a nicety.

        `normalize_provider_entry` defines the canonical provider shape and it
        has no `model` key, so a stored provider NEVER has one. The probe read
        exactly that field, which means it sent no model for any provider at
        all -- every Test that got past the key check reported "chat failing".
        """
        registry.upsert_provider({"name": "deepseek", "model": "deepseek-reasoner"})
        assert "model" not in (registry.get_provider("deepseek") or {})

    def test_a_ticked_model_is_used_when_nothing_is_pinned(self, registry):
        """The reported case: no `model` field, but the operator selected one."""
        registry.set_selected_models("deepseek", ["deepseek-chat"])
        assert registry.probe_model_for("deepseek") == "deepseek-chat"

    def test_a_manually_listed_model_is_used(self, registry):
        registry.upsert_provider({"name": "deepseek", "models": ["deepseek-chat"]})
        assert registry.probe_model_for("deepseek") == "deepseek-chat"

    def test_nothing_configured_returns_empty(self, registry):
        assert registry.probe_model_for("deepseek") == ""

    def test_an_unknown_provider_returns_empty(self, registry):
        assert registry.probe_model_for("not-a-provider") == ""


class TestTheRouteUsesIt:
    def test_the_test_route_resolves_a_model(self):
        import inspect

        from kazma_ui import providers as ui_providers

        src = inspect.getsource(ui_providers)
        assert "probe_model_for" in src
        assert 'str(provider.get("model") or "")' not in src

    def test_no_model_is_reported_as_configuration_not_failure(self):
        """'no model selected' is a thing the operator can fix, and the message
        has to say which button to press — not report it as the provider
        failing."""
        import inspect

        from kazma_ui import providers as ui_providers

        src = inspect.getsource(ui_providers)
        assert "no model is selected for this" in src
        assert "tick at least one model" in src
