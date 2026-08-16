"""Phase 6 — autonomy modes (Commitment §9).

strict / balanced / autonomous / yolo modulate how authorize_effect handles the
remind act. Same ambiguous input, four distinct outcomes.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kazma_core.safety.commitment import authorize_effect

REQUEST_AT = datetime(2026, 8, 11, 10, 0, 0, tzinfo=timezone.utc)
# grok reset Aug 15 → 2 days from a "in 2 days"=Aug 13 candidate (within 7d window)
GROK_NEARBY = [{"predicate": "grok_next_reset", "object": "2026-08-15"}]
# copilot Sep 1 → 19 days from the Aug 13 candidate (within strict's 30d, outside default 7d)
COPILOT_FAR = [{"predicate": "copilot_next_reset", "object": "2026-09-01"}]

AMBIG = "remind me in 2 days"


@pytest.fixture
def ops_db(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "ops.db"))


def test_balanced_clarifies_nearby_event(ops_db):
    d = authorize_effect("schedule_task", {"timing": "2d"}, user_text=AMBIG,
                         request_at=REQUEST_AT, memory_beliefs=GROK_NEARBY,
                         cfg={"mode": "balanced"})
    assert d.decision == "clarify"


def test_yolo_bypasses_semantic_gate(ops_db):
    """yolo: the semantic gate steps aside entirely (audit-only allow, no
    rewrite, no commitment). The security HITL is a separate axis."""
    d = authorize_effect("schedule_task", {"timing": "2d"}, user_text=AMBIG,
                         request_at=REQUEST_AT, memory_beliefs=GROK_NEARBY,
                         cfg={"mode": "yolo"})
    assert d.decision == "allow"
    assert d.rewritten_args is None       # no rewrite
    assert d.commitment_id is None        # audit-only, nothing persisted


def test_active_security_yolo_bypasses_semantic_gate(ops_db, monkeypatch):
    """Incident 2026-08-16 ("YOLO keeps asking"): an active per-thread security
    YOLO bypasses the semantic gate even under the default balanced commitment
    mode. Without this, the user approves once and the next semantic check
    interrupts again. A thread without active YOLO still clarifies."""
    import kazma_core.safety.yolo as yolo_mod

    monkeypatch.setattr(yolo_mod, "is_yolo_active", lambda tid: tid == "th-1")
    d = authorize_effect("schedule_task", {"timing": "2d"}, user_text=AMBIG,
                         request_at=REQUEST_AT, memory_beliefs=GROK_NEARBY,
                         thread_id="th-1")  # balanced (no cfg)
    assert d.decision == "allow"
    assert "yolo" in (d.reason or "").lower()
    assert d.commitment_id is None

    d2 = authorize_effect("schedule_task", {"timing": "2d"}, user_text=AMBIG,
                          request_at=REQUEST_AT, memory_beliefs=GROK_NEARBY,
                          thread_id="th-2")
    assert d2.decision == "clarify"


def test_autonomous_allows_with_candidate(ops_db):
    """autonomous: a nearby-event clarify is downgraded to allow with the
    from-now candidate (less friction). The memory overwrite is still guarded
    by the source-trust gate."""
    d = authorize_effect("schedule_task", {"timing": "2d"}, user_text=AMBIG,
                         request_at=REQUEST_AT, memory_beliefs=GROK_NEARBY,
                         cfg={"mode": "autonomous"})
    assert d.decision == "allow"
    assert d.rewritten_args is not None
    assert d.rewritten_args["timing"].startswith("2026-08-13")  # the from-now candidate


def test_strict_widens_relevance_window(ops_db):
    """strict widens the window to 30d. copilot (19d from the candidate) is
    'nearby' under strict → clarify, but 'far' under balanced (7d) → allow."""
    d_strict = authorize_effect("schedule_task", {"timing": "2d"}, user_text=AMBIG,
                                request_at=REQUEST_AT, memory_beliefs=COPILOT_FAR,
                                cfg={"mode": "strict"})
    assert d_strict.decision == "clarify"
    d_balanced = authorize_effect("schedule_task", {"timing": "2d"}, user_text=AMBIG,
                                  request_at=REQUEST_AT, memory_beliefs=COPILOT_FAR,
                                  cfg={"mode": "balanced"})
    assert d_balanced.decision == "allow"


def test_modes_dont_touch_unambiguous_before_event(ops_db):
    """A clean 'before the reset in 2 days' is allow+rewrite in every mode
    (the memory-anchored resolution is unambiguous) — modes only modulate the
    ambiguous-band, not correct resolutions."""
    for mode in ("strict", "balanced", "autonomous"):
        d = authorize_effect("schedule_task", {"timing": "2d"},
                             user_text="remind me before the copilot reset in 2 days",
                             request_at=REQUEST_AT, memory_beliefs=COPILOT_FAR,
                             cfg={"mode": mode})
        assert d.decision == "allow", mode
        assert d.rewritten_args["timing"].startswith("2026-08-30"), mode
