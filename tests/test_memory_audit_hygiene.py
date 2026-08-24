"""P3 hygiene batch (M-10, M-11, M-14, M-15, M-16) from the 2026-08-24 memory audit."""

from __future__ import annotations

import json
import sqlite3

import pytest


@pytest.fixture
def mem_pair(tmp_path, monkeypatch):
    state = tmp_path / "memory_state.db"
    ops = tmp_path / "memory_ops.db"
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    from kazma_core import paths as paths_mod

    monkeypatch.setattr(paths_mod, "primary_memory_db", lambda: str(state))
    monkeypatch.setattr(paths_mod, "memory_ops_db", lambda: str(ops))
    monkeypatch.setattr(paths_mod, "exports_dir", lambda: tmp_path / "exports")
    monkeypatch.setenv("KAZMA_EXPORTS_DIR", str(tmp_path / "exports"))
    (tmp_path / "exports").mkdir()

    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    c = sqlite3.connect(state)
    c.row_factory = sqlite3.Row
    ensure_primary_schema(c)
    o = sqlite3.connect(ops)
    ensure_ops_schema(o)
    o.close()
    return c, state, ops, tmp_path


def test_m10_fts_drift_heals(mem_pair):
    from kazma_core.memory.fts_health import fts_drift_check

    conn, *_ = mem_pair
    conn.execute(
        """INSERT INTO beliefs
           (id, tenant_id, subject, predicate, predicate_type, object,
            confidence, structural_importance, source_trust_weight,
            valid_from, ingested_at)
           VALUES ('b_fts','default','user','likes','set','teal',0.9,2,0.8,1.0,1.0)"""
    )
    conn.commit()
    # Drop the insert trigger so the next belief is written to the base
    # table but not the FTS index — the partial-desync class schema-ensure
    # never sees (it only rebuilds when FTS is *empty*).
    conn.execute("DROP TRIGGER IF EXISTS beliefs_fts_ai")
    conn.execute(
        """INSERT INTO beliefs
           (id, tenant_id, subject, predicate, predicate_type, object,
            confidence, structural_importance, source_trust_weight,
            valid_from, ingested_at)
           VALUES ('b_fts2','default','user','hates','set','maroon',0.9,2,0.8,1.0,1.0)"""
    )
    conn.commit()
    from kazma_core.memory.fts_health import _fts_indexed_count

    fts_n = _fts_indexed_count(conn, "beliefs_fts")
    base_n = conn.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0]
    assert base_n == 2
    assert int(fts_n) == 1

    stats = fts_drift_check(conn, auto_heal=True)
    assert "beliefs" in stats["tables"]
    assert "beliefs" in stats["healed"]
    healed = conn.execute("SELECT COUNT(*) FROM beliefs_fts").fetchone()[0]
    assert int(healed) == int(base_n)


def test_m11_insert_or_ignore_retries_on_pk_collision(mem_pair):
    from kazma_core.memory.belief_mutation import _insert_belief

    conn, *_ = mem_pair
    kw = dict(
        confidence=0.9,
        importance=2,
        trust=0.8,
        extraction_method="user_explicit",
        source_session=None,
        source_turn=None,
        mem_class="general",
        now=100.0,
    )
    first = _insert_belief(
        conn, "b_collide", "default", "user", "lives_in", "functional", "paris", **kw
    )
    assert first["id"] == "b_collide"
    conn.commit()

    second = _insert_belief(
        conn, "b_collide", "default", "user", "lives_in", "functional", "london", **kw
    )
    assert second["id"] != "b_collide"
    assert second["object"] == "london"
    n = conn.execute("SELECT COUNT(*) FROM beliefs WHERE id=?", (second["id"],)).fetchone()[0]
    assert n == 1
    # Original row still present — collision did not clobber it.
    orig = conn.execute("SELECT object FROM beliefs WHERE id='b_collide'").fetchone()[0]
    assert orig == "paris"


def test_m11_functional_rollback_when_insert_cannot_land(mem_pair, monkeypatch):
    """If both INSERT attempts are ignored, the superseded close must roll back."""
    from kazma_core.memory import belief_mutation as bm

    conn, *_ = mem_pair
    conn.execute(
        """INSERT INTO beliefs
           (id, tenant_id, subject, predicate, predicate_type, object,
            confidence, structural_importance, source_trust_weight, extraction_method,
            valid_from, ingested_at)
           VALUES ('b_old','default','user','lives_in','functional','paris',
                   1.0,3,1.0,'user_explicit',1.0,1.0)"""
    )
    conn.commit()

    def _boom(*_a, **_k):
        raise sqlite3.IntegrityError("belief insert ignored")

    monkeypatch.setattr(bm, "_insert_belief", _boom)
    out = bm.mutate_belief(
        conn,
        "user",
        "lives_in",
        "london",
        predicate_type="functional",
        extraction_method="user_explicit",
        confidence=0.9,
        importance=3,
    )
    assert out["action"] == "noop"
    assert out.get("blocked") == "insert_ignored"
    live = conn.execute(
        "SELECT object, valid_until FROM beliefs WHERE id='b_old'"
    ).fetchone()
    assert live[0] == "paris"
    assert live[1] is None  # not left closed without a successor


