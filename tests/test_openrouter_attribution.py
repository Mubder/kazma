"""OpenRouter app attribution (REMAINING 4.6): a call to OpenRouter's own API
carries the two headers OpenRouter reads to put an app on its public rankings
(HTTP-Referer, X-Title), naming Kazma and never the person; no other provider
gets them, a provider row's own headers win, and
KAZMA_OPENROUTER_ATTRIBUTION=0 turns them off.
"""

from __future__ import annotations

import asyncio

import pytest

from kazma_core.llm_provider import LLMConfig, LLMProvider, _attribution_headers


def _client_headers(base_url: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    provider = LLMProvider(LLMConfig(base_url=base_url, api_key="sk-test-0123456789abcdef", model="m",
                                     extra_headers=extra or {}))

    async def build() -> dict[str, str]:
        client = await provider._get_client()
        try:
            return {k: v for k, v in client.headers.items()}
        finally:
            await client.aclose()

    return asyncio.run(build())


def test_a_call_to_openrouter_names_kazma(monkeypatch) -> None:
    monkeypatch.delenv("KAZMA_OPENROUTER_ATTRIBUTION", raising=False)
    headers = _client_headers("https://openrouter.ai/api/v1")
    assert headers["http-referer"] == "https://kazma.ai"
    assert headers["x-title"] == "Kazma"


@pytest.mark.parametrize("url", ["https://api.openai.com/v1", "https://openrouter.ai.evil.example/v1",
                                 "https://notopenrouter.ai/v1", "http://localhost:11434/v1"])
def test_no_other_provider_gets_them(url, monkeypatch) -> None:
    monkeypatch.delenv("KAZMA_OPENROUTER_ATTRIBUTION", raising=False)
    assert _attribution_headers(url) == {}


def test_the_switch_and_the_rows_own_headers(monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_OPENROUTER_ATTRIBUTION", "0")
    assert "x-title" not in _client_headers("https://openrouter.ai/api/v1")
    monkeypatch.delenv("KAZMA_OPENROUTER_ATTRIBUTION")
    headers = _client_headers("https://openrouter.ai/api/v1", {"X-Title": "My app"})
    assert headers["x-title"] == "My app", "a provider row's own header wins"
