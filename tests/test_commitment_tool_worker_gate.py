"""Phase 2 — tool_worker_node semantic-gate integration (Commitment §3.2 MVP).

Drives the REAL tool_worker_node (not a mock) and proves the commitment gate
fires inside it: a schedule_task call with the model's WRONG date gets its args
rewritten to the memory-anchored correct date before execution; an ambiguous
remind is blocked (held for clarification) instead of firing wrong.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def isolated_dbs(tmp_path, monkeypatch):
    """Isolated memory + ops DBs with a seeded functional belief."""
    state_db = tmp_path / "state.db"
    ops_db = tmp_path / "ops.db"
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(state_db))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(ops_db))
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
    from kazma_core.memory.belief_mutation import mutate_belief

    p = sqlite3.connect(state_db)
    p.row_factory = sqlite3.Row
    o = sqlite3.connect(ops_db)
    ensure_primary_schema(p)
    ensure_ops_schema(o)
    mutate_belief(p, "user", "copilot_next_reset", "2026-09-01",
                  ops_conn=o, importance=5, extraction_method="user_explicit")
    p.commit()
    yield {"copilot": True}
    p.close()
    o.close()


class _RecExecutor:
    """Records execute() calls so the test can inspect the args the gate produced."""
    def __init__(self):
        self.calls = []

    async def execute(self, name, args):
        self.calls.append((name, dict(args)))
        return {"content": f"executed {name}", "is_error": False}


class _NoopTracer:
    def trace_tool_execution(self, **kw):
        pass


def _state(text, tool_name, args):
    from kazma_core.agent.state import initial_supervisor_state

    s = initial_supervisor_state(thread_id="t1")
    s["messages"] = [{"role": "user", "content": text}]
    s["tool_calls_pending"] = [{"id": "c1", "name": tool_name, "arguments": args}]
    return s


@pytest.mark.anyio
async def test_gate_rewrites_wrong_date_to_memory_anchored_date(isolated_dbs):
    """CAPSTONE: tool_worker receives schedule_task with the model's invented
    Aug 13; the gate anchors to copilot_next_reset=Sep 1 and rewrites the args
    to fire_at = Sep 1 − 2d = Aug 30 BEFORE execution."""
    from kazma_core.agent.graph_builder import tool_worker_node

    exe = _RecExecutor()
    state = _state(
        "remind me before the copilot monthly reset in 2 days",
        "schedule_task",
        {"timing": "2026-08-13T10:00:00+00:00", "prompt": "reset soon"},
    )
    await tool_worker_node(state, tool_executor=exe, tracer=_NoopTracer(), hitl_config=None)

    assert exe.calls, "schedule_task should have executed (allow+rewrite)"
    name, args = exe.calls[0]
    assert name == "schedule_task"
    assert args["timing"].startswith("2026-08-30"), (
        f"gate must rewrite to Sep 1 − 2d = Aug 30, got {args['timing']}")
    assert args["prompt"] == "reset soon"  # non-timing args preserved


@pytest.mark.anyio
async def test_gate_blocks_ambiguous_remind(tmp_path, monkeypatch):
    """Ambiguous remind engages the clarify INTERRUPT (Phase 3 card). Outside a
    graph the interrupt signal propagates (no runner to catch it) — proving the
    gate HOLDS the tc for the card rather than executing it. The full pause +
    resume is covered by test_commitment_clarify_card (real graph)."""
    state_db = tmp_path / "state.db"
    ops_db = tmp_path / "ops.db"
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(state_db))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(ops_db))
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.agent.graph_builder import tool_worker_node

    p = sqlite3.connect(state_db); p.row_factory = sqlite3.Row
    o = sqlite3.connect(ops_db)
    ensure_primary_schema(p); ensure_ops_schema(o)
    mutate_belief(p, "user", "grok_next_reset", "2026-08-15", ops_conn=o,
                  importance=5, extraction_method="user_explicit")
    p.commit()

    exe = _RecExecutor()
    state = _state("remind me in 2 days", "schedule_task", {"timing": "2d", "prompt": "x"})
    # interrupt() raises outside a graph — that's the pause signal a real graph
    # runner catches. The point: the tc was held (not executed) for the card.
    with pytest.raises(Exception):
        await tool_worker_node(state, tool_executor=exe, tracer=_NoopTracer(), hitl_config=None)
    assert not exe.calls, "ambiguous remind must be held for the card, not executed"
    p.close(); o.close()


@pytest.mark.anyio
async def test_killswitch_disables_gate(isolated_dbs, monkeypatch):
    """KAZMA_COMMITMENT_ENABLED=0 → gate off → original (wrong) args pass through."""
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    from kazma_core.agent.graph_builder import tool_worker_node

    exe = _RecExecutor()
    state = _state(
        "remind me before the copilot monthly reset in 2 days",
        "schedule_task",
        {"timing": "2026-08-13T10:00:00+00:00", "prompt": "x"},
    )
    await tool_worker_node(state, tool_executor=exe, tracer=_NoopTracer(), hitl_config=None)
    assert exe.calls
    # gate OFF → original wrong timing untouched
    assert exe.calls[0][1]["timing"].startswith("2026-08-13")
