"""PR4 (Class C) — args-first, memory-checked remind gate.

The incident preventer. The model usually resolves the fire time itself and
puts an absolute ISO in ``args.timing``. The gate previously ignored it and
re-parsed the chat text — which fails on a bare "yes"/"confirmed" and caused
the over-clarify loop (incident 2026-08-12). Now an absolute ``timing`` that
is memory-consistent (or has no memory to contradict) is allowed directly.

These tests also lock the CoPilot guard preservation: a timing that
CONTRADICTS a stored belief falls through to the chat resolver (still
clarifies / rewrites), so the wrong-date overwrite class stays blocked.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kazma_core.safety.commitment import authorize_effect
from kazma_core.safety.commitment.relative_time import (
    parse_absolute_timing,
    validate_timing_against_memory,
)

REQUEST_AT = datetime(2026, 8, 11, 10, 0, 0, tzinfo=timezone.utc)
GROK = [{"predicate": "grok_next_reset", "object": "2026-08-15"}]
COPILOT = [{"predicate": "copilot_next_reset", "object": "2026-09-01"}]


@pytest.fixture
def ops_db(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "ops.db"))


# ── parse_absolute_timing ────────────────────────────────────────────


def test_parse_absolute_accepts_iso():
    assert parse_absolute_timing("2026-08-15T09:30:00+00:00") is not None
    assert parse_absolute_timing("2026-08-15") is not None
    assert parse_absolute_timing("2026-08-15T09:30:00Z") is not None


@pytest.mark.parametrize("rel", ["2d", "in 2 days", "tomorrow", "next week", ""])
def test_parse_absolute_rejects_relative(rel):
    assert parse_absolute_timing(rel) is None


# ── validate_timing_against_memory ───────────────────────────────────


def test_validate_consistent_when_on_event_day():
    consistency, matched = validate_timing_against_memory(
        "2026-08-15T09:30:00+00:00", GROK,
    )
    assert consistency == "consistent"
    assert matched is not None and matched["predicate"] == "grok_next_reset"


def test_validate_conflict_when_far_from_belief():
    # Sep 15 is ~31 days from the Aug 15 belief → conflict (CoPilot class).
    consistency, _ = validate_timing_against_memory(
        "2026-09-15T00:00:00+00:00", GROK,
    )
    assert consistency == "conflict"


def test_validate_no_memory_when_no_beliefs():
    consistency, _ = validate_timing_against_memory(
        "2026-08-20T09:00:00+00:00", [],
    )
    assert consistency == "no_memory"


def test_validate_not_absolute_for_relative():
    assert validate_timing_against_memory("2d", GROK)[0] == "not_absolute"


# ── authorize_effect: the incident case now allows ───────────────────


def test_bare_yes_plus_memory_anchored_timing_allows(ops_db):
    """THE incident fix: user said 'yes' to a reminder whose timing the model
    already anchored to the stored reset date. Previously this re-parsed 'yes'
    → 'no time expression' → clarify → infinite card loop. Now: allow."""
    d = authorize_effect(
        "schedule_task",
        {"timing": "2026-08-15T09:30:00+00:00", "prompt": "grok reset"},
        user_text="yes",
        request_at=REQUEST_AT, memory_beliefs=GROK,
        thread_id="t-args-1", turn_id="turn1",
    )
    assert d.decision == "allow", "memory-anchored absolute timing + 'yes' must allow"
    assert d.rewritten_args is not None
    assert d.rewritten_args["timing"].startswith("2026-08-15")
    assert d.rewritten_args["prompt"] == "grok reset"  # other args preserved


def test_no_memory_absolute_timing_allows(ops_db):
    """Absolute timing with no stored belief to contradict → allow (nothing to
    protect; not the CoPilot overwrite class)."""
    d = authorize_effect(
        "schedule_task",
        {"timing": "2026-08-20T09:00:00+00:00", "prompt": "x"},
        user_text="ok",
        request_at=REQUEST_AT, memory_beliefs=[],
        thread_id="t-args-2", turn_id="turn1",
    )
    assert d.decision == "allow"


def test_conflicting_timing_does_not_short_circuit(ops_db):
    """CoPilot guard preserved: a timing that contradicts a belief must NOT be
    auto-allowed by the args-first path — it falls through to clarify."""
    d = authorize_effect(
        "schedule_task",
        {"timing": "2026-09-15T00:00:00+00:00", "prompt": "x"},  # far from GROK Aug 15
        user_text="yes",
        request_at=REQUEST_AT, memory_beliefs=GROK,
        thread_id="t-args-3", turn_id="turn1",
    )
    assert d.decision == "clarify", "conflicting timing must clarify (CoPilot guard)"


def test_relative_timing_falls_through_to_chat(ops_db):
    """Relative timing ('2d') is not short-circuited; chat-text resolution
    handles it as before (from-now allow when no nearby event clarifies)."""
    d = authorize_effect(
        "schedule_task",
        {"timing": "5d", "prompt": "x"},
        user_text="remind me in 5 days",
        request_at=REQUEST_AT, memory_beliefs=COPILOT,  # Sep 1 is ~21d out, not "nearby"
        thread_id="t-args-4", turn_id="turn1",
    )
    assert d.decision == "allow"  # from-now, no nearby event → allow
    assert d.rewritten_args is not None
