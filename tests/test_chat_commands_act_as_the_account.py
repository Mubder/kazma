"""Spaces S3-06: every chat-app command acts as the linked account in the
chat's space.

Through the real gateway handler (``create_graph_handler``), with Spaces
enforced: Dana linked on Telegram, her direct chat moved to the shared space
Garden with ``/space`` (S3-03).

* **The matrix**: every command of the catalog (``command_catalog.COMMANDS``)
  is sent; every reply it makes and every turn it starts happens bound to
  Garden as Dana -- never the owner, never her personal space. ``/space``
  alone is answered before the chat's place exists (it chooses it). A
  command added to the catalog needs a line in ``SAMPLES``.
* **Seasons**: ``/sessions`` lists Garden's chats, ``/new`` makes a Garden
  chat the direct chat continues, ``/switch`` takes over a chat of Garden and
  never one of another space; ``/runs act`` and the ``/memory`` change words
  are decided in the space, not by the gateway-admin rule.
* **Buttons**: a linked member's press on an approval card reaches the
  handler (the gateway-admin gate of the adapters gives way to the card's own
  rule), and in a group the card keeps its buttons.

Negative control: the sender-owner pointer -- the season pointer kept under
the platform sender, whatever the space: ``/new`` in Garden then leaves the
direct chat on the old chat.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import chat_apps, chat_links, identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from kazma_gateway.gateway import IncomingMessage

PASSWORD = "a-long-enough-password"

#: One message for every command of the catalog.
SAMPLES = {
    "help": "/help", "sessions": "/sessions", "seasons": "/seasons", "session": "/session",
    "season": "/season", "switch": "/switch", "space": "/space", "new": "/new Garden plans",
    "reset": "/reset", "compact": "/compact", "research": "/research deep tulip bulbs",
    "swarm": "/swarm", "ide": "/ide", "skill": "/skill list", "documents": "/documents",
    "docs": "/docs", "kb": "/kb list", "long": "/long", "mission": "/mission", "yolo": "/yolo",
    "unrestricted": "/unrestricted", "plan": "/plan", "steer": "/steer more water please",
    "abort": "/abort", "stop": "/stop", "replay": "/replay list", "report": "/report",
    "runs": "/runs", "fork": "/fork", "undo": "/undo", "edit": "/edit", "config": "/config",
    "personality": "/personality list", "model": "/model", "models": "/models",
    "context": "/context", "status": "/status", "memory": "/memory changes", "cost": "/cost",
    "hitl": "/hitl approve gw-none~iid-none", "x": "/x list",
}
#: Commands answered before the chat's place is known, and why.
BEFORE_THE_PLACE = {"space": "it chooses the place the chat works in (S3-03)"}


class _Graph:
    def __init__(self) -> None:
        self.values: dict[str, dict] = {}
        self.runs: list[tuple[Any, str]] = []

    async def aget_state(self, config):
        tid = config["configurable"]["thread_id"]
        return SimpleNamespace(values=self.values.get(tid, {}), next=(), tasks=())

    async def ainvoke(self, state, config):
        tid = config["configurable"]["thread_id"]
        self.runs.append((scope.current_scope(), tid))
        self.values[tid] = {**state, "messages": [*state.get("messages", []),
                                                  {"role": "assistant", "content": "ok"}]}
        return self.values[tid]


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
    uid = f"8{uuid4().int % 10**8}"

    def link(reg: dict) -> None:
        reg["links"].append({"platform": "telegram", "user_id": uid, "account": dana["id"]})

    identity.update_registry(link)
    chat_links.refresh_links()
    graph = _Graph()
    sent: list[tuple[Any, str]] = []

    async def send(out):
        sent.append((scope.current_scope(), out.text))
        return True

    manager = SimpleNamespace(send=AsyncMock(side_effect=send), adapters=[])
    handler = create_graph_handler(graph=graph, manager=manager, store=_InMemoryStore())
    w = SimpleNamespace(dana=dana, rami=rami, garden=garden, uid=uid, graph=graph, sent=sent,
                        handler=handler)
    _say(w, "/space Garden")
    assert "Garden" in w.sent[-1][1]
    w.sent.clear()
    yield w
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    for key in list((store.get_category("session") or {}).keys()):
        if key.startswith("active_thread.telegram:"):
            store.delete(key)
    chat_links._forget_links_for_tests()


def _say(w: Any, text: str) -> None:
    meta = {"chat_id": int(w.uid), "user_id": int(w.uid), "chat_type": "private", "username": "dana"}
    asyncio.run(w.handler(IncomingMessage("telegram", f"telegram:{w.uid}", text, context_metadata=meta)))


# ── the matrix ──────────────────────────────────────────────────────────


def test_every_command_has_a_sample() -> None:
    from kazma_core.agent.command_catalog import COMMANDS

    assert sorted(c.name for c in COMMANDS) == sorted(SAMPLES), "a command added needs a line here"


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_every_command_acts_as_the_account_in_the_space(world, name: str) -> None:
    _say(world, SAMPLES[name])
    seen = [bound for bound, _text in world.sent] + [bound for bound, _tid in world.graph.runs]
    assert seen, f"/{name} answered nothing"
    if name in BEFORE_THE_PLACE:
        return
    here = scope.Scope(world.garden["id"], world.dana["id"])
    assert all(bound == here for bound in seen), (name, seen)


# ── seasons ─────────────────────────────────────────────────────────────


def _garden_thread(w: Any) -> str:
    _say(w, "hello garden")
    return w.graph.runs[-1][1]


def test_new_makes_a_chat_of_the_space_the_direct_chat_continues(world) -> None:
    before = _garden_thread(world)
    _say(world, "/new Bulb order")
    assert "New season" in world.sent[-1][1]
    _say(world, "and now?")
    bound, after = world.graph.runs[-1]
    assert bound == scope.Scope(world.garden["id"], world.dana["id"])
    assert after != before, "/new continues a new chat of Garden"


def _web_chat(space: str, account: str, title: str) -> None:
    from kazma_ui.session_manager import ChatSession, get_session_manager

    with scope.bind_scope(space, account):
        chat = ChatSession(session_id=uuid4().hex, thread_id=uuid4().hex, title=title)
        chat.add_message("user", title)
        get_session_manager().put(chat)


def test_sessions_lists_the_space_s_chats(world) -> None:
    _web_chat(world.garden["id"], world.rami["id"], "Rami's watering plan")
    _web_chat(str(world.dana["personal_space"]), world.dana["id"], "Dana's private diary")
    _say(world, "/sessions")
    listed = world.sent[-1][1]
    assert "watering plan" in listed, listed
    assert "private diary" not in listed, "never another space's chats"


def test_switch_never_takes_over_another_space_s_chat(world) -> None:
    from kazma_core.spaces import threads

    threads.record_thread("dana-diary-thread", str(world.dana["personal_space"]), world.dana["id"])
    _say(world, "/switch dana-diary-thread")
    assert "another space" in world.sent[-1][1] or "No season" in world.sent[-1][1], world.sent[-1][1]


def test_runs_act_is_decided_in_the_space(world) -> None:
    _say(world, "/runs act inc_missing cancel")
    said = world.sent[-1][1]
    assert "admin-only" not in said, said


def test_memory_changes_are_the_space_s(world) -> None:
    _say(world, "/memory changes")
    assert "admin-only" not in world.sent[-1][1], world.sent[-1][1]


# ── buttons ─────────────────────────────────────────────────────────────


def test_a_member_s_press_reaches_the_handler_and_a_group_card_keeps_its_buttons(world) -> None:
    from kazma_gateway.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter(token="123:abc")
    adapter._running = True
    posted: list[tuple[str, dict]] = []

    class _Http:
        async def post(self, path, json=None):
            posted.append((path, json or {}))
            return SimpleNamespace(status_code=200, json=lambda: {"ok": True})

    adapter._http = _Http()
    adapter._ensure_http = lambda: adapter._http
    queue: asyncio.Queue = asyncio.Queue()
    adapter._queue = queue

    async def press() -> list[Any]:
        await adapter._handle_callback_query({
            "id": "cb-1", "data": "hitl:approve:gw-x~iid-1",
            "message": {"message_id": 7, "chat": {"id": -1001, "type": "supergroup"}},
            "from": {"id": int(world.uid)},
        })
        for _ in range(50):
            await asyncio.sleep(0.01)
        return [queue.get_nowait() for _ in range(queue.qsize())]

    queued = asyncio.run(press())
    assert queued and queued[0].text.startswith("/hitl approve"), "the press reached the handler"
    assert not any(path == "/editMessageReplyMarkup" for path, _ in posted), "the group card kept its buttons"


_ADAPTERS = ("telegram", "discord", "slack", "whatsapp")


def _unasked_press_gates(text: str) -> list[str]:
    """Lines that gate an approval press on the gateway-admin rule without
    asking ``press_needs_admin`` first."""
    return [line.strip() for line in text.splitlines()
            if 'action.kind in ("hitl", "swarm", "run")' in line and "press_needs_admin" not in line]


def test_every_adapter_s_press_gate_asks_whose_rule_it_is() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "kazma-gateway" / "kazma_gateway" / "adapters"
    for name in _ADAPTERS:
        text = (root / f"{name}.py").read_text(encoding="utf-8")
        assert 'action.kind in ("hitl", "swarm", "run")' in text, name
        assert _unasked_press_gates(text) == [], name


def test_control_a_planted_press_gate_is_found() -> None:
    planted = '        if action.kind in ("hitl", "swarm", "run"):\n            pass\n'
    assert _unasked_press_gates(planted) != []


def test_before_spaces_a_press_still_needs_a_gateway_admin(world, monkeypatch) -> None:
    from kazma_gateway.allowlists import press_keeps_card, press_needs_admin

    monkeypatch.setattr(scope, "_enforced", False)
    assert press_needs_admin("hitl") and press_needs_admin("run") and press_needs_admin("swarm")
    assert not press_keeps_card("hitl", direct=False)


# ── negative control ────────────────────────────────────────────────────


def test_negative_control_the_sender_owner_pointer(world, monkeypatch) -> None:
    """Planted: the season pointer of before, kept under the platform sender
    whatever the space. ``/new`` in Garden leaves the direct chat on its old
    Garden chat: the check above fails on its own assertion."""
    from kazma_gateway.agent_handler import session_commands

    monkeypatch.setattr(session_commands, "_mouth_key", lambda sender, chat_key: sender)
    with pytest.raises(AssertionError, match="new chat of Garden"):
        test_new_makes_a_chat_of_the_space_the_direct_chat_continues(world)