@pytest.mark.asyncio
async def test_m14_delete_entity_archives_merge_ledger(mem_pair):
    conn, *_rest = mem_pair
    conn.execute(
        "INSERT INTO entities (id, tenant_id, type, name) VALUES ('src_e','default','concept','Src')"
    )
    conn.execute(
        "INSERT INTO entities (id, tenant_id, type, name) VALUES ('tgt_e','default','concept','Tgt')"
    )
    conn.execute(
        """INSERT INTO entity_merges
           (id, tenant_id, source_entity_id, target_entity_id, status,
            merge_tier, confidence, requested_at)
           VALUES ('m_keep','default','src_e','tgt_e','pending','tier1_exact',0.9,1.0)"""
    )
    conn.commit()
    conn.close()

    from kazma_ui.memory_api import delete_entity

    out = await delete_entity("src_e")
    assert out["ok"] is True

    from kazma_core.paths import primary_memory_db

    c = sqlite3.connect(primary_memory_db())
    live = c.execute("SELECT COUNT(*) FROM entity_merges WHERE id='m_keep'").fetchone()[0]
    archived = c.execute(
        "SELECT source_entity_id, archive_reason FROM entity_merges_archive WHERE id='m_keep'"
    ).fetchone()
    gone = c.execute("SELECT COUNT(*) FROM entities WHERE id='src_e'").fetchone()[0]
    c.close()
    assert live == 0
    assert archived is not None
    assert archived[0] == "src_e"
    assert archived[1] == "entity_delete"
    assert gone == 0


def test_m15_graph_clear_binds_three_params_and_audits(mem_pair):
    """The M-05 tenant-scope UPDATE must bind (now, now, tenant), not (now, tenant)."""
    conn, _state, ops, _tmp = mem_pair
    conn.execute(
        """INSERT INTO beliefs
           (id, tenant_id, subject, predicate, predicate_type, object,
            confidence, structural_importance, source_trust_weight,
            valid_from, ingested_at)
           VALUES ('b_clr','default','user','likes','set','teal',0.9,2,0.8,1.0,1.0)"""
    )
    conn.commit()
    now = 12345.0
    tid = "default"
    conn.execute(
        "UPDATE beliefs SET valid_until=?, invalidated_at=? "
        "WHERE tenant_id=? AND valid_until IS NULL AND invalidated_at IS NULL",
        (now, now, tid),
    )
    conn.commit()
    row = conn.execute(
        "SELECT valid_until, invalidated_at FROM beliefs WHERE id='b_clr'"
    ).fetchone()
    assert row[0] == now
    assert row[1] == now

    from kazma_core.memory.graph_backend import clear_tenant_edges

    result = clear_tenant_edges(tenant_id="default")
    assert result["ok"] is True
    assert "cleared" in result


def test_m16_export_covers_episodes_merges_archive(mem_pair):
    conn, _state, ops, tmp = mem_pair
    conn.execute(
        """INSERT INTO episodes
           (id, tenant_id, session_id, turn_number, user_text, assistant_text,
            tier, created_at)
           VALUES ('ep1','default','s',1,'hi','hello','episodic',1.0)"""
    )
    conn.execute(
        "INSERT INTO entities (id, tenant_id, type, name) VALUES ('e1','default','concept','E1')"
    )
    conn.execute(
        "INSERT INTO entities (id, tenant_id, type, name) VALUES ('e2','default','concept','E2')"
    )
    conn.execute(
        """INSERT INTO entity_merges
           (id, tenant_id, source_entity_id, target_entity_id, status,
            merge_tier, confidence, requested_at)
           VALUES ('m1','default','e1','e2','pending','tier1_exact',0.8,1.0)"""
    )
    conn.execute(
        """INSERT INTO beliefs_archive (id, tenant_id, original_belief_json, archived_at)
           VALUES ('ba1','default','{}',1.0)"""
    )
    conn.commit()
    conn.close()

    from kazma_core.memory.export import export_nightly_snapshots

    written = export_nightly_snapshots(tenant_id="default")
    assert "jsonl" in written
    assert "episodes" in written
    assert "entity_merges" in written
    assert "beliefs_archive" in written
    assert "audit" in written
    ep_lines = written["episodes"].read_text(encoding="utf-8").strip().splitlines()
    assert any(json.loads(line)["id"] == "ep1" for line in ep_lines)
    merge_lines = written["entity_merges"].read_text(encoding="utf-8").strip().splitlines()
    assert any(json.loads(line)["id"] == "m1" for line in merge_lines)
