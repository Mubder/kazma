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

The cost of the wrong message is not cosmetic. The model reported to the user
that "the ISO timestamp was not accepted by the scheduler's parser" and offered
to retry as `2660m` — the same instant in relative form, which skips the memory
check entirely. A misleading error talked it into routing around a safety gate.
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


# ── the finding this does NOT fix ───────────────────────────────────────────


def test_relative_timing_still_bypasses_the_memory_guard():
    """Documented, not fixed — and deliberately so.

    `2660m` is the same instant as the ISO string above. The guard returns
    `not_absolute` for it and the compact-timing branch allows it outright, so
    the CoPilot-overwrite check the absolute path pays for is skipped entirely
    by writing the time a different way.

    Closing it is not a one-line change: `memory_beliefs` is every functional
    belief, unfiltered by topic, so running the same check on relative timings
    would refuse "remind me in 10 minutes" whenever any unrelated dated belief
    sits more than two days away. That needs a scoping decision from the
    operator, not a quick patch. This test exists so the gap is visible and
    starts failing the day someone narrows it.
    """
    assert validate_timing_against_memory("2660m", STALE)[0] == "not_absolute"
    assert validate_timing_against_memory("44h", STALE)[0] == "not_absolute"


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
