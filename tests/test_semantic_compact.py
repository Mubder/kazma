"""Semantic compact of dropped history (industry stack part 3)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kazma_core.agent.semantic_compact import inject_summary_of_dropped
from kazma_core.compaction import CompactionEngine
from kazma_core.llm_provider import LLMError, LLMResponse


@pytest.mark.asyncio
async def test_inject_summary_of_dropped_inserts_note() -> None:
    before = [
        {"role": "system", "content": "You are Kazma."},
        {"role": "user", "content": "old task"},
        {"role": "assistant", "content": "did a thing with tools"},
        {"role": "user", "content": "new question"},
    ]
    after = [
        {"role": "system", "content": "You are Kazma."},
        {"role": "user", "content": "new question"},
    ]
    out = await inject_summary_of_dropped(before, after, llm=None)
    assert out[0]["content"] == "You are Kazma."
    assert out[1]["role"] == "system"
    assert "CONTEXT SUMMARY" in out[1]["content"] or "old task" in out[1]["content"]
    assert out[-1]["content"] == "new question"


@pytest.mark.asyncio
async def test_semantic_compact_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_SEMANTIC_COMPACT", "0")
    before = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    after = [{"role": "user", "content": "c"}]
    out = await inject_summary_of_dropped(before, after, llm=None)
    assert out == after


@pytest.mark.asyncio
async def test_compaction_engine_accepts_llm_response() -> None:
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value=LLMResponse(content="[CONTEXT SUMMARY] facts [/CONTEXT SUMMARY]")
    )
    engine = CompactionEngine(llm_client=llm)
    summary = await engine.summarize(
        [{"role": "user", "content": "do the thing"}, {"role": "assistant", "content": "ok"}]
    )
    assert "CONTEXT SUMMARY" in summary
    llm.chat.assert_awaited()


class _HaltNever:
    def should_halt(self) -> bool:
        return False

    def record_cost(self, cost: float) -> None:
        pass

    def record_user_interaction(self) -> None:
        pass


class _NoCounterAuthority:
    async def check_and_enforce(self, state):
        return state


class _NoopTracer:
    def trace_llm_call(self, **kwargs):
        pass


@pytest.mark.asyncio
async def test_overflow_retry_compacts() -> None:
    """Supervisor retry loop: context_overflow triggers semantic compact once."""
    from kazma_core.agent.graph_supervisor import supervisor_node

    calls = {"n": 0}

    class _BoomThenOk:
        async def chat(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise LLMError("prompt too long", transient=False, kind="context_overflow")
            return LLMResponse(
                content="Here is a complete answer after compaction.",
                finish_reason="stop",
                usage={"total_tokens": 8},
            )

    out = await supervisor_node(
        {
            "messages": [{"role": "user", "content": "hello there, please answer"}],
            "iteration": 0,
            "max_iterations": 5,
            "thread_id": "t-compact",
        },
        llm=_BoomThenOk(),
        system_prompt="You are Kazma.",
        tool_definitions=[],
        tool_executor=None,
        cost_breaker=_HaltNever(),
        authority=_NoCounterAuthority(),
        tracer=_NoopTracer(),
    )
    assert calls["n"] == 2
    assert any(
        "complete answer after compaction" in str(m.get("content") or "")
        for m in (out.get("messages") or [])
    )
