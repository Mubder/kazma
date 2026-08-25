"""Optional LiteLLM proxy gateway (generic OpenAI-compat only)."""

from __future__ import annotations

import pytest

from kazma_core.llm_gateway import (
    get_litellm_gateway,
    is_local_openai_compat,
    resolve_generic_egress,
)
from kazma_core.llm_provider import LLMConfig, LLMProvider


class TestLocalDetect:
    def test_openai_cloud_is_not_local(self) -> None:
        assert is_local_openai_compat("https://api.openai.com/v1") is False

    def test_ollama_is_local(self) -> None:
        assert is_local_openai_compat("http://127.0.0.1:11434") is True

    def test_lm_studio_is_local(self) -> None:
        assert is_local_openai_compat("http://localhost:1234/v1") is True

    def test_litellm_port_is_not_local_direct(self) -> None:
        assert is_local_openai_compat("http://127.0.0.1:4000") is False


class TestResolve:
    def test_off_without_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KAZMA_LITELLM_URL", raising=False)
        monkeypatch.delenv("KAZMA_LITELLM", raising=False)
        monkeypatch.setattr(
            "kazma_core.llm_gateway._store_get", lambda *a, **k: ""
        )
        monkeypatch.setattr("kazma_core.llm_gateway._yaml_gateway", lambda: {})
        url, key, via = resolve_generic_egress(
            "https://api.openai.com/v1", "sk-openai"
        )
        assert via is False
        assert "openai.com" in url
        assert key == "sk-openai"

    def test_cloud_rewritten(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAZMA_LITELLM_URL", "http://127.0.0.1:4000")
        monkeypatch.delenv("KAZMA_LITELLM", raising=False)
        url, key, via = resolve_generic_egress(
            "https://api.openai.com/v1", "sk-openai"
        )
        assert via is True
        assert "4000" in url

    def test_local_not_rewritten(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAZMA_LITELLM_URL", "http://127.0.0.1:4000")
        monkeypatch.delenv("KAZMA_LITELLM_LOCAL", raising=False)
        url, key, via = resolve_generic_egress(
            "http://127.0.0.1:11434", "ollama"
        )
        assert via is False
        assert "11434" in url

    def test_include_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAZMA_LITELLM_URL", "http://127.0.0.1:4000")
        monkeypatch.setenv("KAZMA_LITELLM_LOCAL", "1")
        url, _key, via = resolve_generic_egress(
            "http://127.0.0.1:11434", "ollama"
        )
        assert via is True
        assert "4000" in url

    def test_kill_switch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAZMA_LITELLM_URL", "http://127.0.0.1:4000")
        monkeypatch.setenv("KAZMA_LITELLM", "0")
        _url, _key, via = resolve_generic_egress(
            "https://api.openai.com/v1", "sk"
        )
        assert via is False

    def test_master_key_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAZMA_LITELLM_URL", "http://127.0.0.1:4000")
        monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-litellm-master")
        _url, key, via = resolve_generic_egress(
            "https://api.openai.com/v1", "sk-openai"
        )
        assert via is True
        assert key == "sk-litellm-master"


class TestProviderApply:
    def test_generic_rewrites(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAZMA_LITELLM_URL", "http://127.0.0.1:4000")
        p = LLMProvider(LLMConfig(base_url="https://api.openai.com/v1", api_key="sk"))
        assert "4000" in p.config.base_url
        assert p._via_gateway is True
        assert "openai.com" in p._direct_base_url

    def test_ollama_stays_direct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAZMA_LITELLM_URL", "http://127.0.0.1:4000")
        monkeypatch.delenv("KAZMA_LITELLM_LOCAL", raising=False)
        p = LLMProvider(LLMConfig(base_url="http://127.0.0.1:11434", api_key="ollama"))
        assert "11434" in p.config.base_url
        assert p._via_gateway is False

    def test_anthropic_subclass_does_not_rewrite(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAZMA_LITELLM_URL", "http://127.0.0.1:4000")
        from kazma_core.anthropic_llm import AnthropicProvider

        p = AnthropicProvider(LLMConfig(api_key="sk-ant", model="claude-sonnet-4"))
        assert "anthropic.com" in p.config.base_url

    def test_live_kill_switch_on_chat_sync(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAZMA_LITELLM_URL", "http://127.0.0.1:4000")
        p = LLMProvider(LLMConfig(base_url="https://api.openai.com/v1", api_key="sk"))
        assert p._via_gateway is True
        monkeypatch.setenv("KAZMA_LITELLM", "0")
        p._sync_gateway()
        assert p._via_gateway is False
        assert "openai.com" in p.config.base_url


def test_gateway_status_has_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_LITELLM_URL", "http://127.0.0.1:4000")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "super-secret")
    from kazma_core.llm_gateway import gateway_status

    st = gateway_status()
    blob = str(st)
    assert "super-secret" not in blob
    assert st["enabled"] is True
    assert "anthropic" in st["native_untouched"]
