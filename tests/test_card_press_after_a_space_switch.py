"""Stage 3 review, L7: a ``/space`` switch never strands a card.

A card a person's turn paused on lives in the space the turn ran in. When
they then switch their direct chat to another space (``/space Garden``), a
press of that card arrived in the new space, whose checkpoint store reads
no such thread: the card was "stale" and could not be answered.

``hitl._handle_hitl_resume`` now takes the decision in the space the card
lives in when the presser is a member of it, as them (whose card it is is
still asked on the live card, S3-04). Someone who is not a member gets
nothing new: the card stays stale to them.

Through the real gateway handler, Spaces enforced (the S3-04 world).
Negative control: the resume of before, in the space the chat works in.
"""

from __future__ import annotations

from typing import Any

import pytest
from kazma_core.spaces import scope

from tests.test_chat_approvals_asker import IID, _say, world  # noqa: F401  (the fixture)


def _reads_only_its_space(w: Any) -> None:
    """The stand-in graph reads a thread only in its own space, as the real
    checkpoint store does once Spaces is enforced (S1-08: another space's
    thread is "no checkpoints")."""
    from types import SimpleNamespace

    from kazma_core.spaces import threads

    answer = w.graph.aget_state

    async def in_its_space(config):
        tid = config["configurable"]["thread_id"]
        bound = scope.current_scope()
        if bound is not None and bound.space_id != threads.thread_home(tid):
            return SimpleNamespace(values={}, next=(), tasks=())
        return await answer(config)

    w.graph.aget_state = in_its_space


def _card_in_her_personal_space(w: Any) -> str:
    """Dana's turn in her own direct chat (her personal space) pauses on a
    card; she then switches the chat to Garden. Returns the card's thread."""
    _reads_only_its_space(w)
    w.graph.runs.clear()
    _say(w, "dana", "write the plan for my balcony")
    (bound, thread), = w.graph.runs
    personal = bound.space_id
    assert personal != w.garden["id"] and bound.asker == w.who["dana"]
    _say(w, "dana", "/space Garden")
    return thread


def test_her_press_answers_the_card_she_left_in_her_own_space(world) -> None:
    thread = _card_in_her_personal_space(world)
    personal = world.graph.runs[0][0].space_id
    _say(world, "dana", f"/hitl approve {thread}~{IID}", press=True)
    assert world.resumes, "the card left in her own space could not be answered"
    (resumed_as, _cmd), = world.resumes
    assert resumed_as == scope.Scope(personal, world.who["dana"])


def test_someone_who_is_not_a_member_gets_nothing_new(world) -> None:
    thread = _card_in_her_personal_space(world)
    _say(world, "rami", "/space Garden")
    _say(world, "rami", f"/hitl approve {thread}~{IID}", press=True)
    assert world.resumes == [], "a non-member answered Dana's card"
    assert thread in world.graph.paused


def test_negative_control_the_resume_in_the_chat_s_space(world, monkeypatch) -> None:
    """Planted: the resume of before, in the space the chat works in now."""
    from kazma_gateway.agent_handler import hitl

    monkeypatch.setattr(hitl, "_card_space_for_presser", lambda thread: "")
    with pytest.raises(AssertionError, match="could not be answered"):
        test_her_press_answers_the_card_she_left_in_her_own_space(world)
