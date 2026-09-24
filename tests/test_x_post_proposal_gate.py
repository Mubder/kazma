"""A post the commitment resolver verified must reach the approval step.

Live 2026-09-24: four x_post calls were each logged "[commitment] allow
outbound x_post via proposal prop_003dd7eec788 (... stored text wins)" and
then refused in the same pass with "Proposal tool 'x_post' is managed by the
commitment engine and cannot be invoked directly". The proposal check after
the resolver blocked EVERY publish call; it had been dead code (ImportError)
until 2026-09-17 and blocked every chat post from then on. No x_post executed
in the live logs between 2026-09-17 and 2026-09-24.

These drive the REAL resolver against a REAL stored proposal. Only the
approval card is out of scope: that is the security HITL split, which runs
after this gate (x_post is danger tier and always asks).
"""

from __future__ import annotations

import pytest

from kazma_core.agent.graph_tool_worker import _commitment_resolve_gate

STORED = "The stored draft the user approved."


@pytest.fixture
def proposal(tmp_path, monkeypatch):
    from kazma_core.agent import artifacts

    monkeypatch.setenv("KAZMA_ARTIFACTS_DB", str(tmp_path / "agent_artifacts.db"))
    artifacts.reset_artifact_store()
    saved = artifacts.get_artifact_store().save_proposal(
        "default", "t-x", "tweets", ["some other draft", STORED]
    )
    yield saved["proposal_id"]
    artifacts.reset_artifact_store()


def _state():
    return {"thread_id": "t-x", "tenant_id": "default", "messages": []}


def _post(**args):
    return [{"id": "call-1", "name": "x_post", "arguments": args}]


def test_a_verified_post_goes_on_with_the_stored_text(proposal):
    pending, blocked = _commitment_resolve_gate(
        _state(),
        _post(text="whatever the model still holds", proposal_id=f"{proposal}:2"),
        allow_interrupt=False,
    )
    assert not blocked, blocked[0]["content"] if blocked else ""
    assert [tc["name"] for tc in pending] == ["x_post"]
    assert pending[0]["arguments"]["text"] == STORED, "stored text must win"


def test_with_the_commitment_layer_off_a_post_is_blocked(proposal, monkeypatch):
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    pending, blocked = _commitment_resolve_gate(
        _state(),
        _post(text="unverifiable text", proposal_id=f"{proposal}:2"),
        allow_interrupt=False,
    )
    assert pending == []
    assert len(blocked) == 1 and blocked[0]["outcome"] == "terminal"
    msg = blocked[0]["content"]
    assert "Not posted" in msg
    # It must not send the model looking for approval cards that do not exist.
    assert "Nothing is queued for approval" in msg


def test_a_post_without_a_proposal_id_is_still_denied(proposal):
    pending, blocked = _commitment_resolve_gate(
        _state(), _post(text="raw text"), allow_interrupt=False
    )
    assert pending == []
    assert len(blocked) == 1
    assert "requires a proposal_id" in blocked[0]["content"]


def test_an_unknown_proposal_id_is_still_denied(proposal):
    pending, blocked = _commitment_resolve_gate(
        _state(), _post(text="x", proposal_id="prop_000000000000:1"), allow_interrupt=False
    )
    assert pending == []
    assert "does not resolve" in blocked[0]["content"]


def test_other_tools_in_the_batch_are_unaffected(proposal):
    batch = _post(text="x", proposal_id=f"{proposal}:2") + [
        {"id": "call-2", "name": "file_read", "arguments": {"path": "README.md"}}
    ]
    pending, blocked = _commitment_resolve_gate(_state(), batch, allow_interrupt=False)
    assert not blocked
    assert sorted(tc["name"] for tc in pending) == ["file_read", "x_post"]
