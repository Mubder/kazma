"""Unit tests for ProviderSettingsService (settings_providers module)."""

from __future__ import annotations

from unittest.mock import MagicMock

from kazma_core.settings_manager import ProviderSettingsService
from kazma_core.settings_providers import ProviderSettingsService as Direct


def test_reexport_same_class():
    assert ProviderSettingsService is Direct


def test_get_all_and_toggle_delegate_to_registry():
    store = MagicMock()
    registry = MagicMock()
    registry.list_providers.return_value = [{"name": "openai"}]
    svc = ProviderSettingsService(store, registry)
    assert svc.get_all_providers() == [{"name": "openai"}]
    svc.toggle_provider("openai", False)
    registry.toggle_provider.assert_called_once_with("openai", False)


def test_health_update_writes_store():
    store = MagicMock()
    registry = MagicMock()
    svc = ProviderSettingsService(store, registry)
    svc._update_provider_health("openai", "healthy")
    assert store.set.call_count >= 2
    registry.set_provider_health.assert_called_once_with("openai", "healthy")
