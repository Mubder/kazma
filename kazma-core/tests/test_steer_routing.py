"""Tests that an /abort-ed task ("abandoned" status) is not auto-resumed.

Covers the three routing sites that had to learn the new TaskStatus.ABANDONED:
  - classify_turn_intent: a bare "ok"/"yes" after abort → normal (not continue)
  - should_stub_prior_tools: abandoned collapses prior tool chains
  - graph_builder respond-path guards (auto_continue suppression) — covered
    indirectly by the intent/routing functions above.

The defining user requirement: "/abort … model stop that task and completely
abandon it unless the user return and asked that model to do that task again."
"""

from __future__ import annotations

import pytest

from kazma_core.agent.state import TaskStatus
from kazma_core.agent.topic_drift import should_stub_prior_tools
from kazma_core.agent.turn_input import classify_turn_intent


# ── TaskStatus enum ──────────────────────────────────────────────────


def test_abandoned_status_value_exists():
    assert TaskStatus.ABANDONED == "abandoned"
    assert "abandoned" in [s.value for s in TaskStatus]


# ── classify_turn_intent ─────────────────────────────────────────────


@pytest.mark.parametrize("status", ["abandoned", "superseded", "completed"])
def test_bare_continuation_after_terminal_status_is_normal(status):
    """A casual 'ok' must NOT resume an abandoned/superseded/completed task."""
    intent = classify_turn_intent(
        "ok", messages=[], task_status=status, use_embedding_drift=False,
    )
    assert intent == "normal"


def test_bare_continuation_on_open_task_is_continue():
    """Sanity: 'ok' on an in_progress task still continues."""
    intent = classify_turn_intent(
        "ok", messages=[], task_status="in_progress", use_embedding_drift=False,
    )
    assert intent == "continue"


def test_explicit_proceed_after_abortion_still_continues():
    """An explicit 'proceed' is a re-ask — allowed to resume (the abort
    marker in history tells the model to confirm). This matches superseded."""
    intent = classify_turn_intent(
        "proceed", messages=[], task_status="abandoned", use_embedding_drift=False,
    )
    assert intent == "continue"


# ── should_stub_prior_tools ──────────────────────────────────────────


def test_abandoned_stubs_prior_tools_on_normal_mode():
    assert should_stub_prior_tools(intent_mode="normal", prev_task_status="abandoned") is True


def test_abandoned_does_not_stub_when_user_explicitly_continues():
    assert should_stub_prior_tools(intent_mode="continue", prev_task_status="abandoned") is False


def test_abandoned_stubs_like_superseded():
    assert (
        should_stub_prior_tools(intent_mode="normal", prev_task_status="abandoned")
        == should_stub_prior_tools(intent_mode="normal", prev_task_status="superseded")
    )
