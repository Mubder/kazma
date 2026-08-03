"""Huge-corpus reconsolidation partition helpers."""

from __future__ import annotations

import sqlite3

from kazma_core.memory.global_reconsolidation import (
    run_global_reconsolidation,
    subject_partition_index,
)
from kazma_core.memory.schema_v2 import ensure_primary_schema


def test_subject_partition_stable():
    a = subject_partition_index("user", 8)
    b = subject_partition_index("user", 8)
    assert a == b
    assert 0 <= a < 8
    # Different subjects should not all collide (weak check)
    buckets = {subject_partition_index(f"subj_{i}", 8) for i in range(40)}
    assert len(buckets) >= 3


def test_partition_only_merges_own_shard(tmp_path):
    db = tmp_path / "m.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)

    # Insert duplicate SPO under subjects that land in different partitions
    now = 1.0
    rows = []
    for i in range(20):
        sub = f"entity_{i}"
        for dup in range(2):
            bid = f"b_{i}_{dup}"
            conn.execute(
                """INSERT INTO beliefs
                   (id, tenant_id, subject, predicate, predicate_type, object,
                    confidence, structural_importance, source_trust_weight,
                    valid_from, ingested_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    bid,
                    "default",
                    sub,
                    "likes",
                    "set",
                    "teal",
                    0.5 + dup * 0.1,
                    2,
                    0.5,
                    now,
                    now,
                ),
            )
            rows.append((sub, bid))
    conn.commit()

    # Run only partition 0 of 4
    stats = run_global_reconsolidation(
        conn,
        tenant_id="default",
        max_merges=100,
        reembed_limit=0,
        partition_index=0,
        partition_count=4,
        auto_partition=False,
    )
    assert stats["partition_index"] == 0
    assert stats["partition_count"] == 4
    assert stats["has_more"] is True
    assert stats["next_partition_index"] == 1
    # Scanned should be subset of total (20 subjects * ~2 = 40 rows → ~1/4)
    assert stats["active_beliefs_scanned"] < 40
    assert stats["active_beliefs_scanned"] > 0

    # Merges only for subjects in partition 0
    for sub, _bid in rows:
        if subject_partition_index(sub, 4) != 0:
            continue
        active = conn.execute(
            """SELECT COUNT(*) FROM beliefs
               WHERE subject=? AND valid_until IS NULL AND invalidated_at IS NULL""",
            (sub,),
        ).fetchone()[0]
        # Duplicates in this shard should be collapsed to 1
        assert active == 1

    conn.close()


def test_auto_partition_marks_huge_corpus(tmp_path, monkeypatch):
    import kazma_core.memory.global_reconsolidation as gr

    monkeypatch.setattr(gr, "_HUGE_CORPUS_THRESHOLD", 10)

    db = tmp_path / "m.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    now = 1.0
    for i in range(15):
        conn.execute(
            """INSERT INTO beliefs
               (id, tenant_id, subject, predicate, predicate_type, object,
                confidence, structural_importance, source_trust_weight,
                valid_from, ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"b_h_{i}",
                "default",
                f"s_{i}",
                "p",
                "set",
                "o",
                0.5,
                1,
                0.5,
                now,
                now,
            ),
        )
    conn.commit()

    stats = run_global_reconsolidation(
        conn,
        tenant_id="default",
        max_merges=5,
        reembed_limit=0,
        partition_count=1,
        auto_partition=True,
    )
    assert stats["huge_corpus"] is True
    assert stats["partition_count"] > 1
    assert stats["has_more"] is True
    conn.close()
