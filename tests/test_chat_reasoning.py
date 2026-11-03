"""The owner's reasoning level reaches every chat model call (roadmap theme 7).

``agent.chat_reasoning`` (``default`` / ``off`` / ``low`` / ``high`` /
``max``) is sent to DeepSeek's own API as ``thinking`` (off) or
``reasoning_effort``; any other provider gets nothing, and a model that
refuses the field is asked again without it. A real chat turn through the
supervisor graph carries it; the negative control is the call as it was
before the setting (nothing sent), and a planted call site without the level
fails the class gate.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import httpx
import pytest

from kazma_core.llm_provider import LLMConfig, LLMProvider

DEEPSEEK = "https://api.deepseek.com/v1"
ROOT = Path(__file__).resolve().parents[1]

_SSE = (
    'data: {"id":"x","model":"deepseek-flash","choices":[{"index":0,"delta":{"content":"Done."},'
    '"finish_reason":null}]}\n\n'
    'data: {"id":"x","model":"deepseek-flash","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}\n\n'
    "data: [DONE]\n\n"
)


def _provider(seen: list[dict], *, base_url: str = DEEPSEEK, refuse_effort: bool = False) -> LLMProvider:
    """An LLMProvider whose HTTP goes to a scripted DeepSeek."""

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        if refuse_effort and "reasoning_effort" in payload:
            return httpx.Response(400, json={"error": {"message": "Unknown field: reasoning_effort"}})
        if payload.get("stream"):
            return httpx.Response(200, text=_SSE, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={
            "model": "deepseek-flash",
            "choices": [{"message": {"content": "Done."}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})

    provider = LLMProvider(LLMConfig(base_url=base_url, api_key="demo-deepseek-key", model="deepseek-flash"))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url=base_url)

    async def get_client():
        return client

    provider._get_client = get_client
    return provider


def _sent(payload: dict) -> tuple:
    return payload.get("thinking"), payload.get("reasoning_effort")


@pytest.mark.asyncio
@pytest.mark.parametrize(("level", "expected"), [
    (None, (None, None)),
    ("off", ({"type": "disabled"}, None)),
    ("low", (None, "low")),
    ("high", (None, "high")),
    ("max", (None, "max")),
])
async def test_each_level_reaches_deepseek(level, expected):
    seen: list[dict] = []
    await _provider(seen).chat([{"role": "user", "content": "hi"}], reasoning=level)
    assert _sent(seen[-1]) == expected


@pytest.mark.asyncio
async def test_another_provider_gets_nothing():
    seen: list[dict] = []
    provider = _provider(seen, base_url="https://api.example.org/v1")
    assert provider.can_turn_off_reasoning() is False
    await provider.chat([{"role": "user", "content": "hi"}], reasoning="low")
    assert _sent(seen[-1]) == (None, None)


@pytest.mark.asyncio
async def test_a_model_that_refuses_the_field_is_asked_again_without_it():
    seen: list[dict] = []
    response = await _provider(seen, refuse_effort=True).chat(
        [{"role": "user", "content": "hi"}], reasoning="low")
    assert response.content == "Done."
    assert [p.get("reasoning_effort") for p in seen] == ["low", None]


@pytest.mark.asyncio
async def test_the_stream_carries_the_level(monkeypatch):
    import kazma_core.llm_stream as llm_stream

    monkeypatch.setattr(llm_stream, "stream_enabled", lambda: True)
    seen: list[dict] = []
    final = None
    async for delta in _provider(seen).chat_stream([{"role": "user", "content": "hi"}], reasoning="low"):
        final = delta.response or final
    assert seen[-1].get("stream") is True and seen[-1].get("reasoning_effort") == "low"
    assert final is not None and final.content == "Done."


# ── the setting ────────────────────────────────────────────────────────────


def test_the_setting_takes_a_level_and_refuses_anything_else():
    from kazma_core.settings_validation import SettingRejected, validate_setting

    assert validate_setting("agent.chat_reasoning", " Low ") == ("low", "agent")
    with pytest.raises(SettingRejected):
        validate_setting("agent.chat_reasoning", "medium")


@pytest.mark.asyncio
async def test_the_setting_becomes_the_keyword_only_where_it_is_a_switch():
    from kazma_core.agent.chat_reasoning import CHAT_REASONING_KEY, chat_reasoning_kwargs
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    store.set(CHAT_REASONING_KEY, "low", category="agent")
    assert await chat_reasoning_kwargs(_provider([])) == {"reasoning": "low"}
    assert await chat_reasoning_kwargs(object()) == {}, "a client with no switch gets nothing"
    store.set(CHAT_REASONING_KEY, "default", category="agent")
    assert await chat_reasoning_kwargs(_provider([])) == {}
    store.set(CHAT_REASONING_KEY, "sideways", category="agent")  # an old or hand-written value
    assert await chat_reasoning_kwargs(_provider([])) == {}


# ── a real chat turn ───────────────────────────────────────────────────────


async def _turn_payloads(monkeypatch, level: str) -> list[dict]:
    from kazma_core.agent.chat_reasoning import CHAT_REASONING_KEY
    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.authority import create_authority
    from kazma_core.config_store import get_config_store
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.tracing import KazmaTracer
    from tests._no_models import keep_models_out

    keep_models_out(monkeypatch, llm=False)
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    get_config_store().set(CHAT_REASONING_KEY, level, category="agent")
    seen: list[dict] = []
    deepseek = _provider(seen)
    # The supervisor takes the registry's live client for each call
    # (runtime/live_llm.py): the registry hands back the scripted DeepSeek.
    from kazma_core.model_registry import ModelRegistry

    monkeypatch.setattr(ModelRegistry, "get_client", lambda self, *a, **k: deepseek)
    graph = build_supervisor_graph(
        llm=deepseek, system_prompt="base", tool_definitions=[],
        tool_executor=LocalToolRegistry(include_builtins=False),
        cost_breaker=create_cost_breaker(), authority=create_authority(model="test", window=128000),
        tracer=KazmaTracer(backend="console"),
    )
    await graph.ainvoke({"messages": [{"role": "user", "content": "What is 2 + 2?"}]},
                        {"configurable": {"thread_id": f"t-{level}"}})
    assert seen, "the model was not called"
    return seen


@pytest.mark.asyncio
async def test_a_chat_turn_sends_the_chosen_level(monkeypatch):
    seen = await _turn_payloads(monkeypatch, "low")
    assert all(p.get("reasoning_effort") == "low" for p in seen), [_sent(p) for p in seen]


@pytest.mark.asyncio
async def test_a_chat_turn_on_default_sends_nothing(monkeypatch):
    seen = await _turn_payloads(monkeypatch, "default")
    assert all(_sent(p) == (None, None) for p in seen)


@pytest.mark.asyncio
async def test_without_the_setting_the_turn_sends_nothing(monkeypatch):
    # Negative control: the chat call as it was before the setting.
    import kazma_core.agent.graph_supervisor as supervisor

    async def before(client):
        return {}

    monkeypatch.setattr(supervisor, "chat_reasoning_kwargs", before)
    seen = await _turn_payloads(monkeypatch, "low")
    assert all(p.get("reasoning_effort") is None for p in seen)


# ── class gate: every chat model call carries the level ────────────────────

_CHAT_TURN_MODULES = ("kazma-core/kazma_core/agent/graph_supervisor.py",
                      "kazma-core/kazma_core/agent/graph_respond.py")


def _calls_without_the_level(source: str) -> list[int]:
    """Lines of ``invoke_llm_chat(...)`` calls that pass no reasoning level."""
    missing = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "invoke_llm_chat"):
            continue
        if any(kw.arg == "reasoning" for kw in node.keywords):
            continue
        if any(kw.arg is None and "reasoning" in ast.unparse(kw.value) for kw in node.keywords):
            continue
        missing.append(node.lineno)
    return missing


def test_every_chat_model_call_carries_the_level():
    for rel in _CHAT_TURN_MODULES:
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert "invoke_llm_chat(" in source
        assert _calls_without_the_level(source) == [], rel


def test_the_gate_sees_a_call_without_the_level():
    planted = "async def f(llm):\n    return await invoke_llm_chat(llm, [], tools=None)\n"
    assert _calls_without_the_level(planted) == [2]


# ── the Settings page's route ──────────────────────────────────────────────


def test_the_page_reads_the_level_and_whether_the_provider_takes_it(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient

    from kazma_core.config_store import ConfigStore
    from kazma_ui.settings import create_settings_router

    class Registry:
        def __init__(self, base_url):
            self.base_url = base_url

        def get_active_profile(self):
            return {"provider": "deepseek", "model": "deepseek-flash"}

        def list_providers(self):
            return [{"name": "deepseek", "base_url": self.base_url}]

    store = ConfigStore(db_path=str(tmp_path / "api.db"))
    store.set("agent.chat_reasoning", "low", category="agent")
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "settings.html").write_text("ok")
    app = FastAPI()
    app.include_router(create_settings_router(MagicMock(), store, Jinja2Templates(directory=str(tmp_path / "templates"))))
    client = TestClient(app)
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: Registry(DEEPSEEK))
    assert client.get("/api/settings/chat-reasoning").json() == {
        "level": "low", "levels": ["default", "off", "low", "high", "max"],
        "provider": "deepseek", "applies": True}
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry",
                        lambda: Registry("https://api.example.org/v1"))
    assert client.get("/api/settings/chat-reasoning").json()["applies"] is False
