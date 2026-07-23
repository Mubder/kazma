"""Tests for /undo and /edit checkpoint mutation (LangGraph aupdate_state)."""

from __future__ import annotations

from typing import Any, TypedDict

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph


class _MsgState(TypedDict, total=False):
    messages: list[dict[str, Any]]


def _compile_graph():
    g = StateGraph(_MsgState)

    async def node(state: _MsgState) -> _MsgState:
        return {"messages": state.get("messages", [])}

    g.add_node("n", node)
    g.set_entry_point("n")
    g.add_edge("n", END)
    return g.compile(checkpointer=MemorySaver())


async def _seed(graph, thread_id: str, messages: list[dict[str, Any]]) -> dict:
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    await graph.ainvoke({"messages": messages}, config)
    return config


@pytest.mark.asyncio
async def test_undo_removes_last_assistant():
    graph = _compile_graph()
    config = await _seed(
        graph,
        "undo-1",
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "again"},
            {"role": "assistant", "content": "second"},
        ],
    )

    snap = await graph.aget_state(config)
    messages = list(snap.values["messages"])
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "assistant":
            messages.pop(i)
            break
    await graph.aupdate_state(config, {"messages": messages})

    snap2 = await graph.aget_state(config)
    roles = [m["role"] for m in snap2.values["messages"]]
    contents = [m.get("content") for m in snap2.values["messages"]]
    assert roles == ["user", "assistant", "user"]
    assert "second" not in contents
    assert "hello" in contents


@pytest.mark.asyncio
async def test_edit_replaces_last_assistant():
    graph = _compile_graph()
    config = await _seed(
        graph,
        "edit-1",
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "wrong answer"},
        ],
    )

    snap = await graph.aget_state(config)
    messages = list(snap.values["messages"])
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "assistant":
            messages[i] = {"role": "assistant", "content": "corrected"}
            break
    await graph.aupdate_state(config, {"messages": messages})

    snap2 = await graph.aget_state(config)
    assert snap2.values["messages"][-1]["content"] == "corrected"


@pytest.mark.asyncio
async def test_undo_strips_tool_messages_before_assistant():
    graph = _compile_graph()
    config = await _seed(
        graph,
        "undo-tools",
        [
            {"role": "user", "content": "run"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "name": "x"}]},
            {"role": "tool", "content": "out", "tool_call_id": "1"},
            {"role": "assistant", "content": "done"},
        ],
    )

    snap = await graph.aget_state(config)
    messages = list(snap.values["messages"])
    i = len(messages) - 1
    while i >= 0:
        role = messages[i].get("role")
        if role == "assistant":
            messages.pop(i)
            j = i - 1
            while j >= 0:
                r = messages[j].get("role")
                if r == "tool":
                    messages.pop(j)
                    j -= 1
                    continue
                if r == "assistant" and messages[j].get("tool_calls"):
                    messages.pop(j)
                break
            break
        i -= 1
    await graph.aupdate_state(config, {"messages": messages})

    snap2 = await graph.aget_state(config)
    roles = [m["role"] for m in snap2.values["messages"]]
    assert roles == ["user"]
