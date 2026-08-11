"""Phase 3 — semantic clarify/confirm interrupt card, end-to-end (§4.3).

Drives a REAL LangGraph (AsyncSqliteSaver checkpointer) wrapping
tool_worker_node. An ambiguous remind (relative + nearby memory event) pauses
the graph at the semantic interrupt with kind=semantic_clarify + discrete
options. Resume with a chosen option applies its slots_patch and the tool
executes with the chosen fire_at; resume with "cancel" returns a denied result.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypedDict

import aiosqlite
import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command


class ClarifyState(TypedDict, total=False):
    messages: list[dict[str, Any]]
    tool_calls_pending: list[dict[str, Any]]
    tool_calls_done: list[dict[str, Any]]
    tool_results: dict[str, Any]
    next_node: str
    tenant_id: str


class _Rec:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    async def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        args.pop("_hitl_approved", None)
        self.executed.append((name, dict(args)))
        return {"content": f"executed {name}", "is_error": False}


class _Tracer:
    def trace_tool_execution(self, **kw: Any) -> None:
        pass


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    """Isolated DBs with a grok reset ~4d out (nearby a 'in 2 days' candidate)."""
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "ops.db"))
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
    from kazma_core.memory.belief_mutation import mutate_belief

    p = sqlite3.connect(tmp_path / "state.db"); p.row_factory = sqlite3.Row
    o = sqlite3.connect(tmp_path / "ops.db")
    ensure_primary_schema(p); ensure_ops_schema(o)
    grok_date = (datetime.now(timezone.utc) + timedelta(days=4)).date().isoformat()
    mutate_belief(p, "user", "grok_next_reset", grok_date, ops_conn=o,
                  importance=5, extraction_method="user_explicit")
    p.commit()
    yield
    p.close(); o.close()


async def _build_graph(tmp_path):
    from kazma_core.agent.graph_builder import tool_worker_node

    exe = _Rec()
    tr = _Tracer()

    async def worker(state: ClarifyState) -> dict[str, Any]:
        return await tool_worker_node(state, tool_executor=exe, tracer=tr, hitl_config=None)

    b = StateGraph(ClarifyState)
    b.add_node("worker", worker)
    b.set_entry_point("worker")
    b.add_edge("worker", END)
    conn = await aiosqlite.connect(str(tmp_path / "ckpt.db"))
    await conn.execute("PRAGMA journal_mode=WAL")
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    return b.compile(checkpointer=saver), exe, conn


@pytest.mark.asyncio
async def test_clarify_pauses_then_resume_applies_choice(seeded, tmp_path):
    """Ambiguous remind pauses with a semantic_clarify card; resume with an
    option applies its fire_at and the tool executes."""
    graph, exe, conn = await _build_graph(tmp_path)
    config = {"configurable": {"thread_id": "t-clz-1"}}
    state: ClarifyState = {
        "messages": [{"role": "user", "content": "remind me in 2 days"}],
        "tool_calls_pending": [{"id": "tc1", "name": "schedule_task",
                                "arguments": {"timing": "2d", "prompt": "x"}}],
        "tenant_id": "default",
    }
    try:
        await graph.ainvoke(state, config)
        # paused at the semantic interrupt; nothing executed yet
        assert exe.executed == [], "ambiguous remind must NOT execute before the card"
        snap = await graph.aget_state(config)
        assert snap.next is not None, "graph should be paused"
        payloads = [intr.value for task in snap.tasks for intr in task.interrupts]
        clz = next((p for p in payloads if isinstance(p, dict)
                    and p.get("kind") in ("semantic_clarify", "semantic_confirm")), None)
        assert clz is not None, "expected a semantic clarify interrupt"
        assert clz["items"][0]["tool"] == "schedule_task"
        opt_ids = {o["id"] for o in clz["items"][0]["options"]}
        assert "from_now" in opt_ids and "cancel" in opt_ids

        # resume with the from_now option
        await graph.ainvoke(Command(resume={"tc1": "from_now"}), config)
        assert len(exe.executed) == 1, "patched remind should execute after resume"
        name, args = exe.executed[0]
        assert name == "schedule_task"
        # the from_now option patched timing to the candidate (now + 2d)
        from_now_date = (datetime.now(timezone.utc) + timedelta(days=2)).date()
        assert args["timing"].startswith(from_now_date.isoformat()), (
            f"expected from-now timing {from_now_date}, got {args['timing']}")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_clarify_resume_cancel_blocks(seeded, tmp_path):
    """Resume with 'cancel' returns a denied result; the tool never executes."""
    graph, exe, conn = await _build_graph(tmp_path)
    config = {"configurable": {"thread_id": "t-clz-2"}}
    state: ClarifyState = {
        "messages": [{"role": "user", "content": "remind me in 2 days"}],
        "tool_calls_pending": [{"id": "tc1", "name": "schedule_task",
                                "arguments": {"timing": "2d", "prompt": "x"}}],
        "tenant_id": "default",
    }
    try:
        await graph.ainvoke(state, config)
        assert exe.executed == []
        result = await graph.ainvoke(Command(resume={"tc1": "cancel"}), config)
        assert exe.executed == [], "cancelled clarify must NOT execute"
        done = (result or {}).get("tool_calls_done", [])
        assert any("cancelled" in str(d.get("content", "")).lower() for d in done)
    finally:
        await conn.close()
