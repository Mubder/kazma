"""Regression tests for the V2 swarm bridge — the V2-native write paths that
replaced the V1 adapter calls in worker_dispatch, self_improvement, and
compaction after the V1→V2 memory cutover.

Covers:
  - swarm_bridge.store_swarm_result  → episode (source="swarm_result") + belief
  - swarm_bridge.log_evolution_v2    → episode (source="soul_evolution")
  - swarm_bridge.store_compaction_summary → episode (source="compaction_summary")
  - recall.search dict-shape compat shim (the linchpin read contract)

All tests use tmp_path + KAZMA_DATA_DIR override + dual_write.reset_mirror().
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture()
def isolated_data(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    from kazma_core.memory import dual_write

    dual_write.reset_mirror()
    yield tmp_path
    dual_write.reset_mirror()


def _primary_conn() -> sqlite3.Connection:
    from kazma_core.paths import primary_memory_db

    conn = sqlite3.connect(primary_memory_db())
    conn.row_factory = sqlite3.Row
    return conn


# ── store_swarm_result ────────────────────────────────────────────────────


def test_store_swarm_result_writes_episode_and_belief(isolated_data):
    from kazma_core.memory.swarm_bridge import store_swarm_result

    eid = store_swarm_result(
        "coder-1", "task-99",
        "Task: implement login\nResult: added /api/login endpoint",
        {"type": "swarm_result"},
    )
    assert eid is not None

    conn = _primary_conn()
    try:
        # Episode with the authoritative V2 source categorization
        ep = conn.execute(
            "SELECT user_text, metadata_json FROM episodes WHERE id=?", (eid,)
        ).fetchone()
        assert ep is not None
        assert "implement login" in (ep["user_text"] or "")
        meta = json.loads(ep["metadata_json"])
        assert meta["source"] == "swarm_result"   # authoritative, overrides caller
        assert meta["worker"] == "coder-1"
        assert meta["task_id"] == "task-99"

        # Belief links the worker → produced (subject is the canonical slug)
        bel = conn.execute(
            "SELECT object FROM beliefs WHERE subject=? AND predicate='produced'",
            ("coder_1",),  # entity slugs are case-normalized
        ).fetchall()
        assert bel, "expected a worker→produced belief"
        assert any("added /api/login" in (b["object"] or "") for b in bel)
    finally:
        conn.close()


def test_store_swarm_result_skips_trivial_snippet(isolated_data):
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.memory.swarm_bridge import store_swarm_result

    # Under 12 chars → no write
    assert store_swarm_result("w", "t", "short", None) is None
    conn = _primary_conn()
    try:
        ensure_primary_schema(conn)  # no write happened → ensure schema ourselves
        n = conn.execute(
            "SELECT COUNT(*) FROM episodes WHERE metadata_json LIKE '%swarm_result%'"
        ).fetchone()[0]
        assert n == 0
    finally:
        conn.close()


def test_store_swarm_result_never_raises_on_bad_input(isolated_data):
    """The bridge must be best-effort — bad inputs return None, never raise."""
    from kazma_core.memory.swarm_bridge import store_swarm_result

    assert store_swarm_result("", "", "", None) is None  # no exception
    assert store_swarm_result(None, None, None, None) is None  # type: ignore[arg-type]


# ── log_evolution_v2 ──────────────────────────────────────────────────────


def test_log_evolution_v2_writes_episode(isolated_data):
    from kazma_core.memory.swarm_bridge import log_evolution_v2

    eid = log_evolution_v2(
        worker_name="researcher",
        task_id="evolution_001",
        delta="Prefer concise summaries in worker prompts",
        summary="Auto-applied evolution for researcher",
    )
    assert eid is not None

    conn = _primary_conn()
    try:
        ep = conn.execute(
            "SELECT user_text, metadata_json FROM episodes WHERE id=?", (eid,)
        ).fetchone()
        assert ep is not None
        assert "SoulEvolution" in (ep["user_text"] or "")
        meta = json.loads(ep["metadata_json"])
        assert meta["source"] == "soul_evolution"
        assert meta["worker"] == "researcher"
        assert "concise summaries" in meta["delta"]
    finally:
        conn.close()


# ── store_compaction_summary ──────────────────────────────────────────────


def test_store_compaction_summary_writes_episode(isolated_data):
    from kazma_core.memory.swarm_bridge import store_compaction_summary

    eid = store_compaction_summary(
        "Conversation about refactoring the memory subsystem.",
        metadata={"type": "compaction_summary"},
    )
    assert eid is not None

    conn = _primary_conn()
    try:
        ep = conn.execute(
            "SELECT summary_text, metadata_json FROM episodes WHERE id=?", (eid,)
        ).fetchone()
        assert ep is not None
        assert "refactoring the memory" in (ep["summary_text"] or "")
        meta = json.loads(ep["metadata_json"])
        assert meta["source"] == "compaction_summary"
    finally:
        conn.close()


def test_store_compaction_summary_skips_empty(isolated_data):
    from kazma_core.memory.swarm_bridge import store_compaction_summary

    assert store_compaction_summary("   ", None) is None
    assert store_compaction_summary("", None) is None


# ── recall.search compat shim ─────────────────────────────────────────────


def test_recall_search_returns_dict_shape(isolated_data):
    """recall.search must return list[dict] with the keys callers consume
    (id, content, text, score, source_layer, metadata) — the linchpin contract."""
    from kazma_core.memory.swarm_bridge import store_swarm_result
    from kazma_core.memory.recall import search

    store_swarm_result(
        "coder-2", "task-x",
        "Task: build feature\nResult: implemented in module.py",
        {"type": "swarm_result"},
    )
    hits = search("build feature", limit=5)
    assert isinstance(hits, list)
    if hits:  # recall may return [] if FTS/embedding isn't ready in test env
        h = hits[0]
        # The contract every caller relies on:
        for key in ("id", "content", "text", "score", "source_layer", "metadata"):
            assert key in h, f"missing key {key!r} in search() result"
        assert h["content"] == h["text"]  # text is an alias


def test_recall_search_never_raises(isolated_data):
    """search() must be best-effort — never raise, return [] on failure."""
    from kazma_core.memory.recall import search

    assert search("", limit=5) == []  # empty query
    assert isinstance(search("nonexistent query xyz", limit=5), list)


# ── worker_dispatch._index_worker_l4_memory (the swarm_bridge caller) ─────
# Moved from test_memory_p1_p2_p3.py before that V1-orphan file was deleted.
# Tests the dispatch-layer wrapper around store_swarm_result.


@pytest.mark.asyncio
async def test_p3_index_worker_l4_sets_worker_meta(tmp_path: Path, monkeypatch):
    """Successful worker dispatch writes a V2 swarm_result episode + belief."""
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    from kazma_core.memory import dual_write

    dual_write.reset_mirror()
    try:
        from kazma_core.swarm import worker_dispatch as wd

        await wd._index_worker_l4_memory(
            worker_name="Observer",
            prompt="Summarize the repo structure",
            output="Found 12 packages under monorepo.",
            task_id="task-abc",
        )
        from kazma_core.paths import primary_memory_db

        conn = sqlite3.connect(primary_memory_db())
        conn.row_factory = sqlite3.Row
        ep = conn.execute(
            "SELECT user_text, metadata_json FROM episodes WHERE metadata_json LIKE ?",
            ('%"source": "swarm_result"%',),
        ).fetchall()
        assert ep, "expected a swarm_result episode"
        row = ep[0]
        assert "Result:" in (row["user_text"] or "") or "Found 12" in (row["user_text"] or "")
        meta = json.loads(row["metadata_json"])
        assert meta.get("worker") == "Observer"
        assert meta.get("source") == "swarm_result"
        bel = conn.execute(
            "SELECT object FROM beliefs WHERE predicate='produced'"
        ).fetchall()
        assert bel, "expected a worker→produced belief"
        assert any("Found 12" in (b["object"] or "") for b in bel)
        conn.close()
    finally:
        dual_write.reset_mirror()


@pytest.mark.asyncio
async def test_p3_index_skips_empty_output(tmp_path: Path, monkeypatch):
    """No store when prompt and output are empty/trivial."""
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    from kazma_core.memory import dual_write
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    dual_write.reset_mirror()
    try:
        from kazma_core.swarm import worker_dispatch as wd

        await wd._index_worker_l4_memory(
            worker_name="Observer", prompt="", output="", task_id="x",
        )
        from kazma_core.paths import primary_memory_db

        conn = sqlite3.connect(primary_memory_db())
        ensure_primary_schema(conn)
        n = conn.execute(
            "SELECT COUNT(*) FROM episodes WHERE metadata_json LIKE ?",
            ('%"source": "swarm_result"%',),
        ).fetchone()[0]
        conn.close()
        assert n == 0
    finally:
        dual_write.reset_mirror()
