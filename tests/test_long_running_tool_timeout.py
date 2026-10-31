"""A tool that is long by design gets its time (2026-10-31).

Every tool call from chat was bounded at 120 s (a hung tool's bound). A deep
research run takes minutes -- it plans, searches, fetches and reads pages,
writes and checks a report -- so from chat it was always cut off: live, a
Kuwait history paper became a page-by-page search by the model. The tools in
``graph_helpers.LONG_RUNNING_TOOLS`` get at least their own time; every other
tool keeps the bound, and the turn's budget still bounds the whole turn.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from kazma_core.agent import graph_helpers


@pytest.fixture
def default_bound(monkeypatch):
    monkeypatch.delenv("KAZMA_TOOL_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: Mock(get=lambda key: None))


def test_a_long_tool_gets_its_own_time_and_others_keep_the_bound(default_bound):
    assert graph_helpers._resolve_tool_timeout() == 120.0
    assert graph_helpers._resolve_tool_timeout("web_search") == 120.0
    assert graph_helpers._resolve_tool_timeout("run_research_pipeline") == 1500.0
    assert graph_helpers._resolve_tool_timeout("crawl_site") == 900.0


@pytest.mark.parametrize("configured,expected", [("3000", 3000.0), ("0", 0.0), ("-1", -1.0)])
def test_a_longer_or_disabled_bound_is_the_operators(default_bound, monkeypatch, configured, expected):
    monkeypatch.setenv("KAZMA_TOOL_TIMEOUT_SECONDS", configured)
    assert graph_helpers._resolve_tool_timeout("run_research_pipeline") == expected


def test_every_long_tool_is_a_registered_tool():
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    assert set(graph_helpers.LONG_RUNNING_TOOLS) <= set(registry._tools)


async def _run(monkeypatch, name: str, seconds: float):
    from kazma_core.agent import graph_tool_worker
    from kazma_core.agent.state import initial_supervisor_state

    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    monkeypatch.setattr(graph_helpers, "_configured_tool_timeout", lambda: 0.3)
    monkeypatch.setitem(graph_helpers.LONG_RUNNING_TOOLS, "run_research_pipeline", 5.0)
    state = initial_supervisor_state(thread_id="long-tool")
    state["messages"] = [{"role": "user", "content": "research this"}]
    call = {"id": "call-1", "name": name, "arguments": {"query": "kuwait"}}
    state["tool_calls_pending"] = [call]

    async def slow(*args, **kwargs):
        await asyncio.sleep(seconds)
        return {"content": "report ready", "is_error": False}

    executor = Mock(execute=AsyncMock(side_effect=slow))
    out = await graph_tool_worker.tool_worker_node(state, tool_executor=executor, tracer=Mock(), hitl_config=None)
    return " ".join(str(m.get("content", "")) for m in out.get("messages", []) if m.get("role") == "tool")


@pytest.mark.asyncio
async def test_a_deep_research_call_is_not_cut_at_the_default_bound(monkeypatch):
    assert "report ready" in await _run(monkeypatch, "run_research_pipeline", 0.8)


@pytest.mark.asyncio
async def test_any_other_tool_is_still_cut_at_the_bound(monkeypatch):
    """Negative control: the same 0.8 s call of a tool not on the list."""
    assert "report ready" not in await _run(monkeypatch, "web_search", 0.8)
