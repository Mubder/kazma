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
    compact_relative_delta,
    parse_absolute_timing,
    parse_time_expressions,
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
    auto-allowed by the args-first path. Chat text 'yes' has no time options,
    so this is deny (not a Cancel-only card) — still not an allow."""
    d = authorize_effect(
        "schedule_task",
        {"timing": "2026-09-15T00:00:00+00:00", "prompt": "x"},  # far from GROK Aug 15
        user_text="yes",
        request_at=REQUEST_AT, memory_beliefs=GROK,
        thread_id="t-args-3", turn_id="turn1",
    )
    assert d.decision != "allow", "conflicting timing must not allow (CoPilot guard)"
    if d.decision == "clarify":
        assert d.options and any(o.get("id") != "cancel" for o in d.options)


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


# ── 2026-08-16 incident: tz-qualified belief dates + compact shorthand ──
#
# The user's reset beliefs are stored as free text WITH timezone qualifiers
# ("2026-08-17 02:48 Kuwait (UTC+3)"). parse_belief_date returned None for
# all of them, so the gate could only see far-away beliefs (Sep/Oct), judged
# every correct fire time a "conflict", then fell through to chat text which
# had no time words → "no time expression found" loop. The scheduler's own
# compact shorthand ("1386m", the format the schedule_task docstring
# advertises) was also unrecognized by the gate.

from kazma_core.safety.commitment.relative_time import (  # noqa: E402
    parse_belief_date,
    parse_time_expressions,
)

TZ_BELIEFS = [
    {"predicate": "grok_personal_next_reset",
     "object": "2026-08-17 02:48 Kuwait (UTC+3)"},
    {"predicate": "admin_grok_next_reset",
     "object": "2026-08-18 14:36 Kuwait (UTC+3)"},
    {"predicate": "zcode_next_reset", "object": "2026-08-19 13:47 (+8)"},
    {"predicate": "copilot_next_reset", "object": "2026-09-01"},
]

REQUEST_AT_0816 = datetime(2026, 8, 16, 0, 13, 0, tzinfo=timezone.utc)


def test_parse_belief_date_handles_tz_qualifiers():
    assert parse_belief_date("2026-08-17 02:48 Kuwait (UTC+3)") is not None
    assert parse_belief_date("2026-08-19 13:47 (+8)") is not None
    assert parse_belief_date("Aug 17 02:18 Asia/Kuwait") is not None
    # plain ISO + plain dates unaffected
    assert parse_belief_date("2026-09-01") is not None
    assert parse_belief_date("2026-08-17T02:48:00") is not None


def test_validate_consistent_with_tz_qualified_belief():
    """Incident regression: a fire time anchored to a tz-qualified reset
    belief must be 'consistent'. Before the fix the belief was unparseable,
    only far-away beliefs were visible, and this returned 'conflict'."""
    consistency, matched = validate_timing_against_memory(
        "2026-08-17T02:18:00", TZ_BELIEFS,
    )
    assert consistency == "consistent"
    assert matched is not None and matched["predicate"] == "grok_personal_next_reset"


def test_iso_reminder_anchored_to_tz_belief_allows(ops_db):
    """The full incident: agent retry with ISO timing, chat text has no time
    words. Must allow+rewrite — not loop on 'no time expression found'."""
    d = authorize_effect(
        "schedule_task",
        {"timing": "2026-08-17T02:18:00",
         "prompt": "Grok personal reset in 30 minutes"},
        user_text="recompute exact fire times and re-submit all 3 reminders",
        request_at=REQUEST_AT_0816, memory_beliefs=TZ_BELIEFS,
        thread_id="t-0816-1", turn_id="turn1",
    )
    assert d.decision == "allow", f"expected allow, got {d.decision}: {d.reason}"
    assert d.rewritten_args is not None
    assert d.rewritten_args["timing"].startswith("2026-08-17")


def test_compact_shorthand_parses():
    """The cron scheduler's native '5m'/'24h' shorthand (advertised in the
    schedule_task docstring) must be a recognized time expression."""
    for text, lead_min in [("1386m", 1386), ("24h", 24 * 60),
                           ("in 20m", 20), ("5m", 5)]:
        exprs = parse_time_expressions(text, request_at=REQUEST_AT_0816)
        assert exprs, f"{text!r} produced no time expression"
        assert exprs[0].lead == timedelta(minutes=lead_min), text


def test_compact_timing_arg_resolves_without_chat_time_words(ops_db):
    """Scheduler-native '1386m' in args.timing is an explicit from-now delay.
    Nearby reset beliefs must not deny or Cancel-only-card it (2026-09-02)."""
    d = authorize_effect(
        "schedule_task",
        {"timing": "1386m", "prompt": "grok reset ping"},
        user_text="try again",
        request_at=REQUEST_AT_0816, memory_beliefs=TZ_BELIEFS,
        thread_id="t-0816-2", turn_id="turn1",
    )
    assert d.decision == "allow", f"got {d.decision}: {d.reason}"
    assert d.rewritten_args is not None
    assert "T" in d.rewritten_args["timing"]


def test_conflicting_absolute_timing_still_guarded(ops_db):
    """CoPilot guard preserved after the fallback: an absolute timing far from
    every belief must NOT be auto-allowed via the timing-arg fallback."""
    d = authorize_effect(
        "schedule_task",
        {"timing": "2026-11-01T09:00:00+00:00", "prompt": "x"},
        user_text="try again",
        request_at=REQUEST_AT_0816, memory_beliefs=TZ_BELIEFS,
        thread_id="t-0816-3", turn_id="turn1",
    )
    assert d.decision != "allow", "conflicting absolute timing must not allow"
    if d.decision == "clarify":
        assert d.options and any(o.get("id") != "cancel" for o in d.options)


def test_compact_relative_delta():
    assert compact_relative_delta("5m") is not None
    assert compact_relative_delta("5m").total_seconds() == 300
    assert compact_relative_delta("119h").total_seconds() == 119 * 3600
    assert compact_relative_delta("in 5m") is None
    assert compact_relative_delta("2026-09-02T00:00:00+00:00") is None


def test_compact_5m_allows_even_with_nearby_reset(ops_db):
    """The live reschedule: timing='5m' plus a reset later today used to
    nearby-clarify then deny as 'no time expression'. Native compact must allow."""
    d = authorize_effect(
        "schedule_task",
        {"timing": "5m", "prompt": "TEST SCHEDULE"},
        user_text="reschedule them all",
        request_at=REQUEST_AT_0816, memory_beliefs=TZ_BELIEFS,
        thread_id="t-5m", turn_id="turn1",
    )
    assert d.decision == "allow", f"got {d.decision}: {d.reason}"
    assert d.rewritten_args["timing"].startswith("2026-08-16")


def test_missing_time_is_deny_not_cancel_only_card(ops_db):
    """No parseable time in chat AND no absolute args.timing is a missing slot.

    A clarify interrupt with only Cancel parks the turn: no date to pick, no
    assistant reply (2026-09-02). Deny so the model asks in the final reply.
    """
    d = authorize_effect(
        "schedule_task",
        {"prompt": "the weekly reset"},
        user_text="schedule them all",
        request_at=REQUEST_AT, memory_beliefs=[],
        thread_id="t-no-time", turn_id="turn1",
    )
    assert d.decision == "deny"
    assert "ask when to fire" in (d.reason or "").lower() or "no time" in (d.reason or "").lower()
    assert not d.options
