"""A member never deletes, renames, archives or pins another member's chat.

The review of 3da533662: in a shared space ``DELETE /api/chat/sessions/{id}``
asked only that the chat was the space's (``_not_this_spaces_chat``); the
per-person rule ``/reset``, ``/replay clear``, restore and fork ask was
missing. Member A got 403 on B's steps and then deleted B's whole chat, its
steps and its thread record. The routes that change a whole chat now ask
``turn_control_refusal(thread, "reset")``: the chat's opener or an owner of
the space (delete also never while another member's turn runs or waits).
403 ``not_your_chat``; 503 with ``Retry-After`` when who it is cannot be
read.

Every web route that changes a whole chat has a case: delete, rename,
archive, unarchive, pin, unpin (the S1-08 matrix world: the real chat
routers behind the real tenant middleware), and the memory route that keeps
a chat out of memory and forgets its past (the S1-09 world; it asked S1-11's
rule already), and the Dashboard's delete and "Clear saved steps" (every
member's chats of the space: an owner's). A shared space, Garden: B its
owner, A a member.

Negative control for each: today's space-only check.
"""

from __future__ import annotations

from typing import Any

import pytest
from kazma_core.spaces import identity, threads

from tests._chat_space_cases import World
from tests.test_time_travel_restore_per_person import GARDEN, _chat_in_garden, garden  # noqa: F401

CHANGES = (
    ("DELETE", "{s}", None),
    ("PATCH", "{s}", {"title": "a new title"}),
    ("POST", "{s}/archive", None),
    ("POST", "{s}/unarchive", None),
    ("POST", "{s}/pin", None),
    ("POST", "{s}/unpin", None),
)


def _change(w: World, who: str, method: str, tail: str, body: Any, session_id: str) -> Any:
    path = "/api/chat/sessions/" + tail.format(s=session_id)
    kwargs = {"json": body} if body is not None else {}
    return w.call(who, method, path, space=GARDEN, **kwargs)


def _stored(w: World, chat) -> Any:
    from kazma_core.spaces.scope import bind_scope
    from kazma_ui.session_manager import get_session_manager

    with bind_scope(GARDEN, w.account("b")):
        return get_session_manager().get(chat.session_id)


@pytest.mark.parametrize("method,tail,body", CHANGES, ids=[f"{m} {t}" for m, t, _b in CHANGES])
def test_a_member_never_changes_another_member_s_whole_chat(garden, method, tail, body) -> None:
    theirs = _chat_in_garden(garden, "b", f"beds-{method}-{tail}")
    garden.step(theirs)
    answer = _change(garden, "a", method, tail, body, theirs.session_id)
    assert answer.status_code == 403 and answer.json().get("error") == "not_your_chat", (
        f"a member could {method} another member's chat: {answer.status_code} {answer.text}")
    kept = _stored(garden, theirs)
    assert kept is not None and kept.title == f"title {theirs.mark}", "B's chat was changed"
    assert not kept.archived and not getattr(kept, "pinned", False)
    assert garden.steps(theirs) == 1 and threads.record_of(theirs.thread_id) is not None


def test_its_opener_deletes_it(garden) -> None:
    theirs = _chat_in_garden(garden, "b", "roses")
    garden.step(theirs)
    answer = _change(garden, "b", "DELETE", "{s}", None, theirs.session_id)
    assert answer.status_code == 200 and answer.json().get("status") == "ok", answer.text
    assert _stored(garden, theirs) is None


def test_an_owner_of_the_space_renames_and_archives_a_member_s_chat(garden) -> None:
    mine = _chat_in_garden(garden, "a", "shed")
    renamed = _change(garden, "b", "PATCH", "{s}", {"title": "the shed"}, mine.session_id)
    assert renamed.status_code == 200 and renamed.json().get("status") == "ok", renamed.text
    archived = _change(garden, "b", "POST", "{s}/archive", None, mine.session_id)
    assert archived.status_code == 200, archived.text


def test_whose_chat_cannot_be_read_is_a_503_with_retry_after(garden, monkeypatch) -> None:
    theirs = _chat_in_garden(garden, "b", "pond")

    def away(*_a, **_k):
        raise identity.IdentityUnavailable("the settings store is away")

    monkeypatch.setattr(identity, "owns_space", away)
    answer = _change(garden, "a", "PATCH", "{s}", {"title": "x"}, theirs.session_id)
    assert answer.status_code == 503 and answer.headers.get("retry-after"), (answer.status_code, answer.text)


