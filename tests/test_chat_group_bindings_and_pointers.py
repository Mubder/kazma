"""Stage 3 review, L9, L10 and L11: a group's binding, a group's topics and
``/reset``'s pointer in a space.

Through the real gateway handler, Spaces enforced (the S3-06 follow-up's
world: Dana and Rami members of Garden, linked on each chat app):

* **L9** -- a group's binding holds only while the member who bound it is a
  member of the space: once Rami leaves Garden, the group he bound works in
  no space (``chat_apps._group_space``), and Dana's message there runs
  nowhere;
* **L11** -- a Telegram forum topic is one chat for the group: Dana and Rami
  in topic 5 of one group share it, and Dana's topic 5 in two groups bound
  to Garden are two chats (the topic hint named the member who wrote);
* **L10** -- ``/reset`` in a direct chat working in Garden clears that
  chat's own pointer (``active_thread.<key in Garden>``), never the
  sender's pointer of the owner's space.

Negative controls: the binding read without its binder, the thread found
by the member's topic hint, and ``/reset`` clearing the sender's pointer.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import chat_apps, people, scope
from kazma_core.spaces.scope import OWNER_SPACE_ID

from kazma_gateway.gateway import IncomingMessage
from tests.test_chat_turn_control_per_person import _said, world  # noqa: F401  (the fixture)


def _topic_message(w: Any, who: str, group: int, topic: int, text: str) -> IncomingMessage:
    """*who*'s message in forum *topic* of Telegram *group*, as the adapter
    makes it (``telegram_parse``: the hint names the member who wrote)."""
    uid = w.uids["telegram"][who]
    meta = {"chat_id": group, "user_id": int(uid), "chat_type": "supergroup", "username": who,
            "message_thread_id": topic, "thread_hint": f"gw-telegram-{uid}-topic-{topic}"}
    return IncomingMessage("telegram", f"telegram:{uid}", text, context_metadata=meta)


def _threads_of(w: Any, account: str) -> list[str]:
    return [t for b, t in w.graph.runs if b is not None and b.asker == account]


# ── L9: the binder must still be a member ───────────────────────────────


def test_a_group_bound_by_a_member_who_left_works_in_no_space(world) -> None:
    async def run() -> None:
        assert "Garden" in await _said(world, "telegram", "rami", "/space Garden")
        await _said(world, "telegram", "dana", "which beds need water?")
        assert _threads_of(world, world.who["dana"]), "the group works in Garden"
        world.graph.runs.clear()
        people.leave_space(world.garden["id"], by=world.who["rami"])
        said = await _said(world, "telegram", "dana", "and the roses?")
        assert not world.graph.runs, "the group Rami bound still works in Garden after he left"
        assert "works in no space" in said, said

    asyncio.run(run())


def test_negative_control_the_binding_without_its_binder(world, monkeypatch) -> None:
    def before(reg, binding):
        if not isinstance(binding, dict):
            return ""
        bound = str(binding.get("space") or "")
        space = (reg.get("spaces") or {}).get(bound) if bound else None
        return "" if not isinstance(space, dict) or space.get("personal_of") else bound

    monkeypatch.setattr(chat_apps, "_group_space", before)
    with pytest.raises(AssertionError, match="still works in Garden after he left"):
        test_a_group_bound_by_a_member_who_left_works_in_no_space(world)


# ── L11: a forum topic is the group's ───────────────────────────────────

G1, G2 = -1001400777, -1001400888


def test_a_topic_is_one_chat_for_its_group(world) -> None:
    async def run() -> None:
        for group in (G1, G2):
            await world.handler(_topic_message(world, "dana", group, 5, "/space Garden"))
        await world.handler(_topic_message(world, "dana", G1, 5, "plan the beds"))
        await world.handler(_topic_message(world, "rami", G1, 5, "and the roses"))
        await world.handler(_topic_message(world, "dana", G2, 5, "plan the shed"))
        (g1_dana,) = _threads_of(world, world.who["dana"])[:1]
        (g1_rami,) = _threads_of(world, world.who["rami"])
        g2_dana = _threads_of(world, world.who["dana"])[1]
        assert g1_dana == g1_rami, "two members of one topic were given two chats"
        assert g1_dana != g2_dana, "one topic number in two groups was one chat"

    asyncio.run(run())


def test_negative_control_the_thread_found_by_the_member_s_hint(world, monkeypatch) -> None:
    from dataclasses import replace

    from kazma_gateway.agent_handler import graph as graph_mod
    from kazma_gateway.agent_handler.store import _resolve_thread

    def before(msg, sender_key):
        if sender_key == msg.sender_id:
            return _resolve_thread(msg)
        found = _resolve_thread(replace(msg, sender_id=sender_key, context_metadata={
            **(msg.context_metadata or {}), "username": "", "display_name": ""}))
        suffix = "--in-" + sender_key.rsplit("--in-", 1)[-1]
        return found if found.endswith(suffix) else found + suffix

    monkeypatch.setattr(graph_mod, "_resolve_thread_in_place", before)
    with pytest.raises(AssertionError, match="two chats|was one chat"):
        test_a_topic_is_one_chat_for_its_group(world)


# ── L10: /reset clears the chat's own pointer ───────────────────────────


def _direct(w: Any, text: str) -> IncomingMessage:
    uid = w.uids["telegram"]["owner"]
    meta = {"chat_id": int(uid), "user_id": int(uid), "chat_type": "private", "username": "owner"}
    return IncomingMessage("telegram", f"telegram:{uid}", text, context_metadata=meta)


def test_reset_clears_the_chat_s_own_pointer_in_its_space(world) -> None:
    store = get_config_store()
    sender = f"telegram:{world.uids['telegram']['owner']}"
    in_garden = f"{sender}--in-{world.garden['id']}"

    async def run() -> None:
        await world.handler(_direct(world, "hello"))
        assert world.graph.runs[-1][0].space_id == OWNER_SPACE_ID
        await world.handler(_direct(world, "/space Garden"))
        await world.handler(_direct(world, "which beds need water?"))
        assert world.graph.runs[-1][0].space_id == world.garden["id"]
        assert store.get(f"active_thread.{sender}") and store.get(f"active_thread.{in_garden}")
        await world.handler(_direct(world, "/reset"))

    asyncio.run(run())
    assert not store.get(f"active_thread.{in_garden}"), "/reset left the Garden chat's pointer"
    assert store.get(f"active_thread.{sender}"), "/reset cleared the owner's space's pointer"


def test_negative_control_reset_clears_the_sender_s_pointer(world) -> None:
    """Planted: /reset's pointer step of before, keyed by the platform
    sender (the step lives inside the handler, so it is replayed on the same
    state): the Garden chat's pointer stays and the owner's space's goes."""
    store = get_config_store()
    sender = f"telegram:{world.uids['telegram']['owner']}"
    in_garden = f"{sender}--in-{world.garden['id']}"

    async def run() -> None:
        await world.handler(_direct(world, "hello"))
        await world.handler(_direct(world, "/space Garden"))
        await world.handler(_direct(world, "which beds need water?"))
        # The old step 2: the pointer named by the platform sender.
        store.delete(f"active_thread.{sender}")

    asyncio.run(run())
    with pytest.raises(AssertionError, match="cleared the owner's space's pointer|left the Garden"):
        assert not store.get(f"active_thread.{in_garden}"), "/reset left the Garden chat's pointer"
        assert store.get(f"active_thread.{sender}"), "/reset cleared the owner's space's pointer"
    assert scope.spaces_enforced()
