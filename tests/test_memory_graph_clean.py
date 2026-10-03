"""The memory graph shows what memory holds, and extraction keeps it clean.

Live 2026-10-03: the Memory graph showed many nodes alone, and some were
junk. Read against the code, most "alone" nodes were made by the drawing:
the route loaded at most 800 facts ranked by importance * confidence, so the
anchor links that join every concept to the user (importance 1) were the
first dropped; it cut nodes one by one by belief count, so a fact node went
and left its subject alone; and a value such as ``true`` was dropped with
its link. The data side added work-item subjects ("phase_25_...") minted as
entities, facts filed under a raw name after a vector merge folded that name
into another entity, and empty entities left behind when their last fact
was invalidated.

Each rule below has its negative control: the old behaviour, which fails it.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kazma_core.memory.graph_view import isolated_ids, keep_connected

# ── The cut keeps groups whole ───────────────────────────────────────────


def _star(n_leaves: int) -> tuple[list[dict], list[dict]]:
    """``user`` → n concepts (heavy), each with one fact node (weight 1)."""
    nodes = [{"id": "user", "beliefCount": 500}]
    links = []
    for i in range(n_leaves):
        c, f = f"c{i}", f"fact{i}"
        nodes += [{"id": c, "beliefCount": 2}, {"id": f, "beliefCount": 1}]
        links += [{"source": "user", "target": c}, {"source": c, "target": f}]
    return nodes, links


def _painted_alone(nodes, links, kept) -> list[str]:
    kept_nodes = [n for n in nodes if n["id"] in kept]
    kept_links = [ln for ln in links if ln["source"] in kept and ln["target"] in kept]
    return isolated_ids(kept_nodes, kept_links)


def test_the_cut_never_paints_a_connected_node_alone():
    nodes, links = _star(60)
    kept = keep_connected(nodes, links, 50)
    assert len(kept) == 50 and "user" in kept
    assert _painted_alone(nodes, links, kept) == []


def test_the_old_cut_painted_connected_nodes_alone():
    """Negative control: sort by belief count, keep the first *limit*."""
    nodes, links = _star(60)
    # A second group the old cut ranks above the hub's concepts.
    nodes += [{"id": "x", "beliefCount": 3}, {"id": "y", "beliefCount": 1}]
    links += [{"source": "x", "target": "y"}]
    old = {n["id"] for n in sorted(nodes, key=lambda n: -n["beliefCount"])[:50]}
    assert "x" in old and _painted_alone(nodes, links, old)
    assert _painted_alone(nodes, links, keep_connected(nodes, links, 50)) == []


def test_truly_isolated_nodes_are_still_shown_when_there_is_room():
    nodes = [{"id": "user", "beliefCount": 5}, {"id": "a", "beliefCount": 1},
             {"id": "lone", "beliefCount": 1}]
    links = [{"source": "user", "target": "a"}]
    assert keep_connected(nodes, links, 3) == {"user", "a", "lone"}
    assert isolated_ids(nodes, links) == ["lone"]


# ── The route ────────────────────────────────────────────────────────────


def _belief(conn, bid, subject, predicate, obj, *, importance=3, confidence=0.9, ptype="set"):
    now = time.time()
    conn.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, "
        "confidence, structural_importance, valid_from, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (bid, "default", subject, predicate, ptype, obj, confidence, importance, now, now),
    )


@pytest.fixture()
def graph_client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
    from kazma_core.paths import memory_ops_db, primary_memory_db

    primary = sqlite3.connect(primary_memory_db())
    primary.row_factory = sqlite3.Row
    ensure_primary_schema(primary)
    ops = sqlite3.connect(memory_ops_db())
    ensure_ops_schema(ops)
    ops.close()

    from kazma_ui.app import create_app

    yield primary, TestClient(create_app())
    primary.close()


def test_hub_links_survive_the_fact_cap(graph_client):
    """The cap is limit*4 facts; the anchor links rank last and must stay."""
    primary, client = graph_client
    for i in range(60):  # 60 heavy facts, far above limit*4 = 40 at limit=10
        _belief(primary, f"heavy{i}", "user", "noted", f"note number {i}", importance=5)
    _belief(primary, "f1", "qudrax_ai", "status", "fully_clean", importance=3)
    _belief(primary, "anchor", "user", "related_to", "qudrax_ai", importance=1)
    primary.commit()

    body = client.get("/api/memory/v2/graph", params={"limit": 10}).json()
    ids = {n["id"] for n in body["nodes"]}
    if "qudrax_ai" in ids:
        pairs = {(ln["source"], ln["target"]) for ln in body["links"]}
        assert ("user", "qudrax_ai") in pairs
    linked = {ln["source"] for ln in body["links"]} | {ln["target"] for ln in body["links"]}
    assert ids <= linked, f"painted alone: {sorted(ids - linked)}"


def test_a_concept_whose_anchor_is_capped_stays_joined_at_a_larger_limit(graph_client):
    primary, client = graph_client
    for i in range(900):  # above the 800-fact ceiling
        _belief(primary, f"h{i}", "user", "noted", f"n{i}", importance=4)
    _belief(primary, "f1", "qudrax_ai", "status", "fully_clean", importance=5)
    _belief(primary, "anchor", "user", "related_to", "qudrax_ai", importance=1)
    primary.commit()
    body = client.get("/api/memory/v2/graph", params={"limit": 2000}).json()
    pairs = {(ln["source"], ln["target"]) for ln in body["links"]}
    assert ("user", "qudrax_ai") in pairs


def test_a_literal_value_is_drawn_on_its_subject(graph_client):
    """``x → enabled → true`` used to drop the value and its link."""
    primary, client = graph_client
    _belief(primary, "v1", "memory_needs_cleanup", "enabled", "true")
    _belief(primary, "v2", "dark_mode", "enabled", "true")
    primary.commit()
    body = client.get("/api/memory/v2/graph").json()
    values = [n for n in body["nodes"] if n.get("isValue")]
    assert len(values) == 2, "one value node per fact, never one shared 'true' node"
    linked = {ln["source"] for ln in body["links"]} | {ln["target"] for ln in body["links"]}
    assert {"memory_needs_cleanup", "dark_mode"} <= linked
    assert body["stats"]["isolated"] == 0


def test_isolation_in_the_data_is_reported(graph_client):
    primary, client = graph_client
    _belief(primary, "v1", "orphan_thing", "status", "")  # no object: nothing to draw it to
    _belief(primary, "v2", "kazma", "is", "great")
    primary.commit()
    body = client.get("/api/memory/v2/graph").json()
    assert body["stats"]["isolated"] >= 1


# ── Extraction ───────────────────────────────────────────────────────────


@pytest.fixture()
def dbs(tmp_path, monkeypatch):
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    # No embedding model in the unit suite: tier-2 merges are driven by the
    # fake below when a test wants them.
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda _t: None)
    p = sqlite3.connect(tmp_path / "state.db")
    p.row_factory = sqlite3.Row
    o = sqlite3.connect(tmp_path / "ops.db")
    ensure_primary_schema(p)
    ensure_ops_schema(o)
    p.execute(
        "INSERT INTO entities (id, tenant_id, type, name) VALUES ('user','default','person','User')"
    )
    p.commit()
    yield p, o
    p.close()
    o.close()


def _raw(subject, predicate, obj, ptype="set"):
    return {"subject": subject, "predicate": predicate, "object": obj,
            "predicate_type": ptype, "confidence": 0.9, "importance": 3}


def _subjects(p) -> set[str]:
    return {r[0] for r in p.execute(
        "SELECT subject FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL "
        "AND predicate != 'related_to'")}


def _fake_vectors(monkeypatch):
    """"qudrax ..." names share one vector; everything else another."""
    import numpy as np

    near = np.ones(8, dtype=np.float32).tobytes()
    far = np.zeros(8, dtype=np.float32).tobytes()
    monkeypatch.setattr(
        "kazma_core.memory.embedder.encode_text_to_blob",
        lambda t: near if "qudrax" in t.lower() else far,
    )


def _qudrax_ai(p):
    p.execute("INSERT INTO entities (id, tenant_id, type, name) "
              "VALUES ('qudrax_ai','default','concept','qudrax ai')")
    p.commit()


def _live_entities(p) -> set[str]:
    return {r[0] for r in p.execute(
        "SELECT id FROM entities WHERE COALESCE(json_extract(metadata_json, '$.merged_into'), '') = ''")}


def test_a_fact_about_a_merged_name_is_filed_under_the_kept_entity(dbs, monkeypatch):
    """A vector merge folds "qudrax" into "qudrax_ai": one entity, one subject."""
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2

    p, o = dbs
    _qudrax_ai(p)
    _fake_vectors(monkeypatch)
    _apply_beliefs_to_v2([_raw("qudrax", "status", "fully_clean")], p, o)
    assert _subjects(p) == {"qudrax_ai"}
    assert _live_entities(p) == {"user", "qudrax_ai"}
    # The merged name is a redirect the next write follows, and the merge is
    # in the ledger.
    meta = p.execute("SELECT metadata_json FROM entities WHERE id='qudrax'").fetchone()[0]
    assert '"merged_into": "qudrax_ai"' in meta
    assert p.execute("SELECT status FROM entity_merges").fetchone()[0] == "auto_merged"


def test_the_old_merge_broke_on_the_ledger_and_left_a_duplicate(dbs, monkeypatch):
    """Negative control: the old _auto_merge (alias only, then the ledger row
    naming an entity that does not exist) under the enforced foreign key."""
    import json as _json

    from kazma_core.memory import entity_resolution
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2

    def old_auto_merge(conn, new_id, target_id, name, ahash, tenant_id, entity_type):
        row = conn.execute("SELECT aliases_json FROM entities WHERE id=?", (target_id,)).fetchone()
        aliases = _json.loads(row[0] or "[]") + [name]
        conn.execute("UPDATE entities SET aliases_json=? WHERE id=?", (_json.dumps(aliases), target_id))
        entity_resolution._record_merge(conn, new_id, target_id, tenant_id, confidence=0.88,
                                        tier="tier2_vector", status="auto_merged")

    p, o = dbs
    _qudrax_ai(p)
    _fake_vectors(monkeypatch)
    monkeypatch.setattr(entity_resolution, "_auto_merge", old_auto_merge)
    _apply_beliefs_to_v2([_raw("qudrax", "status", "fully_clean")], p, o)
    assert _live_entities(p) == {"user", "qudrax_ai", "qudrax"}
    assert _subjects(p) == {"qudrax"}


def test_an_alias_names_the_entity(dbs):
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2
    from kazma_core.memory.entity_resolution import alias_hash

    p, o = dbs
    p.execute("INSERT INTO entities (id, tenant_id, type, name, aliases_json) "
              "VALUES ('shipx','default','project','ShipX', ?)", (f'["{alias_hash("ship x")}"]',))
    p.commit()
    _apply_beliefs_to_v2([_raw("ship_x", "deploys_to", "fly_io")], p, o)
    assert _subjects(p) == {"shipx"}


def test_a_work_item_subject_keeps_its_fact_but_is_no_entity(dbs):
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2

    p, o = dbs
    _apply_beliefs_to_v2([_raw("phase_25_unified_notification_alert_system", "status", "done")], p, o)
    assert _subjects(p) == {"phase_25_unified_notification_alert_system"}
    assert p.execute("SELECT COUNT(*) FROM entities WHERE id LIKE 'phase%'").fetchone()[0] == 0


def test_the_extractor_is_given_the_subjects_in_use(dbs):
    from kazma_core.memory.entity_resolution import entity_vocabulary

    p, _ = dbs
    p.execute("INSERT INTO entities (id, tenant_id, type, name, belief_count) "
              "VALUES ('shipx','default','project','ShipX', 7), ('gone','default','concept','g', 0)")
    p.execute("INSERT INTO entities (id, tenant_id, type, name, belief_count, metadata_json) "
              "VALUES ('ship_x','default','concept','s', 3, '{\"merged_into\":\"shipx\"}')")
    p.commit()
    assert entity_vocabulary(p, tenant_id="default") == ["shipx"]


# ── An emptied entity goes, if it holds nothing else ─────────────────────


def _entity(p, eid, *, etype="concept", aliases=None, meta="{}", major=0):
    import json

    from kazma_core.memory.entity_resolution import alias_hash

    name = eid.replace("_", " ")
    p.execute(
        "INSERT INTO entities (id, tenant_id, type, name, aliases_json, metadata_json, is_major) "
        "VALUES (?,?,?,?,?,?,?)",
        (eid, "default", etype, name, json.dumps(aliases or [name, alias_hash(name)]), meta, major),
    )


def test_invalidating_the_last_fact_removes_a_plain_entity(dbs):
    from kazma_core.memory.hygiene import invalidate_belief

    p, _ = dbs
    _entity(p, "old_tool")
    _belief(p, "b1", "old_tool", "status", "retired")
    p.commit()
    invalidate_belief("b1", conn=p)
    assert p.execute("SELECT COUNT(*) FROM entities WHERE id='old_tool'").fetchone()[0] == 0
    # The fact stays, as history.
    assert p.execute("SELECT invalidated_at IS NOT NULL FROM beliefs WHERE id='b1'").fetchone()[0]


@pytest.mark.parametrize("kind", ["merged_alias", "person", "major", "grouped", "redirect", "has_fact"])
def test_an_entity_holding_something_else_is_kept(dbs, kind):
    from kazma_core.memory.hygiene import invalidate_belief

    p, _ = dbs
    if kind == "merged_alias":
        _entity(p, "acme", aliases=["acme", "acme corp"])
    elif kind == "person":
        _entity(p, "acme", etype="person")
    elif kind == "major":
        _entity(p, "acme", major=1)
    elif kind == "redirect":
        _entity(p, "acme", meta='{"merged_into":"acme_inc"}')
    else:
        _entity(p, "acme")
    if kind == "grouped":
        p.execute("INSERT INTO graph_associations (id, tenant_id, group_root, member, created_at) "
                  "VALUES ('g1','default','user','acme', 0)")
    if kind == "has_fact":
        _belief(p, "b2", "acme", "based_in", "berlin")
    _belief(p, "b1", "acme", "status", "active")
    p.commit()
    invalidate_belief("b1", conn=p)
    assert p.execute("SELECT COUNT(*) FROM entities WHERE id='acme'").fetchone()[0] == 1


def test_a_superseded_value_entity_goes_with_its_last_fact(dbs):
    from kazma_core.memory.belief_mutation import mutate_belief

    p, o = dbs
    _entity(p, "paris")
    p.commit()
    mutate_belief(p, "user", "lives_in", "paris", ops_conn=o, predicate_type="functional",
                  confidence=0.9, extraction_method="user_explicit", now=1000.0)
    mutate_belief(p, "user", "lives_in", "london", ops_conn=o, predicate_type="functional",
                  confidence=0.9, extraction_method="user_explicit", now=2000.0)
    assert p.execute("SELECT COUNT(*) FROM entities WHERE id='paris'").fetchone()[0] == 0


# ── The gate: a noisy batch leaves no junk entity and no lone subject ────

#: What an extractor emits on a busy working day (shapes from the live graph).
NOISY_BATCH = [
    _raw("phase_25_unified_notification_alert_system", "status", "shipped"),
    _raw("JIRA-142", "assigned_to", "user"),
    _raw("v0.10.2", "released_on", "2026-09-30"),
    _raw("C:/Users/x/kazma/README.md", "mentions", "install"),
    _raw("the_user_wants_to_know_whether_the_deploy_finished", "answer", "yes"),
    _raw("a1b2c3d4e5", "is_commit_of", "kazma"),
    _raw("2026_09_30", "was", "a monday"),
    _raw("qudrax_ai", "status", "fully_clean"),
    _raw("hadidfit_ai", "noted", "CoachFaris identity + architecture"),
    _raw("memory_needs_cleanup", "enabled", "true"),
    _raw("shipx", "deploys_to", "fly_io", ptype="functional"),
]


def _clutter(p) -> dict:
    from kazma_core.memory.entity_counts import recompute_entity_counts
    from kazma_core.memory.graph_hygiene import graph_hygiene_report

    recompute_entity_counts(p, [r[0] for r in p.execute("SELECT id FROM entities")])
    return graph_hygiene_report(p)


def test_a_noisy_batch_leaves_no_junk_entity_and_no_lone_subject(dbs):
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2

    p, o = dbs
    _apply_beliefs_to_v2(list(NOISY_BATCH), p, o)
    report = _clutter(p)
    assert report["work_items"]["count"] == 0, report["work_items"]
    assert report["isolated"]["count"] == 0, report["isolated"]
    assert report["empty"]["count"] == 0, report["empty"]
    # Every fact kept: nothing about the batch was dropped to get clean.
    assert len(_subjects(p)) >= 9


def test_the_gate_catches_entities_minted_for_every_subject(dbs, monkeypatch):
    """Negative control: mint for every subject (the old rule)."""
    from kazma_core.memory import ego_anchor
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2

    p, o = dbs
    judge = ego_anchor.subject_should_mint_entity
    monkeypatch.setattr(ego_anchor, "subject_should_mint_entity", lambda s: bool(s))
    _apply_beliefs_to_v2(list(NOISY_BATCH), p, o)
    minted = [r[0] for r in p.execute("SELECT id FROM entities")]
    assert len([e for e in minted if not judge(e)]) >= 5


def test_the_report_finds_duplicates_and_work_items(dbs):
    from kazma_core.memory.graph_hygiene import graph_hygiene_report

    p, _ = dbs
    for eid in ("shipx_app", "app_shipx", "phase_3_rollout", "kazma"):
        _entity(p, eid)
    p.commit()
    report = graph_hygiene_report(p)
    assert report["duplicates"]["examples"] == [["app_shipx", "shipx_app"]]
    assert report["work_items"]["examples"] == ["phase_3_rollout"]


def test_a_product_with_a_number_is_still_an_entity():
    """Subjects are slugged lower case, so a ticket's shape cannot be told
    from a product's by its case: "gpt-4", "windows-11" and "covid-19" are
    things the user talks about and keep their node; "jira-142" is a ticket."""
    from kazma_core.memory.ego_anchor import subject_should_mint_entity

    for name in ("gpt-4", "windows-11", "covid-19", "python-3"):
        assert subject_should_mint_entity(name), name
    for name in ("jira-142", "abc-1234"):
        assert not subject_should_mint_entity(name), name
