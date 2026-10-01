"""Regrouping a memory-graph node moves it (2026-10-01).

The Memory page's "Group under" called the create route for every node. For a
node already in a group the create route reused the old row's id for a second
parent, the primary key refused it, and the page said "Group failed": a
grouped node could never be moved. The move route (which replaces the old
edge and re-tiers the nodes below) and the tier route had no control at all.

Now the page moves a grouped node and has a Tier action, and the create route
refuses a second parent by name instead of failing on the key.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
JS = REPO / "kazma-ui" / "kazma_ui" / "static" / "js" / "memory_console.js"


def _seed(root: Path) -> None:
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    c = sqlite3.connect(root / "memory_state.db")
    ensure_primary_schema(c)
    c.executemany(
        "INSERT INTO entities (id, tenant_id, type, name) VALUES (?, 'default', ?, ?)",
        [
            ("work", "concept", "Work"),
            ("home", "concept", "Home"),
            ("project", "concept", "Project"),
            ("task", "concept", "Task"),
        ],
    )
    c.commit()
    c.close()
    o = sqlite3.connect(root / "memory_ops.db")
    ensure_ops_schema(o)
    o.commit()
    o.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("KAZMA_MEMORY_STATE_DB", raising=False)
    monkeypatch.delenv("KAZMA_MEMORY_OPS_DB", raising=False)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    _seed(tmp_path)
    from kazma_ui.memory_api import mount_memory_api

    app = FastAPI()
    mount_memory_api(app)
    return TestClient(app, raise_server_exceptions=False)


def _edges(root: Path) -> list[tuple[str, str, int]]:
    c = sqlite3.connect(root / "memory_state.db")
    try:
        return sorted(
            (r[0], r[1], r[2])
            for r in c.execute("SELECT member, group_root, member_tier FROM graph_associations")
        )
    finally:
        c.close()


def test_a_grouped_node_moves_with_the_nodes_below_it(client, tmp_path) -> None:
    groups = "/api/memory/v2/graph/groups"
    assert client.post(groups, json={"group_root": "work", "member": "project"}).json()["ok"]
    assert client.post(groups, json={"group_root": "project", "member": "task"}).json()["ok"]
    assert _edges(tmp_path) == [("project", "work", 2), ("task", "project", 3)]

    # A second parent through the create route is refused by name.
    refused = client.post(groups, json={"group_root": "home", "member": "project"}).json()
    assert refused["ok"] is False
    assert refused["group_root"] == "work"
    assert "/member/project/move" in refused["error"]
    assert _edges(tmp_path) == [("project", "work", 2), ("task", "project", 3)]

    moved = client.post(f"{groups}/member/project/move", json={"new_root": "user"}).json()
    assert moved["ok"] and moved["new_tier"] == 1 and moved["subtree_retiered"] == 1
    # The old edge is gone, and the node below moved up a tier with it.
    assert _edges(tmp_path) == [("project", "user", 1), ("task", "project", 2)]


def test_a_grouped_nodes_tier_can_be_set(client, tmp_path) -> None:
    assert client.post(
        "/api/memory/v2/graph/groups", json={"group_root": "work", "member": "task"}
    ).json()["ok"]
    out = client.post("/api/memory/v2/graph/groups/node/task/tier", json={"tier": 4}).json()
    assert out["ok"] and out["member_tier"] == 4
    assert _edges(tmp_path) == [("task", "work", 4)]


def test_the_page_moves_a_grouped_node_and_sets_tiers() -> None:
    """The page calls both routes: a grouped node takes the move route, and a
    grouped node offers a Tier action that posts to the tier route."""
    src = JS.read_text(encoding="utf-8")
    do_group = src[src.index("async function _v2gDoGroup"):src.index("// Override a grouped node's tier")]
    assert "/move'" in do_group and "new_root: rootId" in do_group
    assert "'/tier'" in src[src.index("async function _v2gSetTier"):src.index("async function _v2gUngroup")]
    assert "_actBtn('set-tier'" in src and "act === 'set-tier'" in src


def test_the_old_create_route_failed_on_a_second_parent(tmp_path) -> None:
    """Negative control: the create route's old upsert, reusing the existing
    row's id for a second parent, is refused by the table's primary key."""
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    c = sqlite3.connect(tmp_path / "m.db")
    ensure_primary_schema(c)
    now = time.time()
    sql = (
        "INSERT INTO graph_associations "
        "(id, tenant_id, group_root, member, member_tier, label, created_at, created_by) "
        "VALUES (?, 'default', ?, 'project', 2, NULL, ?, 'operator') "
        "ON CONFLICT(tenant_id, group_root, member) DO UPDATE SET member_tier=excluded.member_tier"
    )
    c.execute(sql, ("assoc_1", "work", now))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(sql, ("assoc_1", "home", now))
    c.close()
