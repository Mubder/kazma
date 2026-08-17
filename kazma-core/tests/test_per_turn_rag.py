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
    """Memories render as a bulleted '## Relevant context from memory' block,
    wrapped in the untrusted prompt-fence so the model treats them as
    observation data, not instructions."""
    mems = [
        {"content": "User prefers concise answers."},
        {"content": "Project uses Python 3.12."},
    ]
    block = _format_retrieved_memories(mems)
    assert "## Relevant context from memory" in block
    assert "- User prefers concise answers." in block
    assert "- Project uses Python 3.12." in block
    # Wrapped in the untrusted-data fence (defense-in-depth vs injection).
    assert "memory_rag" in block and "untrusted" in block


def test_format_memories_empty_returns_empty():
    assert _format_retrieved_memories([]) == ""


def test_format_memories_caps_long_entries():
    """Each memory entry is capped (300 chars + ellipsis) so the context
    window does not blow up. The cap is per-memory; the surrounding
    prompt-fence wrapper adds fixed overhead unrelated to the cap."""
    long = "x" * 1000
    block = _format_retrieved_memories([{"content": long}])
    # Should be truncated with an ellipsis.
    assert "…" in block
    # The single memory line ("- <300 x's>…") holds exactly 300 capped chars.
    # (The fence wrapper contributes a few stray "x"s of its own, e.g. "text",
    # so assert on the memory bullet line, not the whole block.)
    mem_line = next(l for l in block.splitlines() if l.startswith("- "))
    assert mem_line.count("x") == 300
    assert mem_line.endswith("…")


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

    async def chat(self, messages=None, tools=None, model=None, **kwargs):
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


async def test_retrieval_injects_at_iteration_0(monkeypatch):
    """At iteration 0, V2-recalled memories are injected as a system message.

    The V1 4-layer RRF path was removed in the V1→V2 cutover, so this now
    exercises the V2 recall path: force ``memory_v2_enabled`` True and mock
    ``recall`` + ``format_recall_block`` to return a block containing the
    memory, then assert it lands in the LLM's system messages.
    """
    import kazma_core.memory.config as _mcfg
    import kazma_core.memory.recall as _recall_mod

    monkeypatch.setattr(_mcfg, "memory_v2_enabled", lambda cfg=None: True)

    class _FakeRecallResult:
        empty = False
        beliefs = [{"content": "User likes dark mode."}]
        episodes = []

    captured_queries: list[str] = []

    def _fake_recall(query, limit=5, session_id=None, tenant_id=None, explain=False):
        captured_queries.append(query)
        return _FakeRecallResult()

    def _fake_format(result, explain=False):
        return "## Recalled memories\n- User likes dark mode."

    monkeypatch.setattr(_recall_mod, "recall", _fake_recall)
    monkeypatch.setattr(_recall_mod, "format_recall_block", _fake_format)

    from kazma_core.agent.graph_builder import supervisor_node

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
        authority=_FakeAuthority([]),
        tracer=_FakeTracer(),
    )

    # The LLM should have received the injected memory system message.
    assert llm.chat_calls, "LLM was not called"
    sent = llm.chat_calls[0]["messages"]
    system_msgs = [m for m in sent if m.get("role") == "system"]
    assert any("dark mode" in m.get("content", "") for m in system_msgs), \
        "Memory block not injected into LLM messages"
    # recall was invoked with the user's message as the query.
    assert captured_queries
    assert "theme" in captured_queries[0]


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
