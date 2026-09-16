"""A model id and the client it is sent on must travel together.

Live failure, 2026-09-16, Telegram (kazma.log 17:09:28-33):

    model_registry : Profile provider=deepseek model=deepseek-flash has no
                     usable API key; using Z.AI/glm-5.3-flash which has one
    llm_provider   : LLMProvider initialized:
                     base_url=https://api.z.ai/api/paas/v4 model=glm-5.3-flash
    graph_supervisor: [Supervisor] live client model=deepseek-flash
    Z.AI           : {"error":{"code":"1211","message":"Unknown Model, ..."}}

The registry did the right thing: deepseek had no usable key, so it swapped in
a provider that did, correctly paired with that provider's own model.
``resolve_live_client`` then returned ``(substituted_client, pinned)`` — the
Z.AI client carrying deepseek's model id — because a pinned model was treated
as authoritative even when the provider under it had been replaced.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kazma_core.runtime import live_llm


class _Cfg(SimpleNamespace):
    pass


def _client(base_url: str, model: str, key: str = "sk-real-key"):
    """Something _is_real_provider() accepts."""
    from kazma_core.llm_provider import LLMProvider

    obj = LLMProvider.__new__(LLMProvider)
    obj.config = _Cfg(base_url=base_url, model=model, api_key=key)
    return obj


@pytest.fixture
def catalog(monkeypatch):
    """Z.AI serves glm-*, deepseek serves deepseek-*."""
    from kazma_core.llm_provider import LLMProvider

    def _known(base_url: str) -> set[str]:
        host = live_llm._host_of(base_url)
        return {
            "api.z.ai": {"glm-5.3-flash", "glm-4.6"},
            "api.deepseek.com": {"deepseek-flash", "deepseek-v4-pro"},
        }.get(host, set())

    monkeypatch.setattr(LLMProvider, "_known_models_for", staticmethod(_known))


def test_pinned_model_does_not_ride_onto_a_substituted_provider(catalog, monkeypatch):
    """The reported bug, reproduced exactly."""
    substituted = _client("https://api.z.ai/api/paas/v4", "glm-5.3-flash")
    monkeypatch.setattr(
        live_llm,
        "current_turn_model",
        lambda: "deepseek-flash",
    )
    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        lambda: SimpleNamespace(get_client=lambda _m: substituted),
    )
    graph_llm = _client("https://api.deepseek.com/v1", "deepseek-flash", key="")

    client, model = live_llm.resolve_live_client(graph_llm, state={}, model=None)

    assert client is substituted
    assert model == "glm-5.3-flash", (
        "the Z.AI client was returned carrying deepseek's model id — that is "
        "the 1211 'Unknown Model' failure"
    )


def test_pin_is_kept_when_the_provider_does_serve_it(catalog, monkeypatch):
    """No substitution happened: the pin must survive untouched."""
    same = _client("https://api.deepseek.com/v1", "deepseek-v4-pro")
    monkeypatch.setattr(live_llm, "current_turn_model", lambda: "deepseek-flash")
    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        lambda: SimpleNamespace(get_client=lambda _m: same),
    )
    graph_llm = _client("https://api.deepseek.com/v1", "deepseek-flash")

    _client_out, model = live_llm.resolve_live_client(graph_llm, state={}, model=None)
    assert model == "deepseek-flash", (
        "deepseek DOES serve deepseek-flash; config.model is only a default "
        "for OpenAI-compatible providers and must not override the pin"
    )


def test_unknown_catalog_keeps_the_pin(monkeypatch):
    """No catalog => no proof => do not change behaviour."""
    from kazma_core.llm_provider import LLMProvider

    monkeypatch.setattr(LLMProvider, "_known_models_for", staticmethod(lambda _u: set()))
    other = _client("https://api.z.ai/api/paas/v4", "glm-5.3-flash")
    monkeypatch.setattr(live_llm, "current_turn_model", lambda: "deepseek-flash")
    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        lambda: SimpleNamespace(get_client=lambda _m: other),
    )
    graph_llm = _client("https://api.deepseek.com/v1", "deepseek-flash", key="")

    _c, model = live_llm.resolve_live_client(graph_llm, state={}, model=None)
    assert model == "deepseek-flash"


def test_catalog_lookup_failure_is_never_fatal(monkeypatch):
    from kazma_core.llm_provider import LLMProvider

    def _boom(_u):
        raise RuntimeError("config store down")

    monkeypatch.setattr(LLMProvider, "_known_models_for", staticmethod(_boom))
    assert live_llm._provider_serves("https://api.z.ai/x", "anything") is True
