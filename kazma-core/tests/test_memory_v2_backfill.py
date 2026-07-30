"""V2 backfill migration tests.

Seeds legacy stores (memories, kg_nodes, kg_edges), runs the backfill,
and verifies:
  - Legacy memories → V2 episodes (correct tier, importance, content)
  - Legacy kg_nodes → V2 entities (type, name, high-stakes flag)
  - Legacy kg_edges → V2 beliefs (subject/predicate/object, bi-temporal)
  - Idempotency: re-running produces ZERO additional rows
  - backfill_status() reports the migrated counts
  - dry_run counts sources without writing
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest


@pytest.fixture()
def isolated_data(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    yield tmp_path


def _seed_legacy_memory(isolated_data):
    """Create a legacy memory.db with a `memories` table + sample rows."""
    from kazma_core.paths import fts5_memory_path

    conn = sqlite3.connect(fts5_memory_path())
    conn.execute(
        """CREATE TABLE memories (
            id TEXT PRIMARY KEY, content TEXT, metadata TEXT DEFAULT '{}',
            timestamp INTEGER DEFAULT 0, source TEXT DEFAULT '',
            relevance REAL DEFAULT 1.0, embedding BLOB, tenant_id TEXT
        )"""
    )
    now = int(time.time())
    rows = [
        ("m1", "User prefers dark mode", now, "consolidator", 0.95, None, "default"),
        ("m2", "Random chit-chat about weather", now, "turns", 0.5, None, "default"),
        ("m3", "Project uses Python 3.12", now, "consolidator", 0.9, b"\x00\x01\x02", "default"),
    ]
    for r in rows:
        conn.execute(
            "INSERT INTO memories (id, content, timestamp, source, relevance, embedding, tenant_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            r,
        )
    conn.commit()
    conn.close()


def _seed_legacy_graph(isolated_data):
    """Create a legacy knowledge_graph.db with kg_nodes + kg_edges."""
    from kazma_core.paths import knowledge_graph_db

    conn = sqlite3.connect(knowledge_graph_db())
    conn.execute(
        """CREATE TABLE kg_nodes (
            id TEXT PRIMARY KEY, entity_type TEXT, label TEXT, content TEXT,
            properties TEXT DEFAULT '{}', tenant_id TEXT, updated_at REAL
        )"""
    )
    conn.execute(
        """CREATE TABLE kg_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT, target_id TEXT,
            relation_type TEXT, properties TEXT DEFAULT '{}', tenant_id TEXT, created_at REAL
        )"""
    )
    now = time.time()
    # Nodes: a person, a project, a tool
    conn.execute(
        "INSERT INTO kg_nodes (id, entity_type, label, content, properties, tenant_id, updated_at) "
        "VALUES ('john', 'person', 'John', 'user John', '{}', 'default', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO kg_nodes (id, entity_type, label, content, properties, tenant_id, updated_at) "
        "VALUES ('kazma', 'project', 'Kazma', 'the Kazma project', '{}', 'default', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO kg_nodes (id, entity_type, label, content, properties, tenant_id, updated_at) "
        "VALUES ('python', 'tool', 'Python', 'language', '{}', 'default', ?)",
        (now,),
    )
    # Edges: john works_at kazma, kazma uses python
    import json

    conn.execute(
        "INSERT INTO kg_edges (source_id, target_id, relation_type, properties, tenant_id, created_at) "
        "VALUES ('john', 'kazma', 'works_at', ?, 'default', ?)",
        (json.dumps({"fact": "John works at Kazma", "confidence": 0.9, "importance": 4}), now),
    )
    conn.execute(
        "INSERT INTO kg_edges (source_id, target_id, relation_type, properties, tenant_id, created_at) "
        "VALUES ('kazma', 'python', 'uses_tool', ?, 'default', ?)",
        (json.dumps({"fact": "Kazma uses Python", "confidence": 0.8, "importance": 3}), now),
    )
    conn.commit()
    conn.close()


# ── Tests ─────────────────────────────────────────────────────────────────


def test_backfill_memories_to_episodes(isolated_data):
    from kazma_core.memory.backfill_v2 import run_backfill
    from kazma_core.paths import primary_memory_db

    _seed_legacy_memory(isolated_data)
    stats = run_backfill()
    mem = stats["memories"]
    assert mem["memories_seen"] == 3
    assert mem["episodes_inserted"] == 3

    # Verify content + tier + importance
    conn = sqlite3.connect(primary_memory_db())
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT user_text, tier, structural_importance FROM episodes WHERE metadata_json LIKE '%backfill_memories%'"
    ).fetchall()
    conn.close()
    contents = {r["user_text"] for r in rows}
    assert "User prefers dark mode" in contents
    assert all(r["tier"] == "episodic" for r in rows)
    # consolidator-sourced + high relevance → importance 4
    imp = {r["user_text"]: r["structural_importance"] for r in rows}
    assert imp["User prefers dark mode"] >= 4  # relevance 0.95
    assert imp["Random chit-chat about weather"] == 2  # low source


def test_backfill_graph_to_beliefs_and_entities(isolated_data):
    from kazma_core.memory.backfill_v2 import run_backfill
    from kazma_core.paths import primary_memory_db

    _seed_legacy_graph(isolated_data)
    stats = run_backfill()
    g = stats["graph"]
    assert g["nodes_seen"] == 3
    assert g["entities_inserted"] == 3
    assert g["edges_seen"] == 2
    assert g["beliefs_inserted"] == 2

    conn = sqlite3.connect(primary_memory_db())
    conn.row_factory = sqlite3.Row
    # Entities
    ents = {(r["type"], r["name"]) for r in conn.execute(
        "SELECT type, name FROM entities WHERE metadata_json LIKE '%backfill_kg_nodes%'"
    ).fetchall()}
    assert ("person", "John") in ents
    assert ("project", "Kazma") in ents
    # High-stakes flag on person/project
    john = conn.execute("SELECT is_high_stakes FROM entities WHERE name='John'").fetchone()
    assert john["is_high_stakes"] == 1
    python = conn.execute("SELECT is_high_stakes FROM entities WHERE name='Python'").fetchone()
    assert python["is_high_stakes"] == 0

    # Beliefs
    beliefs = {(r["subject"], r["predicate"], r["object"]) for r in conn.execute(
        "SELECT subject, predicate, object FROM beliefs WHERE metadata_json LIKE '%backfill_kg_edges%'"
    ).fetchall()}
    assert ("john", "works_at", "kazma") in beliefs
    assert ("kazma", "uses_tool", "python") in beliefs
    # Bi-temporal: valid_until NULL (still believed), valid_from = created_at
    b = conn.execute(
        "SELECT valid_until, valid_from FROM beliefs WHERE subject='john' AND predicate='works_at'"
    ).fetchone()
    assert b["valid_until"] is None
    assert b["valid_from"] is not None
    # predicate_type classification
    pt = conn.execute(
        "SELECT predicate_type FROM beliefs WHERE predicate='works_at'"
    ).fetchone()["predicate_type"]
    assert pt == "functional"
    conn.close()


def test_backfill_idempotent(isolated_data):
    """Re-running backfill must NOT duplicate rows."""
    from kazma_core.memory.backfill_v2 import run_backfill
    from kazma_core.paths import primary_memory_db

    _seed_legacy_memory(isolated_data)
    _seed_legacy_graph(isolated_data)
    run_backfill()
    conn = sqlite3.connect(primary_memory_db())
    e1 = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    b1 = conn.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0]
    ent1 = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    conn.close()

    # Re-run
    run_backfill()
    conn = sqlite3.connect(primary_memory_db())
    e2 = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    b2 = conn.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0]
    ent2 = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    conn.close()

    assert e2 == e1, f"episodes duplicated: {e1} → {e2}"
    assert b2 == b1, f"beliefs duplicated: {b1} → {b2}"
    assert ent2 == ent1, f"entities duplicated: {ent1} → {ent2}"


def test_backfill_status(isolated_data):
    from kazma_core.memory.backfill_v2 import backfill_status, run_backfill

    _seed_legacy_memory(isolated_data)
    _seed_legacy_graph(isolated_data)
    run_backfill()
    status = backfill_status()
    assert status["backfilled_episodes"] == 3
    assert status["backfilled_entities"] == 3
    assert status["backfilled_beliefs"] == 2


def test_backfill_dry_run(isolated_data):
    from kazma_core.memory.backfill_v2 import run_backfill
    from kazma_core.paths import primary_memory_db

    _seed_legacy_memory(isolated_data)
    _seed_legacy_graph(isolated_data)
    stats = run_backfill(dry_run=True)
    assert stats["dry_run"] is True
    assert stats["memories"]["memories_seen"] == 3
    assert stats["graph"]["nodes_seen"] == 3
    assert stats["graph"]["edges_seen"] == 2
    # dry_run must NOT write anything
    import os

    assert not os.path.exists(primary_memory_db())


def test_backfill_no_legacy_stores(isolated_data):
    """Backfill is a no-op (not an error) when legacy stores are absent."""
    from kazma_core.memory.backfill_v2 import run_backfill

    stats = run_backfill()
    assert stats["memories"]["memories_seen"] == 0
    assert stats["graph"]["nodes_seen"] == 0
