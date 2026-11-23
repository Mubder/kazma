"""Spaces S3-04: a chat app's approval card is its asker's.

Through the real gateway handler (``create_graph_handler``), with Spaces
enforced: a group bound to the shared space Garden (S3-03), Dana and Rami
members of it, the owner its admin, Omar a linked person outside it.

* Dana's turn pauses on a card; it reaches the group with buttons;
* another person's press -- Rami, the owner (the gateway admin of before),
  Omar from outside the space -- is told the approval is not theirs, and
  changes nothing: no resume, the card still pending;
* Dana's own press resumes the turn, as Dana, in Garden;
* with an approver set for Dana (N4), the card reaches the group without
  buttons and says whom it waits for; the approver's "Approve for task"
  resumes the turn as Dana and grants the task to Dana, never to the
  approver; answered from the approver's own chat, the rest goes to the
  group that asked;
* another member's message never drops Dana's waiting card;
* a swarm result goes to the asker's own chat: the group bound to the
  task's space, or their own chat in it; never another person's chat (the
  O1 rule of before kept it off every chat: the second control).

Negative control: the gateway-admin rule -- the admin's press approves a
card that is not theirs.
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
GROUP = -100991
IID = "iid-plan-1"


class _Graph:
    """Pauses each new turn on a card naming its asker; answers at once."""

    def __init__(self) -> None:
        self.values: dict[str, dict] = {}
        self.paused: dict[str, dict] = {}
        self.runs: list[tuple[Any, str]] = []

    async def aget_state(self, config):
        tid = config["configurable"]["thread_id"]
        card = self.paused.get(tid)
        if card is None:
            return SimpleNamespace(values=self.values.get(tid, {}), next=(), tasks=())
        intr = SimpleNamespace(id=IID, value=card)
        return SimpleNamespace(values=self.values.get(tid, {}), next=("tool_worker",),
                               tasks=(SimpleNamespace(interrupts=(intr,)),))

    async def ainvoke(self, state, config):
        from kazma_core.spaces.approvals import card_fields

        tid = config["configurable"]["thread_id"]
        bound = scope.current_scope()
        self.runs.append((bound, tid))
        self.values[tid] = {**state, "_gateway": {"platform": "telegram",
                                                  "delivery_target": f"telegram:{GROUP}"}}
        self.paused[tid] = {"type": "hitl_approval", "tool": "file_write",
                            "args": {"path": "plan.md", "content": "beds"},
                            **card_fields(bound.space_id, bound.asker, ["file_write"])}
        return self.values[tid]


@pytest.fixture
def world(monkeypatch):
    from kazma_gateway.agent_handler import hitl
    from kazma_gateway.agent_handler.graph import create_graph_handler
    from kazma_gateway.agent_handler.store import _InMemoryStore

    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    monkeypatch.setattr(scope, "_enforced", True)
    store = get_config_store()
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    chat_apps._unbound_said.clear()
    hitl._recent_cards.clear()
    dana = people.create_person(f"dana-{uuid4().hex[:6]}", PASSWORD, by=OWNER_ACCOUNT_ID)
    rami = people.create_person(f"rami-{uuid4().hex[:6]}", PASSWORD, by=OWNER_ACCOUNT_ID)
    omar = people.create_person(f"omar-{uuid4().hex[:6]}", PASSWORD, by=OWNER_ACCOUNT_ID)
    garden = people.create_shared_space("Garden", by=OWNER_ACCOUNT_ID)
    for person in (dana, rami):
        people.add_member(garden["id"], person["id"], by=OWNER_ACCOUNT_ID)
    who = {"dana": dana["id"], "rami": rami["id"], "omar": omar["id"], "owner": OWNER_ACCOUNT_ID}
    uids = {name: f"{n}{uuid4().int % 10**8}" for n, name in enumerate(who, start=3)}

    def link(reg: dict) -> None:
        for name, account in who.items():
            reg["links"].append({"platform": "telegram", "user_id": uids[name], "account": account})

    identity.update_registry(link)
    chat_links.refresh_links()
    # The gateway admin of before: the owner (the control's rule).
    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", f"telegram:{uids['owner']}")
    graph = _Graph()
    sent: list[tuple[str, str, Any]] = []

    async def send(out):
        sent.append((out.target_id, out.text, (out.context_metadata or {}).get("reply_markup")))
        return True

    resumes: list[tuple[Any, Any]] = []

    async def run_resume(graph_, cmd, config, thread_id):
        resumes.append((scope.current_scope(), cmd))
        graph.paused.pop(thread_id, None)
        messages = [{"role": "user", "content": "write the plan"},
                    {"role": "assistant", "content": "Done: the plan is written."}]
        return {"messages": messages, hitl._SAID_BEFORE: [],
                "_gateway": {"platform": "telegram", "delivery_target": f"telegram:{GROUP}"}}, None

    monkeypatch.setattr(hitl, "_run_resume", run_resume)
    manager = SimpleNamespace(send=AsyncMock(side_effect=send), adapters=[])
    handler = create_graph_handler(graph=graph, manager=manager, store=_InMemoryStore())
    w = SimpleNamespace(who=who, uids=uids, garden=garden, graph=graph, sent=sent, resumes=resumes,
                        handler=handler)
    _say(w, "dana", "/space Garden", group=True)
    yield w
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    for key in list((store.get_category("session") or {}).keys()):
        if key.startswith("active_thread.telegram:"):
            store.delete(key)
    chat_links._forget_links_for_tests()
    hitl._recent_cards.clear()


def _say(w: Any, who: str, text: str, *, group: bool = False, press: bool = False) -> None:
    uid = w.uids[who]
    meta = {"chat_id": GROUP if group else int(uid), "user_id": int(uid),
            "chat_type": "supergroup" if group else "private", "username": who}
    if press:
        meta["callback_query_id"] = f"cb-{uuid4().hex[:8]}"
    asyncio.run(w.handler(IncomingMessage("telegram", f"telegram:{uid}", text, context_metadata=meta)))


def _paused_card(w: Any) -> tuple[str, dict]:
    """Dana asks in the group; her turn pauses on a card."""
    _say(w, "dana", "write the plan for the beds", group=True)
    (bound, thread), = w.graph.runs
    assert bound == scope.Scope(w.garden["id"], w.who["dana"])
    return thread, w.graph.paused[thread]


def _press(w: Any, who: str, thread: str, action: str = "approve", *, group: bool = True) -> str:
    _say(w, who, f"/hitl {action} {thread}~{IID}", group=group, press=group)
    return w.sent[-1][1]


def _gate_state(thread: str) -> str | None:
    from kazma_core.safety.hitl_gates import gate_for

    row = gate_for(IID)
    return getattr(row, "state", None) if row is not None else None


# ── the card and whose press counts ─────────────────────────────────────


def test_the_card_reaches_the_group_with_buttons(world) -> None:
    _paused_card(world)
    target, text, markup = world.sent[-1]
    assert target == f"telegram:{GROUP}"
    assert "Approval required" in text or "file_write" in text, text
    assert markup, "the asker answers from the group's card"


def test_another_member_s_press_is_not_theirs_and_changes_nothing(world) -> None:
    thread, _card = _paused_card(world)
    said = _press(world, "rami", thread)
    assert "not yours" in said and "dana" in said.lower(), said
    assert world.resumes == [], "nothing was resumed"
    assert thread in world.graph.paused, "the card still waits"
    assert _gate_state(thread) in (None, "pending")


def test_the_admin_s_press_is_not_theirs_either(world) -> None:
    thread, _card = _paused_card(world)
    said = _press(world, "owner", thread)
    assert "not yours" in said, said
    assert world.resumes == [] and thread in world.graph.paused


def test_a_press_from_outside_the_space_is_not_theirs(world) -> None:
    thread, _card = _paused_card(world)
    said = _press(world, "omar", thread)
    assert "not yours" in said, said
    assert world.resumes == [] and thread in world.graph.paused


def test_the_asker_s_press_resumes_the_turn_as_the_asker(world) -> None:
    thread, _card = _paused_card(world)
    said = _press(world, "dana", thread)
    (bound, _cmd), = world.resumes
    assert bound == scope.Scope(world.garden["id"], world.who["dana"])
    assert "plan is written" in said and world.sent[-1][0] == f"telegram:{GROUP}"


def test_another_member_s_message_never_drops_a_waiting_card(world) -> None:
    thread, _card = _paused_card(world)
    _say(world, "rami", "what is the weather?", group=True)
    assert "waits for" in world.sent[-1][1] and "dana" in world.sent[-1][1].lower()
    assert len(world.graph.runs) == 1 and thread in world.graph.paused
    _say(world, "rami", "ما حال الطقس؟", group=True)
    assert "بانتظار" in world.sent[-1][1]


# ── an approver answers in the asker's place ────────────────────────────


def _with_an_approver(w: Any) -> None:
    from kazma_core.spaces.approvals import APPROVERS_POLICY

    def set_approver(reg: dict) -> None:
        reg.setdefault("policies", {}).setdefault(w.who["dana"], {})[APPROVERS_POLICY] = [w.who["rami"]]

    identity.update_registry(set_approver)


def test_a_card_for_an_approver_waits_for_them_without_buttons(world) -> None:
    _with_an_approver(world)
    _paused_card(world)
    _target, text, markup = world.sent[-1]
    assert markup is None, "the asker cannot answer it"
    assert "Waiting for" in text and "rami" in text.lower(), text


def test_the_approver_s_task_approval_runs_and_grants_as_the_asker(world) -> None:
    from kazma_core.safety.task_grants import has_task_grant

    _with_an_approver(world)
    thread, _card = _paused_card(world)
    _press(world, "rami", thread, "approve_task")
    (bound, _cmd), = world.resumes
    assert bound == scope.Scope(world.garden["id"], world.who["dana"]), "the turn is Dana's"
    assert has_task_grant(thread, person=world.who["dana"]), "the grant is Dana's"
    assert not has_task_grant(thread, person=world.who["rami"]), "never the approver's"


def test_answered_from_the_approver_s_chat_the_rest_goes_to_the_group(world) -> None:
    _with_an_approver(world)
    thread, _card = _paused_card(world)
    _say(world, "rami", "/space Garden")
    _press(world, "rami", thread, group=False)
    assert world.resumes, "the approver's answer resumed the turn"
    to_group = [t for t, text, _ in world.sent if t == f"telegram:{GROUP}" and "plan is written" in text]
    assert to_group, "the turn's answer goes to the group that asked"
    to_rami = [text for t, text, _ in world.sent if t == f"telegram:{world.uids['rami']}"]
    assert any("the rest goes to the chat that asked" in text.lower() for text in to_rami), to_rami
    assert not any("plan is written" in text for text in to_rami), "never the approver's chat"


# ── results go to the asker's own chat ──────────────────────────────────


def _deliver_result(w: Any, monkeypatch, *, asker: str, target: str, source_thread: str = "",
                    space: str = "") -> list:
    """A finished swarm task of *asker* in Garden from *target*, handed to
    the chat-app delivery; returns what was sent."""
    from kazma_core.swarm import chat_delivery

    sent: list[tuple[str, str]] = []

    async def send_message(to, text, backend="", **_kw):
        sent.append((to, text))
        return f"sent:{to}"

    from kazma_core.tools import send_message as sending

    monkeypatch.setattr(sending, "send_message", send_message)
    task = SimpleNamespace(id=f"t-{uuid4().hex[:6]}", metadata={
        "source": "chat", "source_platform": "telegram", "source_delivery_target": target,
        "source_thread_id": source_thread, "tenant_id": space or w.garden["id"], "asker": w.who[asker]})
    result = SimpleNamespace(status="success", aggregated_output="The beds are planned.",
                             duration_seconds=60.0, error="", worker_results=[])
    asyncio.run(chat_delivery._deliver(task, result, "telegram", target))
    return sent


def test_a_result_goes_to_the_group_its_asker_works_in(world, monkeypatch) -> None:
    sent = _deliver_result(world, monkeypatch, asker="dana", target=f"telegram:{GROUP}")
    assert sent and sent[0][0] == f"telegram:{GROUP}" and "beds are planned" in sent[0][1]


def test_a_result_goes_to_the_asker_s_own_chat_in_the_space(world, monkeypatch) -> None:
    from kazma_core.spaces import threads

    dm = f"telegram:{world.uids['dana']}"
    threads.record_thread("gw-dana-garden-dm", world.garden["id"], world.who["dana"])
    sent = _deliver_result(world, monkeypatch, asker="dana", target=dm, source_thread="gw-dana-garden-dm")
    assert [to for to, _ in sent] == [dm]


def test_a_result_never_goes_to_someone_else_s_chat(world, monkeypatch) -> None:
    from kazma_core.spaces import threads

    rami_dm = f"telegram:{world.uids['rami']}"
    threads.record_thread("gw-rami-garden-dm", world.garden["id"], world.who["rami"])
    assert _deliver_result(world, monkeypatch, asker="dana", target=rami_dm,
                           source_thread="gw-rami-garden-dm") == []
    personal = next(sid for sid, sp in identity.read_registry()["spaces"].items()
                    if sp.get("personal_of") == world.who["omar"])
    assert _deliver_result(world, monkeypatch, asker="omar", target=f"telegram:{GROUP}",
                           space=personal) == [], "a group bound to another space"


def test_negative_control_results_kept_off_every_chat(world, monkeypatch) -> None:
    """Planted: O1 as before -- someone else's result goes to no chat. The
    group's result never arrives: the check above fails on its own
    assertion."""
    from kazma_core.swarm import chat_delivery

    monkeypatch.setattr(chat_delivery, "_the_askers_chat", lambda *_a: False)
    with pytest.raises(AssertionError):
        test_a_result_goes_to_the_group_its_asker_works_in(world, monkeypatch)


# ── negative control ────────────────────────────────────────────────────


def test_negative_control_the_gateway_admin_rule(world, monkeypatch) -> None:
    """Planted: the rule of before -- a gateway admin answers any card. The
    owner's press approves Dana's card: the check above fails on its own
    assertion."""
    from kazma_gateway.allowlists import is_gateway_admin
    from kazma_ui import hitl_answerers

    def admin_rule(thread_id, gate_id="", payload=None, *, caller=None, row=None):
        bound = scope.current_scope()
        admin = bound is not None and bound.asker == OWNER_ACCOUNT_ID and is_gateway_admin(
            f"telegram:{world.uids['owner']}", "telegram")
        return (None if admin or (bound and bound.asker == (payload or {}).get("asker"))
                else "not_yours"), hitl_answerers.whose_card(thread_id, gate_id, payload)

    monkeypatch.setattr(hitl_answerers, "refusal_for_card", admin_rule)
    with pytest.raises(AssertionError, match="not yours"):
        test_the_admin_s_press_is_not_theirs_either(world)
