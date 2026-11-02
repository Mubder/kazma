"""A provider change reaches the next failover, not the next restart (FA-106).

The 2026-11 functional audit: ``resilient_chat`` -- the swarm workers', the
Refiner's and research's model calls -- cached its failover clients and
cooldowns for the life of the process. ``graph_supervisor.reset_failover_cache``
cleared only the graph's own cache, so after the owner saved a new key or base
URL for the failover model's provider, swarm and research failover went on
calling with the old one until a restart.

The registry now counts its changes (``ModelRegistry.generation``), and both
failover caches hold for one generation. The real registry, a real provider
save, and the failover client the registry builds; the old cache (that never
looks at the registry) is the negative control.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kazma_core.agent import graph_supervisor, nonstop, resilient_chat
from kazma_core.llm_provider import LLMError, LLMProvider, LLMResponse

FB_PROVIDER = "fa106-provider"
FB_MODEL = "fa106-backup-model"


@pytest.fixture()
def registry(monkeypatch):
    from kazma_core.model_registry import get_model_registry

    reg = get_model_registry()
    reg.upsert_provider({"name": FB_PROVIDER, "base_url": "https://fa106.example/v1",
                         "api_key": "sk-fa106-old-key", "models": [FB_MODEL], "enabled": True})
    real = nonstop.get_nonstop_config

    def with_failover(*args, **kwargs):
        cfg = real(*args, **kwargs)
        cfg.failover.enabled, cfg.failover.chain, cfg.failover.cooldown_seconds = True, [FB_MODEL], 0.0
        return cfg

    monkeypatch.setattr(nonstop, "get_nonstop_config", with_failover)
    used: list[str] = []

    async def chat(self, messages=None, tools=None, *args, **kwargs):
        """The failover provider's answer, saying which key it was built with."""
        used.append(self.config.api_key)
        return LLMResponse(content="ok", tool_calls=[], finish_reason="stop", model=FB_MODEL,
                           usage={"total_tokens": 1}, cost_usd=0.0)

    async def chat_stream(self, messages=None, tools=None, *args, **kwargs):
        from kazma_core.llm_stream import StreamDelta

        yield StreamDelta(response=await chat(self, messages, tools))

    monkeypatch.setattr(LLMProvider, "chat", chat)
    monkeypatch.setattr(LLMProvider, "chat_stream", chat_stream)
    resilient_chat.reset_failover_cache()
    graph_supervisor.reset_failover_cache()
    yield reg, used
    reg.delete_provider(FB_PROVIDER)
    resilient_chat.reset_failover_cache()
    graph_supervisor.reset_failover_cache()


class _Down:
    """The primary model: a transient outage, so failover runs."""

    async def chat(self, *args, **kwargs):
        raise LLMError("primary down", transient=True)


def _worker_call() -> Any:
    return asyncio.run(resilient_chat.resilient_chat(
        _Down(), messages=[{"role": "user", "content": "q"}], model="primary", max_attempts=1,
        label="fa106",
    ))


def _change_key(reg) -> None:
    reg.upsert_provider({"name": FB_PROVIDER, "api_key": "sk-fa106-new-key"})


def test_a_saved_key_reaches_the_next_worker_failover(registry):
    reg, used = registry
    _worker_call()
    _change_key(reg)
    _worker_call()
    assert used == ["sk-fa106-old-key", "sk-fa106-new-key"], used


def test_a_saved_key_reaches_the_next_chat_failover(registry, monkeypatch):
    """The supervisor's failover, through a real graph."""
    from kazma_core import retry
    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.authority import create_authority
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.tracing import KazmaTracer
    from tests._no_models import keep_models_out

    reg, used = registry
    keep_models_out(monkeypatch, llm=False)
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    monkeypatch.setattr(retry, "load_retry_config",
                        lambda: {"max_attempts": 1, "min_wait": 0, "max_wait": 0})
    graph = build_supervisor_graph(
        llm=_Down(), system_prompt="base", tool_definitions=[], tool_executor=None,
        cost_breaker=create_cost_breaker(), authority=create_authority(model="test", window=128000),
        tracer=KazmaTracer(backend="console"),
    )

    def turn(thread: str) -> str:
        out = asyncio.run(graph.ainvoke({"messages": [{"role": "user", "content": "hello"}]},
                                        {"configurable": {"thread_id": thread}}))
        return out["messages"][-1]["content"]

    assert turn("fa106-a") == "ok"
    _change_key(reg)
    assert turn("fa106-b") == "ok"
    assert used == ["sk-fa106-old-key", "sk-fa106-new-key"], used


def test_a_model_switch_clears_the_worker_cache(registry, monkeypatch):
    from types import SimpleNamespace

    from kazma_core.agent_runner import KazmaAgent

    resilient_chat._clients["x"] = object()
    resilient_chat._cooldowns["x"] = 1e12
    client = SimpleNamespace(config=SimpleNamespace(model="m"))
    agent = SimpleNamespace(_graph=None, _streaming_graph=None)
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry",
                        lambda: SimpleNamespace(get_client=lambda: client))
    KazmaAgent.sync_active_model(agent)
    assert resilient_chat._clients == {} and resilient_chat._cooldowns == {}


def test_the_old_worker_cache_kept_the_old_key(registry, monkeypatch):
    """Negative control: the cache before FA-106 never looked at the registry."""
    reg, used = registry
    monkeypatch.setattr(resilient_chat, "_follow", lambda registry: None)
    # Nor did anything else clear it: a provider write can reach an agent's
    # sync_active_model through a settings listener an earlier test left.
    monkeypatch.setattr(resilient_chat, "reset_failover_cache", lambda: None)
    _worker_call()
    _change_key(reg)
    _worker_call()
    assert used == ["sk-fa106-old-key", "sk-fa106-old-key"], used
