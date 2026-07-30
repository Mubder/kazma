"""Regression tests for the silent empty-answer turn ("Done", no reply).

Background: an empty LLM reply (``content=""``, no tool calls) at iteration 0
was passed straight through to the UI, which showed the turn as "Done" with
no bubble text. Two guards now cover this:

1. ``supervisor_node`` recovers an empty reply with a nudge retry on ANY
   iteration (previously gated on ``iteration > 0``).
2. ``respond_node`` injects an honest fallback message if the final assistant
   text is empty on a non-max-iteration turn (defence-in-depth).

These are pure-logic / monkeypatched tests — no real network/API calls.
"""

from __future__ import annotations

import pytest


# ── Minimal stand-ins (mirrors the patterns in test_per_turn_rag.py) ──────


class _FakeCompactor:
    async def retrieve_memories(self, query, limit=5):
        return []


class _FakeAuthority:
    def __init__(self):
        self.compactor = _FakeCompactor()

    async def check_and_enforce(self, state):
        return state


class _FakeCostBreaker:
    def should_halt(self) -> bool:
        return False

    def record_cost(self, cost: float) -> None:
        pass


class _FakeTracer:
    def trace_llm_call(self, **kwargs):
        pass


class _Response:
    def __init__(self, content="", tool_calls=None, model="fake-model"):
        self.content = content
        self.tool_calls = tool_calls or []
        self.model = model
        self.usage = {"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5}
        self.cost_usd = 0.001


class _ScriptedLLM:
    """LLM stub that returns scripted responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.chat_calls: list[dict] = []

    async def chat(self, messages=None, tools=None, model=None):
        self.chat_calls.append({"messages": list(messages or []), "tools": tools, "model": model})
        if self._responses:
            return self._responses.pop(0)
        return _Response(content="fallback")


# ── supervisor_node: empty reply at iteration 0 triggers a nudge ─────────


@pytest.mark.asyncio
async def test_empty_reply_at_iteration_0_triggers_nudge(monkeypatch):
    """The 2026-07-31 regression: an empty reply on the FIRST supervisor
    round (iteration=0, tool_calls=0, content="") must be recovered via the
    nudge retry — not streamed to the user as an empty bubble."""
    from kazma_core.agent.graph_builder import supervisor_node
    from kazma_core.agent.state import NodeName

    # First call returns empty (the bug); the nudge retry returns real text.
    llm = _ScriptedLLM([
        _Response(content=""),                       # empty first reply
        _Response(content="Here is my opinion on…"),  # recovered on nudge
    ])

    state = {
        "messages": [
            {"role": "system", "content": "You are Kazma."},
            {"role": "user", "content": "What's the best memory structure?"},
        ],
        "iteration": 0,  # <-- the key: first round
    }

    out = await supervisor_node(
        state,
        llm=llm,
        system_prompt="You are Kazma.",
        tool_definitions=[],
        tool_executor=None,
        cost_breaker=_FakeCostBreaker(),
        authority=_FakeAuthority(),
        tracer=_FakeTracer(),
    )

    # The nudge retry must have run — two LLM calls.
    assert len(llm.chat_calls) == 2, "nudge retry did not fire on iteration 0"

    # The recovered content is the assistant message, routed to respond.
    assistant_msgs = [
        m for m in out["messages"]
        if m.get("role") == "assistant" and (m.get("content") or "").strip()
    ]
    assert assistant_msgs, "empty reply was not recovered into a real answer"
    assert "Here is my opinion" in assistant_msgs[-1]["content"]

    # No tool calls pending — turn goes to the respond node.
    assert out["next_node"] == NodeName.RESPOND


# ── respond_node: empty final text on a normal turn gets a fallback ──────


@pytest.mark.asyncio
async def test_respond_node_injects_fallback_when_final_text_empty(monkeypatch):
    """Defence-in-depth: if a normal (non-max-iter) turn somehow reaches
    respond_node with empty final assistant text, the user still gets a
    visible message instead of an empty "Done" turn."""
    from kazma_core.agent import graph_builder as gb

    monkeypatch.setattr(
        "kazma_core.memory.consolidator.schedule_post_turn_memory",
        lambda *_a, **_k: None,
    )

    class _LLM:
        async def chat(self, messages, tools=None):
            # Synthesis should NOT run (this is not a max-iter turn); if it
            # did, it would be a fabricated answer.
            return _Response(content="FABRICATED")

    state = {
        "messages": [
            {"role": "user", "content": "give me your opinion"},
            # Empty final assistant message — the symptom.
            {"role": "assistant", "content": ""},
        ],
        "iteration": 1,            # not at the max-iter limit
        "max_iterations": 15,
    }

    out = await gb.respond_node(state, llm=_LLM())

    finals = [
        m["content"]
        for m in out["messages"]
        if m.get("role") == "assistant" and (m.get("content") or "").strip()
    ]
    # A fallback was appended so the UI is not empty.
    assert finals, "no assistant message was injected for an empty final turn"
    assert "FABRICATED" not in finals  # synthesis did not run
    assert any(t.strip().startswith("⚠️") for t in finals), \
        "fallback message was not an honest notice"


@pytest.mark.asyncio
async def test_respond_node_no_fallback_when_final_text_present(monkeypatch):
    """A normal turn with a real final answer must NOT get the fallback."""
    from kazma_core.agent import graph_builder as gb

    monkeypatch.setattr(
        "kazma_core.memory.consolidator.schedule_post_turn_memory",
        lambda *_a, **_k: None,
    )

    class _LLM:
        async def chat(self, messages, tools=None):
            return _Response(content="SHOULD NOT BE USED")

    state = {
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Here is a full, real answer for you."},
        ],
        "iteration": 1,
        "max_iterations": 15,
    }

    out = await gb.respond_node(state, llm=_LLM())

    # No extra assistant message appended — the real answer stands alone.
    assistant_after = [
        m for m in out["messages"]
        if m.get("role") == "assistant" and (m.get("content") or "").strip()
    ]
    assert len(assistant_after) == 1
    assert "real answer" in assistant_after[0]["content"]
