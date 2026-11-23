"""Spaces S3-03: a chat-app message works in its chat's space.

Through the real gateway handler (``create_graph_handler``), with Spaces
enforced and each sender a linked account (S3-01, S3-02):

* a direct chat works in its person's personal space at first, as them; the
  owner's direct chat stays the owner's, in the owner's space, on the thread
  it always had;
* ``/space`` lists the person's spaces; ``/space 2`` moves the direct chat,
  and the next message runs in that space, on that space's own thread;
* a group works in the one shared space a member bound it to with
  ``/space <name>``: a member's message runs there as them; a sender who is
  not a member of it is ignored; an unbound group answers only ``/space``
  (and says so at most every ten minutes); a group never binds to a
  personal space, and a bound group is moved only by a member of its space.

Negative control: the sender's thread with no space in it -- the personal
and the shared space share one chat, and the message never runs in the
space the chat was moved to.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import chat_apps, chat_links, identity, people, scope, threads
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

from kazma_gateway.gateway import IncomingMessage

PASSWORD = "a-long-enough-password"
GROUP = -100777


class _Graph:
    """Answers at once; records the scope and thread each turn ran in."""

    def __init__(self) -> None:
        self.values: dict = {}
        self.runs: list[tuple[Any, str]] = []

    async def aget_state(self, config):
        return SimpleNamespace(values=self.values, next=(), tasks=())

    async def ainvoke(self, state, config):
        self.runs.append((scope.current_scope(), config["configurable"]["thread_id"]))
        self.values = {**state, "messages": [*state["messages"], {"role": "assistant", "content": "ok"}]}
        return self.values


@pytest.fixture
def world(monkeypatch):
    """Dana and Omar, linked on Telegram; a shared space Garden with Dana in
    it; the owner linked too."""
    from kazma_gateway.agent_handler.graph import create_graph_handler
    from kazma_gateway.agent_handler.store import _InMemoryStore

    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    monkeypatch.setattr(scope, "_enforced", True)
    store = get_config_store()
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    chat_apps._unbound_said.clear()
    dana = people.create_person("dana-space-binding", PASSWORD, by=OWNER_ACCOUNT_ID)
    omar = people.create_person("omar-space-binding", PASSWORD, by=OWNER_ACCOUNT_ID)
    garden = people.create_shared_space("Garden", by=OWNER_ACCOUNT_ID)
    people.add_member(garden["id"], dana["id"], by=OWNER_ACCOUNT_ID)
    uids = {"dana": f"5{uuid4().int % 10**8}", "omar": f"6{uuid4().int % 10**8}",
            "owner": f"7{uuid4().int % 10**8}"}

    def link(reg: dict) -> None:
        for who, account in (("dana", dana["id"]), ("omar", omar["id"]), ("owner", OWNER_ACCOUNT_ID)):
            reg["links"].append({"platform": "telegram", "user_id": uids[who], "account": account})

    identity.update_registry(link)
    chat_links.refresh_links()
    graph = _Graph()
    sent: list[str] = []

    async def send(out):
        sent.append(out.text)
        return True

    manager = SimpleNamespace(send=AsyncMock(side_effect=send), adapters=[])
    handler = create_graph_handler(graph=graph, manager=manager, store=_InMemoryStore())
    made: list[str] = []
    yield SimpleNamespace(dana=dana, omar=omar, garden=garden, uids=uids, graph=graph, sent=sent,
                          handler=handler, made=made)
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    for key in list((store.get_category("session") or {}).keys()):
        if key.startswith("active_thread.telegram:"):
            store.delete(key)
    chat_links._forget_links_for_tests()
    from kazma_core.security.vault import reset_posture_cache

    reset_posture_cache()


def _say(w: Any, who: str, text: str, *, group: bool = False) -> None:
    import asyncio

    uid = w.uids[who]
    meta = {"chat_id": GROUP if group else int(uid), "user_id": int(uid),
            "chat_type": "supergroup" if group else "private", "username": who}
    asyncio.run(w.handler(IncomingMessage("telegram", f"telegram:{uid}", text, context_metadata=meta)))


def _personal(person: dict) -> str:
    return str(person["personal_space"])


# ── direct chats ────────────────────────────────────────────────────────


def test_a_direct_chat_works_in_its_person_s_personal_space(world) -> None:
    _say(world, "dana", "hello")
    (ran, thread), = world.graph.runs
    assert ran == scope.Scope(_personal(world.dana), world.dana["id"])
    assert thread.endswith(f"--in-{_personal(world.dana)}"), thread
    assert threads.record_of(thread).space_id == _personal(world.dana)


def test_the_owner_s_direct_chat_stays_where_it_always_was(world) -> None:
    _say(world, "owner", "hello")
    (ran, thread), = world.graph.runs
    assert ran == scope.Scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)
    assert thread == f"gw-telegram-{world.uids['owner']}", "the owner's thread is the one of before"


def _moved_to_garden(world) -> tuple[str, str]:
    """Dana's chat in her personal space, then moved to Garden: the
    threads the two messages ran on."""
    _say(world, "dana", "first, in my own space")
    _say(world, "dana", "/space")
    listed = world.sent[-1]
    assert "1." in listed and "Garden" in listed and "2." in listed, listed
    _say(world, "dana", "/space 2")
    assert "Garden" in world.sent[-1]
    _say(world, "dana", "now in the garden")
    first, second = world.graph.runs[0], world.graph.runs[-1]
    assert second[0] == scope.Scope(world.garden["id"], world.dana["id"]), (
        "after /space 2 the message runs in the bound space")
    return first[1], second[1]


def test_space_moves_a_direct_chat_to_another_of_its_person_s_spaces(world) -> None:
    personal, shared = _moved_to_garden(world)
    assert personal != shared, "each space keeps its own chat"
    assert threads.record_of(shared).space_id == world.garden["id"]
    _say(world, "dana", "/space 1")
    _say(world, "dana", "back home")
    assert world.graph.runs[-1] == (scope.Scope(_personal(world.dana), world.dana["id"]), personal)


def test_space_names_only_the_person_s_own_spaces(world) -> None:
    _say(world, "omar", "/space Garden")
    assert "not a member" in world.sent[-1]
    _say(world, "omar", "after")
    assert world.graph.runs[-1][0].space_id == _personal(world.omar)


def test_space_answers_in_arabic(world) -> None:
    _say(world, "dana", "/space الحديقة")
    assert "لست عضوًا" in world.sent[-1]


# ── groups ──────────────────────────────────────────────────────────────


def test_an_unbound_group_answers_only_space_and_says_so_once(world) -> None:
    _say(world, "dana", "hello group", group=True)
    _say(world, "dana", "anyone?", group=True)
    assert world.graph.runs == []
    assert len(world.sent) == 1 and "/space" in world.sent[0]


def test_a_group_bound_to_a_shared_space_answers_its_members_only(world) -> None:
    _say(world, "dana", "/space Garden", group=True)
    assert "Garden" in world.sent[-1]
    _say(world, "dana", "plan the beds", group=True)
    (ran, thread), = world.graph.runs
    assert ran == scope.Scope(world.garden["id"], world.dana["id"])
    assert thread == f"gw-telegram-grp{GROUP}--in-{world.garden['id']}"
    said = len(world.sent)
    _say(world, "omar", "let me in", group=True)
    assert len(world.graph.runs) == 1, "a sender who is not a member is ignored in the group"
    assert len(world.sent) == said, "and told nothing there"


def test_a_group_never_binds_to_a_personal_space(world) -> None:
    _say(world, "dana", "/space 1", group=True)
    assert "shared space only" in world.sent[-1]
    assert chat_apps._bindings()["group"] == {}


def test_a_bound_group_is_moved_only_by_a_member_of_its_space(world) -> None:
    _say(world, "dana", "/space Garden", group=True)
    _say(world, "omar", "/space 1", group=True)
    assert "Only a member" in world.sent[-1]
    assert chat_apps._bindings()["group"][f"telegram:{GROUP}"]["space"] == world.garden["id"]
    _say(world, "dana", "/space none", group=True)
    assert chat_apps._bindings()["group"] == {}


def test_a_member_who_left_is_no_longer_answered_in_the_group(world) -> None:
    _say(world, "dana", "/space Garden", group=True)
    people.remove_member(world.garden["id"], world.dana["id"], by=OWNER_ACCOUNT_ID)
    _say(world, "dana", "still here?", group=True)
    assert world.graph.runs == []


def test_before_spaces_nothing_changes(world, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    assert not chat_apps.is_space_command("/spaces")
    _say(world, "dana", "hello", group=True)
    assert len(world.graph.runs) == 1 and world.graph.runs[0][1] == f"gw-telegram-{world.uids['dana']}"


# ── negative control ────────────────────────────────────────────────────


def test_negative_control_the_sender_s_thread_with_no_space(world, monkeypatch) -> None:
    """Planted: the sender key without its space -- one chat for every space.
    After /space 2 the message is refused as a chat of another space and
    never runs in Garden: the check above fails on its own assertion."""
    monkeypatch.setattr(chat_apps, "_key_for", lambda sender_id, space_id: sender_id)
    with pytest.raises(AssertionError, match="runs in the bound space"):
        _moved_to_garden(world)
