"""Contract tests for the ``/api/memory/v2/graph`` route filter logic.

Asserts the self-consistency invariant the canvas depends on: every link's
source AND target must resolve to a node present in the same payload (no
dangling edges). The route enforces this in two places — at link emission
(the ``entity_type`` guard) and again against the top-``limit`` node set —
so this test pins the invariant so a future refactor of either filter
cannot silently regress it.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def seeded_client(tmp_path: Path, monkeypatch) -> TestClient:
    """Build the app against an isolated data dir and seed a small graph.

    Seeds two entity types (``person`` + ``tool``) and beliefs linking a
    ``person`` subject to plain-text object facts, so an ``entity_type``
    filter has something to remove.
    """
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))

    # Import after the env override so paths resolve into tmp_path.
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
    from kazma_core.paths import memory_ops_db, primary_memory_db

    primary = sqlite3.connect(primary_memory_db())
    primary.row_factory = sqlite3.Row
    ensure_primary_schema(primary)
    ops = sqlite3.connect(memory_ops_db())
    ensure_ops_schema(ops)

    now = time.time()
    primary.execute(
        "INSERT OR IGNORE INTO entities (id, tenant_id, type, name) VALUES (?, ?, ?, ?)",
        ("alice", "default", "person", "Alice"),
    )
    primary.execute(
        "INSERT OR IGNORE INTO entities (id, tenant_id, type, name) VALUES (?, ?, ?, ?)",
        ("git", "default", "tool", "Git"),
    )
    # Two functional beliefs off the person subject; object texts become
    # virtual "concept" nodes that the entity_type=person filter must drop.
    primary.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, "
        "confidence, structural_importance, valid_from, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("b1", "default", "alice", "lives_in", "functional", "Paris", 0.9, 4, now, now),
    )
    primary.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, "
        "confidence, structural_importance, valid_from, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("b2", "default", "alice", "speaks", "functional", "French", 0.8, 3, now, now),
    )
    primary.commit()
    primary.close()
    ops.close()

    from kazma_ui.app import create_app

    return TestClient(create_app())


def test_entity_type_filter_leaves_no_dangling_links(seeded_client: TestClient) -> None:
    """Filtering by entity_type must drop links whose target was filtered out.

    The ``entity_type=person`` filter removes the virtual ``Paris``/``French``
    concept nodes (type ``concept``); any link pointing at them must also be
    absent. The route guards this at link emission, and this test pins the
    end-to-end invariant so a future change to either filter step cannot
    leak a dangling edge into the canvas.
    """
    resp = seeded_client.get("/api/memory/v2/graph", params={"entity_type": "person"})
    assert resp.status_code == 200
    payload = resp.json()

    node_ids = {n["id"] for n in payload["nodes"]}
    # The Paris/French virtual concept nodes must be gone (filtered to person).
    assert "Paris" not in node_ids
    assert "French" not in node_ids

    # The core invariant: every link endpoint must resolve to a surviving node.
    for link in payload["links"]:
        assert link["source"] in node_ids, (
            f"dangling link source {link['source']!r} not in nodes"
        )
        assert link["target"] in node_ids, (
            f"dangling link target {link['target']!r} not in nodes"
        )

    # With only the person node surviving and its concept targets filtered out,
    # no links should remain at all (the link's target was always a concept).
    assert payload["links"] == []


def test_no_filter_returns_full_graph(seeded_client: TestClient) -> None:
    """Sanity: without entity_type the virtual concept nodes and links return."""
    resp = seeded_client.get("/api/memory/v2/graph")
    assert resp.status_code == 200
    payload = resp.json()

    node_ids = {n["id"] for n in payload["nodes"]}
    assert "Paris" in node_ids  # virtual concept node present
    targets = {l["target"] for l in payload["links"]}
    assert targets <= node_ids, "every link target must be a node even unfiltered"


def test_entity_object_id_collision_emits_one_node(
    tmp_path: Path, monkeypatch,
) -> None:
    """Belief object text equal to an entity id must not emit a virtual twin.

    Regression for the orphaned-shipx canvas bug: entity ``shipx`` (subject of
    N beliefs) plus ``user → has_project → shipx`` used to produce two nodes
    with id ``shipx`` (real entity + virtual fact). The client id→index map
    last-write-wins orphaned one of them. The route must emit exactly one
    ``shipx`` node (the real entity, not isVirtual) and keep the inbound link.
    """
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))

    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
    from kazma_core.paths import memory_ops_db, primary_memory_db

    primary = sqlite3.connect(primary_memory_db())
    primary.row_factory = sqlite3.Row
    ensure_primary_schema(primary)
    ops = sqlite3.connect(memory_ops_db())
    ensure_ops_schema(ops)

    now = time.time()
    primary.execute(
        "INSERT OR IGNORE INTO entities (id, tenant_id, type, name) VALUES (?, ?, ?, ?)",
        ("shipx", "default", "concept", "shipx"),
    )
    primary.execute(
        "INSERT OR IGNORE INTO entities (id, tenant_id, type, name) VALUES (?, ?, ?, ?)",
        ("user", "default", "person", "User"),
    )
    # shipx as subject of two beliefs (its "own" graph weight)
    for i, obj in enumerate(("phase1", "phase2"), start=1):
        primary.execute(
            "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, "
            "confidence, structural_importance, valid_from, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"sx{i}", "default", "shipx", "has_feature", "set", obj, 0.9, 4, now, now),
        )
    # user → has_project → shipx  (object text == entity id — the collision)
    primary.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, "
        "confidence, structural_importance, valid_from, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("hp1", "default", "user", "has_project", "functional", "shipx", 0.95, 5, now, now),
    )
    primary.commit()
    primary.close()
    ops.close()

    from kazma_ui.app import create_app

    client = TestClient(create_app())
    resp = client.get("/api/memory/v2/graph")
    assert resp.status_code == 200
    payload = resp.json()

    shipx_nodes = [n for n in payload["nodes"] if n["id"] == "shipx"]
    assert len(shipx_nodes) == 1, (
        f"expected exactly one shipx node, got {len(shipx_nodes)}: {shipx_nodes}"
    )
    shipx = shipx_nodes[0]
    assert not shipx.get("isVirtual"), "shipx must be the real entity, not a virtual fact"
    # 2 subject beliefs + 1 inbound has_project
    assert shipx["beliefCount"] >= 3

    # Inbound link must resolve; no dangling / duplicate target
    inbound = [
        l for l in payload["links"]
        if l["source"] == "user" and l["target"] == "shipx"
    ]
    assert len(inbound) == 1
    assert inbound[0]["label"] == "has_project"

    # Global uniqueness of node ids (canvas map invariant)
    ids = [n["id"] for n in payload["nodes"]]
    assert len(ids) == len(set(ids)), f"duplicate node ids in payload: {ids}"


# ── Phase 1.1: graph truncation stats + beliefs offset ───────────────────


def test_graph_reports_truncation_when_capped(seeded_client: TestClient) -> None:
    """When the node count exceeds `limit`, stats must flag truncation and
    report the full pre-slice total so the UI can show "showing N of M"."""
    # seeded_client has alice + Paris + French + git (~4 nodes). Cap at 1.
    resp = seeded_client.get("/api/memory/v2/graph", params={"limit": 1})
    assert resp.status_code == 200
    payload = resp.json()
    stats = payload["stats"]

    assert stats["truncated"] is True
    assert stats["total_nodes"] > stats["nodes"], (
        f"total_nodes ({stats['total_nodes']}) must exceed kept nodes ({stats['nodes']})"
    )
    assert stats["nodes"] == len(payload["nodes"]) <= 1


def test_graph_no_truncation_when_under_limit(seeded_client: TestClient) -> None:
    """Under the limit, truncated=False and total_nodes equals the kept set."""
    resp = seeded_client.get("/api/memory/v2/graph", params={"limit": 200})
    assert resp.status_code == 200
    stats = resp.json()["stats"]
    assert stats["truncated"] is False
    assert stats["total_nodes"] == stats["nodes"]


def test_beliefs_offset_paginates(seeded_client: TestClient) -> None:
    """beliefs endpoint returns total + offset and offset shifts the window."""
    full = seeded_client.get("/api/memory/v2/beliefs", params={"limit": 100}).json()
    total = full["total"]
    assert total >= 2, f"expected >=2 seeded beliefs, got {total}"
    assert full["offset"] == 0

    first = full["beliefs"]
    paged = seeded_client.get(
        "/api/memory/v2/beliefs", params={"limit": 1, "offset": 1}
    ).json()
    assert paged["total"] == total
    assert paged["offset"] == 1
    assert len(paged["beliefs"]) == 1
    # The offset=1 row must differ from the offset=0 row (ordered query).
    if len(first) >= 2:
        assert paged["beliefs"][0]["id"] != first[0]["id"]
