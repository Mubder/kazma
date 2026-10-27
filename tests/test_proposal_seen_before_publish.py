"""A draft is never published in the turn that wrote it.

Live 2026-10-16 12:25 UTC: asked to "check the metrics", the model saved a new
draft (12:25:12) and asked to post it at 12:25:14, in the same turn; the
owner approved the card believing it was part of the reading, and the post
went out. ``save_proposal`` now stamps a draft with the running turn (thread +
the turn's ``created_at``) and the outbound resolver refuses a draft whose
stamp is the running turn: the model shows it, and a later message asks to
post it. End to end on the real graph:
``tests/e2e/test_always_ask_one_time_approval.py``.
"""

from __future__ import annotations

import inspect

import pytest

from kazma_core.safety.commitment import proposals
from kazma_core.safety.commitment.proposals import (
    DRAFTED_THIS_TURN,
    bind_turn,
    current_turn,
    drafted_this_turn,
    reset_turn,
    _turn_of as turn_of,
)

TURN_1 = {"thread_id": "th-1", "created_at": "2026-10-16T12:24:59+00:00"}
TURN_2 = {"thread_id": "th-1", "created_at": "2026-10-16T12:31:02+00:00"}


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from kazma_core.agent import artifacts

    monkeypatch.setenv("KAZMA_ARTIFACTS_DB", str(tmp_path / "agent_artifacts.db"))
    artifacts.reset_artifact_store()
    yield artifacts.get_artifact_store()
    artifacts.reset_artifact_store()


def _post(ref: str):
    from kazma_core.safety.commitment.authorize import _resolve_proposal_backed_post

    return _resolve_proposal_backed_post(
        None, "x_post", {"text": "whatever the model holds", "proposal_id": ref},
        audit=None, thread_id="th-1", tenant_id="default", cfg={}, source="test",
    )


def _save(store, turn_state) -> str:
    token = bind_turn(turn_state)
    try:
        payload = store.save_proposal("default", "th-1", "tweets", ["Kazma metrics"],
                                      saved_turn=current_turn())
    finally:
        reset_turn(token)
    return payload["items"][0]["id"]


def test_a_draft_written_this_turn_is_refused_with_its_text(store):
    ref = _save(store, TURN_1)
    token = bind_turn(TURN_1)
    try:
        decision = _post(ref)
    finally:
        reset_turn(token)
    assert decision.decision == "deny", decision
    assert DRAFTED_THIS_TURN in decision.reason
    assert "Kazma metrics" in decision.reason  # the model can show the draft


def test_the_same_draft_is_posted_after_the_user_answers(store):
    ref = _save(store, TURN_1)
    token = bind_turn(TURN_2)
    try:
        decision = _post(ref)
    finally:
        reset_turn(token)
    assert decision.decision == "allow", decision
    assert decision.rewritten_args["text"] == "Kazma metrics"  # stored text wins


def test_another_chat_is_another_turn(store):
    ref = _save(store, TURN_1)
    token = bind_turn({"thread_id": "th-2", "created_at": TURN_1["created_at"]})
    try:
        assert _post(ref).decision == "allow"
    finally:
        reset_turn(token)


def test_a_draft_saved_by_a_page_or_an_older_build_is_not_stamped(store):
    payload = store.save_proposal("default", "th-1", "tweets", ["From X Studio"])
    token = bind_turn(TURN_1)
    try:
        assert _post(payload["items"][0]["id"]).decision == "allow"
    finally:
        reset_turn(token)


def test_a_stamped_draft_with_no_running_turn_is_refused(store):
    ref = _save(store, TURN_1)
    assert current_turn() == ""
    assert _post(ref).decision == "deny"


def test_a_state_without_turn_scope_names_no_turn():
    assert turn_of({"thread_id": "th-1"}) == ""
    assert turn_of({"created_at": "x"}) == ""
    assert turn_of(None) == ""
    assert turn_of(TURN_1) == "th-1|2026-10-16T12:24:59+00:00"


def test_the_old_resolver_posts_a_draft_written_this_turn(store, monkeypatch):
    """Negative control: without the check, the live incident's call passes."""
    ref = _save(store, TURN_1)
    monkeypatch.setattr(proposals, "drafted_this_turn", lambda info: False)
    token = bind_turn(TURN_1)
    try:
        assert _post(ref).decision == "allow"
    finally:
        reset_turn(token)
    assert drafted_this_turn({"saved_turn": "th-1|x"}) is True  # no running turn


def test_the_save_tool_stamps_the_running_turn_and_says_so():
    """save_proposal reads the running turn and tells the model the user has
    not seen the drafts; the tool worker binds the turn around its tools."""
    from kazma_core.agent import graph_tool_worker
    from kazma_core.agent.tool_builtins import external

    src = inspect.getsource(external)
    assert "saved_turn=turn" in src and "current_turn as _turn" in src
    assert "never published in" in src
    worker = inspect.getsource(graph_tool_worker.tool_worker_node)
    assert "bind_turn(state)" in worker and "reset_turn(_proposal_turn_token)" in worker
    # Bound before the commitment gate runs.
    # (The gate runs in a thread since AUD-111; to_thread copies the context
    # that holds the bound turn.)
    assert worker.index("bind_turn(state)") < worker.index("_commitment_resolve_gate, state")


def test_every_publish_tool_goes_through_the_check():
    from kazma_core.safety.commitment import authorize

    assert authorize._PROPOSAL_REQUIRED_TOOLS == proposals.PROPOSAL_TOOLS
    body = inspect.getsource(authorize._resolve_proposal_backed_post)
    assert "drafted_this_turn(info)" in body
    assert body.index("drafted_this_turn(info)") < body.index('rewritten["text"] = stored_text')
