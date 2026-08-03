"""Phase C + D + eval smoke tests for Memory V2 remaining sprints."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest


@pytest.fixture()
def mem_db(tmp_path, monkeypatch):
    db = tmp_path / "memory_state.db"
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(db))
    try:
        import kazma_core.paths as paths

        monkeypatch.setattr(paths, "primary_memory_db", lambda: str(db))
    except Exception:
        pass
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    conn = sqlite3.connect(str(db), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    yield conn
    conn.close()


def test_procedural_match_and_fence(mem_db):
    from kazma_core.memory.procedural import (
        format_procedural_hints,
        match_procedural_dags,
        record_procedural_outcome,
    )

    record_procedural_outcome(
        mem_db,
        name="file_edit_flow",
        description="Edit a file with read then write tools",
        preconditions={"intent": "edit_file"},
        dag_steps=[{"tool": "file_read"}, {"tool": "file_write"}],
        postconditions={"file_changed": True},
        success=True,
    )
    # Raise confidence with more successes
    for _ in range(3):
        record_procedural_outcome(
            mem_db,
            name="file_edit_flow",
            description="Edit a file with read then write tools",
            preconditions={"intent": "edit_file"},
            dag_steps=[{"tool": "file_read"}, {"tool": "file_write"}],
            postconditions={"file_changed": True},
            success=True,
        )
    dags = match_procedural_dags(mem_db, "please edit this file for me", limit=3)
    assert dags, "expected procedural match on edit_file skill"
    block = format_procedural_hints(dags)
    assert block
    assert "kazma:data" in block or "untrusted" in block.lower() or "Procedural" in block


def test_procedural_quarantine(mem_db):
    from kazma_core.memory.procedural import record_procedural_outcome

    cfg = {"v2": {"procedural_quarantine_threshold": 0.4, "procedural_quarantine_min_trials": 3}}
    # Fail enough times to quarantine
    for ok in (False, False, False, False):
        r = record_procedural_outcome(
            mem_db,
            name="bad_skill",
            description="always fails",
            preconditions={"x": 1},
            dag_steps=[{"tool": "noop"}],
            postconditions={},
            success=ok,
            cfg=cfg,
        )
    row = mem_db.execute(
        "SELECT status, confidence_score, total_trials FROM procedural_dags WHERE name='bad_skill'"
    ).fetchone()
    assert row is not None
    assert int(row["total_trials"]) >= 3
    assert row["status"] == "quarantine" or float(row["confidence_score"]) < 0.5


def test_entity_merge_decide(mem_db):
    from kazma_core.memory.entity_resolution import decide_entity_merge, list_pending_merges

    now = time.time()
    mem_db.execute(
        """INSERT INTO entities (id, tenant_id, type, name, aliases_json, is_high_stakes)
           VALUES ('john_smith','default','person','John Smith','[]',1),
                  ('j_smith','default','person','J Smith','[]',1)"""
    )
    mem_db.execute(
        """INSERT INTO entity_merges
           (id, tenant_id, source_entity_id, target_entity_id, status, merge_tier, confidence, requested_at)
           VALUES ('m1','default','j_smith','john_smith','pending','tier2_vector',0.9,?)""",
        (now,),
    )
    mem_db.commit()
    pending = list_pending_merges(mem_db)
    assert any(m["id"] == "m1" for m in pending)
    result = decide_entity_merge(mem_db, "m1", approve=True)
    assert result.get("ok") is True
    row = mem_db.execute("SELECT status FROM entity_merges WHERE id='m1'").fetchone()
    assert row["status"] == "approved"


def test_global_reconsolidation_dedupes(mem_db):
    from kazma_core.memory.global_reconsolidation import run_global_reconsolidation

    now = time.time()
    for i, conf in enumerate((0.5, 0.9)):
        mem_db.execute(
            """INSERT INTO beliefs
               (id, tenant_id, subject, predicate, predicate_type, object,
                confidence, structural_importance, source_trust_weight,
                valid_from, ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"b{i}",
                "default",
                "user",
                "likes",
                "functional",
                "teal",
                conf,
                3,
                1.0,
                now,
                now,
            ),
        )
    mem_db.commit()
    stats = run_global_reconsolidation(mem_db, max_merges=10, reembed_limit=0)
    assert stats["duplicate_beliefs_merged"] >= 1
    active = mem_db.execute(
        "SELECT COUNT(*) FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL"
    ).fetchone()[0]
    assert active == 1


def test_working_tier_promote_and_clear(mem_db, monkeypatch):
    from kazma_core.memory.consolidator import clear_working_memory

    # Direct insert as working
    now = time.time()
    mem_db.execute(
        """INSERT INTO episodes
           (id, tenant_id, session_id, turn_number, user_text, tier, created_at)
           VALUES ('w1','default','sess-w',1,'hello working','working',?)""",
        (now,),
    )
    mem_db.commit()
    n = clear_working_memory("sess-w", tenant_id="default")
    assert n >= 1
    left = mem_db.execute(
        "SELECT COUNT(*) FROM episodes WHERE session_id='sess-w' AND tier='working'"
    ).fetchone()[0]
    assert left == 0


def test_tenant_isolation_beliefs(mem_db):
    from kazma_core.memory.recall import recall

    now = time.time()
    mem_db.execute(
        """INSERT INTO beliefs
           (id, tenant_id, subject, predicate, predicate_type, object,
            confidence, structural_importance, source_trust_weight,
            valid_from, ingested_at)
           VALUES ('ba','tenant-a','user','likes','functional','red',0.9,3,1.0,?,?),
                  ('bb','tenant-b','user','likes','functional','blue',0.9,3,1.0,?,?)""",
        (now, now, now, now),
    )
    mem_db.commit()
    ra = recall("likes color", conn=mem_db, tenant_id="tenant-a", limit=5)
    rb = recall("likes color", conn=mem_db, tenant_id="tenant-b", limit=5)
    ids_a = {h.id for h in ra.beliefs}
    ids_b = {h.id for h in rb.beliefs}
    assert "ba" in ids_a or any("red" in h.content for h in ra.beliefs)
    assert "bb" not in ids_a
    assert "bb" in ids_b or any("blue" in h.content for h in rb.beliefs)


def test_backends_default_local(monkeypatch):
    from kazma_core.memory.backends import (
        get_backends_cfg,
        mask_backends_cfg,
        test_vector_backend,
    )

    cfg = get_backends_cfg()
    assert cfg["mode"] == "local"
    assert cfg["vector"]["provider"] == "sqlite_vec"
    masked = mask_backends_cfg(
        {
            "mode": "remote",
            "vector": {"provider": "qdrant", "api_key": "secret123", "url": "http://x"},
            "embedder": {"provider": "local", "api_key": ""},
            "graph": {},
            "failover": {},
        }
    )
    assert masked["vector"]["api_key"] == "***"


def test_eval_golden_set_exists():
    root = Path(__file__).resolve().parents[2]
    golden = root / "kazma-core" / "tests" / "fixtures" / "memory_golden.json"
    assert golden.is_file(), "golden set fixture missing"
    data = json.loads(golden.read_text(encoding="utf-8"))
    assert isinstance(data.get("cases"), list)
    assert len(data["cases"]) >= 1
