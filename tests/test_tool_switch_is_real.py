"""Settings -> Tools' switch takes a tool away from the model (FA-250).

The 2026-11 functional audit: a tool switched off on Settings -> Tools was
still offered to the model and still ran. The switch wrote
``tools.<name>.enabled`` and only the page's own listing read it.

Through the real route; then the registry's definitions, a direct call, the
list a graph built before the switch holds (``offered``), and a real
supervisor model call. The negative control is the registry before the fix,
which read the skill switch alone.
"""

from __future__ import annotations

import asyncio

import pytest

from kazma_core.skills import switches

TOOL = "current_datetime"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient

    from kazma_core.config_store import get_config_store
    from kazma_ui.settings import create_settings_router

    monkeypatch.setattr(switches, "_tools_off", None)
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "settings.html").write_text("ok")
    app = FastAPI()
    app.include_router(create_settings_router(MagicMock(), get_config_store(),
                                              Jinja2Templates(directory=str(tmp_path / "templates"))))
    yield TestClient(app)
    monkeypatch.setattr(switches, "_tools_off", None)


def _registry():
    from kazma_core.agent.tool_registry import LocalToolRegistry

    return LocalToolRegistry(include_builtins=True)


def _names(defs):
    return {d["function"]["name"] for d in defs}


def _switch(client, enabled: bool) -> None:
    resp = client.put(f"/api/settings/tools/{TOOL}/toggle", json={"enabled": enabled})
    assert resp.status_code == 200


def test_a_tool_switched_off_is_neither_offered_nor_run(client):
    registry = _registry()
    built = registry.get_tool_definitions()  # what a graph holds from its build
    assert TOOL in _names(built)

    _switch(client, False)
    assert TOOL not in _names(registry.get_tool_definitions())
    assert TOOL not in _names(switches.offered(built))  # the next model call's list
    result = asyncio.run(registry.execute(TOOL, {}))
    assert result["is_error"] and "switched off" in result["content"]

    _switch(client, True)
    assert TOOL in _names(registry.get_tool_definitions())
    assert asyncio.run(registry.execute(TOOL, {}))["is_error"] is False


def test_the_next_model_call_does_not_get_the_tool(client, monkeypatch):
    """A real supervisor graph, built while the tool was on."""
    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.authority import create_authority
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.llm_provider import LLMResponse
    from kazma_core.tracing import KazmaTracer
    from tests._no_models import keep_models_out

    keep_models_out(monkeypatch, llm=False)
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    seen: list[set[str]] = []

    class RecordingLLM:
        async def chat(self, *, messages, tools=None, model=None, **kwargs):
            seen.append(_names(tools or []))
            return LLMResponse(content="Done.", tool_calls=[], finish_reason="stop",
                               model="stub", usage={"total_tokens": 1}, cost_usd=0.0)

    registry = _registry()
    graph = build_supervisor_graph(
        llm=RecordingLLM(), system_prompt="base", tool_definitions=registry.get_tool_definitions(),
        tool_executor=registry, cost_breaker=create_cost_breaker(),
        authority=create_authority(model="test", window=128000), tracer=KazmaTracer(backend="console"),
    )

    async def ask(thread):
        await graph.ainvoke({"messages": [{"role": "user", "content": "what time is it?"}]},
                            {"configurable": {"thread_id": thread}})

    asyncio.run(ask("before"))
    _switch(client, False)
    asyncio.run(ask("after"))
    assert TOOL in seen[0] and TOOL not in seen[-1]


def test_the_old_registry_still_offered_and_ran_it(client, monkeypatch):
    """Negative control: the skill switch alone (the registry before the fix)."""
    _switch(client, False)
    monkeypatch.setattr(switches, "_is_tool_enabled", lambda name: True)
    registry = _registry()
    assert TOOL in _names(registry.get_tool_definitions())
    assert asyncio.run(registry.execute(TOOL, {}))["is_error"] is False
