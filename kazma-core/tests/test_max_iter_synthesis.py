"""Max-iteration respond must synthesize when last message is a tool result.

Regression: early assistant chatter (pre-HITL thought) made respond_node skip
synthesis after max iterations, so YOLO/shell rabbit-holes delivered only a
short leftover (~53 chars) and the UI looked like no final answer.
"""

from __future__ import annotations

import pytest


def _has_final_assistant_after_tools(msgs: list[dict]) -> bool:
    """Mirror of the decision helper in graph_builder.respond_node."""
    last_tool_idx = -1
    for i, m in enumerate(msgs):
        if isinstance(m, dict) and m.get("role") in ("tool", "function"):
            last_tool_idx = i
    if last_tool_idx < 0:
        for m in reversed(msgs):
            if not isinstance(m, dict):
                continue
            if m.get("role") not in ("assistant", "ai"):
                continue
            content = m.get("content") or ""
            if isinstance(content, str) and content.strip() and not m.get("tool_calls"):
                return True
        return False
    for m in msgs[last_tool_idx + 1 :]:
        if not isinstance(m, dict):
            continue
        if m.get("role") not in ("assistant", "ai"):
            continue
        content = m.get("content") or ""
        if isinstance(content, str) and content.strip() and not m.get("tool_calls"):
            return True
    return False


def test_mid_loop_assistant_chatter_does_not_count_as_final():
    msgs = [
        {"role": "user", "content": "افحص الذاكرة"},
        {"role": "assistant", "content": "ممتاز قاعدة البيانات فيها FTS5"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "name": "shell_exec"}]},
        {"role": "tool", "content": "not authorized COUNT"},
        {"role": "tool", "content": "not authorized LIKE"},
    ]
    # Old bug: any plain assistant text → skip synthesis
    old = any(
        isinstance(m, dict)
        and m.get("role") in ("assistant", "ai")
        and str(m.get("content") or "").strip()
        and not m.get("tool_calls")
        for m in msgs
    )
    assert old is True
    assert _has_final_assistant_after_tools(msgs) is False


def test_final_assistant_after_tools_counts():
    msgs = [
        {"role": "user", "content": "check memory"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "ok"},
        {"role": "assistant", "content": "كل شيء شغال"},
    ]
    assert _has_final_assistant_after_tools(msgs) is True


def test_needs_synthesis_when_last_is_tool():
    msgs = [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": "thinking out loud"},
        {"role": "tool", "content": "result"},
    ]
    last_role = msgs[-1]["role"]
    max_hit = True
    needs = max_hit and (
        last_role in ("tool", "function")
        or not _has_final_assistant_after_tools(msgs)
    )
    assert needs is True


@pytest.mark.asyncio
async def test_respond_node_synthesizes_after_max_with_tool_tail(monkeypatch):
    """Full respond_node path: last role=tool + early chatter → LLM synthesis runs."""
    from kazma_core.agent import graph_builder as gb

    class _Resp:
        content = "تقرير: memory_search نجح؛ sqlite_query محدود — لا حاجة لقلق."

    class _LLM:
        async def chat(self, messages, tools=None):
            assert tools is None
            # last wrap message should force final answer
            assert messages[-1]["role"] == "user"
            assert "tool-round limit" in messages[-1]["content"]
            return _Resp()

    state = {
        "messages": [
            {"role": "user", "content": "افحص لي الداكره"},
            {"role": "assistant", "content": "ممتاز قاعدة البيانات"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "shell_exec", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "ok listing"},
        ],
        "iteration": 15,
        "max_iterations": 15,
        "_llm": _LLM(),
    }

    # Avoid scheduling post-turn memory side effects
    monkeypatch.setattr(
        "kazma_core.memory.consolidator.schedule_post_turn_memory",
        lambda *_a, **_k: None,
    )

    out = await gb.respond_node(state, llm=_LLM())
    finals = [
        m
        for m in out["messages"]
        if m.get("role") == "assistant" and (m.get("content") or "").strip() and not m.get("tool_calls")
    ]
    assert any("memory_search" in (m.get("content") or "") or "تقرير" in (m.get("content") or "") for m in finals)
