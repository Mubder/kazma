"""Call-time LLM client resolution — the DeepSeek 401 that was not a bad key.

Live ledger (2026-09-11, thread 7c5a3568-…):

    it=0  model=deepseek-flash  ok
    it=1  model=                error  (401 from api.deepseek.com)

The key was valid. Iteration 0 used a registry client for the pinned model.
Later iterations of the same turn lost the ContextVar pin and fell back to
the compile-time captured ``llm``, which still had DeepSeek's URL and
yesterday's empty/not-needed key.

Previous commits fixed the pre-stream *check* and the error wording. They
never replaced the client the graph actually calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kazma_core.llm_provider import LLMConfig, LLMError, LLMProvider
from kazma_core.runtime.live_llm import (
    coerce_api_key,
    key_is_usable,
    resolve_live_client,
    url_is_cloud,
)


CLOUD = "https://api.deepseek.com/v1"
REAL_KEY = "sk-live-deepseek-aaaaaaaa"


@pytest.fixture
def registry_with_deepseek():
    from kazma_core.model_registry import get_model_registry

    registry = get_model_registry()
    registry.upsert_provider({
        "name": "deepseek",
        "base_url": CLOUD,
        "api_key": REAL_KEY,
        "models": ["deepseek-flash", "deepseek-chat"],
        "enabled": True,
    })
    registry.set_active_provider(
        "deepseek", base_url=CLOUD, api_key=REAL_KEY, model="deepseek-flash",
    )
    return registry


class TestKeyHygiene:
    def test_coerce_never_stringifies_none(self) -> None:
        assert coerce_api_key(None) == ""
        assert coerce_api_key("None") == "None"  # a literal is still a literal

    def test_coerce_strips_bom_quotes_whitespace(self) -> None:
        assert coerce_api_key('  "sk-abc"  ') == "sk-abc"
        assert coerce_api_key("\ufeffsk-abc") == "sk-abc"

    @pytest.mark.parametrize(
        "junk",
        ["", "not-needed", "***", "****", "None", "null", "sk-real-key",
         "vault://cfg:providers.list.deepseek.api_key", "****abcd"],
    )
    def test_placeholders_are_not_usable(self, junk: str) -> None:
        assert key_is_usable(junk) is False
        assert key_is_usable(None) is False

    def test_a_real_key_is_usable(self) -> None:
        assert key_is_usable(REAL_KEY) is True

    def test_deepseek_url_is_cloud(self) -> None:
        assert url_is_cloud(CLOUD) is True
        assert url_is_cloud("http://localhost:11434/v1") is False


class TestResolveLiveClient:
    def test_checkpointed_last_model_beats_stale_capture(
        self, registry_with_deepseek,
    ) -> None:
        """The live 401: pin gone, last_model still deepseek-flash."""
        stale = LLMProvider(LLMConfig(base_url=CLOUD, api_key="", model="deepseek-flash"))
        client, model = resolve_live_client(
            stale, state={"last_model": "deepseek-flash"},
        )
        assert model == "deepseek-flash"
        assert client is not stale
        assert client.config.api_key == REAL_KEY
        assert "deepseek.com" in (client.config.base_url or "")

    def test_pin_uses_registry_key(self, registry_with_deepseek, monkeypatch) -> None:
        from kazma_core.runtime.turn_model import pin_turn_model, reset_turn_model

        stale = LLMProvider(LLMConfig(base_url=CLOUD, api_key="not-needed"))
        tok = pin_turn_model("deepseek-flash")
        try:
            client, model = resolve_live_client(stale)
            assert model == "deepseek-flash"
            assert client.config.api_key == REAL_KEY
        finally:
            reset_turn_model(tok)

    def test_last_model_wins_over_router_hint(
        self, registry_with_deepseek,
    ) -> None:
        """Later iterations re-run the keyword router; that must not win."""
        stale = LLMProvider(LLMConfig(base_url=CLOUD, api_key=""))
        client, model = resolve_live_client(
            stale,
            state={"last_model": "deepseek-flash"},
            model="qwen3.8-max",
        )
        assert model == "deepseek-flash"
        assert client.config.api_key == REAL_KEY

    def test_unusable_cloud_fallback_is_replaced(
        self, registry_with_deepseek,
    ) -> None:
        stale = LLMProvider(LLMConfig(base_url=CLOUD, api_key="not-needed"))
        client, _model = resolve_live_client(stale, state={})
        assert client.config.api_key == REAL_KEY

    def test_mocks_are_not_replaced(self, registry_with_deepseek) -> None:
        mock = MagicMock()
        client, _model = resolve_live_client(
            mock, state={"last_model": "deepseek-flash"},
        )
        assert client is mock

    def test_get_client_drops_unusable_cache(
        self, registry_with_deepseek,
    ) -> None:
        """Boot-cached DeepSeek with an empty key must not be reused."""
        stale = LLMProvider(LLMConfig(base_url=CLOUD, api_key=""))
        registry_with_deepseek._clients["deepseek"] = stale
        client = registry_with_deepseek.get_client()
        assert client is not stale
        assert client.config.api_key == REAL_KEY

    def test_sk_real_key_placeholder_is_not_sent(
        self, registry_with_deepseek,
    ) -> None:
        stale = LLMProvider(LLMConfig(base_url=CLOUD, api_key="sk-real-key"))
        client, _model = resolve_live_client(stale, state={})
        assert client.config.api_key == REAL_KEY
        assert client.config.api_key != "sk-real-key"


class TestRefuseJunkCloudKey:
    @pytest.mark.asyncio
    async def test_empty_cloud_key_fails_before_http(self) -> None:
        provider = LLMProvider(LLMConfig(base_url=CLOUD, api_key=""))
        with pytest.raises(LLMError) as ei:
            await provider.chat([{"role": "user", "content": "hi"}])
        assert ei.value.transient is False
        assert "401" in str(ei.value)
        assert "deepseek.com" in str(ei.value)

    @pytest.mark.asyncio
    async def test_placeholder_cloud_key_fails_before_http(self) -> None:
        provider = LLMProvider(LLMConfig(base_url=CLOUD, api_key="sk-real-key"))
        with pytest.raises(LLMError) as ei:
            await provider.chat([{"role": "user", "content": "hi"}])
        assert "401" in str(ei.value)

    @pytest.mark.asyncio
    async def test_local_dummy_key_is_still_allowed(self) -> None:
        """Ollama/LM Studio must not be blocked by the cloud-key gate."""
        provider = LLMProvider(
            LLMConfig(base_url="http://localhost:11434/v1", api_key="")
        )
        # Building the httpx client is enough — we do not need a live Ollama.
        client = await provider._get_client()
        assert client is not None
        assert provider.config.api_key in ("not-needed", "ollama", "")


class TestUserTaskDefaultOwnerIsAName:
    def test_find_provider_returns_dict_but_route_uses_name(
        self, registry_with_deepseek,
    ) -> None:
        from kazma_core.models.router import TaskProfile
        from kazma_core.models.selection import user_task_default

        registry_with_deepseek._config_store.set(
            "models.defaults.general", "deepseek-flash", category="models",
        )
        hit = user_task_default(registry_with_deepseek, TaskProfile.GENERAL)
        assert hit is not None
        provider_name, model_id = hit
        assert isinstance(provider_name, str)
        assert provider_name == "deepseek"
        assert model_id == "deepseek-flash"
        client = registry_with_deepseek.get_client_by_provider(
            provider_name, model=model_id,
        )
        assert client is not None
        assert client.config.api_key == REAL_KEY


class TestMockProvidersAreLeftAlone:
    """`resolve_live_client` must not swap out a caller's test double.

    The docstring always promised this ("unless *fallback* is a test mock"),
    but the guard was a bare ``isinstance(obj, LLMProvider)`` — and
    ``MagicMock(spec=LLMProvider)`` sets ``__class__``, so it *passes*
    isinstance. Mocks were therefore replaced by a live registry client, and a
    test that had carefully stubbed the LLM instead dialled whatever provider
    was configured on the machine running it. Three suites went red that way,
    one of them by trying to POST to a `custom` provider whose base_url had no
    scheme.
    """

    @staticmethod
    def _mock_provider():
        from kazma_core.llm_provider import LLMProvider

        return MagicMock(spec=LLMProvider)

    def test_a_spec_mock_still_passes_isinstance(self):
        """The trap itself — guard against someone 'simplifying' the check."""
        from kazma_core.llm_provider import LLMProvider

        assert isinstance(self._mock_provider(), LLMProvider) is True

    def test_a_mock_is_not_treated_as_a_real_provider(self):
        from kazma_core.runtime.live_llm import _is_real_provider

        assert _is_real_provider(self._mock_provider()) is False

    def test_a_genuine_provider_still_is(self):
        """Negative control: the swap must still happen for real clients."""
        from kazma_core.llm_provider import LLMProvider
        from kazma_core.runtime.live_llm import _is_real_provider

        assert _is_real_provider(LLMProvider.__new__(LLMProvider)) is True

    def test_resolve_returns_the_mock_untouched(self):
        from kazma_core.runtime.live_llm import resolve_live_client

        mock = self._mock_provider()
        client, _model = resolve_live_client(mock)
        assert client is mock, "a test double must survive live resolution"
