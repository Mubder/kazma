"""Phase 0 — CoPilot incident failing goldens (Commitment Layer).

Freezes the canonical failure story from
``docs/plans/INTELLIGENT_AGENT_COMMITMENT_LAYER.md`` as checked-in spec tests:

  User:     "remind me before the CoPilot monthly reset in 2 days..."
  Memory:   copilot_next_reset = 2026-09-01 (user-asserted)
  Agent:    treated "in 2 days" as the event date, scheduled a wrong job,
            then on clarification overwrote the Sep 1 memory with the
            invented date.

These tests encode the CORRECT behavior. They are expected to FAIL today
because no commitment gate / source-trust gating exists yet. The golden is
marked ``xfail(strict=True)`` so:

  * CI stays green now (the test fails as expected -> xfailed).
  * When Phase 1 lands the ``mutate_belief`` source-trust gate, the test
    passes -> strict-xfail flips the suite red -> the maintainer removes the
    xfail because the invariant now holds.

The accompanying ``test_current_behavior_control`` test PASSES today and
documents the vulnerable behavior, proving the golden fails for the right
reason (not a setup error). Invert/delete the control in Phase 1.

Scope: memory-layer only. The schedule-layer repro (wrong fire_at) is
exercised by the EN+AR corpus + G2 gate in PR-B (Phase 0 exit).
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


def test_current_behavior_control(dbs):
    """CONTROL (passes today): documents the current VULNERABLE behavior.

    A lower-trust ``llm_inferred`` write freely supersedes a higher-trust
    ``user_explicit`` functional belief. This is the memory-corruption half
    of the CoPilot incident and the reason the golden below is xfail.

    Invert / delete in Phase 1 once the source-trust gate in
    ``mutate_belief`` blocks this path.
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

    # Current (vulnerable) behavior: inferred source DOES supersede.
    assert result["action"] == "supersede"
    active = _active(p, "copilot_next_reset")
    assert len(active) == 1
    assert active[0][0] == "2026-08-13"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Commitment Layer Phase 1 not yet implemented: mutate_belief does not "
        "gate functional supersedes by source trust, so an llm_inferred write "
        "overwrites a user_explicit belief (CoPilot memory-corruption path)."
    ),
)
def test_functional_belief_not_superseded_by_inferred_source(dbs):
    """INCIDENT CORE: an invented (llm_inferred) date must NOT overwrite a
    user-asserted functional belief without an explicit user assertion.

    Desired invariant (not yet built): a functional supersede where the
    incoming source trust is BELOW the active belief's trust must be blocked
    (or downgraded to a low-confidence append / clarify), never silently
    replace the user-asserted value.

    See plan §3.6 (memory as constraint system) + §6 (post-turn reform).
    """
    from kazma_core.memory.belief_mutation import mutate_belief

    p, o = dbs
    mutate_belief(
        p, "user", "copilot_next_reset", "2026-09-01",
        ops_conn=o, importance=5, extraction_method="user_explicit",
    )
    # The agent invents "Aug 13" and the post-turn extractor writes it back.
    result = mutate_belief(
        p, "user", "copilot_next_reset", "2026-08-13",
        ops_conn=o, importance=3, extraction_method="llm_inferred",
    )

    # Desired invariant: lower-trust inferred source may not supersede a
    # higher-trust user-asserted functional belief.
    assert result["action"] != "supersede", (
        "inferred source must not supersede a user_explicit functional belief"
    )
    active = _active(p, "copilot_next_reset")
    assert len(active) == 1
    assert active[0][0] == "2026-09-01"
