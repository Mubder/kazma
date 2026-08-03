"""Current-fact rotation: Grok/ZCode next_reset supersedes, not stacks."""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def dbs(tmp_path):
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    p = sqlite3.connect(tmp_path / "state.db")
    p.row_factory = sqlite3.Row
    o = sqlite3.connect(tmp_path / "ops.db")
    ensure_primary_schema(p)
    ensure_ops_schema(o)
    yield p, o
    p.close()
    o.close()


def test_parse_grok_and_zcode_from_combined_message():
    from kazma_core.memory.current_facts import parse_current_facts

    text = (
        "Great now save those: My Grok weekly next reset: Next reset: August 10, 02:48 local time. "
        "And my ZCode weekly next reset: Reset Time: 2026-08-05 13:47 +8 so convert it into a local"
    )
    facts = parse_current_facts(text)
    preds = {f["predicate"]: f["object"] for f in facts}
    assert "grok_next_reset" in preds
    assert "zcode_next_reset" in preds
    assert "August 10" in preds["grok_next_reset"] or "02:48" in preds["grok_next_reset"]
    assert "2026-08-05" in preds["zcode_next_reset"] or "13:47" in preds["zcode_next_reset"]
    assert all(f["predicate_type"] == "functional" for f in facts)


def test_parse_meta_service_next_reset():
    from kazma_core.memory.current_facts import parse_current_facts

    facts = parse_current_facts(
        "ignored free text",
        {"service": "SuperGrok", "next_reset": "2026-08-10T02:48:00+03:00"},
    )
    assert len(facts) == 1
    assert facts[0]["predicate"] == "grok_next_reset"
    assert facts[0]["object"].startswith("2026-08-10")
    assert facts[0]["predicate_type"] == "functional"


def test_parse_meta_explicit_predicate():
    from kazma_core.memory.current_facts import parse_current_facts

    facts = parse_current_facts(
        "anything",
        {"predicate": "zcode_next_reset", "object": "2026-08-05 08:47 Kuwait"},
    )
    assert facts[0]["predicate"] == "zcode_next_reset"
    assert facts[0]["predicate_type"] == "functional"


def test_generic_text_not_current_fact():
    from kazma_core.memory.current_facts import parse_current_facts

    assert parse_current_facts("Remember that ShipX is multi-channel commerce") == []


def test_grok_next_reset_supersedes(dbs):
    from kazma_core.memory.belief_mutation import mutate_belief

    p, o = dbs
    r1 = mutate_belief(
        p,
        "user",
        "grok_next_reset",
        "August 3, 2026 02:48",
        ops_conn=o,
        importance=5,
        extraction_method="user_explicit",
    )
    r2 = mutate_belief(
        p,
        "user",
        "grok_next_reset",
        "August 10, 2026 02:48",
        ops_conn=o,
        importance=5,
        extraction_method="user_explicit",
    )
    assert r1["action"] == "supersede"
    assert r2["action"] == "supersede"
    assert r2["superseded_id"] == r1["belief_id"]

    active = p.execute(
        """
        SELECT object FROM beliefs
        WHERE predicate='grok_next_reset'
          AND valid_until IS NULL AND invalidated_at IS NULL
        """
    ).fetchall()
    assert len(active) == 1
    assert "August 10" in active[0][0]

    closed = p.execute(
        """
        SELECT COUNT(*) FROM beliefs
        WHERE predicate='grok_next_reset' AND valid_until IS NOT NULL
        """
    ).fetchone()[0]
    assert closed == 1


def test_zcode_and_grok_independent(dbs):
    from kazma_core.memory.belief_mutation import mutate_belief

    p, o = dbs
    mutate_belief(p, "user", "grok_next_reset", "Aug 10", ops_conn=o, importance=5)
    mutate_belief(p, "user", "zcode_next_reset", "Aug 5", ops_conn=o, importance=5)
    mutate_belief(p, "user", "grok_next_reset", "Aug 17", ops_conn=o, importance=5)

    active = {
        row[0]: row[1]
        for row in p.execute(
            """
            SELECT predicate, object FROM beliefs
            WHERE valid_until IS NULL AND invalidated_at IS NULL
            """
        ).fetchall()
    }
    assert active["grok_next_reset"] == "Aug 17"
    assert active["zcode_next_reset"] == "Aug 5"


def test_reminder_still_never_supersedes(dbs):
    """Regression: has_reminder stays append-only."""
    from kazma_core.memory.belief_mutation import mutate_belief

    p, o = dbs
    r1 = mutate_belief(p, "user", "has_reminder", "Grok A", ops_conn=o, importance=3)
    r2 = mutate_belief(p, "user", "has_reminder", "Grok B", ops_conn=o, importance=3)
    assert r1["action"] == "append"
    assert r2["action"] == "append"
    n = p.execute(
        "SELECT COUNT(*) FROM beliefs WHERE predicate='has_reminder' "
        "AND valid_until IS NULL AND invalidated_at IS NULL"
    ).fetchone()[0]
    assert n == 2


def test_heuristic_extracts_next_reset():
    from kazma_core.memory.belief_extractor import extract_beliefs_heuristic

    beliefs = extract_beliefs_heuristic(
        "Save: My Grok weekly next reset: August 10, 02:48 local time."
    )
    preds = {b["predicate"] for b in beliefs}
    assert "grok_next_reset" in preds
    grok = next(b for b in beliefs if b["predicate"] == "grok_next_reset")
    assert grok["predicate_type"] == "functional"


def test_is_functional_suffix():
    from kazma_core.memory.current_facts import is_functional_current_predicate

    assert is_functional_current_predicate("myapp_next_reset")
    assert is_functional_current_predicate("acme_weekly_reset")
    assert not is_functional_current_predicate("has_reminder")
    assert not is_functional_current_predicate("noted")


def test_noted_near_dedupe(dbs):
    """Near-identical noted blobs must not stack (ShipX Overview twice)."""
    from kazma_core.memory.belief_mutation import mutate_belief

    p, o = dbs
    text_a = "ShipX — Overview: end-to-end Kuwaiti commerce intelligence platform."
    text_b = "ShipX — Overview:  end-to-end   Kuwaiti commerce intelligence platform."
    r1 = mutate_belief(
        p, "user", "noted", text_a, ops_conn=o, predicate_type="set", importance=5
    )
    r2 = mutate_belief(
        p, "user", "noted", text_b, ops_conn=o, predicate_type="set", importance=5
    )
    assert r1["action"] == "append"
    assert r2["action"] == "noop"
    assert r2.get("deduped") == "noted_near"
    n = p.execute(
        "SELECT COUNT(*) FROM beliefs WHERE predicate='noted' "
        "AND valid_until IS NULL AND invalidated_at IS NULL"
    ).fetchone()[0]
    assert n == 1
