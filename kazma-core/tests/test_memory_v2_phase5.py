"""Memory V2 Phase 5 tests — wiring the cognitive loop live.

Covers the "last mile" that turns V2 from built into actually running:
  - belief_extractor: heuristic extraction, gatekeeper, E2E mutation
  - entity resolution wired into the extractor (entities registered)
  - format_recall_block token budget enforcement
  - worker_bootstrap: handler registration + macro_sleep handler E2E
  - procedural feed via LocalToolRegistry.execute

All tests use tmp_path + KAZMA_DATA_DIR override and run in demo mode
(skip legacy LLM) so the V2 thread completes fast.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _disable_commitment_gate(monkeypatch: pytest.MonkeyPatch):
    """Disable the commitment layer for the procedural-feed test.

    It executes a fabricated tool name ("echo_tool") not in the side-effect
    registry — the unregistered-mutator fail-closed DENY (default ON since
    2026-08-15) blocks it before the procedural recorder ever observes a
    run, so the procedural_dags table is never written (deep-audit
    2026-08-19 CI triage; same fixture as tests/test_mcp_bridge.py).
    """
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")


@pytest.fixture()
def isolated_data(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_DEMO_MODE", "1")
    from kazma_core.memory import dual_write

    dual_write.reset_mirror()
    yield tmp_path
    dual_write.reset_mirror()


def _open_dbs(isolated_data):
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
    from kazma_core.paths import memory_ops_db, primary_memory_db

    p = sqlite3.connect(primary_memory_db(), isolation_level=None)
    p.row_factory = sqlite3.Row
    ensure_primary_schema(p)
    o = sqlite3.connect(memory_ops_db(), isolation_level=None)
    ensure_ops_schema(o)
    return p, o


# ── Extractor + gatekeeper ────────────────────────────────────────────────


def test_gatekeeper_skips_filler(isolated_data):
    from kazma_core.memory.belief_extractor import is_filler_turn

    assert is_filler_turn("okay thanks!")
    assert is_filler_turn("lol")
    assert is_filler_turn("got it")
    assert not is_filler_turn("My name is Alice")
    assert not is_filler_turn("I live in Paris")
    # Durable cue wins even if it contains a filler word
    assert not is_filler_turn("remember that ok")


@pytest.mark.asyncio
async def test_extract_and_apply_heuristic(isolated_data):
    from kazma_core.memory.belief_extractor import extract_and_apply_beliefs

    p, o = _open_dbs(isolated_data)
    stats = await extract_and_apply_beliefs(
        p, o, "My name is Alice and I live in Paris", use_llm=False, session_id="s1", turn=1
    )
    assert stats["source"] == "heuristic"
    assert stats["applied"] >= 2
    beliefs = [
        (r["predicate"], r["object"])
        for r in p.execute(
            "SELECT predicate, object FROM beliefs WHERE valid_until IS NULL"
        ).fetchall()
    ]
    assert ("name_is", "Alice") in beliefs
    assert ("lives_in", "Paris") in beliefs
    p.close()
    o.close()


@pytest.mark.asyncio
async def test_extract_and_apply_filler_skipped(isolated_data):
    from kazma_core.memory.belief_extractor import extract_and_apply_beliefs

    p, o = _open_dbs(isolated_data)
    stats = await extract_and_apply_beliefs(p, o, "okay thanks", use_llm=False)
    assert stats["skipped_filler"] is True
    assert stats["applied"] == 0
    p.close()
    o.close()


@pytest.mark.asyncio
async def test_extract_and_apply_contradiction_supersedes(isolated_data):
    """Paris → London: extractor drives the supersede via mutate_belief."""
    from kazma_core.memory.belief_extractor import extract_and_apply_beliefs

    p, o = _open_dbs(isolated_data)
    await extract_and_apply_beliefs(p, o, "My name is Alice and I live in Paris", use_llm=False, session_id="s1", turn=1)
    await extract_and_apply_beliefs(p, o, "I live in London now", use_llm=False, session_id="s1", turn=2)
    lives = [
        r["object"]
        for r in p.execute(
            "SELECT object FROM beliefs WHERE subject='user' AND predicate='lives_in' AND valid_until IS NULL"
        ).fetchall()
    ]
    assert lives == ["London"], f"Paris superseded, got {lives}"
    name = [
        r["object"]
        for r in p.execute(
            "SELECT object FROM beliefs WHERE predicate='name_is' AND valid_until IS NULL"
        ).fetchall()
    ]
    assert name == ["Alice"]
    p.close()
    o.close()


@pytest.mark.asyncio
async def test_extract_registers_entities(isolated_data):
    """Resolution #5.5: extracted objects are registered as entities."""
    from kazma_core.memory.belief_extractor import extract_and_apply_beliefs

    p, o = _open_dbs(isolated_data)
    await extract_and_apply_beliefs(p, o, "My name is Alice and I live in Paris", use_llm=False, session_id="s1", turn=1)
    names = [r["name"] for r in p.execute("SELECT name FROM entities").fetchall()]
    assert any("paris" in n.lower() for n in names), f"Paris must be registered, got {names}"
    p.close()
    o.close()


