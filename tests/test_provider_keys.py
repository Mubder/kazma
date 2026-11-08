"""Several keys per provider, and providers checked in the background
(providers stage 4, 2026-11-08).

Before: one key per provider -- a second account of a vendor was a second
provider row (Z.AI and zai), and a key that was refused, ran out of credit
or ran out of requests stopped every chat until the owner pasted another --
and a provider's health was known only when someone pressed Test. Each rule
has its control.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from kazma_core import provider_keys as pk


class _Store(dict):
    """The settings store's two calls the key ring makes."""

    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value, category=""):
        self[key] = value

    def atomic_update(self, key, updater, category=""):
        self[key] = updater(self.get(key))
        return self[key]


ROW = {"name": "groq", "api_key": "gsk-first-key", "api_keys": [
    {"id": "k2", "label": "second account", "api_key": "gsk-second-key"},
    {"id": "k3", "label": "", "api_key": "gsk-first-key"},  # the same key twice is one key
    {"id": "k4", "label": "third", "api_key": "gsk-third-key"},
]}


def test_a_providers_keys_in_order_each_once():
    assert pk.keys_of("gsk-first-key", ROW) == [
        ("", "gsk-first-key"), ("second account", "gsk-second-key"), ("third", "gsk-third-key")]
    assert pk.keys_of("gsk-only", {}) == [("", "gsk-only")]


def test_a_failed_key_rests_and_the_next_answers():
    store = _Store()
    assert pk.current_key(store, "groq", "gsk-first-key", ROW) == "gsk-first-key"
    assert pk.next_key(store, "groq", "gsk-first-key", ROW, "gsk-first-key", "refused") == (
        "second account", "gsk-second-key")
    assert pk.current_key(store, "groq", "gsk-first-key", ROW) == "gsk-second-key"
    assert "gsk-first-key" not in json.dumps(store)  # a key is named by its fingerprint
    assert pk.next_key(store, "groq", "gsk-first-key", ROW, "gsk-second-key", "no_credit") == (
        "third", "gsk-third-key")
    assert pk.next_key(store, "groq", "gsk-first-key", ROW, "gsk-third-key", "rate_limited") is None
    # Every key resting: the one whose rest ends first is tried.
    assert pk.current_key(store, "groq", "gsk-first-key", ROW) == "gsk-third-key"


def test_one_key_never_switches():
    assert pk.next_key(_Store(), "groq", "gsk-only", {"api_key": "gsk-only"}, "gsk-only", "refused") is None


def test_only_a_keys_failure_switches():
    from kazma_core.llm_provider import LLMError

    assert pk.key_failure(LLMError("LLM call failed (HTTP 401): invalid key"))[0] == "refused"
    assert pk.key_failure(LLMError("LLM call failed (HTTP 403): forbidden"))[0] == "refused"
    assert pk.key_failure(LLMError("LLM call failed (HTTP 402): pay"))[0] == "no_credit"
    assert pk.key_failure(LLMError("The x account has no credit left (model m)."))[0] == "no_credit"
    assert pk.key_failure(LLMError("rate", kind="rate_limit_exhausted", waited_s=40))[0] == "rate_limited"
    for other in ("LLM call failed (HTTP 404): no such model", "LLM call failed (network): reset",
                  "LLM call failed (HTTP 500): oops"):
        assert pk.key_failure(LLMError(other)) == ("", 0.0)


# ── inside one call ─────────────────────────────────────────────────────


def _client(answers: dict[str, int], switch: Any):
    from kazma_core.llm_provider import LLMConfig, LLMProvider

    seen: list[str] = []

    def endpoint(request: httpx.Request) -> httpx.Response:
        key = request.headers.get("authorization", "").removeprefix("Bearer ")
        seen.append(key)
        status = answers.get(key, 200)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "invalid api key"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})

    provider = LLMProvider(LLMConfig.from_dict({"base_url": "https://api.groq.com/openai/v1",
                                                "api_key": "gsk-first-key", "model": "m", "key_switch": switch}))

    async def get_client():
        return httpx.AsyncClient(base_url="https://api.groq.com/openai/v1", transport=httpx.MockTransport(endpoint),
                                 headers={"Authorization": f"Bearer {provider.config.api_key}"})

    provider._get_client = get_client
    return provider, seen


def test_a_refused_key_gives_way_inside_the_call():
    switched: list[str] = []

    def switch(failed, exc):
        switched.append(failed)
        return "gsk-second-key"

    provider, seen = _client({"gsk-first-key": 401}, switch)
    resp = asyncio.run(provider.chat([{"role": "user", "content": "hi"}]))
    assert resp.content == "ok" and seen == ["gsk-first-key", "gsk-second-key"]
    assert switched == ["gsk-first-key"]


def test_control_without_a_second_key_the_refusal_stands():
    from kazma_core.llm_provider import LLMError

    provider, seen = _client({"gsk-first-key": 401}, None)
    with pytest.raises(LLMError):
        asyncio.run(provider.chat([{"role": "user", "content": "hi"}]))
    assert seen == ["gsk-first-key"]


def test_a_failure_that_is_not_the_keys_never_switches():
    from kazma_core.llm_provider import LLMError

    calls: list[str] = []
    provider, _seen = _client({"gsk-first-key": 404}, lambda failed, exc: calls.append(failed) or "gsk-second-key")
    with pytest.raises(LLMError):
        asyncio.run(provider.chat([{"role": "user", "content": "hi"}]))
    assert calls == []


