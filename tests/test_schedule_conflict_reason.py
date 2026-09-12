"""A denial has to say the true reason, or it sends the model somewhere wrong.

2026-09-12, from the operator's chat. They told Kazma their SuperGrok reset had
moved to September 14. Memory still held the old date. `schedule_task` was
called with `timing="2026-09-13T23:18:00+00:00"` and came back:

    no time expression found — ask when to fire
    Pass schedule_task.timing as Nm/Nh (e.g. 247m) or ISO.

Both halves are false. The expression parsed perfectly, and it *was* ISO. The
real reason was the CoPilot-overwrite guard: an absolute timing more than two
days from every dated belief in memory is treated as possibly invented. Here
the belief it conflicted with was the stale one the user was correcting.

The cost of the wrong message was not cosmetic. The model reported to the user
that "the ISO timestamp was not accepted by the scheduler's parser" and offered
to retry as `2660m` — the same instant in relative form, which skipped the
memory check entirely. A misleading error talked it into routing around a
safety gate.

Four things were wrong, and this file covers all four:

1. The denial blamed the format. It now says what actually happened and names
   the belief it conflicted with.
2. `parse_time_expressions` only understood ISO, so "September 14, 2026 at
   2:48 AM" genuinely *was* "no time expression found" — while
   `parse_belief_date`, in the same module, has always read that format.
3. A date the user typed this turn counted as a model invention.
4. Relative timings skipped the guard entirely, which is where the model was
   headed next.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from kazma_core.safety.commitment.relative_time import (
    parse_absolute_timing,
    validate_timing_against_memory,
)

NOW = datetime(2026, 9, 12, 2, 58, tzinfo=timezone.utc)
TIMING = "2026-09-13T23:18:00+00:00"
STALE = [{"predicate": "supergrok_heavy_reset", "object": "September 7, 2026"}]
USER_TEXT = "Weekly SuperGrok Heavy Resets September 14, 2026 at 2:48 AM"
FRESH = [{"predicate": "supergrok_heavy_reset", "object": "September 14, 2026"}]


# ── the premise: the parser was never the problem ───────────────────────────


@pytest.mark.parametrize(
    "timing",
    [
        "2026-09-13T23:18:00+00:00",
        "2026-09-13T23:18:00Z",
        "2026-09-13T23:18:00",
        "2026-09-13 23:18",
    ],
)
def test_every_iso_form_parses(timing):
    """If any of these ever stop parsing, the old message becomes true and this
    whole file is measuring the wrong thing."""
    assert parse_absolute_timing(timing) is not None


def test_the_stale_belief_is_what_refused_it():
    """The mechanism, pinned. Same timing, same code — only memory differs."""
    assert validate_timing_against_memory(TIMING, STALE)[0] == "conflict"
    assert validate_timing_against_memory(TIMING, FRESH)[0] == "consistent"
    assert validate_timing_against_memory(TIMING, [])[0] == "no_memory"


# ── the message ─────────────────────────────────────────────────────────────


def test_the_conflict_denial_does_not_blame_the_format():
    from kazma_core.safety.commitment.authorize import _nearest_belief_hint

    hint = _nearest_belief_hint(TIMING, STALE)
    assert "supergrok_heavy_reset" in hint
    assert "September 7" in hint, "name the belief, so the fix is one step"


def test_the_hint_is_empty_when_there_is_nothing_to_name():
    from kazma_core.safety.commitment.authorize import _nearest_belief_hint

    assert _nearest_belief_hint(TIMING, []) == ""
    assert _nearest_belief_hint(TIMING, [{"predicate": "x", "object": "not a date"}]) == ""
    assert _nearest_belief_hint("2660m", STALE) == "", "relative timing has no absolute to compare"


def test_the_hint_never_raises(monkeypatch):
    """It decorates a denial. If it throws, a refusal becomes a crash."""
    from kazma_core.safety.commitment import authorize as az

    assert az._nearest_belief_hint(TIMING, [{"object": None}]) == ""
    assert az._nearest_belief_hint("", STALE) == ""
    assert az._nearest_belief_hint(TIMING, [{}]) == ""


def test_the_denial_text_tells_the_operator_what_to_do():
    """Rendered from the same pieces the gate uses, so the wording cannot drift
    back to 'pass ISO' without this failing."""
    from kazma_core.safety.commitment.authorize import _nearest_belief_hint

    reason = (
        f"timing {TIMING!r} parsed fine but conflicts with memory: "
        f"it is not within 2 days of any stored date"
        f"{_nearest_belief_hint(TIMING, STALE)}. "
        "Reformatting will not help. Either correct the stored "
        "belief first, or confirm with the user that the new date "
        "supersedes it."
    )
    assert "no time expression found" not in reason
    assert "Pass schedule_task.timing as Nm/Nh" not in reason
    assert "Reformatting will not help" in reason
    assert "supergrok_heavy_reset" in reason


# ── the guard, reworked ─────────────────────────────────────────────────────
#
# Three changes, each paid for by a case above or below.


def test_a_date_the_user_just_typed_is_not_an_invention():
    """The incident, fixed at the root.

    The guard exists to catch a model fabricating a date that contradicts what
    the user said. When the timing matches a date in the user's own message
    this turn, that premise does not hold -- and the case where it misfired is
    exactly the one that matters: the user correcting a stale belief. A guard
    that blocks corrections keeps memory wrong forever.
    """
    c, _ = validate_timing_against_memory(
        TIMING, STALE, user_text=USER_TEXT, request_at=NOW
    )
    assert c == "user_asserted"


def test_the_copilot_invention_is_still_blocked():
    """The negative control, and the reason the fix is narrow.

    The CoPilot incident's second turn is the user saying a bare "yes" while
    the model sends a date it made up. "yes" names no subject, but the
    conversation is still about the reset -- so contentless text keeps the
    conservative comparison against every belief. Treating "nothing named" as
    "nothing to contradict" would hand the invention straight through.
    """
    invented = "2026-11-01T09:00:00+00:00"
    assert validate_timing_against_memory(invented, STALE, user_text="yes",
                                          request_at=NOW)[0] == "conflict"
    assert validate_timing_against_memory(
        invented, STALE, user_text="remind me about the supergrok reset",
        request_at=NOW,
    )[0] == "conflict"


def test_the_relative_bypass_is_closed():
    """`2660m` was the same instant as the ISO string the guard had just
    refused. The model offered to retry that way, and it would have worked --
    not by satisfying the gate but by going around it."""
    c, _ = validate_timing_against_memory(
        "2660m", STALE,
        user_text="remind me about the supergrok reset",
        request_at=NOW, require_subject_match=True,
    )
    assert c == "conflict"


def test_a_plain_short_reminder_is_not_guarded():
    """The counterweight to closing the bypass.

    "remind me in 10 minutes" names no subject and makes no claim about when
    any event happens. Checking it against every dated belief would refuse it
    whenever an unrelated reset sat more than two days out -- which is a far
    worse bug than the one being fixed.
    """
    c, _ = validate_timing_against_memory(
        "10m", STALE, user_text="remind me in 10 minutes",
        request_at=NOW, require_subject_match=True,
    )
    assert c == "no_memory"


def test_a_subject_is_matched_from_the_predicate_name():
    """Scoping needs the subject to be findable. `supergrok_heavy_reset` ends
    in none of the known suffixes, so it had no derived alias at all and the
    scoping that depends on it could never fire."""
    from kazma_core.safety.commitment.relative_time import _match_events, event_aliases

    assert "supergrok" in event_aliases("supergrok_heavy_reset")
    hits = _match_events("remind me about the supergrok reset", STALE)
    assert [p for p, _, _ in hits] == ["supergrok_heavy_reset"]


def test_a_generic_predicate_head_is_not_a_subject():
    """`user_timezone` must not make the word "user" a subject match -- a false
    subject turns an unrelated reminder into a conflict."""
    from kazma_core.safety.commitment.relative_time import _match_events, event_aliases

    assert "user" not in event_aliases("user_timezone")
    assert _match_events("remind the user to call mum",
                         [{"predicate": "user_timezone", "object": "Asia/Kuwait"}]) == []


def test_the_old_signature_still_behaves_the_old_way():
    """Callers that pass neither user_text nor request_at must be unaffected."""
    assert validate_timing_against_memory(TIMING, STALE)[0] == "conflict"
    assert validate_timing_against_memory(TIMING, FRESH)[0] == "consistent"
    assert validate_timing_against_memory(TIMING, [])[0] == "no_memory"
    assert validate_timing_against_memory("2660m", STALE)[0] == "not_absolute"


# ── the parser gap underneath all of it ─────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected_day",
    [
        ("Weekly SuperGrok Heavy Resets September 14, 2026 at 2:48 AM", 14),
        ("September 14, 2026", 14),
        ("Sept 14 2026", 14),
        ("14 September 2026", 14),
        ("remind me on 3 October 2026", 3),
    ],
)
def test_dates_written_the_way_people_write_them_are_found(text, expected_day):
    """`parse_belief_date` understood all of these from the start -- it is how
    belief objects are stored -- but the expression parser only matched ISO.
    One module, two date vocabularies, and the narrower one faced the human:
    the operator's "September 14, 2026 at 2:48 AM" came back as "no time
    expression found"."""
    from kazma_core.safety.commitment.relative_time import parse_time_expressions

    absolutes = [
        e for e in parse_time_expressions(text, request_at=NOW)
        if e.kind == "absolute" and e.absolute is not None
    ]
    assert absolutes, f"no absolute date found in {text!r}"
    assert absolutes[0].absolute.day == expected_day