# ── Token budget ──────────────────────────────────────────────────────────


def test_format_recall_block_token_budget(isolated_data):
    from kazma_core.memory.recall import RecallHit, RecallResult, format_recall_block

    big = RecallResult(
        beliefs=[RecallHit(id=f"b{i}", content="x" * 500, score=1.0, kind="belief") for i in range(20)],
        episodes=[RecallHit(id=f"e{i}", content="y" * 500, score=0.5, kind="episode") for i in range(20)],
    )
    tight = format_recall_block(big, max_tokens=50)  # ~200 char budget
    loose = format_recall_block(big, max_tokens=10000)
    assert len(tight) < len(loose), "tighter budget must produce a smaller block"
    assert len(tight) < 1500, f"budget not enforced, got {len(tight)} chars"


def test_format_recall_block_empty(isolated_data):
    from kazma_core.memory.recall import RecallResult, format_recall_block

    assert format_recall_block(RecallResult([], [])) == ""


# ── Worker handler E2E ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_worker_macro_sleep_handler(isolated_data):
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.memory.task_queue import enqueue_task, reset_worker, start_worker
    from kazma_core.memory.worker_bootstrap import register_v2_handlers
    from kazma_core.paths import memory_ops_db, primary_memory_db

    # Seed an old low-importance episode
    p = sqlite3.connect(primary_memory_db(), isolation_level=None)
    ensure_primary_schema(p)
    now = time.time()
    p.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, tier, "
        "structural_importance, access_count, last_accessed, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("e_old", "default", "s1", 1, "old note", "episodic", 1, 0, now - 86400 * 60, now - 86400 * 60),
    )
    p.commit()
    p.close()

    reset_worker()
    register_v2_handlers()
    start_worker()
    tid = enqueue_task("macro_sleep", {"tenant_id": "default"})

    # Poll for completion
    for _ in range(40):
        await asyncio.sleep(0.1)
        c = sqlite3.connect(memory_ops_db())
        st = c.execute("SELECT status FROM memory_task_queue WHERE id=?", (tid,)).fetchone()
        c.close()
        if st and st[0] in ("completed", "failed"):
            break
    assert st[0] == "completed", f"macro_sleep task must complete, got {st[0]}"

    c = sqlite3.connect(primary_memory_db())
    c.row_factory = sqlite3.Row
    tier = c.execute("SELECT tier FROM episodes WHERE id='e_old'").fetchone()["tier"]
    c.close()
    assert tier == "archived", f"old episodic must demote to archived, got {tier}"


def test_register_v2_handlers_idempotent(isolated_data):
    from kazma_core.memory.worker_bootstrap import register_v2_handlers

    register_v2_handlers()
    register_v2_handlers()  # second call must not raise or duplicate
    register_v2_handlers()


# ── Procedural feed ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_procedural_feed_via_tool_registry(isolated_data):
    from kazma_core.agent.tool_registry import LocalToolRegistry

    reg = LocalToolRegistry()

    async def echo(**kw):
        return "ok"

    reg.register_function("echo_tool", echo)
    await reg.execute("echo_tool", {"msg": "hi"})
    # Allow the daemon procedural-record thread to complete
    await asyncio.sleep(1.5)

    from kazma_core.paths import primary_memory_db

    c = sqlite3.connect(primary_memory_db())
    c.row_factory = sqlite3.Row
    dags = [
        (r["name"], r["success_count"], r["total_trials"])
        for r in c.execute("SELECT name, success_count, total_trials FROM procedural_dags").fetchall()
    ]
    c.close()
    assert any(d[0] == "echo_tool" and d[1] >= 1 for d in dags), f"echo_tool must be recorded, got {dags}"
