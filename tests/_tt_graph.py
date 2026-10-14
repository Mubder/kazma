"""A real LangGraph graph for the Time Travel tests (audit TT-12).

Every restore, fork and approve test of the Time Travel remediation runs on
this: the real ``SupervisorState`` schema (the scratchpad's merge reducer
included), the product's node names and routing (supervisor -> tool_worker
or respond, tool_worker -> supervisor, respond -> END), a tool worker that
pauses with ``interrupt()`` before a tool, the product's capture hook
(``time_travel.capture_after_supervisor``), and a real saver: the product's
SQLite saver (``checkpoints_shared.get_shared_sqlite_saver``) or the
Postgres one (``checkpoints_pg.open_postgres_checkpointer``, when
``KAZMA_DATABASE_URL`` and ``KAZMA_TEST_ALLOW_REAL_DB=1`` name a throwaway
database). The fakes that recorded ``aupdate_state`` arguments are gone: a
fake cannot show what a reducer, ``next`` or an interrupt does with a write.

A turn's goal names what the scripted supervisor does: ``"tool:target"``
calls ``tool`` with ``{"target": target}`` behind an approval, then
answers; anything else answers at once.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

SAVERS = ("sqlite", "postgres")


def run(coro: Any) -> Any:
    """``asyncio.run`` on the server's loop kind: a selector loop (psycopg's
    async driver refuses Windows' default Proactor loop; AGENTS §23)."""
    import asyncio
    import sys

    if sys.platform == "win32":
        return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(coro)


def postgres_dsn() -> str | None:
    """The throwaway database's DSN, or None (the Postgres variants skip)."""
    dsn = os.environ.get("KAZMA_DATABASE_URL") or ""
    if dsn and os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") == "1":
        return dsn
    return None


@asynccontextmanager
async def open_saver(kind: str, tmp_path: Path):
    """A product saver of *kind*; closed after."""
    if kind == "sqlite":
        from kazma_core.checkpoints_shared import close_shared_checkpoints, get_shared_sqlite_saver

        saver = await get_shared_sqlite_saver(str(tmp_path / "checkpoints.db"))
        try:
            yield saver
        finally:
            await close_shared_checkpoints()
        return
    dsn = postgres_dsn()
    if dsn is None:
        import pytest

        pytest.skip("needs a real Postgres (KAZMA_DATABASE_URL + KAZMA_TEST_ALLOW_REAL_DB=1)")
    from kazma_core.checkpoints_pg import close_postgres_checkpointer, open_postgres_checkpointer

    saver = await open_postgres_checkpointer(dsn, max_size=2)
    try:
        yield saver
    finally:
        await close_postgres_checkpointer(saver)


