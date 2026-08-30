"""Topic-shift focus soft-reset + priority-note coverage.

Guards the subject-persistence remediation:
- short pivots get LATEST USER MESSAGE PRIORITY (no len>=80 gate)
- explicit/heuristic shifts classify as ``shift``
- continuations still expand prior goals
"""

from __future__ import annotations

from kazma_core.agent.turn_input import (
    classify_turn_intent,
    is_explicit_topic_shift,
    is_short_continuation,
    latest_turn_priority_note,
    prior_substantive_user_texts,
)


def test_priority_note_always_has_base_and_shift_clause():
    base = latest_turn_priority_note()
    assert "LATEST USER MESSAGE PRIORITY" in base
    assert "SUPERSEDED" not in base

    shift = latest_turn_priority_note(topic_shift=True)
    assert "TOPIC SHIFT" in shift
    assert "SUPERSEDED" in shift
    assert "do not resume" in shift.lower()


def test_explicit_topic_shift_phrases():
    assert is_explicit_topic_shift("new topic: weather please") is True
    assert is_explicit_topic_shift("forget that, different question") is True
    assert is_explicit_topic_shift("موضوع ثاني وش الطقس") is True
    # Continuations are never shifts
    assert is_explicit_topic_shift("proceed") is False
    assert is_explicit_topic_shift("continue please") is False


def test_classify_continue_store_cleanup():
    assert classify_turn_intent("Proceed") == "continue"
    assert classify_turn_intent("try now") == "continue"
    store = "Now read this and add it to the ShipX memory:\n\nOverview\n" + ("x" * 700)
    assert classify_turn_intent(store) == "store"
    cleanup = (
        "Entities is still messy. Clean junk true/false and merge "
        "duplicate entities in the graph."
    )
    assert classify_turn_intent(cleanup) == "cleanup"


def test_classify_shift_after_memory_work():
    history = [
        {
            "role": "user",
            "content": (
                "Please clean up the memory graph entities for kazma and ShipX "
                "and merge junk true/false nodes under Mubder."
            ),
        },
        {"role": "assistant", "content": "I will list and merge entities."},
    ]
    # S2-1: regex/heuristic pivots are shift_EXPLICIT (legacy "shift" is
    # treated as explicit by consumers).
    assert classify_turn_intent("What's the weather?", messages=history) == "shift_explicit"
    assert classify_turn_intent("new topic — check my email", messages=history) == "shift_explicit"


def test_classify_normal_without_prior_task():
    # No multi-step prior → casual ask is normal (not shift)
    assert classify_turn_intent("What's the weather?", messages=[]) == "normal"
    assert classify_turn_intent("hello there") == "normal"


def test_bare_ok_after_completed_is_normal_not_continue():
    assert classify_turn_intent("ok", task_status="completed") == "normal"
    assert classify_turn_intent("yes", task_status="superseded") == "normal"
    # Hard continue still works after completed
    assert classify_turn_intent("proceed", task_status="completed") == "continue"
    assert is_short_continuation("proceed") is True


def test_prior_substantive_skips_continuation_phrases():
    msgs = [
        {"role": "user", "content": "Clean memory junk entities for kazma graph"},
        {"role": "user", "content": "Proceed"},
        {"role": "user", "content": "try now"},
    ]
    priors = prior_substantive_user_texts(msgs, exclude="ok", limit=3)
    assert len(priors) == 1
    assert "memory" in priors[0].lower()


def test_compaction_summary_mentions_superseded():
    from kazma_core.compaction import _SUMMARY_SYSTEM

    assert "SUPERSEDED" in _SUMMARY_SYSTEM
    assert "subject change" in _SUMMARY_SYSTEM.lower() or "topic shift" in _SUMMARY_SYSTEM.lower()


def test_task_status_in_initial_state():
    from kazma_core.agent.state import TaskStatus, initial_supervisor_state

    st = initial_supervisor_state(thread_id="t-shift-test")
    assert st["task_status"] == TaskStatus.IDLE
    assert st["intent_mode"] == "normal"
    assert st["task_goal_summary"] == ""
