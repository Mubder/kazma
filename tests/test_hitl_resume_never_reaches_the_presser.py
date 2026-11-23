"""Stage 3 review, M2: an approval answered from another chat never sends
the asker's work to the presser.

Dana's turn ran on the web (no chat-app route) and paused on a card; Rami,
her approver (N4), answers it with ``/hitl approve`` from his own Telegram.
Before the fix the resume fell back to the presser's chat and the turn's
answer -- "DANA-SECRET" -- went to Rami's Telegram, and the resumed turn ran
with Rami's chat as its delivery target, so a ``send_file`` or
``send_message`` of Dana's (or a swarm task's ``source_delivery_target``)
would go there too.

Now, once Spaces is enforced:

* the resumed turn runs as Dana with no delivery target of the presser's
  (``hitl_answerers.as_the_asker`` clears it: the tool worker takes the
  turn's own ``_gateway`` block);
* the turn's answer goes to the asker's own chat-app route when it has one
  and it is hers (``chat_apps.chat_is_the_askers``, which now also asks that
  a direct chat is her own address), else nowhere: the web keeps it;
* the presser is told only that it was answered.

Through the real gateway handler (the S3-04 world). Negative controls: the
destination of before (the presser's chat when the asker has no route), and
``as_the_asker`` leaving the presser's delivery target bound.
"""

from __future__ import annotations

from typing import Any

import pytest
from kazma_core.spaces import scope, threads

from tests.test_chat_approvals_asker import (  # noqa: F401  (the fixture)
    IID,
    _say,
    _with_an_approver,
    world,
)


def _dana_s_web_card(w: Any) -> str:
    """Dana's web turn, paused on a card of hers in Garden."""
    from uuid import uuid4

    from kazma_core.spaces.approvals import card_fields

    thread = f"web-dana-plan-{uuid4().hex[:8]}"
    threads.record_thread(thread, w.garden["id"], w.who["dana"])
    w.graph.values[thread] = {
        "messages": [{"role": "user", "content": "write the plan"}],
        "_gateway": {"platform": "web", "delivery_target": ""},
    }
    w.graph.paused[thread] = {
        "type": "hitl_approval", "tool": "file_write", "args": {"path": "plan.md", "content": "beds"},
        **card_fields(w.garden["id"], w.who["dana"], ["file_write"]),
    }
    return thread


def _answer(world, monkeypatch):
    """Rami answers Dana's web card from his own Telegram; what the resume
    saw as its delivery target, and what was sent where."""
    from kazma_core.tools import send_message
    from kazma_gateway.agent_handler import hitl

    seen: list[Any] = []

    async def run_resume(graph_, cmd, config, thread_id):
        seen.append((scope.current_scope(), send_message.get_current_delivery_target()))
        graph_.paused.pop(thread_id, None)
        return {"messages": [{"role": "user", "content": "write the plan"},
                             {"role": "assistant", "content": "DANA-SECRET answer"}],
                hitl._SAID_BEFORE: [], "_gateway": {"platform": "web", "delivery_target": ""}}, None

    monkeypatch.setattr(hitl, "_run_resume", run_resume)
    _with_an_approver(world)
    thread = _dana_s_web_card(world)
    _say(world, "rami", "/space Garden")
    _say(world, "rami", f"/hitl approve {thread}~{IID}")
    return world, seen


@pytest.fixture
def answered(world, monkeypatch):
    return _answer(world, monkeypatch)


def test_the_asker_s_answer_never_reaches_the_presser(answered) -> None:
    w, seen = answered
    assert seen, "the approver's answer resumed the turn"
    to_rami = [text for t, text, _ in w.sent if t == f"telegram:{w.uids['rami']}"]
    assert not any("DANA-SECRET" in text for text in to_rami), "Dana's answer reached the presser"
    assert any("answered" in text.lower() or "rest goes" in text.lower() for text in to_rami), to_rami


def test_the_resume_runs_with_none_of_the_presser_s_chat(answered) -> None:
    w, seen = answered
    (bound, target), = seen
    assert bound == scope.Scope(w.garden["id"], w.who["dana"]), "the turn is Dana's"
    assert target != f"telegram:{w.uids['rami']}", "the resume kept the presser's chat as its target"


def test_a_chat_that_is_not_the_asker_s_own_is_not_hers(world) -> None:
    from kazma_core.spaces import chat_apps

    thread = _dana_s_web_card(world)
    assert not chat_apps.chat_is_the_askers(
        world.garden["id"], world.who["dana"], "telegram", f"telegram:{world.uids['rami']}", thread), (
        "another person's chat passed as the asker's")
    assert chat_apps.chat_is_the_askers(
        world.garden["id"], world.who["dana"], "telegram", f"telegram:{world.uids['dana']}", thread)


def test_negative_control_the_destination_of_before(world, monkeypatch) -> None:
    """Planted: the destination of before -- the presser's chat when the
    asker's turn has no chat-app route."""
    from kazma_gateway.agent_handler import hitl

    async def before(manager, msg, ctx, result_state, **_kw):
        from kazma_gateway.agent_handler.hitl import _build_target_id

        return msg.platform, _build_target_id(msg.platform, ctx), ctx

    monkeypatch.setattr(hitl, "_resume_destination", before)
    with pytest.raises(AssertionError, match="reached the presser"):
        test_the_asker_s_answer_never_reaches_the_presser(_answer(world, monkeypatch))


def test_negative_control_the_presser_s_target_kept(world, monkeypatch) -> None:
    """Planted: ``as_the_asker`` that binds the asker and keeps the
    presser's delivery target."""
    from contextlib import contextmanager

    from kazma_ui import hitl_answerers

    @contextmanager
    def keeps(whose):
        with scope.bind_scope(whose.space_id, whose.asker):
            yield

    monkeypatch.setattr(hitl_answerers, "as_the_asker", keeps)
    with pytest.raises(AssertionError, match="kept the presser's chat"):
        test_the_resume_runs_with_none_of_the_presser_s_chat(_answer(world, monkeypatch))
