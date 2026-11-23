"""The web's Time Travel restore and fork ask who the person is.

``thread_rewind.rewind_thread`` and ``fork_thread`` -- the one service the
Time Travel page, the terminal app and the chat apps call -- asked only that
the chat was the space's (``_in_this_space``, S1-08). In a shared space any
member restored another member's chat, taking back their later turns, or
forked it into a chat of their own. Once Spaces is enforced they now ask the
rule a chat app's ``/replay`` asks (``hitl_answerers.turn_control_refusal``,
action ``rewind``; S2-05): the chat's opener or an owner of the space
(``thread_rewind._theirs_to_change``, 403 ``not_your_chat``, worded on the
page in English and Arabic).

Through the real ``/api/replay/restore`` and ``/api/replay/fork`` routes
behind the real tenant middleware (the S1-08 matrix world), Spaces enforced:
a shared space, Garden, B its owner and A a member.

* B opened a chat: A's restore and fork are refused and nothing changes;
  B's own are taken.
* A opened a chat: B, the owner of the space, restores it.

Negative control: today's space-only check -- A restores B's chat.
"""

from __future__ import annotations

import secrets
from typing import Any

import pytest
from kazma_core.spaces import identity, threads

from tests import _tt_graph as tt
from tests._chat_space_cases import Chat, World, world
from tests._space_matrix import Matrix

GARDEN = "matrix-garden"


@pytest.fixture
def garden():
    with Matrix() as matrix:
        identity.create_space("Garden", created_by=matrix.account_b, space_id=GARDEN)
        identity.add_member(GARDEN, matrix.account_a)
        with world(matrix) as w:
            yield w


def _chat_in_garden(w: World, opener: str, mark: str) -> Chat:
    """A chat of Garden opened by *opener*, with one saved step."""
    from kazma_core.spaces.scope import bind_scope
    from kazma_ui.session_manager import get_session_manager

    made = Chat(session_id=f"s{secrets.token_hex(8)}", thread_id=f"t{secrets.token_hex(8)}", mark=mark)
    with bind_scope(GARDEN, w.account(opener)):
        store = get_session_manager()
        session = store.get_or_create(made.session_id, thread_id=made.thread_id)
        session.add_message("user", f"question {mark}")
        session.add_message("assistant", f"answer {mark}")
        session.title = f"title {mark}"
        store.put(session)
        threads.record_thread(made.thread_id, GARDEN, w.account(opener))
    return made


def _state(w: World, chat: Chat) -> str:
    return str(tt.run(w.graph.aget_state(tt.config(chat.thread_id))).values)


def _ask(w: World, who: str, action: str, chat: Chat, step: str) -> Any:
    return w.call(who, "POST", f"/api/replay/{action}", space=GARDEN,
                  json={"thread_id": chat.thread_id, "snapshot_id": step})


def test_a_member_never_restores_or_forks_another_member_s_chat(garden) -> None:
    theirs = _chat_in_garden(garden, "b", "beds")
    step = garden.step(theirs)
    for action in ("restore", "fork"):
        answer = _ask(garden, "a", action, theirs, step)
        assert answer.status_code == 403 and answer.json().get("error") == "not_your_chat", (
            f"a member could {action} another member's chat: {answer.status_code} {answer.text}")
    assert theirs.mark not in _state(garden, theirs), "B's chat was restored by A"


def test_its_opener_restores_and_forks_it(garden) -> None:
    theirs = _chat_in_garden(garden, "b", "roses")
    step = garden.step(theirs)
    assert _ask(garden, "b", "restore", theirs, step).status_code == 200
    assert theirs.mark in _state(garden, theirs)
    forked = _ask(garden, "b", "fork", theirs, step)
    assert forked.status_code == 200, forked.text


def test_an_owner_of_the_space_restores_a_member_s_chat(garden) -> None:
    mine = _chat_in_garden(garden, "a", "shed")
    step = garden.step(mine)
    answer = _ask(garden, "b", "restore", mine, step)
    assert answer.status_code == 200, answer.text
    assert mine.mark in _state(garden, mine)


def test_negative_control_today_s_space_only_check(garden, monkeypatch) -> None:
    """Planted: today's check, the space alone. A restores B's chat: the
    check above fails on its own assertion."""
    from kazma_ui import thread_rewind

    monkeypatch.setattr(thread_rewind, "_theirs_to_change", lambda thread_id: None)
    with pytest.raises(AssertionError, match="a member could restore another member's chat"):
        test_a_member_never_restores_or_forks_another_member_s_chat(garden)


# ── /replay clear: the same rule (the second review of the fixes) ────────


def test_a_member_never_deletes_another_member_s_steps(garden) -> None:
    from kazma_core.spaces.scope import bind_scope
    from kazma_gateway import slash_commands
    from kazma_ui.sse_chat._time_travel import time_travel_reply

    theirs = _chat_in_garden(garden, "b", "pond")
    garden.step(theirs)
    web = garden.call("a", "DELETE", f"/api/replay/threads/{theirs.thread_id}", space=GARDEN)
    assert web.status_code == 403 and web.json().get("error") == "not_your_chat", (
        f"a member deleted another member's steps on the web: {web.status_code} {web.text}")
    with bind_scope(GARDEN, garden.account("a")):
        said_web_chat = time_travel_reply("/replay clear", theirs.thread_id, garden.recorder)
        said_chat_app = slash_commands._replay_clear(
            garden.recorder, theirs.thread_id, {"platform": "telegram", "sender_id": "telegram:1"})
    assert "started this chat" in said_web_chat, said_web_chat
    assert "started this chat" in said_chat_app, said_chat_app
    assert garden.steps(theirs) == 1, "another member's steps were deleted"


def test_its_opener_deletes_its_steps(garden) -> None:
    theirs = _chat_in_garden(garden, "b", "path")
    garden.step(theirs)
    done = garden.call("b", "DELETE", f"/api/replay/threads/{theirs.thread_id}", space=GARDEN)
    assert done.status_code == 200, done.text
    assert garden.steps(theirs) == 0


def test_negative_control_today_s_clear(garden, monkeypatch) -> None:
    """Planted: today's clear, the space alone: A deletes B's steps."""
    from kazma_ui import thread_rewind

    monkeypatch.setattr(thread_rewind, "_theirs_to_change", lambda thread_id: None)
    with pytest.raises(AssertionError, match="deleted another member's steps on the web"):
        test_a_member_never_deletes_another_member_s_steps(garden)
