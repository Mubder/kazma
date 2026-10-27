"""A client pinned to a provider is sent one of THAT provider's models.

Audit 2026-10-26 AUD-015: ``get_client_by_provider(name)`` with no model
fell back to ``entry.get("model") or self._active_model``. Provider rows keep
``models`` (a list), never ``model``, so the fallback was the ACTIVE model,
whoever's it was: a swarm worker pinned to ``anthropic`` while
``deepseek-flash`` was active sent ``deepseek-flash`` to Anthropic and got a
404, classed permanent. AGENTS.md §1: a model and its provider move together.
"""

from __future__ import annotations

import pytest

CLOUD = "https://api.deepseek.com/v1"
DEEPSEEK_KEY = "sk-live-deepseek-aaaaaaaa"
ANTHROPIC_KEY = "sk-ant-live-aaaaaaaaaaaa"


@pytest.fixture
def registry():
    from kazma_core.model_registry import get_model_registry

    reg = get_model_registry()
    reg.upsert_provider({
        "name": "deepseek",
        "base_url": CLOUD,
        "api_key": DEEPSEEK_KEY,
        "models": ["deepseek-flash", "deepseek-chat"],
        "enabled": True,
    })
    reg.upsert_provider({
        "name": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "api_key": ANTHROPIC_KEY,
        "models": ["claude-sonnet-demo"],
        "enabled": True,
    })
    reg.set_active_provider(
        "deepseek", base_url=CLOUD, api_key=DEEPSEEK_KEY, model="deepseek-flash",
    )
    return reg


def _model_of(client) -> str:
    return str(client.config.model)


def test_a_pin_to_another_provider_is_never_sent_the_active_model(registry):
    client = registry.get_client_by_provider("anthropic")
    assert client is not None
    assert _model_of(client) == "claude-sonnet-demo"
    assert _model_of(client) != "deepseek-flash"


def test_a_pin_to_the_active_provider_keeps_the_active_model(registry):
    client = registry.get_client_by_provider("deepseek")
    assert client is not None
    assert _model_of(client) == "deepseek-flash"


def test_a_provider_with_no_models_gets_its_own_default(registry):
    from kazma_core.providers import default_model_for

    registry.upsert_provider({
        "name": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "api_key": ANTHROPIC_KEY,
        "models": [],
        "enabled": True,
    })
    client = registry.get_client_by_provider("anthropic")
    assert client is not None
    assert _model_of(client) == default_model_for("anthropic")
    assert _model_of(client) != "deepseek-flash"


def test_an_explicit_model_still_wins(registry):
    client = registry.get_client_by_provider("anthropic", model="claude-explicit")
    assert _model_of(client) == "claude-explicit"


def test_negative_control_the_old_fallback_sent_the_active_model(registry):
    """The old rule, evaluated on the same registry: the Anthropic pin
    resolves to DeepSeek's active model -- the bug the tests above reject."""
    from kazma_core.providers import default_model_for

    entry = registry.get_provider("anthropic")
    old = str(entry.get("model", "") or "") or (
        registry._active_model or default_model_for("anthropic")
    )
    assert old == "deepseek-flash"