def test_the_registry_switches_rests_and_says_so(tmp_path, monkeypatch):
    from kazma_core.config_store import ConfigStore
    from kazma_core.llm_provider import LLMError
    from kazma_core.model_registry import ModelRegistry

    said: list[tuple[str, str]] = []
    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert",
                        lambda key, title, detail="", **kw: said.append((key, detail)) or True)
    monkeypatch.setattr("kazma_core.model_registry_store.detect_gcp_project_id", lambda: "")
    store = ConfigStore(db_path=str(tmp_path / "settings.db"))
    try:
        store.set("providers.list", [{**ROW, "base_url": "https://api.groq.com/openai/v1", "models": ["m"]}],
                  category="providers")
        registry = ModelRegistry(store)
        registry._deserialize()
        assert registry.resolve_provider_credentials("groq")[1] == "gsk-first-key"
        assert callable(registry.get_client("groq:m").config.key_switch)
        new = registry.switch_provider_key("groq", "gsk-first-key", LLMError("LLM call failed (HTTP 401): bad"))
        assert new == "gsk-second-key"
        assert registry.resolve_provider_credentials("groq")[1] == "gsk-second-key"
        assert said and said[0][0] == "llm.key_switched.groq" and "second account" in said[0][1]
        assert "gsk-" not in json.dumps(said)
    finally:
        store.close()


def test_a_stream_switches_before_it_has_said_anything():
    from kazma_core.llm_provider import LLMConfig, LLMError, LLMProvider

    class _Once(LLMProvider):
        def __init__(self):
            super().__init__(LLMConfig.from_dict({"base_url": "https://x.example/v1", "api_key": "gsk-first-key",
                                                  "model": "m", "key_switch": lambda f, e: "gsk-second-key"}))
            self.keys: list[str] = []

        async def _chat_stream_once(self, *a, **k):
            self.keys.append(self.config.api_key)
            if self.config.api_key == "gsk-first-key":
                raise LLMError("LLM call failed (HTTP 401): bad")
            from kazma_core.llm_stream import StreamDelta

            yield StreamDelta(content="ok")

    async def drain(p):
        return [d async for d in p.chat_stream([{"role": "user", "content": "hi"}])]

    p = _Once()
    assert [d.content for d in asyncio.run(drain(p))] == ["ok"]
    assert p.keys == ["gsk-first-key", "gsk-second-key"]


# ── in the background ───────────────────────────────────────────────────


class _Found:
    def __init__(self, ok: bool, error: str = "") -> None:
        self.ok, self.error = ok, error


class _Registry:
    def __init__(self, answers: list[_Found]) -> None:
        self.answers = answers
        self.health: dict[str, str] = {}
        self.switched: list[str] = []
        self.row = {"name": "groq", "base_url": "https://api.groq.com/openai/v1", "api_key": "gsk-real-key-1",
                    "enabled": True, "health": "unknown"}

    def list_providers(self):
        return [dict(self.row, health=self.health.get("groq", "unknown"))]

    def get_discovered_models(self, name):
        return []

    def get_provider(self, name):
        return dict(self.row)

    def resolve_provider_credentials(self, name):
        return self.row["base_url"], self.row["api_key"]

    def get_active_profile(self):
        return {"provider": "groq"}

    async def discover(self, name):
        return self.answers.pop(0)

    def set_provider_health(self, name, status):
        self.health[name] = status

    def switch_provider_key(self, name, key, exc):
        self.switched.append(key)
        return None


def test_unreachable_twice_is_said_once_and_an_answer_says_not_tested(monkeypatch):
    from kazma_core import provider_health as ph

    store = _Store()
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: store)
    said: list[str] = []
    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert",
                        lambda key, title, detail="", **kw: said.append(key) or True)
    registry = _Registry([_Found(False, "unreachable"), _Found(False, "unreachable"), _Found(True)])
    assert ph.health_sweep(registry=registry, now=10_000_000.0) == {"groq": "unreachable"}
    assert registry.health == {} and said == []  # once is not twice
    assert ph.health_sweep(registry=registry, now=10_000_001.0) == {}  # not due within the hour
    ph.health_sweep(registry=registry, now=10_000_000.0 + ph.EVERY_S)
    assert registry.health == {"groq": "down"} and said == ["llm.provider_unreachable.groq"]
    ph.health_sweep(registry=registry, now=10_000_000.0 + 2 * ph.EVERY_S)
    assert registry.health == {"groq": "unknown"} and said[-1] == "llm.provider_reachable.groq"


def test_a_refused_key_rests_when_the_provider_has_another(monkeypatch):
    from kazma_core import provider_health as ph

    store = _Store()
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: store)
    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert", lambda *a, **k: True)
    registry = _Registry([_Found(False, "http_401"), _Found(False, "http_401")])
    ph.health_sweep(registry=registry, now=10_000_000.0)
    assert registry.switched == []  # control: one key, nothing to switch to
    registry.row["api_keys"] = [{"id": "k2", "label": "b", "api_key": "gsk-real-key-2"}]
    ph.health_sweep(registry=registry, now=10_000_000.0 + ph.EVERY_S)
    assert registry.switched == ["gsk-real-key-1"]


def test_the_sweep_is_on_the_maintenance_cadence():
    from kazma_core.memory.worker_bootstrap import _MAINTENANCE_SWEEPS

    assert "provider health" in [name for name, _fn in _MAINTENANCE_SWEEPS]
