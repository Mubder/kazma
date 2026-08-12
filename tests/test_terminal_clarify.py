"""PR3 — terminal clarify invariant (Class B loop kill).

An unresolved / cancelled / denied commitment clarify is now a TERMINAL turn
outcome: it routes straight to RESPOND (never hands the model a retryable tool
error) and does NOT credit the hard-failure breaker (so it can't poison the
next turn). These tests lock both halves of the invariant — the classifier
semantics and the end-to-end graph behavior — that make the permission-card
loop class unkillable (incident 2026-08-12).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

import aiosqlite
import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from kazma_core.agent.tool_loop_breaker import (
    HARD_FAILURE_THRESHOLD,
    ToolOutcome,
    classify_tool_result,
    update_breaker,
)


# ── Unit: TERMINAL classification + credit hygiene ───────────────────


def test_terminal_outcome_classifies_as_terminal_not_hard():
    r = {"content": "clarify unresolved", "is_error": True, "outcome": "terminal"}
    assert classify_tool_result(r) is ToolOutcome.TERMINAL


def test_terminal_does_not_credit_hard_failure_breaker():
    """A terminal round must reset consecutive to 0 (was 2) — not +1 to 3."""
    terminal = {"content": "x", "is_error": True, "outcome": "terminal"}
    state, _ = update_breaker(2, [terminal])
    assert state.consecutive_hard_rounds == 0
    assert state.tripped is False


def test_hard_failure_still_credits():
    """Sanity: a genuine HARD failure still credits +1 toward the threshold."""
    hard = {"content": "boom", "is_error": True}
    state, _ = update_breaker(HARD_FAILURE_THRESHOLD - 1, [hard])
    assert state.consecutive_hard_rounds == HARD_FAILURE_THRESHOLD
    assert state.tripped is True


# ── Integration: graph-level terminal routing ─────────────────────────


class ClarifyState(TypedDict, total=False):
    messages: list[dict[str, Any]]
    tool_calls_pending: list[dict[str, Any]]
    tool_calls_done: list[dict[str, Any]]
    tool_results: dict[str, Any]
    next_node: str
    consecutive_tool_failures: int
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


def _ambiguous_state() -> ClarifyState:
    return {
        "messages": [{"role": "user", "content": "remind me in 2 days"}],
        "tool_calls_pending": [{"id": "tc1", "name": "schedule_task",
                                "arguments": {"timing": "2d", "prompt": "x"}}],
        "tenant_id": "default",
    }


@pytest.mark.asyncio
async def test_cancel_is_terminal_forces_respond_no_counter_poison(seeded, tmp_path):
    graph, exe, conn = await _build_graph(tmp_path)
    config = {"configurable": {"thread_id": "t-term-cancel"}}
    try:
        await graph.ainvoke(_ambiguous_state(), config)
        assert exe.executed == []
        result = await graph.ainvoke(Command(resume={"tc1": "cancel"}), config)
        assert exe.executed == [], "cancelled clarify must NOT execute"
        # TERMINAL → RESPOND (not back to supervisor for a retry)
        assert (result or {}).get("next_node") == "respond"
        # counter not poisoned
        assert int((result or {}).get("consecutive_tool_failures", 0)) == 0
        done = (result or {}).get("tool_calls_done", [])
        assert any(str(d.get("outcome")) == "terminal" for d in done), \
            "cancelled clarify must stamp outcome=terminal"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_unresolved_resume_is_terminal_ends_turn(seeded, tmp_path):
    """The loop cause: a resume value that maps to no option. Previously this
    became a 'retry' tool error and the model re-issued schedule_task forever.
    Now it is TERMINAL — the turn ends immediately, no execution, no loop."""
    graph, exe, conn = await _build_graph(tmp_path)
    config = {"configurable": {"thread_id": "t-term-unresolved"}}
    try:
        await graph.ainvoke(_ambiguous_state(), config)
        assert exe.executed == []
        # bogus option id → no matching option, not cancel → unresolved terminal
        result = await graph.ainvoke(Command(resume={"tc1": "totally_bogus_opt"}), config)
        assert exe.executed == [], "unresolved clarify must NOT execute — no retry"
        assert (result or {}).get("next_node") == "respond", \
            "unresolved clarify must force RESPOND (end the turn)"
        assert int((result or {}).get("consecutive_tool_failures", 0)) == 0
        done = (result or {}).get("tool_calls_done", [])
        assert any(str(d.get("outcome")) == "terminal" for d in done)
        assert any("unresolved" in str(d.get("content", "")).lower() for d in done)
    finally:
        await conn.close()
