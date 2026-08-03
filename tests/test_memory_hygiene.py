"""Memory hygiene: blocked subjects, FTS heal helper, invalidate API shape."""

from __future__ import annotations

import sqlite3

import pytest


def test_blocked_kazma_v2_subjects():
    from kazma_core.memory.hygiene import (
        is_blocked_belief_subject,
        is_blocked_belief_triple,
    )

    assert is_blocked_belief_subject("kazma_v2_4_0")
    assert is_blocked_belief_subject("kazma_v2")
    assert is_blocked_belief_subject("memory_v2_engine")
    assert is_blocked_belief_subject("v2_4_0")
    assert is_blocked_belief_triple("kazma_v2_4_0", "has_feature", "table_rendering")

    assert not is_blocked_belief_subject("user")
    assert not is_blocked_belief_subject("kazma")
    assert not is_blocked_belief_subject("kca")
    assert not is_blocked_belief_subject("shipx")


def test_mutate_belief_rejects_blocked_subject(tmp_path, monkeypatch):
    from kazma_core.memory import schema_v2
    from kazma_core.memory.belief_mutation import mutate_belief

    db = tmp_path / "mem.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    schema_v2.ensure_primary_schema(conn)

    out = mutate_belief(
        conn,
        "kazma_v2_4_0",
        "has_feature",
        "table_rendering",
        predicate_type="set",
        confidence=0.9,
        importance=3,
    )
    assert out.get("action") == "noop"
    assert out.get("rejected") == "blocked_subject"
    n = conn.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0]
    assert n == 0
    conn.close()


def test_sanitize_rejects_blocked_subject():
    from kazma_core.memory.belief_extractor import _sanitize_belief

    clean = _sanitize_belief(
        {
            "subject": "kazma_v2_4_0",
            "predicate": "status_is",
            "object": "stable",
            "predicate_type": "set",
            "confidence": 0.9,
            "importance": 3,
        }
    )
    assert clean is None


def test_beliefs_write_heals_fts(tmp_path):
    from kazma_core.memory import schema_v2
    from kazma_core.memory.hygiene import beliefs_write, rebuild_beliefs_fts

    db = tmp_path / "mem.db"
    conn = sqlite3.connect(str(db))
    schema_v2.ensure_primary_schema(conn)
    assert rebuild_beliefs_fts(conn) is True

    # Normal write path
    beliefs_write(
        conn,
        """INSERT INTO beliefs
           (id, tenant_id, subject, predicate, predicate_type, object,
            confidence, structural_importance, source_trust_weight,
            valid_from, ingested_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "b_hygiene_1",
            "default",
            "user",
            "likes",
            "set",
            "teal",
            0.8,
            2,
            0.5,
            1.0,
            1.0,
        ),
    )
    conn.commit()
    n = conn.execute(
        "SELECT COUNT(*) FROM beliefs WHERE id='b_hygiene_1'"
    ).fetchone()[0]
    assert n == 1
    conn.close()


def test_invalidate_belief_marks_row(tmp_path, monkeypatch):
    from kazma_core.memory import schema_v2
    from kazma_core.memory.hygiene import invalidate_belief

    db = tmp_path / "mem.db"
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    # Point primary_memory_db via monkeypatch if needed
    import kazma_core.paths as paths

    monkeypatch.setattr(paths, "primary_memory_db", lambda: str(db))

    conn = sqlite3.connect(str(db))
    schema_v2.ensure_primary_schema(conn)
    conn.execute(
        """INSERT INTO beliefs
           (id, tenant_id, subject, predicate, predicate_type, object,
            confidence, structural_importance, source_trust_weight,
            valid_from, ingested_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "b_inv_1",
            "default",
            "user",
            "likes",
            "set",
            "teal",
            0.8,
            2,
            0.5,
            1.0,
            1.0,
        ),
    )
    conn.commit()
    conn.close()

    out = invalidate_belief("b_inv_1", remove_graph=False)
    assert out.get("ok") is True
    assert out.get("updated") == 1

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT valid_until, invalidated_at FROM beliefs WHERE id='b_inv_1'"
    ).fetchone()
    assert row[0] is not None
    assert row[1] is not None
    conn.close()
