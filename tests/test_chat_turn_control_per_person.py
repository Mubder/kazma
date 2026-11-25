"""Spaces S3-06 follow-up: in a chat app, a member never steers, stops or
clears another member's turn.

Through the real gateway handler (``create_graph_handler``), with Spaces
enforced: a group of each chat app that has groups -- a Telegram group, a
Discord server channel, a Slack channel -- bound to the shared space Garden
(S3-03), Dana and Rami members of it, the owner its owner. Rami opened the
chat; Dana's turn is running:

* each user row the gateway saves names the account that sent it
  (``reply_sink.carry_authors``): the row Dana's turn opens with is hers, so
  whose turn it is (``hitl_answerers.whose_turn``) reads Dana -- never the
  chat's opener;
* Rami's ``/steer`` and ``/steer!``, his ``/abort`` and ``/stop`` and his
  ``/reset`` are refused (the web chat's rule,
  ``hitl_answerers.turn_control_refusal``), told whose turn it is in his
  language; nothing is pushed, cancelled or cleared;
* Dana's own ``/steer`` is taken; the owner of the space stops it;
* WhatsApp has only direct chats: Dana's chat in Garden is hers alone, her
  own steer is taken, and another member's ``/abort`` in his own chat never
  reaches her turn;
* before Spaces nothing is read: anyone's steer is taken, as today.

Negative controls: the unrefused press -- the handler without the rule
takes Rami's steer -- and the rows without their authors, where whose turn
it is falls back to the chat's opener, Rami.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from kazma_core.agent import steer
from kazma_core.config_store import get_config_store
from kazma_core.spaces import chat_apps, chat_links, identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
from kazma_gateway.gateway import IncomingMessage

PASSWORD = "a-long-enough-password"
GROUP_PLATFORMS = ("telegram", "discord", "slack")


class _Graph:
    """Keeps each thread's state; Dana's turn waits until released."""

    def __init__(self) -> None:
        self.values: dict[str, dict] = {}
        self.hold: str = ""
        self.release: asyncio.Event | None = None
        self.runs: list[tuple[Any, str]] = []

    async def aget_state(self, config):
        tid = config["configurable"]["thread_id"]
        return SimpleNamespace(values=self.values.get(tid, {}), next=(), tasks=())

    async def aupdate_state(self, config, values, as_node=None):
        tid = config["configurable"]["thread_id"]
        self.values[tid] = {**self.values.get(tid, {}), **values}

    async def ainvoke(self, state, config):
        tid = config["configurable"]["thread_id"]
        bound = scope.current_scope()
        self.runs.append((bound, tid))
        if self.hold and bound is not None and bound.asker == self.hold and self.release is not None:
            await self.release.wait()
        self.values[tid] = {**state, "messages": [*state["messages"], {"role": "assistant", "content": "ok"}]}
        return self.values[tid]


def _user_id(platform: str, n: int) -> str:
    return {"slack": f"U{n:08d}", "whatsapp": f"9655{n % 10**7:07d}"}.get(platform, str(10**8 + n))


@pytest.fixture
def world(monkeypatch):
    from kazma_gateway.agent_handler.graph import create_graph_handler
    from kazma_gateway.agent_handler.store import _InMemoryStore

    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    monkeypatch.setattr(scope, "_enforced", True)
    store = get_config_store()
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    chat_apps._unbound_said.clear()
    dana = people.create_person(f"dana-{uuid4().hex[:6]}", PASSWORD, by=OWNER_ACCOUNT_ID)
    rami = people.create_person(f"rami-{uuid4().hex[:6]}", PASSWORD, by=OWNER_ACCOUNT_ID)
    garden = people.create_shared_space("Garden", by=OWNER_ACCOUNT_ID)
    for person in (dana, rami):
        people.add_member(garden["id"], person["id"], by=OWNER_ACCOUNT_ID)
    who = {"dana": dana["id"], "rami": rami["id"], "owner": OWNER_ACCOUNT_ID}
    base = uuid4().int % 10**6
    uids = {plat: {name: _user_id(plat, base * 10 + n) for n, name in enumerate(who)}
            for plat in (*GROUP_PLATFORMS, "whatsapp")}

    def link(reg: dict) -> None:
        for plat, people_ids in uids.items():
            for name, uid in people_ids.items():
                reg["links"].append({"platform": plat, "user_id": uid, "account": who[name]})

    identity.update_registry(link)
    chat_links.refresh_links()
    graph = _Graph()
    sent: list[tuple[str, str]] = []

    async def send(out):
        sent.append((out.target_id, out.text))
        return True

    manager = SimpleNamespace(send=AsyncMock(side_effect=send), adapters=[])
    handler = create_graph_handler(graph=graph, manager=manager, store=_InMemoryStore())
    group = f"{base}"
    yield SimpleNamespace(who=who, uids=uids, garden=garden, graph=graph, sent=sent, handler=handler,
                          group=group)
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    for key in list((store.get_category("session") or {}).keys()):
        if key.startswith("active_thread."):
            store.delete(key)
    chat_links._forget_links_for_tests()


def _message(w: Any, platform: str, who: str, text: str) -> IncomingMessage:
    """*who*'s message in the platform's group (WhatsApp: their own chat)."""
    uid = w.uids[platform][who]
    if platform == "telegram":
        meta = {"chat_id": -int("100" + w.group), "user_id": int(uid), "chat_type": "supergroup",
                "username": who}
        return IncomingMessage("telegram", f"telegram:{uid}", text, context_metadata=meta)
    if platform == "discord":
        channel = f"77{w.group}"
        meta = {"channel_id": channel, "guild_id": "4242", "user_id": uid, "username": who}
        return IncomingMessage("discord", f"discord:{uid}:{channel}", text, context_metadata=meta)
    if platform == "slack":
        meta = {"channel_id": f"C{w.group}", "user_id": uid, "username": who}
        return IncomingMessage("slack", f"slack:{uid}", text, context_metadata=meta)
    meta = {"chat_id": uid, "user_id": uid, "username": who, "chat_type": "private"}
    return IncomingMessage("whatsapp", f"whatsapp:{uid}", text, context_metadata=meta)


async def _said(w: Any, platform: str, who: str, text: str) -> str:
    before = len(w.sent)
    await w.handler(_message(w, platform, who, text))
    return " | ".join(t for _, t in w.sent[before:])


async def _until(check, *, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.02)


async def _dana_s_turn_running(w: Any, platform: str) -> tuple[str, asyncio.Task]:
    """Rami opens the group's chat; Dana's turn starts and waits. Returns its
    thread and the handler task running it."""
    from kazma_ui.active_turns import is_turn_running

    if platform != "whatsapp":
        await _said(w, platform, "dana", "/space Garden")
    else:
        for who in ("dana", "rami"):
            assert "Garden" in await _said(w, platform, who, "/space Garden")
        await _said(w, platform, "dana", "hello")
    await _said(w, platform, "rami", "hello, which beds need water?")
    w.graph.hold = w.who["dana"]
    w.graph.release = asyncio.Event()
    turn = asyncio.create_task(w.handler(_message(w, platform, "dana", "write the plan for the beds")))
    await _until(lambda: any(b is not None and b.asker == w.who["dana"] for b, _ in w.graph.runs))
    thread = next(t for b, t in w.graph.runs if b is not None and b.asker == w.who["dana"])
    await _until(lambda: is_turn_running(thread))
    return thread, turn


async def _end(w: Any, turn: asyncio.Task) -> None:
    w.graph.release.set()
    try:
        await asyncio.wait_for(turn, 10)
    except asyncio.CancelledError:
        pass


def _whose(w: Any, thread: str) -> str:
    from kazma_ui.hitl_answerers import whose_turn

    with scope.bind_scope(w.garden["id"], w.who["dana"]):
        return whose_turn(thread).asker


# ── the group chat apps ─────────────────────────────────────────────────


@pytest.mark.parametrize("platform", GROUP_PLATFORMS)
def test_another_member_s_steer_abort_and_reset_are_refused(world, platform) -> None:
    async def run() -> None:
        thread, turn = await _dana_s_turn_running(world, platform)
        try:
            assert _whose(world, thread) == world.who["dana"], "the row Dana's turn opens with is hers"
            said = await _said(world, platform, "rami", "/steer use roses")
            assert "can steer this turn" in said, said
            assert steer.drain_soft_steers(thread) == [], "Rami's steer was taken"
            said = await _said(world, platform, "rami", "/steer! stop and use roses")
            assert "can steer this turn" in said and steer.peek_hard_steer(thread) is None, said
            for command in ("/abort", "/stop"):
                said = await _said(world, platform, "rami", command)
                assert "only they or an owner of this space can stop it" in said, said
            said = await _said(world, platform, "rami", "/reset")
            assert "busy with" in said, said
            from kazma_ui.active_turns import is_turn_running

            assert is_turn_running(thread) and not turn.done(), "Dana's turn runs on"
            said = await _said(world, platform, "dana", "/steer use roses")
            assert "Steer noted" in said, said
            assert [s["text"] for s in steer.drain_soft_steers(thread)] == ["use roses"]
        finally:
            await _end(world, turn)

    asyncio.run(run())


def test_the_refusal_is_in_the_sender_s_language(world) -> None:
    async def run() -> None:
        thread, turn = await _dana_s_turn_running(world, "telegram")
        try:
            said = await _said(world, "telegram", "rami", "/steer استخدم الورد الجوري في الأحواض من فضلك")
            assert "لا يوجّه هذا الدور إلا" in said and "فهو دوره" in said, said
        finally:
            await _end(world, turn)

    asyncio.run(run())


def test_the_owner_of_the_space_stops_it(world) -> None:
    async def run() -> None:
        thread, turn = await _dana_s_turn_running(world, "telegram")
        try:
            said = await _said(world, "telegram", "owner", "/abort")
            assert "Task aborted" in said, said
            await _until(turn.done)
        finally:
            await _end(world, turn)

    asyncio.run(run())


# ── WhatsApp: direct chats only ─────────────────────────────────────────


def test_whatsapp_dana_s_chat_is_hers_alone(world) -> None:
    async def run() -> None:
        from kazma_ui.active_turns import is_turn_running

        thread, turn = await _dana_s_turn_running(world, "whatsapp")
        try:
            assert _whose(world, thread) == world.who["dana"]
            await _said(world, "whatsapp", "rami", "/abort")
            assert is_turn_running(thread) and not turn.done(), "another member's chat never reaches it"
            said = await _said(world, "whatsapp", "dana", "/steer use roses")
            assert "Steer noted" in said, said
            assert [s["text"] for s in steer.drain_soft_steers(thread)] == ["use roses"]
        finally:
            await _end(world, turn)

    asyncio.run(run())


# ── authors and before Spaces ───────────────────────────────────────────


def test_identical_messages_keep_their_own_authors() -> None:
    from kazma_gateway.agent_handler.graph import _merge_transcript
    from kazma_ui.reply_sink import carry_authors

    existing = [{"role": "user", "content": "go", "author": "dana"},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "go", "author": "rami"},
                {"role": "assistant", "content": "ok again"}]
    converted = [{"role": "user", "content": "go"}, {"role": "assistant", "content": "ok"},
                 {"role": "user", "content": "go"}, {"role": "assistant", "content": "ok again"},
                 {"role": "user", "content": "go"}]
    out = carry_authors(existing, _merge_transcript(existing, converted), "owner")
    assert [m.get("author") for m in out if m["role"] == "user"] == ["dana", "rami", "owner"]
    out = carry_authors(out, _merge_transcript(out, converted))
    assert [m.get("author") for m in out if m["role"] == "user"] == ["dana", "rami", "owner"], (
        "a later save keeps every row's author")


@pytest.mark.parametrize("command", ["/steer use roses", "/reset"])
def test_before_spaces_nothing_is_read(world, monkeypatch, command) -> None:
    from kazma_ui import hitl_answerers

    monkeypatch.setattr(scope, "_enforced", False)
    monkeypatch.setattr(hitl_answerers, "turn_control_refusal",
                        lambda *a, **k: pytest.fail("the rule is read before Spaces"))
    said = asyncio.run(_said(world, "telegram", "rami", command))
    assert "Steer noted" in said or "Conversation cleared" in said, said


# ── negative controls ───────────────────────────────────────────────────


def test_negative_control_the_unrefused_press(world, monkeypatch) -> None:
    """Planted: the handler without the rule. Rami's steer is taken: the
    check above fails on its own assertion."""
    from kazma_ui import hitl_answerers

    monkeypatch.setattr(hitl_answerers, "turn_control_refusal", lambda *a, **k: None)
    with pytest.raises(AssertionError, match="can steer this turn"):
        test_another_member_s_steer_abort_and_reset_are_refused(world, "telegram")


def test_negative_control_rows_without_their_authors(world, monkeypatch) -> None:
    """Planted: rows saved with no author. Whose turn it is falls back to the
    chat's opener, Rami: the check above fails on its own assertion."""
    from kazma_ui import reply_sink

    monkeypatch.setattr(reply_sink, "carry_authors",
                        lambda existing, out, author="": [{k: v for k, v in m.items() if k != "author"}
                                                          for m in out])
    with pytest.raises(AssertionError, match="the row Dana's turn opens with is hers"):
        test_another_member_s_steer_abort_and_reset_are_refused(world, "telegram")
