"""Tests for per-turn memory retrieval (RAG) in the supervisor node.

Locks in the two core properties:
  1. ``_format_retrieved_memories`` renders memories into a compact block.
  2. The supervisor injects retrieved memories at iteration 0 but skips at
     iteration > 0 (once-per-turn, not per-ReAct-iteration).
"""

from __future__ import annotations

import pytest

from kazma_core.agent.graph_builder import (
    _format_retrieved_memories,
    _rag_top_k,
)


# ── Pure-function tests ────────────────────────────────────────────────


def test_format_memories_renders_block():
    """Memories render as a numbered '## Relevant context from memory' block."""
    mems = [
        {"content": "User prefers concise answers."},
        {"content": "Project uses Python 3.12."},
    ]
    block = _format_retrieved_memories(mems)
    assert "## Relevant context from memory" in block
    assert "1. User prefers concise answers." in block
    assert "2. Project uses Python 3.12." in block


def test_format_memories_empty_returns_empty():
    assert _format_retrieved_memories([]) == ""


def test_format_memories_caps_long_entries():
    """Each memory is capped to avoid blowing up the context window."""
    long = "x" * 1000
    block = _format_retrieved_memories([{"content": long}])
    # Should be truncated with ellipsis.
    assert "…" in block
    assert len(block) < 400


def test_format_memories_skips_empty_content():
    """Memories with no content are skipped."""
    block = _format_retrieved_memories([{"content": ""}, {"content": None}, {"text": "real"}])
    assert "real" in block
    assert "## Relevant context from memory" in block


def test_format_memories_uses_text_fallback():
    """Falls back to 'text' key when 'content' is absent."""
    block = _format_retrieved_memories([{"text": "from text key"}])
    assert "from text key" in block


def test_rag_top_k_default():
    """_rag_top_k returns a sane default (5) when config is unavailable."""
    k = _rag_top_k()
    assert isinstance(k, int)
    assert k >= 1


# ── Injection-logic tests (mocked authority) ───────────────────────────


class _FakeCompactor:
    """Minimal stand-in for CompactionEngine.retrieve_memories."""

    def __init__(self, memories: list[dict] | None = None) -> None:
        self._memories = memories if memories is not None else []
        self.called_with: list[tuple] = []

    async def retrieve_memories(self, query: str, limit: int = 5):
        self.called_with.append((query, limit))
        return self._memories


class _FakeAuthority:
    """Minimal stand-in for ContextAuthority."""

    def __init__(self, memories: list[dict] | None = None) -> None:
        self.compactor = _FakeCompactor(memories)

    async def check_and_enforce(self, state):
        return state  # no compaction


class _FakeLLMResponse:
    """Minimal stand-in for the LLM response object."""

    def __init__(self, content="ok"):
        self.content = content
        self.tool_calls = []
        self.model = "fake-model"
        self.usage = {"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5}
        self.cost_usd = 0.001


class _FakeLLM:
    """Captures the messages passed to .chat() so we can assert injection."""

    def __init__(self):
        self.chat_calls: list[dict] = []

    async def chat(self, messages=None, tools=None, model=None):
        self.chat_calls.append({"messages": list(messages or []), "model": model})
        return _FakeLLMResponse()


class _FakeCostBreaker:
    def should_halt(self) -> bool:
        return False

    def record_cost(self, cost: float) -> None:
        pass


class _FakeTracer:
    def trace_llm_call(self, **kwargs):
        pass


async def test_retrieval_injects_at_iteration_0():
    """At iteration 0, retrieved memories are injected as a system message."""
    from kazma_core.agent.graph_builder import supervisor_node
    from kazma_core.agent.state import NodeName

    mems = [{"content": "User likes dark mode."}]
    authority = _FakeAuthority(mems)
    llm = _FakeLLM()

    state = {
        "messages": [
            {"role": "system", "content": "You are Kazma."},
            {"role": "user", "content": "What theme do I like?"},
        ],
        "iteration": 0,
    }

    await supervisor_node(
        state,
        llm=llm,
        system_prompt="You are Kazma.",
        tool_definitions=[],
        tool_executor=None,
        cost_breaker=_FakeCostBreaker(),
        authority=authority,
        tracer=_FakeTracer(),
    )

    # The LLM should have received the injected memory system message.
    assert llm.chat_calls, "LLM was not called"
    sent = llm.chat_calls[0]["messages"]
    system_msgs = [m for m in sent if m.get("role") == "system"]
    assert any("dark mode" in m.get("content", "") for m in system_msgs), \
        "Memory block not injected into LLM messages"
    # retrieve_memories was called with the user's message as query.
    assert authority.compactor.called_with
    assert "theme" in authority.compactor.called_with[0][0]


async def test_retrieval_skipped_at_iteration_1():
    """At iteration > 0, retrieval is skipped (once per turn)."""
    from kazma_core.agent.graph_builder import supervisor_node

    mems = [{"content": "should not be injected"}]
    authority = _FakeAuthority(mems)
    llm = _FakeLLM()

    state = {
        "messages": [
            {"role": "system", "content": "You are Kazma."},
            {"role": "user", "content": "follow up"},
        ],
        "iteration": 1,  # not the first iteration
    }

    await supervisor_node(
        state,
        llm=llm,
        system_prompt="You are Kazma.",
        tool_definitions=[],
        tool_executor=None,
        cost_breaker=_FakeCostBreaker(),
        authority=authority,
        tracer=_FakeTracer(),
    )

    # retrieve_memories should NOT have been called.
    assert not authority.compactor.called_with, "Retrieval fired on iteration > 0"
