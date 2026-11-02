"""A gate turned on after the graph was built gates the next tool call (FA-147).

The 2026-11 functional audit: the chat graph is built once and cached (the
app's holder graph, recompiled on a model switch; the agent's streaming
graph). Both build sites passed ``hitl_config=None`` when HITL was off at
build time, and the tool worker reads ``None`` as "this graph never gates":
turned on in Settings afterwards, the gate stayed off until a restart. The
worker re-reads the policy live whenever the graph holds a config, so the
sites pass the config whatever its state.

A real supervisor graph with a scripted model that asks for ``file_write``,
built the way the fixed site builds it while the gate is off; then the gate
is turned on in the settings store and a turn runs. The negative control is
the old site's ``None``.
"""

from __future__ import annotations

import asyncio

import pytest

from kazma_core.safety.hitl import get_hitl_config


def _graph(hitl_config):
    from langgraph.checkpoint.memory import InMemorySaver

    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.authority import create_authority
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.llm_provider import LLMResponse, ToolCall
    from kazma_core.tracing import KazmaTracer

    class AsksToWrite:
        calls = 0

        async def chat(self, *, messages, tools=None, model=None, **kwargs):
            AsksToWrite.calls += 1
            if AsksToWrite.calls == 1:
                return LLMResponse(content="", tool_calls=[ToolCall(
                    id="call-1", name="file_write", arguments={"path": "note.txt", "content": "hi"})],
                    finish_reason="tool_calls", model="stub", usage={"total_tokens": 1}, cost_usd=0.0)
            return LLMResponse(content="Done.", tool_calls=[], finish_reason="stop", model="stub",
                               usage={"total_tokens": 1}, cost_usd=0.0)

    registry = LocalToolRegistry(include_builtins=True)
    return build_supervisor_graph(
        llm=AsksToWrite(), system_prompt="base", tool_definitions=registry.get_tool_definitions(),
        tool_executor=registry, cost_breaker=create_cost_breaker(),
        authority=create_authority(model="test", window=128000), tracer=KazmaTracer(backend="console"),
        checkpointer=InMemorySaver(), hitl_config=hitl_config,
    )


def _interrupted(graph) -> bool:
    async def turn():
        config = {"configurable": {"thread_id": "t-147"}}
        await graph.ainvoke({"messages": [{"role": "user", "content": "write a note"}]}, config)
        snap = await graph.aget_state(config)
        return any(getattr(t, "interrupts", None) for t in snap.tasks or ())

    return asyncio.run(turn())


@pytest.fixture()
def gate_off_then_on(monkeypatch, tmp_path):
    from kazma_core.config_store import get_config_store
    from tests._no_models import keep_models_out

    keep_models_out(monkeypatch, llm=False)
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    monkeypatch.setenv("KAZMA_WORKSPACE", str(tmp_path))
    store = get_config_store()
    store.set("safety.hitl_enabled", False, category="safety")
    built_with = get_hitl_config({})  # what the site reads at build time: off
    assert built_with.get("enabled") is False
    yield built_with
    store.delete("safety.hitl_enabled")


def test_the_config_a_site_passes_is_read_live(gate_off_then_on):
    from kazma_core.config_store import get_config_store

    graph = _graph(gate_off_then_on)  # the fixed sites: the config, whatever its state
    get_config_store().set("safety.hitl_enabled", True, category="safety")
    assert _interrupted(graph)


def test_the_old_none_never_gated(gate_off_then_on):
    """Negative control: the sites passed None when the gate was off."""
    from kazma_core.config_store import get_config_store

    graph = _graph(None)
    get_config_store().set("safety.hitl_enabled", True, category="safety")
    assert not _interrupted(graph)


def test_both_cached_sites_pass_the_config_whatever_its_state():
    """The two build sites the audit named no longer drop a disabled config."""
    import inspect

    from kazma_core.agent_runner import KazmaAgent

    source = inspect.getsource(KazmaAgent.get_streaming_graph)
    assert "streaming_hitl = None" not in source
    from pathlib import Path

    app = (Path(__file__).resolve().parents[1] / "kazma-ui/kazma_ui/app.py").read_text(encoding="utf-8")
    start = app.index("def _recompile_holder_graph")
    assert "_hitl = None" not in app[start:start + 1500]
