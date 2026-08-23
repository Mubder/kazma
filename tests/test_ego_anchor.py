"""Ego-graph anchoring + degree semantics — orphan-node root fix tests.

Live-data verified 2026-08-24: leaf belief subjects (scalar payload objects,
no entity-side linkage) rendered as disconnected components / never fired
the isolated flag. These tests lock the write-time anchor, the idempotent
backfill, and the entity-only degree semantics.
"""

from __future__ import annotations

import sqlite3

import pytest

from kazma_core.memory.ego_anchor import (
    anchor_leaf_subject,
    anchor_orphan_leaf_concepts,
    is_payload_object,
    subject_has_entity_link,
)
from kazma_core.memory.schema_v2 import ensure_primary_schema


@pytest.fixture()
def mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    # The ego node is an entity (self hub).
    conn.execute(
        "INSERT INTO entities (id, name, type) VALUES ('user', 'User', 'person')"
    )
    conn.commit()
    yield conn
    conn.close()


def _add_entity(conn, eid, etype="concept"):
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, name, type) VALUES (?, ?, ?)",
        (eid, eid, etype),
    )
    conn.commit()


def _add_belief(conn, subject, predicate, obj):
    import time as _time

    conn.execute(
        """INSERT INTO beliefs (id, subject, predicate, object, predicate_type,
                                valid_from, ingested_at, confidence)
           VALUES (('b_' || hex(randomblob(8))), ?, ?, ?, 'semantic', ?, ?, 0.9)""",
        (subject, predicate, obj, _time.time(), _time.time()),
    )
    conn.commit()


class TestPayloadClassification:
    def test_scalar_string_is_payload(self, mem_conn):
        assert is_payload_object(mem_conn, "fully_clean") is True
        assert is_payload_object(mem_conn, "4/4") is True
        assert is_payload_object(
            mem_conn, r"C:\Users\balfa\kazma\NATIVE-IOS-ANDROID-CHANGES.md"
        ) is True

    def test_entity_object_is_not_payload(self, mem_conn):
        _add_entity(mem_conn, "hadidfit_ai")
        assert is_payload_object(mem_conn, "hadidfit_ai") is False

    def test_junk_and_empty_never_payload(self, mem_conn):
        assert is_payload_object(mem_conn, "true") is False
        assert is_payload_object(mem_conn, "") is False
        assert is_payload_object(mem_conn, "user") is False


class TestSubjectLinkage:
    def test_payload_only_subject_has_no_entity_link(self, mem_conn):
        _add_belief(mem_conn, "sakhrfit", "availability_status", "fully_clean")
        assert subject_has_entity_link(mem_conn, "sakhrfit") is False

    def test_entity_object_counts_as_link(self, mem_conn):
        _add_entity(mem_conn, "hadidfit_ai")
        _add_belief(mem_conn, "sakhrfit", "competes_with", "hadidfit_ai")
        assert subject_has_entity_link(mem_conn, "sakhrfit") is True

    def test_inbound_from_entity_counts_as_link(self, mem_conn):
        _add_entity(mem_conn, "research_session_1")
        _add_belief(mem_conn, "research_session_1", "covered_brand", "sakhrfit")
        assert subject_has_entity_link(mem_conn, "sakhrfit") is True


class TestAnchor:
    def test_anchor_creates_user_related_to_belief(self, mem_conn):
        _add_belief(mem_conn, "sakhrfit", "availability_status", "fully_clean")
        result = anchor_leaf_subject(mem_conn, "sakhrfit")
        assert result.get("action") in ("append", "supersede", "transition")
        row = mem_conn.execute(
            "SELECT subject, predicate, object, extraction_method FROM beliefs "
            "WHERE predicate='related_to' AND object='sakhrfit'"
        ).fetchone()
        assert row is not None
        assert row["subject"] == "user"
        assert row["extraction_method"] == "system_tool"

    def test_anchor_idempotent(self, mem_conn):
        _add_belief(mem_conn, "vroxiq", "brand_name_status", "rejected")
        first = anchor_leaf_subject(mem_conn, "vroxiq")
        second = anchor_leaf_subject(mem_conn, "vroxiq")
        assert second.get("action") == "noop"
        assert second.get("reason") == "already_linked"
        n = mem_conn.execute(
            "SELECT COUNT(*) FROM beliefs WHERE object='vroxiq' AND predicate='related_to'"
        ).fetchone()[0]
        assert n == 1
        assert first.get("action") != "error"

    def test_linked_subject_never_anchored(self, mem_conn):
        _add_entity(mem_conn, "hadidfit_ai")
        _add_belief(mem_conn, "thravor", "competes_with", "hadidfit_ai")
        result = anchor_leaf_subject(mem_conn, "thravor")
        assert result == {"action": "noop", "reason": "already_linked"}


class TestBackfillSweep:
    def test_backfill_anchors_all_leaves_and_is_idempotent(self, mem_conn):
        _add_entity(mem_conn, "connected_brand")
        _add_belief(mem_conn, "leaf_a", "status", "clean")          # leaf
        _add_belief(mem_conn, "leaf_b", "score", "4/4")             # leaf
        _add_belief(mem_conn, "hubby", "mentions", "connected_brand")  # linked

        stats = anchor_orphan_leaf_concepts(mem_conn)
        assert stats["anchored"] == 2  # leaf_a + leaf_b; hubby untouched
        n = mem_conn.execute(
            "SELECT COUNT(*) FROM beliefs WHERE predicate='related_to' "
            "AND object IN ('leaf_a','leaf_b','hubby')"
        ).fetchone()[0]
        assert n == 2

        # Second sweep: nothing new.
        again = anchor_orphan_leaf_concepts(mem_conn)
        assert again["anchored"] == 0

    def test_sweep_respects_limit(self, mem_conn):
        for i in range(5):
            _add_belief(mem_conn, f"leaf_{i}", "status", f"val_{i}")
        stats = anchor_orphan_leaf_concepts(mem_conn, limit=2)
        assert stats["anchored"] == 2


class TestDegreeSemantics:
    def test_degree_sql_ignores_payload_objects(self, mem_conn):
        """The isolated flag must fire for scalar-only leaf concepts."""
        from kazma_ui.memory_api import _entity_degree_sql

        _add_belief(mem_conn, "sakhrfit", "availability_status", "fully_clean")
        mem_conn.execute(
            "INSERT INTO entities (id, name, type) VALUES ('sakhrfit', 'sakhrfit', 'concept')"
        )
        mem_conn.commit()

        degree = mem_conn.execute(
            f"SELECT {_entity_degree_sql()} FROM entities e WHERE e.id='sakhrfit'"
        ).fetchone()[0]
        assert degree == 0  # 'fully_clean' is payload text, not a neighbor

        # And an entity-object neighbor DOES count.
        _add_entity(mem_conn, "hadidfit_ai")
        _add_belief(mem_conn, "sakhrfit", "competes_with", "hadidfit_ai")
        degree = mem_conn.execute(
            f"SELECT {_entity_degree_sql()} FROM entities e WHERE e.id='sakhrfit'"
        ).fetchone()[0]
        assert degree == 1