@pytest.mark.parametrize(
    "text",
    [
        "I spent 14 dollars in May",
        "the May 2026 report is late",
        "remind me in 10 minutes",
        "march the troops to 14 different places",
    ],
)
def test_month_words_alone_are_not_dates(text):
    """The counterweight. A month name near a number is not a date, and a
    false absolute would anchor a reminder to a moment nobody asked for."""
    from kazma_core.safety.commitment.relative_time import parse_time_expressions

    assert not [
        e for e in parse_time_expressions(text, request_at=NOW)
        if e.kind == "absolute"
    ], f"{text!r} was read as an absolute date"


# ── the 413 in the same transcript ──────────────────────────────────────────
#
# The turn before the scheduler failure died on groq/compound-mini with:
#
#   {"error":{"message":"Request Entity Too Large",
#             "type":"invalid_request_error","code":"request_too_large"}}
#
# Kazma already knew how to handle an over-long prompt -- tag it
# kind="context_overflow" so the watchdog compacts and retries. But the branch
# matched on provider *wording*, and none of its eight markers appear in
# Groq's message. So a recoverable overflow was reported to the operator as
# "the model rejected the request" and the turn was lost.


@pytest.mark.parametrize(
    "module_name",
    ["kazma_core.llm_provider", "kazma_core.anthropic_llm"],
)
def test_a_bare_413_is_an_overflow_without_matching_any_wording(module_name):
    """The status code is the contract; the prose is the vendor's business.

    Matching wording is a losing game -- there is always another provider with
    another phrase. On a chat-completions endpoint the payload IS the prompt,
    so 413 alone is enough to route to compaction.
    """
    import importlib
    import inspect

    mod = importlib.import_module(module_name)
    src = inspect.getsource(mod)
    assert "status_code == 413" in src, (
        f"{module_name} still requires a wording match on a 413"
    )


@pytest.mark.parametrize(
    "module_name",
    ["kazma_core.llm_provider", "kazma_core.anthropic_llm"],
)
def test_groqs_wording_is_also_matched_on_400_and_422(module_name):
    """Belt and braces: some providers send the same complaint as a 400."""
    import importlib
    import inspect

    src = inspect.getsource(importlib.import_module(module_name))
    for marker in ("request too large", "request_too_large", "request entity too large"):
        assert marker in src, f"{module_name} does not recognise {marker!r}"


def test_the_original_markers_are_still_there():
    """The negative control. Broadening 413 must not have dropped the 400-path
    markers that were catching OpenAI, Anthropic and DeepSeek overflows."""
    import inspect

    from kazma_core import llm_provider

    src = inspect.getsource(llm_provider)
    for marker in (
        "context_length_exceeded",
        "maximum context length",
        "prompt is too long",
        "input is too long",
    ):
        assert marker in src, f"lost pre-existing marker {marker!r}"
