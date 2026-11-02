"""The bubble names the model that answered, not the active one (FA-210).

The 2026-11 functional audit (C10): a turn pinned to another model showed the
ACTIVE model in the live done frame and the bubble's meta line, so a failover
answer was credited to the model that had failed. The done frame read
``get_active_profile()``; the turn's checkpoint already said who answered
(``last_model``, set by the supervisor from the answering call).

A real supervisor graph with a real checkpointer, through the real streamer:
an answer from the failover model, and one from a model other than the active
profile's. The negative control is the active profile's model, which the old
frame showed.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from kazma_core.llm_provider import LLMError, LLMResponse


def _frames_of(frames: list[str], event: str) -> list[dict[str, Any]]:
    out = []
    for frame in frames:
        body = frame.lstrip("id: 0123456789\n")
        if body.startswith(f"event: {event}"):
            out.append(json.loads(body.split("data: ", 1)[1].split("\n\n", 1)[0]))
    return out


class _Answers:
    def __init__(self, model: str) -> None:
        self.model = model

    async def chat(self, messages=None, tools=None, *args, **kwargs):
        return LLMResponse(content=f"Answered by {self.model}.", tool_calls=[], finish_reason="stop",
                           model=self.model, usage={"total_tokens": 3}, cost_usd=0.0)


class _Fails:
    async def chat(self, messages=None, tools=None, *args, **kwargs):
        raise LLMError("provider down", transient=True)


def _active_model() -> str:
    from kazma_core.model_registry import get_model_registry

    return str(get_model_registry().get_active_profile().get("model") or "")


def _turn(monkeypatch, tmp_path, llm: Any, thread: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """One turn through the real graph and streamer: (done frame, stored row)."""
    from langgraph.checkpoint.memory import MemorySaver

    from kazma_core import retry
    from kazma_core.checkpoint_serde import kazma_checkpoint_serde
    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.authority import create_authority
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.tracing import KazmaTracer
    from kazma_ui.session_manager import get_session_manager, reset_session_manager
    from kazma_ui.sse_chat import _stream_langgraph_events
    from tests._no_models import keep_models_out

    keep_models_out(monkeypatch, llm=False)
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    monkeypatch.setattr(retry, "load_retry_config",
                        lambda: {"max_attempts": 1, "min_wait": 0, "max_wait": 0})
    reset_session_manager()
    session = f"sess-{thread}"
    sess = get_session_manager().get_or_create(session)
    sess.messages = [{"role": "user", "content": "hello"},
                     {"role": "assistant", "turn_id": f"turn-{thread}", "content": ""}]
    graph = build_supervisor_graph(
        llm=llm, system_prompt="base", tool_definitions=[], tool_executor=None,
        cost_breaker=create_cost_breaker(), checkpointer=MemorySaver(serde=kazma_checkpoint_serde()),
        authority=create_authority(model="test", window=128000), tracer=KazmaTracer(backend="console"),
    )

    async def collect():
        return [f async for f in _stream_langgraph_events(
            graph, {"messages": [{"role": "user", "content": "hello"}]},
            {"configurable": {"thread_id": thread}},
            thread_id=thread, session_id=session, reply_turn_id=f"turn-{thread}",
        )]

    done = _frames_of(asyncio.run(collect()), "done")[-1]
    row = next(m for m in get_session_manager().get(session).messages
               if m.get("role") == "assistant" and m.get("turn_id") == f"turn-{thread}")
    return done, row


def test_a_failover_answer_names_the_failover_model(monkeypatch, tmp_path):
    from kazma_core.agent import graph_supervisor, nonstop
    from kazma_core.model_registry import get_model_registry

    backup = "backup-model-fa210"
    real = nonstop.get_nonstop_config

    def with_failover(*args, **kwargs):
        cfg = real(*args, **kwargs)
        cfg.failover.enabled, cfg.failover.chain, cfg.failover.cooldown_seconds = True, [backup], 0.0
        return cfg

    monkeypatch.setattr(nonstop, "get_nonstop_config", with_failover)
    # The failover model's client, cached for the registry as it is now.
    monkeypatch.setitem(graph_supervisor._failover_clients, backup, _Answers(backup))
    monkeypatch.setattr(graph_supervisor, "_failover_built_for", get_model_registry().generation)
    done, row = _turn(monkeypatch, tmp_path, _Fails(), "fa210-failover")
    assert done["content"] == f"Answered by {backup}."
    assert done["model"] == backup and row.get("model") == backup
    # Negative control: the frame before FA-210 named the active model, which
    # is not the one that answered.
    assert _active_model() != backup


def test_an_answer_from_another_model_names_that_model(monkeypatch, tmp_path):
    other = "pinned-model-fa210"
    done, row = _turn(monkeypatch, tmp_path, _Answers(other), "fa210-other")
    assert done["model"] == other and row.get("model") == other
    assert _active_model() != other


@pytest.mark.parametrize("answered", ["", "  "])
def test_with_no_answering_model_the_active_one_is_named(monkeypatch, tmp_path, answered):
    """A checkpoint that names no model (no model call) keeps the old answer."""
    class _NoModel(_Answers):
        async def chat(self, *args, **kwargs):
            resp = await super().chat(*args, **kwargs)
            resp.model = answered
            return resp

    done, _row = _turn(monkeypatch, tmp_path, _NoModel(answered), f"fa210-none-{len(answered)}")
    assert done["model"] == _active_model()
