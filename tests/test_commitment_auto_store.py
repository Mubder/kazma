"""Phase 1 — conservative post-turn auto-store mode (Commitment §6.2).

Post-turn extraction may write episodes freely, but BELIEFS are throttled:
in the default "conservative" mode, low-confidence inferred beliefs are
dropped (keeps the belief graph clean, incl. dates the assistant invented in
dialogue). User_explicit stores always pass; the functional-supersede-of-
user-fact case is the mutate_belief source-trust gate (tested separately).
"""

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


def _active_count(p, predicate):
    return p.execute(
        "SELECT COUNT(*) FROM beliefs WHERE predicate=? "
        "AND valid_until IS NULL AND invalidated_at IS NULL",
        (predicate,),
    ).fetchone()[0]


def _raw(text, conf):
    return {"subject": "user", "predicate": "noted", "object": text,
            "predicate_type": "set", "confidence": conf, "importance": 3}


def test_get_auto_store_mode_default_is_conservative(monkeypatch):
    monkeypatch.delenv("KAZMA_AUTO_STORE_BELIEFS", raising=False)
    from kazma_core.memory.belief_extractor import get_auto_store_mode

    assert get_auto_store_mode(None) == "conservative"


def test_get_auto_store_mode_env_override(monkeypatch):
    from kazma_core.memory.belief_extractor import get_auto_store_mode

    monkeypatch.setenv("KAZMA_AUTO_STORE_BELIEFS", "aggressive")
    assert get_auto_store_mode({"memory": {"auto_store_beliefs": "off"}}) == "aggressive"


def test_conservative_drops_low_confidence_inferred(dbs):
    """Default mode: a 0.4-confidence inferred belief is NOT stored."""
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2

    p, o = dbs
    stats = _apply_beliefs_to_v2([_raw("maybe likes tea", 0.4)], p, o)
    assert stats["skipped_low_confidence"] == 1
    assert _active_count(p, "noted") == 0


def test_conservative_keeps_high_confidence_inferred(dbs):
    """Default mode: a 0.9-confidence inferred belief IS stored."""
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2

    p, o = dbs
    stats = _apply_beliefs_to_v2([_raw("likes coffee", 0.9)], p, o)
    assert stats["applied"] == 1
    assert _active_count(p, "noted") == 1


def test_user_explicit_stores_always_pass(dbs):
    """User_explicit extraction must NEVER be throttled — even at low conf."""
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2

    p, o = dbs
    stats = _apply_beliefs_to_v2(
        [_raw("low conf user fact", 0.2)], p, o, extraction_method="user_explicit",
        cfg={"memory": {"auto_store_beliefs": "conservative"}},
    )
    assert stats["applied"] == 1
    assert _active_count(p, "noted") == 1


def test_aggressive_mode_keeps_low_confidence(dbs):
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2

    p, o = dbs
    stats = _apply_beliefs_to_v2(
        [_raw("low conf", 0.3)], p, o,
        cfg={"memory": {"auto_store_beliefs": "aggressive"}},
    )
    assert stats.get("skipped_low_confidence", 0) == 0
    assert stats["applied"] == 1


def test_off_mode_stores_nothing(dbs):
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2

    p, o = dbs
    stats = _apply_beliefs_to_v2(
        [_raw("high conf", 0.95), _raw("also high", 0.9)], p, o,
        cfg={"memory": {"auto_store_beliefs": "off"}},
    )
    assert stats["skipped_auto_store_off"] == 2
    assert _active_count(p, "noted") == 0