def build_graph(recorder: Any, executed: list[str], checkpointer: Any) -> Any:
    """The scripted graph (module docstring)."""
    from langgraph.graph import END, StateGraph
    from langgraph.types import interrupt

    from kazma_core.agent.state import NodeName, SupervisorState
    from kazma_core.time_travel import capture_after_supervisor

    async def supervisor(state: dict[str, Any]) -> dict[str, Any]:
        it = int(state.get("iteration") or 0)
        goal = str(state.get("active_goal") or "")
        messages = list(state.get("messages") or [])
        tool, sep, target = goal.partition(":")
        if state.get("tool_calls_done") or not sep:
            # As graph_supervisor does: the final step carries the answer and
            # routes to respond, which finalizes it.
            messages.append({"role": "assistant", "content": f"Answer to {goal}"})
            result: dict[str, Any] = {"iteration": it + 1, "next_node": NodeName.RESPOND,
                                      "tool_calls_pending": [], "messages": messages}
        else:
            call = {"id": f"call-{uuid.uuid4().hex[:8]}", "name": tool, "arguments": {"target": target}}
            messages.append({"role": "assistant", "content": "", "tool_calls": [{
                "id": call["id"], "type": "function",
                "function": {"name": tool, "arguments": json.dumps(call["arguments"])},
            }]})
            result = {
                "iteration": it + 1, "next_node": NodeName.TOOL_WORKER, "messages": messages,
                "tool_calls_pending": [call], "tool_calls_done": [],
                "scratchpad": {f"note {target}": target},
            }
        return await capture_after_supervisor(recorder, state, result)

    def tool_worker(state: dict[str, Any]) -> dict[str, Any]:
        pending = list(state.get("tool_calls_pending") or [])
        decision = interrupt({
            "type": "hitl_approval", "kind": "security",
            "tool": pending[0]["name"] if pending else "",
            "args": pending[0]["arguments"] if pending else {},
            "tools": [p["name"] for p in pending],
            "message": "approve?",
        })
        ok = isinstance(decision, dict) and bool(decision.get("approved"))
        messages = list(state.get("messages") or [])
        done = []
        for p in pending:
            if ok:
                executed.append(p["arguments"]["target"])
            content = "ran" if ok else "denied"
            messages.append({"role": "tool", "tool_call_id": p["id"], "content": content})
            done.append({"tool_call_id": p["id"], "name": p["name"], "content": content})
        return {"tool_calls_done": done, "tool_calls_pending": [], "messages": messages,
                "next_node": NodeName.SUPERVISOR}

    def respond(state: dict[str, Any]) -> dict[str, Any]:
        return {"next_node": "end", "messages": list(state.get("messages") or [])}

    def route(state: dict[str, Any]) -> str:
        nxt = state.get("next_node")
        if nxt == NodeName.TOOL_WORKER:
            return NodeName.TOOL_WORKER
        if nxt == NodeName.SUPERVISOR:
            return NodeName.SUPERVISOR
        return NodeName.RESPOND

    g = StateGraph(SupervisorState)
    g.add_node(NodeName.SUPERVISOR, supervisor)
    g.add_node(NodeName.TOOL_WORKER, tool_worker)
    g.add_node(NodeName.RESPOND, respond)
    g.set_entry_point(NodeName.SUPERVISOR)
    g.add_conditional_edges(NodeName.SUPERVISOR, route, {
        NodeName.SUPERVISOR: NodeName.SUPERVISOR,
        NodeName.TOOL_WORKER: NodeName.TOOL_WORKER,
        NodeName.RESPOND: NodeName.RESPOND,
    })
    g.add_edge(NodeName.TOOL_WORKER, NodeName.SUPERVISOR)
    g.add_edge(NodeName.RESPOND, END)
    return g.compile(checkpointer=checkpointer)


def config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


async def turn_input(graph: Any, thread_id: str, text: str) -> dict[str, Any]:
    """What a transport sends for a new turn: a fresh state (iteration 0, a
    new ``created_at``) carrying the checkpoint's messages and the question."""
    from kazma_core.agent.state import initial_supervisor_state

    snap = await graph.aget_state(config(thread_id))
    history = list(((snap.values if snap is not None else None) or {}).get("messages") or [])
    state = dict(initial_supervisor_state(thread_id=thread_id, max_iterations=15))
    state.pop("scratchpad", None)  # the transports never send it (merge reducer, §29A)
    state["messages"] = [*history, {"role": "user", "content": text}]
    state["active_goal"] = text
    return state


async def run_turn(graph: Any, thread_id: str, text: str) -> Any:
    """One turn, until it ends or pauses at an approval."""
    return await graph.ainvoke(await turn_input(graph, thread_id, text), config(thread_id))


def live_interrupt(snapshot: Any) -> tuple[str, dict[str, Any] | None]:
    """The approval interrupt a checkpoint is paused at: ``(id, payload)``."""
    for task in getattr(snapshot, "tasks", None) or ():
        for intr in getattr(task, "interrupts", None) or ():
            value = getattr(intr, "value", None)
            if isinstance(value, dict) and value.get("type") == "hitl_approval":
                return str(intr.id), value
    return "", None


async def decide(graph: Any, thread_id: str, approved: bool) -> Any:
    """Answer the live approval the way every transport does: the product's
    resume builder, keyed by the live interrupt's id."""
    from kazma_core.safety.commitment.resume import build_resume_command

    snap = await graph.aget_state(config(thread_id))
    iid, payload = live_interrupt(snap)
    cmd = build_resume_command(payload, approved=approved, interrupt_id=iid)
    return await graph.ainvoke(cmd, config(thread_id))
