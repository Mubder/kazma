"""A model id and its provider must agree, or the vendor rejects it cryptically.

Live report, 2026-09-16 (Telegram):

    ⚠️ The model rejected the request: LLM call failed (HTTP 400):
    {"error":{"code":"1211","message":"Unknown Model, please check the model code."}}

The same model is spelled differently depending on how you reach it —
``z-ai/glm-5.3-flash`` through OpenRouter, ``glm-5.3-flash`` at Z.AI's own
API. Any name containing "/" is passed through untouched, so choosing the
aggregator spelling while pointed at the vendor endpoint sends a name that
vendor has never heard of. Nothing in the product noticed, and the error named
neither the model nor the endpoint, so it was unattributable.

Blanket prefix-stripping is the obvious fix and is wrong: ``groq/compound-mini``
IS Groq's real upstream id. The rule has to come from each provider's own
discovered model list, which is what these tests pin.
"""

from __future__ import annotations

import pytest

from kazma_core.llm_provider import LLMProvider


@pytest.fixture
def known(monkeypatch):
    """Stub the per-provider discovered-model lists."""

    def _fake(base_url: str) -> set[str]:
        host = LLMProvider._host_of(base_url)
        return {
            "api.z.ai": {"glm-5.3-flash", "glm-4.6", "glm-5"},
            "openrouter.ai": {"z-ai/glm-5.3-flash", "openai/gpt-5.4"},
            "api.groq.com": {"groq/compound-mini", "openai/gpt-oss-120b"},
            "api.deepseek.com": {"deepseek-flash", "deepseek-v4-pro"},
        }.get(host, set())

    monkeypatch.setattr(LLMProvider, "_known_models_for", staticmethod(_fake))
    return _fake


@pytest.mark.parametrize(
    "model,base_url,expected,why",
    [
        (
            "z-ai/glm-5.3-flash", "https://api.z.ai/api/paas/v4/", "glm-5.3-flash",
            "the reported failure: an OpenRouter id sent to Z.AI direct",
        ),
        (
            "glm-5.3-flash", "https://api.z.ai/api/paas/v4/", "glm-5.3-flash",
            "already the vendor's own spelling",
        ),
        (
            "z-ai/glm-5.3-flash", "https://openrouter.ai/api/v1", "z-ai/glm-5.3-flash",
            "correct FOR OpenRouter — stripping here would break a working setup",
        ),
        (
            "groq/compound-mini", "https://api.groq.com/openai/v1", "groq/compound-mini",
            "Groq's real id CONTAINS the prefix; this is why blanket stripping is wrong",
        ),
        (
            "openai/gpt-oss-120b", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b",
            "Groq really serves this under the openai/ namespace",
        ),
        (
            "some/unknown-model", "https://api.z.ai/api/paas/v4/", "some/unknown-model",
            "unknown either way — never guess, guessing is what caused this",
        ),
    ],
)
def test_vendor_prefix_is_reconciled_from_the_providers_own_catalog(
    known, model, base_url, expected, why
):
    assert LLMProvider._strip_routing_prefix(model, base_url) == expected, why


def test_local_routing_prefixes_still_stripped(known):
    """Pre-existing behaviour for local servers must not regress."""
    assert LLMProvider._strip_routing_prefix(
        "ollama/qwen2.5:7b", "http://localhost:11434/v1"
    ) == "qwen2.5:7b"
    assert LLMProvider._strip_routing_prefix(
        "lm-studio/whatever", "http://localhost:1234/v1"
    ) == "whatever"


def test_no_catalog_means_no_change(monkeypatch):
    """With nothing discovered we must not invent a normalisation."""
    monkeypatch.setattr(
        LLMProvider, "_known_models_for", staticmethod(lambda _u: set())
    )
    assert LLMProvider._strip_routing_prefix(
        "z-ai/glm-5.3-flash", "https://api.z.ai/api/paas/v4/"
    ) == "z-ai/glm-5.3-flash"


def test_catalog_lookup_failure_is_not_fatal(monkeypatch):
    """A broken lookup must never take down a live LLM call."""

    def _boom(_url):
        raise RuntimeError("config store is down")

    monkeypatch.setattr(LLMProvider, "_known_models_for", staticmethod(_boom))
    assert LLMProvider._strip_routing_prefix(
        "z-ai/glm-5.3-flash", "https://api.z.ai/api/paas/v4/"
    ) == "z-ai/glm-5.3-flash"


def test_rejection_error_names_the_model_and_the_endpoint():
    """The 400 branch printed only the vendor's body — unattributable.

    The 401 branch has always said "rejected by {model} / {base_url}". A model
    /provider mismatch is precisely the case where you need to know WHICH model
    went WHERE, and it was the one case that did not say.
    """
    import inspect

    from kazma_core import llm_provider

    src = inspect.getsource(llm_provider)
    marker = 'f"LLM call failed (HTTP {status_code}) for model "'
    assert marker in src, (
        "the permanent-rejection error no longer names the model; without it a "
        "vendor's 'Unknown Model' cannot be attributed to a provider"
    )
    assert "_describe_endpoint" in src, "the error must also name the endpoint"
