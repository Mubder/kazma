"""Offline recovery must preserve old rows and never follow live path overrides."""
from __future__ import annotations

import json
import sqlite3

import pytest
from kazma_core.memory import backfill_v2


@pytest.fixture
def generation(tmp_path, monkeypatch):
    root = tmp_path / "restored"
    root.mkdir()
    with sqlite3.connect(root / "memory.db") as db:
        db.execute("CREATE TABLE memories (id TEXT, content TEXT, tenant_id TEXT, timestamp REAL, source TEXT, embedding BLOB, relevance REAL)")
        db.executemany("INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?)", [
            ("old-ar", "يفضل المستخدم اللغة العربية", "tenant-a", 123.0, "chat", b"embedding", 0.8),
            ("old-en", "The project uses Python", "tenant-b", 124.0, "chat", None, 0.9),
        ])
    with sqlite3.connect(root / "knowledge_graph.db") as db:
        db.execute("CREATE TABLE kg_nodes (id TEXT, entity_type TEXT, label TEXT, tenant_id TEXT, content TEXT, properties TEXT)")
        db.executemany("INSERT INTO kg_nodes VALUES (?, ?, ?, ?, ?, ?)", [
            ("person", "person", "Alice", "tenant-a", "", "{}"),
            ("project", "project", "Kazma", "tenant-a", "", "{}"),
        ])
        db.execute("CREATE TABLE kg_edges (id TEXT, relation_type TEXT, source_id TEXT, target_id TEXT, tenant_id TEXT, created_at REAL, properties TEXT)")
        db.executemany("INSERT INTO kg_edges VALUES (?, ?, ?, ?, ?, ?, ?)", [
            ("fact", "works_at", "person", "project", "tenant-a", 125.0, "{}"),
            ("structure", "has_memory", "person", "project", "tenant-a", 125.0, "{}"),
        ])
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline migration contacted a provider")
    monkeypatch.setattr(backfill_v2, "_llm_extract_beliefs_from_memories", forbidden)
    return root


def invoke(root, mode, capsys):
    assert backfill_v2.main(["--data-dir", str(root), mode]) == 0
    return json.loads(capsys.readouterr().out)


def test_dry_run_leaves_legacy_bytes_and_v2_untouched(generation, capsys):
    before = {path.name: path.read_bytes() for path in generation.iterdir()}
    result = invoke(generation, "--dry-run", capsys)
    assert result == {"dry_run": True, "memories": {"memories_seen": 2}, "graph": {"nodes_seen": 2, "edges_seen": 2}}
    assert {path.name: path.read_bytes() for path in generation.iterdir()} == before


def test_apply_preserves_text_tenant_embedding_and_rerun_counts(generation, capsys):
    first = invoke(generation, "--apply", capsys)
    assert first["memories"]["episodes_inserted"] == 2
    assert first["graph"]["entities_inserted"] == 2
    assert first["graph"]["beliefs_inserted"] == 1
    assert first["memories"]["beliefs_extracted"] == 0
    with sqlite3.connect(generation / "memory_state.db") as db:
        episodes = db.execute("SELECT user_text, tenant_id, created_at, embedding FROM episodes ORDER BY created_at").fetchall()
        assert episodes == [
            ("يفضل المستخدم اللغة العربية", "tenant-a", 123.0, b"embedding"),
            ("The project uses Python", "tenant-b", 124.0, None),
        ]
        ids = db.execute("SELECT id FROM episodes ORDER BY id").fetchall()
        assert db.execute("SELECT subject,predicate,object,tenant_id FROM beliefs").fetchall() == [("alice", "works_at", "kazma", "tenant-a")]
    second = invoke(generation, "--apply", capsys)
    assert second["memories"]["episodes_inserted"] == 0
    assert second["graph"]["entities_inserted"] == 0
    assert second["graph"]["beliefs_inserted"] == 0
    with sqlite3.connect(generation / "memory_state.db") as db:
        assert db.execute("SELECT id FROM episodes ORDER BY id").fetchall() == ids


def test_explicit_generation_overrides_inherited_live_paths(generation, tmp_path, monkeypatch, capsys):
    decoy = tmp_path / "live.db"
    decoy.write_bytes(b"live database must not be touched")
    keys = ["KAZMA_DATA_DIR", "KAZMA_FTS5_PATH", "KAZMA_KNOWLEDGE_GRAPH_DB", "KAZMA_MEMORY_STATE_DB", "KAZMA_MEMORY_OPS_DB"]
    for key in keys:
        monkeypatch.setenv(key, str(decoy))
    invoke(generation, "--apply", capsys)
    assert decoy.read_bytes() == b"live database must not be touched"
    import os
    assert all(os.environ[key] == str(decoy) for key in keys)


def test_missing_generation_and_implicit_modes_refuse(tmp_path):
    with pytest.raises(SystemExit) as missing:
        backfill_v2.main(["--data-dir", str(tmp_path / "missing"), "--apply"])
    assert missing.value.code == 2
    assert not (tmp_path / "missing").exists()
    with pytest.raises(SystemExit):
        backfill_v2.main(["--data-dir", str(tmp_path)])


def test_external_database_symlink_refuses_before_writing(generation, tmp_path):
    outside = tmp_path / "live.db"
    outside.write_bytes(b"untouched")
    try:
        (generation / "memory_state.db").symlink_to(outside)
    except OSError:
        pytest.skip("This host cannot create symlinks")
    with pytest.raises(SystemExit) as result:
        backfill_v2.main(["--data-dir", str(generation), "--apply"])
    assert result.value.code == 2
    assert outside.read_bytes() == b"untouched"
