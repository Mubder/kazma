"""CoPilot incident regression goldens (Commitment Layer).

Freezes the canonical failure story from
``docs/plans/INTELLIGENT_AGENT_COMMITMENT_LAYER.md`` as checked-in spec tests:

  User:     "remind me before the CoPilot monthly reset in 2 days..."
  Memory:   copilot_next_reset = 2026-09-01 (user-asserted)
  Agent:    treated "in 2 days" as the event date, scheduled a wrong job,
            then on clarification overwrote the Sep 1 memory with the
            invented date.

Phase 0: the golden here was xfail(strict=True) and a paired control test
documented the vulnerable behavior (an inferred write superseded a
user_explicit functional belief).

Phase 1 (landed): the ``mutate_belief`` source-trust gate
(``functional_supersede_requires_user_assert``) now blocks that path. The
golden therefore PASSES (regression guard), the control is retired, and a
kill-switch test confirms the gate is config-gated (operators can loosen it).

Scope: memory-layer only. The schedule-layer repro (wrong fire_at) is covered
by the EN+AR corpus + G2 gate (``test_commitment_corpus.py``).
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def dbs(tmp_path):
    """Isolated primary + ops memory DBs (pattern: test_memory_current_facts)."""
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    p = sqlite3.connect(tmp_path / "state.db")
    p.row_factory = sqlite3.Row
    o = sqlite3.connect(tmp_path / "ops.db")
    ensure_primary_schema(p)
    ensure_ops_schema(o)
    yield p, o
    p.close()
    o.close()


def _active(p: sqlite3.Connection, predicate: str) -> list[sqlite3.Row]:
    return p.execute(
        "SELECT object FROM beliefs WHERE predicate=? "
        "AND valid_until IS NULL AND invalidated_at IS NULL",
        (predicate,),
    ).fetchall()


def test_functional_belief_not_superseded_by_inferred_source(dbs):
    """INCIDENT CORE (regression guard, Phase 1): an invented (llm_inferred)
    date must NOT overwrite a user-asserted functional belief.

    The source-trust gate in ``_mutate_functional`` drops the lower-trust
    overwrite; the user-asserted value stands. This is the memory-corruption
    half of the CoPilot incident, now blocked in code (plan §3.6 rule 2).
    """
    from kazma_core.memory.belief_mutation import mutate_belief

    p, o = dbs
    mutate_belief(
        p, "user", "copilot_next_reset", "2026-09-01",
        ops_conn=o, importance=5, extraction_method="user_explicit",
    )
    # The agent invents "Aug 13" (now + 2 days) during clarification and the
    # post-turn extractor writes it back as an inferred fact.
    result = mutate_belief(
        p, "user", "copilot_next_reset", "2026-08-13",
        ops_conn=o, importance=3, extraction_method="llm_inferred",
    )

    # The gate blocks: inferred source may not supersede a user_explicit fact.
    assert result["action"] != "supersede", (
        "inferred source must not supersede a user_explicit functional belief"
    )
    assert result.get("blocked") == "lower_trust_source"
    active = _active(p, "copilot_next_reset")
    assert len(active) == 1
    assert active[0][0] == "2026-09-01", "the user-asserted value must stand"


def test_user_explicit_can_still_supersede_user_explicit(dbs):
    """Legitimate user revision must NOT be blocked: a user_explicit belief
    may be superseded by another user_explicit assertion (e.g. "actually it's
    Aug 13 now"). The gate only blocks *lower-trust* overwrites."""
    from kazma_core.memory.belief_mutation import mutate_belief

    p, o = dbs
    mutate_belief(
        p, "user", "copilot_next_reset", "2026-09-01",
        ops_conn=o, importance=5, extraction_method="user_explicit",
    )
    result = mutate_belief(
        p, "user", "copilot_next_reset", "2026-08-13",
        ops_conn=o, importance=5, extraction_method="user_explicit",
    )
    assert result["action"] == "supersede"
    active = _active(p, "copilot_next_reset")
    assert active[0][0] == "2026-08-13"


def test_killswitch_disables_source_trust_gate(dbs):
    """Operators can loosen the gate via cfg.v2.functional_supersede_requires
    _user_assert=False (plan §2.3 #10 kill-switch). With it off, an inferred
    write superseds as it did pre-Phase-1."""
    from kazma_core.memory.belief_mutation import mutate_belief

    p, o = dbs
    mutate_belief(
        p, "user", "copilot_next_reset", "2026-09-01",
        ops_conn=o, importance=5, extraction_method="user_explicit",
    )
    result = mutate_belief(
        p, "user", "copilot_next_reset", "2026-08-13",
        ops_conn=o, importance=3, extraction_method="llm_inferred",
        cfg={"v2": {"functional_supersede_requires_user_assert": False}},
    )
    assert result["action"] == "supersede", (
        "kill-switch off → legacy supersede behavior restored"
    )
    active = _active(p, "copilot_next_reset")
    assert active[0][0] == "2026-08-13"
