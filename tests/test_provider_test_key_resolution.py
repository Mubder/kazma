"""Test must look for a key everywhere a real message would.

Reported from the live install: a provider whose key works — chat answers,
messages go through — returned

    Unreachable
    No API key stored for this provider. Paste the full key into the field…

The key was in `.env`. `ModelRegistry._resolve_provider_config` resolves a key
from the stored provider row, the legacy `llm.*` settings, *or*
`<PROVIDER>_API_KEY` in the environment, and chat uses all of them. The Test
route read only the provider row, so it reported a working provider as having
no key at all — the check contradicting the thing it was checking.

This is the same defect as the `/models`-vs-`/chat/completions` bug that
started `docs/plans/PROVIDER_LAYER_PLAN.md`, in a different variable: a check
that does not exercise the path the product uses is not a check.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def registry(tmp_path):
    from kazma_core.config_store import ConfigStore
    from kazma_core.model_registry import ModelRegistry

    store = ConfigStore(
        db_path=str(tmp_path / "creds.db"),
        yaml_path=str(tmp_path / "nonexistent.yaml"),
    )
    reg = ModelRegistry(store)
    reg.upsert_provider(
        {"name": "groq", "base_url": "https://api.groq.com/openai/v1", "api_key": ""}
    )
    return reg


class TestResolveProviderCredentials:
    def test_the_stored_key_wins(self, registry, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "from-env")
        registry.upsert_provider({"name": "groq", "api_key": "from-the-row"})
        _, key = registry.resolve_provider_credentials("groq")
        assert key == "from-the-row"

    def test_an_env_key_is_found_when_the_row_is_empty(self, registry, monkeypatch):
        """The reported bug. The row is blank, the environment has the key,
        and every message this provider sends works."""
        monkeypatch.setenv("GROQ_API_KEY", "gsk_env_value")
        _, key = registry.resolve_provider_credentials("groq")
        assert key == "gsk_env_value"

    def test_nothing_anywhere_resolves_to_empty(self, registry, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("KAZMA_API_KEY", raising=False)
        _, key = registry.resolve_provider_credentials("groq")
        assert key == ""

    def test_it_returns_the_base_url_too(self, registry, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "gsk_env_value")
        url, _ = registry.resolve_provider_credentials("groq")
        assert url == "https://api.groq.com/openai/v1"


class TestTheTestRouteUsesIt:
    def test_the_route_asks_the_runtime_rather_than_the_row(self):
        """Guards the fix at the only place it matters: if this route goes
        back to reading `provider["api_key"]` alone, an operator with a
        working .env key is told they have no key."""
        import inspect

        from kazma_ui import providers as ui_providers

        src = inspect.getsource(ui_providers)
        assert "resolve_provider_credentials" in src, (
            "the Test route resolves keys by hand again"
        )

    def test_the_chat_probe_sends_the_resolved_key(self):
        """The models-list call and the chat probe must send the same key.
        The probe used to read the provider row directly, so with an
        env-resolved key it would have sent nothing while the models call
        authenticated fine — reporting 'reachable, chat failing' on a provider
        that works."""
        import inspect

        from kazma_ui import providers as ui_providers

        src = inspect.getsource(ui_providers)
        assert 'typed_key or str(provider.get("api_key")' not in src
        assert "typed_key or api_key" in src
