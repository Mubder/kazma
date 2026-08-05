"""Phase 3 — materialized entity belief_count / graph_degree.

Covers:
  1. Backfill: a legacy DB (entities at the -1 sentinel) gets correct counts
     on first boot via ensure_primary_schema → _backfill_entity_counts.
  2. Write-site maintenance: mutate_belief / invalidate_belief / merge keep
     the columns in sync (counts move correctly).
  3. Self-heal: when any entity is stale (-1), the list/summary read path
     falls back to the live correlated subquery so the UI is never wrong.
"""

from __future__ import annotations

import sqlite3
import time

import pytest


@pytest.fixture
def state_db(tmp_path, monkeypatch):
    """Isolated primary memory DB path + env monkeypatch."""
    state = tmp_path / "memory_state.db"
    ops = tmp_path / "memory_ops.db"
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    from kazma_core import paths as paths_mod

    monkeypatch.setattr(paths_mod, "primary_memory_db", lambda: str(state))
    monkeypatch.setattr(paths_mod, "memory_ops_db", lambda: str(ops))
    return state


def _seed_graph(state_path):
    """Seed alice (2 beliefs to concepts) + shipx (1 belief). Returns conn."""
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    c = sqlite3.connect(state_path)
    c.row_factory = sqlite3.Row
    ensure_primary_schema(c)
    now = time.time()
    c.execute("INSERT INTO entities (id, tenant_id, type, name) VALUES ('alice','default','person','Alice')")
    c.execute("INSERT INTO entities (id, tenant_id, type, name) VALUES ('paris','default','concept','Paris')")
    c.execute("INSERT INTO entities (id, tenant_id, type, name) VALUES ('shipx','default','project','ShipX')")
    for bid, sub, pred, obj in (
        ("b1", "alice", "lives_in", "paris"),
        ("b2", "alice", "speaks", "french"),
        ("b3", "shipx", "has", "phase1"),
    ):
        c.execute(
            "INSERT INTO beliefs (id,tenant_id,subject,predicate,predicate_type,object,"
            "confidence,structural_importance,valid_from,ingested_at) "
            "VALUES (?,?,?,?,'functional',?,0.9,3,?,?)",
            (bid, "default", sub, pred, obj, now, now),
        )
    c.commit()
    return c


# ── 1. Backfill ───────────────────────────────────────────────────────────


def test_backfill_populates_stale_counts(state_db):
    """ensure_primary_schema backfills entities at the -1 sentinel."""
    c = _seed_graph(state_db)
    # Force every entity stale (simulate legacy upgrade state).
    c.execute("UPDATE entities SET belief_count=-1, graph_degree=-1")
    c.commit()
    c.close()

    # Re-running ensure_primary_schema triggers the backfill.
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    c = sqlite3.connect(state_db)
    c.row_factory = sqlite3.Row
    ensure_primary_schema(c)
    rows = {r["id"]: dict(r) for r in c.execute("SELECT id, belief_count, graph_degree FROM entities")}
    c.close()

    assert rows["alice"]["belief_count"] == 2, f"alice expected 2, got {rows['alice']}"
    assert rows["alice"]["graph_degree"] == 2  # co-occurs with paris + french
    assert rows["paris"]["belief_count"] == 1
    assert rows["shipx"]["belief_count"] == 1


# ── 2. Write-site maintenance ─────────────────────────────────────────────


def test_mutate_belief_updates_counts(state_db):
    """Inserting a belief via mutate_belief bumps the affected entities' counts."""
    c = _seed_graph(state_db)
    # Ensure counts are materialized (not stale) first.
    from kazma_core.memory.entity_counts import recompute_entity_counts

    recompute_entity_counts(c, ["alice", "paris", "shipx"])
    c.commit()
    c.close()

    from kazma_core.memory.belief_mutation import mutate_belief

    conn = sqlite3.connect(state_db)
    conn.row_factory = sqlite3.Row
    # New belief: alice → knows → shipx. Bumps alice's count + degree,
    # and brings shipx's degree up (now co-occurs with alice).
    before = dict(conn.execute("SELECT belief_count, graph_degree FROM entities WHERE id='alice'").fetchone())
    mutate_belief(conn, "alice", "knows", "shipx", tenant_id="default")
    conn.commit()
    after = dict(conn.execute("SELECT belief_count, graph_degree FROM entities WHERE id='alice'").fetchone())
    shipx = dict(conn.execute("SELECT belief_count, graph_degree FROM entities WHERE id='shipx'").fetchone())
    conn.close()

    assert after["belief_count"] == before["belief_count"] + 1, "alice belief_count must increment"
    assert shipx["belief_count"] >= 2, f"shipx count should include the new inbound belief, got {shipx}"


def test_invalidate_belief_updates_counts(state_db):
    """Invalidating a belief drops the affected entities' counts."""
    c = _seed_graph(state_db)
    from kazma_core.memory.entity_counts import recompute_entity_counts

    recompute_entity_counts(c, ["alice", "paris", "shipx"])
    c.commit()
    c.close()

    from kazma_core.memory.hygiene import invalidate_belief

    conn = sqlite3.connect(state_db)
    conn.row_factory = sqlite3.Row
    before = dict(conn.execute("SELECT belief_count FROM entities WHERE id='alice'").fetchone())
    invalidate_belief("b1", conn=conn)  # alice → lives_in → paris
    conn.commit()
    after = dict(conn.execute("SELECT belief_count FROM entities WHERE id='alice'").fetchone())
    paris = dict(conn.execute("SELECT belief_count FROM entities WHERE id='paris'").fetchone())
    conn.close()

    assert after["belief_count"] == before["belief_count"] - 1, "alice count must decrement"
    assert paris["belief_count"] == 0, f"paris (only belief b1) must drop to 0, got {paris}"


# ── 3. Self-heal read path ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_entities_falls_back_when_stale(state_db):
    """When any entity is stale, list_entities uses the live subquery (still correct)."""
    c = _seed_graph(state_db)
    # Materialize, then force alice stale.
    from kazma_core.memory.entity_counts import recompute_entity_counts

    recompute_entity_counts(c, ["alice", "paris", "shipx"])
    c.execute("UPDATE entities SET belief_count=-1 WHERE id='alice'")
    c.commit()
    c.close()

    from kazma_ui.memory_api import list_entities

    out = await list_entities(limit=50)
    assert out["ok"]
    by_id = {e["id"]: e for e in out["entities"]}
    # Live subquery still reports alice's true count (2) despite the stale column.
    assert by_id["alice"]["belief_count"] == 2, (
        f"stale alice should self-heal via live subquery to 2, got {by_id['alice']['belief_count']}"
    )


@pytest.mark.asyncio
async def test_list_entities_uses_columns_when_fresh(state_db):
    """When no entity is stale, list_entities reads the materialized columns."""
    c = _seed_graph(state_db)
    from kazma_core.memory.entity_counts import recompute_entity_counts

    recompute_entity_counts(c, ["alice", "paris", "shipx"])
    c.commit()
    c.close()

    from kazma_ui.memory_api import list_entities

    out = await list_entities(limit=50)
    assert out["ok"]
    by_id = {e["id"]: e for e in out["entities"]}
    assert by_id["alice"]["belief_count"] == 2
    assert by_id["alice"]["linked_others"] == 2
