"""Phase 2 — authorize_effect remind-act decisions (Commitment §3.4 / §3.7).

The schedule-path capstone: the gate anchors relative phrases to memory events
and rewrites the tool args to the correct fire_at — so the CoPilot wrong-date
schedule becomes impossible. Clarify cases persist a needs_clarify commitment.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kazma_core.safety.commitment import authorize_effect
from kazma_core.safety.commitment.store import get_commitment

REQUEST_AT = datetime(2026, 8, 11, 10, 0, 0, tzinfo=timezone.utc)
COPILOT = [{"predicate": "copilot_next_reset", "object": "2026-09-01"}]
GROK = [{"predicate": "grok_next_reset", "object": "2026-08-15"}]


@pytest.fixture
def ops_db(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "ops.db"))


def test_copilot_incident_path_rewrites_to_correct_date(ops_db):
    """THE capstone: 'before the copilot reset in 2 days' → args rewritten to
    Sep 1 − 2d = Aug 30, regardless of what the model put in timing. This is
    the schedule half of the incident, now unbreakable."""
    d = authorize_effect(
        "schedule_task",
        {"timing": "2026-08-13T10:00:00+00:00", "prompt": "reset soon"},  # model's WRONG date
        user_text="remind me before the copilot monthly reset in 2 days",
        request_at=REQUEST_AT, memory_beliefs=COPILOT,
        thread_id="t1", turn_id="turn1",
    )
    assert d.decision == "allow"
    assert d.rewritten_args is not None
    assert d.rewritten_args["timing"].startswith("2026-08-30"), (
        "fire_at must be Sep 1 − 2d = Aug 30, not the model's invented Aug 13"
    )
    assert d.rewritten_args["prompt"] == "reset soon"  # non-timing args preserved
    # commitment persisted as ready (caller flips to committed on execution)
    assert d.commitment_id
    c = get_commitment(d.commitment_id)
    assert c is not None and c.status == "ready" and c.act == "remind"
    assert c.slots["fire_at"].startswith("2026-08-30")


def test_ambiguous_nearby_event_clarifies_and_persists(ops_db):
    """'in 2 days' with a nearby memory event (grok, 4d) is ambiguous → clarify,
    and a needs_clarify commitment is persisted (24h TTL)."""
    d = authorize_effect(
        "schedule_task", {"timing": "2d", "prompt": "x"},
        user_text="remind me in 2 days",
        request_at=REQUEST_AT, memory_beliefs=GROK,
        thread_id="t1",
    )
    assert d.decision == "clarify"
    assert d.rewritten_args is None
    assert d.clarify_question
    assert d.commitment_id
    c = get_commitment(d.commitment_id)
    assert c.status == "needs_clarify"
    assert c.expires_at is not None  # TTL set


def test_from_now_with_no_nearby_event_allows_and_rewrites(ops_db):
    """'in 5 days' + copilot (16d away, outside window) → allow, rewrite to
    request_at + 5d."""
    d = authorize_effect(
        "schedule_task", {"timing": "5d"},
        user_text="remind me in 5 days",
        request_at=REQUEST_AT, memory_beliefs=COPILOT,
    )
    assert d.decision == "allow"
    assert d.rewritten_args["timing"].startswith("2026-08-16")  # Aug 11 + 5d


def test_arabic_remind_anchors_to_event(ops_db):
    """AR phrasing resolves identically to EN (Phase 0 corpus parity)."""
    d = authorize_effect(
        "schedule_task", {"timing": "2d"},
        user_text="ذكرني قبل يومين من إعادة تعيين copilot",
        request_at=REQUEST_AT, memory_beliefs=COPILOT,
    )
    assert d.decision == "allow"
    assert d.rewritten_args["timing"].startswith("2026-08-30")


def test_non_remind_act_stays_audit_only(ops_db):
    """Acts without a resolver yet (memory_store → store_fact) remain audit-only
    — no commitment persisted, no rewrite. The memory corruption half is already
    gated at mutate_belief."""
    d = authorize_effect(
        "memory_store", {"text": "remember x"},
        user_text="remember that I like tea",
        request_at=REQUEST_AT, memory_beliefs=COPILOT,
    )
    assert d.decision == "allow"
    assert d.rewritten_args is None
    assert d.commitment_id is None  # audit-only, nothing persisted


def test_remind_without_resolution_inputs_is_audit_only(ops_db):
    """Phase 1 callers (no user_text/memory) get audit-only even for remind —
    this is the LocalToolRegistry.execute choke's behavior."""
    d = authorize_effect("schedule_task", {"timing": "2d"})
    assert d.decision == "allow"
    assert d.rewritten_args is None
    assert d.commitment_id is None
