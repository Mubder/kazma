"""Phase 2 — consolidated §8.3 scenario suite (Commitment Layer).

Readable end-to-end regression scenarios. The targeted suites
(test_commitment_* ) cover the units; this file consolidates the headline
flows a reviewer or on-call engineer can read top-to-bottom to see the system
do the right thing. Led by the full CoPilot incident replay — BOTH gates
(schedule + memory) exercised together, the scenario the whole plan exists to
prevent.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    """Isolated DBs with copilot_next_reset = 2026-09-01 (user-asserted)."""
    state_db = tmp_path / "state.db"
    ops_db = tmp_path / "ops.db"
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(state_db))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(ops_db))
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
    from kazma_core.memory.belief_mutation import mutate_belief

    p = sqlite3.connect(state_db); p.row_factory = sqlite3.Row
    o = sqlite3.connect(ops_db)
    ensure_primary_schema(p); ensure_ops_schema(o)
    mutate_belief(p, "user", "copilot_next_reset", "2026-09-01",
                  ops_conn=o, importance=5, extraction_method="user_explicit")
    p.commit()
    yield (p, o)
    p.close(); o.close()


class _Rec:
    def __init__(self): self.calls = []
    async def execute(self, name, args):
        self.calls.append((name, dict(args)))
        return {"content": "ok", "is_error": False}


class _Tracer:
    def trace_tool_execution(self, **kw): pass


def _active_belief(p, predicate):
    return p.execute(
        "SELECT object FROM beliefs WHERE predicate=? "
        "AND valid_until IS NULL AND invalidated_at IS NULL", (predicate,),
    ).fetchone()


# ── Scenario 1: the full CoPilot incident replay (BOTH gates) ──────────────

@pytest.mark.anyio
async def test_scenario_copilot_incident_full_replay(seeded):
    """THE scenario the plan exists for. Memory holds Sep 1; the user asks
    'before the reset in 2 days'; the model schedules the WRONG date and the
    post-turn extractor tries to overwrite Sep 1 with the invented date.

    Both halves must be blocked:
      (a) schedule gate rewrites fire_at to Sep 1 − 2d = Aug 30 (not Aug 13)
      (b) memory gate refuses to overwrite the user_explicit Sep 1 belief
    """
    from kazma_core.agent.graph_builder import tool_worker_node
    from kazma_core.agent.state import initial_supervisor_state
    from kazma_core.memory.belief_mutation import mutate_belief

    p, o = seeded
    exe = _Rec()
    state = initial_supervisor_state(thread_id="t1")
    state["messages"] = [{"role": "user",
                          "content": "remind me before the copilot monthly reset in 2 days"}]
    state["tool_calls_pending"] = [{"id": "c1", "name": "schedule_task",
                                    "arguments": {"timing": "2026-08-13T10:00:00+00:00",
                                                  "prompt": "reset soon"}}]
    await tool_worker_node(state, tool_executor=exe, tracer=_Tracer(), hitl_config=None)

    # (a) schedule half: gate rewrote the args to the correct date
    assert exe.calls and exe.calls[0][1]["timing"].startswith("2026-08-30"), (
        "schedule gate must anchor to memory (Aug 30), not the model's Aug 13")

    # (b) memory half: the extractor's invented date may NOT overwrite Sep 1
    mutate_belief(p, "user", "copilot_next_reset", "2026-08-13",
                  ops_conn=o, importance=3, extraction_method="llm_inferred")
    assert _active_belief(p, "copilot_next_reset")[0] == "2026-09-01", (
        "memory gate must preserve the user-asserted Sep 1 belief")


# ── Scenario 6: pure read is unaffected by the gate ────────────────────────

@pytest.mark.anyio
async def test_scenario_pure_read_unaffected(seeded):
    """file_read executes without any gate interference (semantic_tier=none)."""
    from kazma_core.agent.graph_builder import tool_worker_node
    from kazma_core.agent.state import initial_supervisor_state

    exe = _Rec()
    state = initial_supervisor_state(thread_id="t1")
    state["messages"] = [{"role": "user", "content": "read the config file"}]
    state["tool_calls_pending"] = [{"id": "c1", "name": "file_read",
                                    "arguments": {"path": "/etc/config"}}]
    await tool_worker_node(state, tool_executor=exe, tracer=_Tracer(), hitl_config=None)
    assert exe.calls and exe.calls[0][0] == "file_read"
    assert exe.calls[0][1]["path"] == "/etc/config"  # unchanged


# ── Scenario 7: clear-intent danger tool still runs (semantic allow) ───────

@pytest.mark.anyio
async def test_scenario_clear_remind_allows_and_rewrites(seeded):
    """A clean 'before the reset in N days' is allow+rewrite — the gate adds
    safety without blocking correct intent. (The HITL security card for
    schedule_task is a separate axis, tested in test_hitl_graph_integration.)"""
    from kazma_core.agent.graph_builder import tool_worker_node
    from kazma_core.agent.state import initial_supervisor_state

    exe = _Rec()
    state = initial_supervisor_state(thread_id="t1")
    state["messages"] = [{"role": "user",
                          "content": "remind me 3 days before the copilot reset"}]
    state["tool_calls_pending"] = [{"id": "c1", "name": "schedule_task",
                                    "arguments": {"timing": "3d", "prompt": "x"}}]
    await tool_worker_node(state, tool_executor=exe, tracer=_Tracer(), hitl_config=None)
    assert exe.calls  # allowed to execute
    assert exe.calls[0][1]["timing"].startswith("2026-08-29")  # Sep 1 − 3d


# ── Scenario: kill-switch disables the whole layer ─────────────────────────

@pytest.mark.anyio
async def test_scenario_killswitch_off_passes_model_args(seeded, monkeypatch):
    """KAZMA_COMMITMENT_ENABLED=0 → the gate steps aside entirely; the model's
    (wrong) timing passes through unchanged. The escape hatch works."""
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    from kazma_core.agent.graph_builder import tool_worker_node
    from kazma_core.agent.state import initial_supervisor_state

    exe = _Rec()
    state = initial_supervisor_state(thread_id="t1")
    state["messages"] = [{"role": "user",
                          "content": "remind me before the copilot monthly reset in 2 days"}]
    state["tool_calls_pending"] = [{"id": "c1", "name": "schedule_task",
                                    "arguments": {"timing": "2026-08-13T10:00:00+00:00",
                                                  "prompt": "x"}}]
    await tool_worker_node(state, tool_executor=exe, tracer=_Tracer(), hitl_config=None)
    assert exe.calls
    assert exe.calls[0][1]["timing"].startswith("2026-08-13")  # untouched


# ── Scenario: approve-after-expiry is denied (§3.9 rule 2) ─────────────────

def test_scenario_approve_after_expiry_denied(seeded):
    """§3.9 rule 2 (no late approve): an expired commitment cannot be revived
    to committed/ready — an approval arriving after the TTL is refused at the
    store level, regardless of the resume path."""
    from kazma_core.safety.commitment.store import (
        Commitment, create_commitment, get_commitment, sweep_expired, update_status,
    )
    import time
    cid = create_commitment(Commitment(thread_id="t1", act="remind", status="needs_confirm"))
    import sqlite3
    from kazma_core.paths import memory_ops_db
    with sqlite3.connect(memory_ops_db()) as conn:
        conn.execute("UPDATE commitments SET expires_at=? WHERE commitment_id=?",
                     (time.time() - 10, cid))
        conn.commit()
    assert sweep_expired() == 1
    assert get_commitment(cid).status == "expired"
    # late approve attempt → REFUSED, stays expired
    out = update_status(cid, "committed", event_type="late_approve_attempt")
    assert out is not None
    assert out.status == "expired", "expired commitment must not be revived to committed"
    # late re-ready attempt also refused
    assert update_status(cid, "ready").status == "expired"