@pytest.mark.parametrize("method,tail,body", CHANGES, ids=[f"{m} {t}" for m, t, _b in CHANGES])
def test_negative_control_today_s_space_only_check(garden, monkeypatch, method, tail, body) -> None:
    """Planted: today's routes, the space alone. A changes B's chat: the
    check above fails on its own assertion."""
    from kazma_ui import sse_chat

    async def no_turn_rule(*_a, **_k):
        return None

    monkeypatch.setattr(sse_chat, "_whole_chat_refusal_sync", lambda session_id: None)
    monkeypatch.setattr(sse_chat, "_turn_control", no_turn_rule)
    with pytest.raises(AssertionError, match="a member could"):
        test_a_member_never_changes_another_member_s_whole_chat(garden, method, tail, body)


# ── the memory route: keep a chat out, forget its past ───────────────────


@pytest.fixture
def memory():
    from tests._memory_space_cases import SPACE_A, memory_world
    from tests._space_matrix import Matrix

    with Matrix() as matrix:
        identity.create_space("Alpha", created_by=matrix.account_a, space_id=SPACE_A)
        identity.add_member(SPACE_A, matrix.account_b)
        threads.record_thread("chat-of-a", SPACE_A, matrix.account_a)
        with memory_world(matrix, who="b", space=SPACE_A) as w:
            yield w


def _keep_out(w) -> Any:
    return w.client.request("PUT", "/api/memory/v2/chats/chat-of-a/memory",
                            json={"remember": False, "forget_past": True})


def test_a_member_never_keeps_another_member_s_chat_out_of_memory(memory) -> None:
    from kazma_core.memory.speakers import NOT_YOURS

    answer = _keep_out(memory)
    assert answer.json().get("error") == NOT_YOURS, (
        f"a member kept another member's chat out of memory: {answer.text}")


def test_negative_control_the_memory_route_without_its_rule(memory, monkeypatch) -> None:
    from kazma_core.memory import speakers

    monkeypatch.setattr(speakers, "may_keep_chat_out", lambda *a, **k: True)
    with pytest.raises(AssertionError, match="kept another member's chat out of memory"):
        test_a_member_never_keeps_another_member_s_chat_out_of_memory(memory)


# ── the Dashboard's delete and clear-all ─────────────────────────────────


def _checkpoint_in_garden(w: World, who: str, chat) -> None:
    made = w.call(who, "POST", "/_matrix/checkpoint", space=GARDEN, json={"thread_id": chat.thread_id})
    assert made.status_code == 200, made.text


def _has_checkpoint(w: World, chat) -> bool:
    got = w.call("b", "GET", f"/_matrix/checkpoint/{chat.thread_id}", space=GARDEN)
    assert got.status_code == 200, got.text
    return bool(got.json()["found"])


def test_the_dashboard_never_deletes_another_member_s_chat(garden) -> None:
    theirs = _chat_in_garden(garden, "b", "dashboard-beds")
    _checkpoint_in_garden(garden, "b", theirs)
    answer = garden.call("a", "DELETE", f"/api/sessions/{theirs.thread_id}", space=GARDEN)
    assert answer.status_code == 403 and answer.json().get("error") == "not_your_chat", (
        f"the Dashboard deleted another member's chat: {answer.status_code} {answer.text}")
    assert _has_checkpoint(garden, theirs) and _stored(garden, theirs) is not None
    assert threads.record_of(theirs.thread_id) is not None


def test_the_dashboard_s_clear_all_is_an_owner_s(garden) -> None:
    theirs = _chat_in_garden(garden, "b", "dashboard-clear")
    _checkpoint_in_garden(garden, "b", theirs)
    refused = garden.call("a", "POST", "/api/sessions/clear-all", space=GARDEN)
    assert refused.status_code == 403 and refused.json().get("error") == "not_an_owner", (
        f"a member cleared every chat of the space: {refused.status_code} {refused.text}")
    assert _has_checkpoint(garden, theirs)
    done = garden.call("b", "POST", "/api/sessions/clear-all", space=GARDEN)
    assert done.status_code == 200, done.text
    assert not _has_checkpoint(garden, theirs)


def test_negative_control_the_dashboard_s_space_only_check(garden, monkeypatch) -> None:
    from kazma_ui import dashboard, sse_chat

    async def no_turn_rule(*_a, **_k):
        return None

    monkeypatch.setattr(sse_chat, "_turn_control", no_turn_rule)
    monkeypatch.setattr(dashboard, "_clear_all_refusal", lambda: None)
    with pytest.raises(AssertionError, match="the Dashboard deleted"):
        test_the_dashboard_never_deletes_another_member_s_chat(garden)
    with pytest.raises(AssertionError, match="cleared every chat"):
        test_the_dashboard_s_clear_all_is_an_owner_s(garden)
