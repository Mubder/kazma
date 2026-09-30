"""A graph node's ContextVar bindings end with the node.

``graph_supervisor.supervisor_node`` binds the active turn context and the
scratchpad thread for the tools of its hop and never resets them. That is
correct only because LangGraph runs each node in a copy of the caller's
context (``asyncio.create_task(..., context=copy_context())``): the binding
dies with the hop. If a LangGraph upgrade ran nodes in the caller's context
instead, one turn's scope (a quarantined document search, suppressed recall)
would outlive the hop -- into the respond node and, on a WebSocket that runs
every turn of a connection in one task, into the next turn. This holds the
property the supervisor relies on.
"""

from __future__ import annotations

from typing import TypedDict

import pytest
from kazma_core.agent import turn_input
from kazma_core.agent.turn_input import (
    bind_scratchpad_thread,
    get_active_turn_context,
    reset_active_turn_context,
    reset_scratchpad_thread,
    set_active_turn_context,
)
from langgraph.graph import END, StateGraph


class _State(TypedDict, total=False):
    seen_in_next_node: dict


async def _binding_node(state: _State) -> _State:
    set_active_turn_context(active_goal="audit only", quarantine_documents_search=True)
    bind_scratchpad_thread("thread-A")
    assert get_active_turn_context()["active_goal"] == "audit only"
    return {}


async def _reading_node(state: _State) -> _State:
    return {
        "seen_in_next_node": {
            "turn": get_active_turn_context(),
            "scratchpad_thread": turn_input._scratchpad_thread_ctx.get(),
        }
    }


def _graph():
    g = StateGraph(_State)
    g.add_node("bind", _binding_node)
    g.add_node("read", _reading_node)
    g.set_entry_point("bind")
    g.add_edge("bind", "read")
    g.add_edge("read", END)
    return g.compile()


@pytest.mark.asyncio
async def test_a_nodes_bindings_do_not_reach_the_caller_or_the_next_node() -> None:
    assert get_active_turn_context() == {}
    result = await _graph().ainvoke({})

    # The next node runs in a copy of the CALLER's context, not the first node's.
    assert result["seen_in_next_node"] == {"turn": {}, "scratchpad_thread": None}
    # And nothing is left behind in the caller.
    assert get_active_turn_context() == {}
    assert turn_input._scratchpad_thread_ctx.get() is None


@pytest.mark.asyncio
async def test_negative_control_the_same_node_run_in_the_callers_context_leaks() -> None:
    """Awaited directly (no graph), the binding stays: the check above can fail."""
    turn_before = turn_input._active_turn_ctx.set(None)
    thread_before = turn_input._scratchpad_thread_ctx.set(None)
    try:
        await _binding_node({})
        assert get_active_turn_context()["active_goal"] == "audit only"
        assert turn_input._scratchpad_thread_ctx.get() == "thread-A"
    finally:
        reset_active_turn_context(turn_before)
        reset_scratchpad_thread(thread_before)
    assert get_active_turn_context() == {}
