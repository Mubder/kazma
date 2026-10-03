"""The entity counts the Memory page shows are a cache, and it converges.

``entities.belief_count`` / ``graph_degree`` are maintained by the writers
(``entity_counts.recompute_entity_counts``). A merge moved the source's facts
onto the target and recomputed those two only, so an entity linked to both
kept a neighbour it no longer had; on 2026-10-03, 11 of 377 live entities
were wrong, two since an August merge. Merges now recompute the neighbours,
and the "entity count repair" maintenance sweep recomputes any row whose
stored counts differ from the live ones.
"""

from __future__ import annotations

import json
import sqlite3

import pytest


def _entity(conn, eid):
    conn.execute(
        "INSERT INTO entities (id, tenant_id, type, name, aliases_json, metadata_json) "
        "VALUES (?, 'default', 'concept', ?, ?, '{}')",
        (eid, eid, json.dumps([eid])),
    )


def _fact(conn, bid, subject, predicate, obj):
    conn.execute(
        """INSERT INTO beliefs
           (id, tenant_id, subject, predicate, predicate_type, object, confidence,
            structural_importance, source_trust_weight, extraction_method, valid_from, ingested_at)
           VALUES (?, 'default', ?, ?, 'set', ?, 1.0, 3, 1.0, 'user_explicit', 1.0, 1.0)""",
        (bid, subject, predicate, obj),
    )


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from kazma_core.memory.entity_counts import recompute_entity_counts
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    state, ops = tmp_path / "memory_state.db", tmp_path / "memory_ops.db"
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(state))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(ops))
    conn = sqlite3.connect(state)
    ensure_primary_schema(conn)
    for eid in ("hub", "grok_admin", "admin_grok", "acme"):
        _entity(conn, eid)
    # The hub links to both names of one account, and each has a fact.
    _fact(conn, "b1", "hub", "uses_account", "grok_admin")
    _fact(conn, "b2", "hub", "uses_account", "admin_grok")
    _fact(conn, "b3", "grok_admin", "resets", "Tuesday")
    _fact(conn, "b4", "acme", "built_by", "hub")
    recompute_entity_counts(conn, ["hub", "grok_admin", "admin_grok", "acme"])
    conn.commit()
    conn.close()
    o = sqlite3.connect(ops)
    ensure_ops_schema(o)
    o.close()
    return state


def _stored_and_live(db, eid):
    from kazma_core.memory.entity_counts import belief_count_sql, entity_degree_sql

    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            f"SELECT e.belief_count, {belief_count_sql()}, e.graph_degree, {entity_degree_sql()} "
            "FROM entities e WHERE e.id = ?",
            (eid,),
        ).fetchone()
    finally:
        conn.close()


def test_a_merge_recomputes_the_neighbours(db):
    from kazma_ui.memory_api import _merge_entities_sync

    assert _stored_and_live(db, "hub")[2:] == (3, 3)
    assert _merge_entities_sync({"source_id": "grok_admin", "target_id": "admin_grok"})["ok"] is True
    stored_count, live_count, stored_degree, live_degree = _stored_and_live(db, "hub")
    assert (stored_degree, live_degree) == (2, 2)
    assert stored_count == live_count


def test_without_the_neighbours_the_hub_kept_a_neighbour_it_lost(db):
    """Negative control: the merge's old recompute, source and target only."""
    from kazma_core.memory.entity_counts import recompute_entity_counts

    conn = sqlite3.connect(db)
    conn.execute("UPDATE beliefs SET subject='admin_grok' WHERE subject='grok_admin'")
    conn.execute("UPDATE beliefs SET object='admin_grok' WHERE object='grok_admin'")
    recompute_entity_counts(conn, ["grok_admin", "admin_grok"])
    conn.commit()
    conn.close()
    _count, _live, stored_degree, live_degree = _stored_and_live(db, "hub")
    assert (stored_degree, live_degree) == (3, 2)


def test_the_repair_converges_whatever_drifted(db):
    from kazma_core.memory.entity_counts import repair_entity_counts

    conn = sqlite3.connect(db)
    conn.execute("UPDATE entities SET belief_count = 7 WHERE id = 'acme'")
    conn.execute("UPDATE entities SET graph_degree = 9 WHERE id = 'grok_admin'")
    conn.execute("UPDATE entities SET belief_count = -1, graph_degree = -1 WHERE id = 'admin_grok'")
    with conn:
        assert repair_entity_counts(conn) == ["acme", "admin_grok", "grok_admin"]
    with conn:
        assert repair_entity_counts(conn) == []
    conn.close()
    for eid in ("acme", "admin_grok", "grok_admin"):
        stored_count, live_count, stored_degree, live_degree = _stored_and_live(db, eid)
        assert (stored_count, stored_degree) == (live_count, live_degree)


def test_the_maintenance_sweep_repairs_the_install_database(db):
    from kazma_core.memory import worker_bootstrap as wb

    conn = sqlite3.connect(db)
    conn.execute("UPDATE entities SET belief_count = 5 WHERE id = 'acme'")
    conn.commit()
    conn.close()
    assert ("entity count repair", wb._repair_entity_counts) in wb._MAINTENANCE_SWEEPS
    wb._repair_entity_counts()
    stored_count, live_count, _sd, _ld = _stored_and_live(db, "acme")
    assert stored_count == live_count == 1
