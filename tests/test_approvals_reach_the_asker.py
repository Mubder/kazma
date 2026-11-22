"""Spaces S2-05: an approval card goes to the person it asks, and nobody else
answers it (decisions N4 and N21).

Two people, Reem and Omar, share a space. Reem's turn pauses at a card.

* Omar is refused on the approve route (403 ``not_yours``) and the gate
  stays pending; Reem answers it.
* The resumed turn runs as Reem whoever answered: an approver or the admin
  pressing for her never runs her turn as themself.
* The pending list, the clear route, the bell and Web Push are each
  person's own: Omar sees and clears none of Reem's cards.
* The decision recorder every surface writes through (the chat apps'
  buttons included) refuses Omar too.
* The one rule reads an "approver" set on the asker's account (N4: empty
  for everyone, so it changes nothing yet), and sends a Development holder's
  always-ask card in a shared space with a project folder to the admin
  (N21; tests/test_development_per_person.py runs it over every tool).
* Nothing changes on an install that does not enforce Spaces.

Control: the thread-owner rule (every member of the chat's space answers
it, the rule before S2-05) lets Omar decide Reem's card.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import approvals, identity, people, scope, threads
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, bind_scope
from kazma_ui import auth, space_binding

pytestmark = pytest.mark.real_features

_XHR = {"x-requested-with": "XMLHttpRequest"}


def _person(name: str) -> dict[str, Any]:
    from kazma_core.security import platform_rbac

    user = platform_rbac.create_local_user(name, secrets.token_urlsafe(16))
    return {"username": name, "role": "operator", "user_id": user.user_id, "actor": "login",
            "source": "session"}


@pytest.fixture
def team(monkeypatch):
    """Spaces enforced; Reem and Omar in one shared space."""
    from kazma_core.security.vault import reset_posture_cache

    monkeypatch.setattr(scope, "_enforced", True)
    get_config_store().delete(identity.REGISTRY_KEY)
    try:
        principals = {"reem": _person("reem-s205"), "omar": _person("omar-s205")}
        accounts = {who: space_binding.account_of(p) for who, p in principals.items()}
        space = people.create_shared_space("Team", by=OWNER_ACCOUNT_ID)["id"]
        for account in accounts.values():
            people.add_member(space, account, by=OWNER_ACCOUNT_ID)
        yield SimpleNamespace(space=space, principals=principals, accounts=accounts)
    finally:
        get_config_store().delete(identity.REGISTRY_KEY)
        reset_posture_cache()


def _card(space: str, asker: str, tool: str = "file_write") -> dict[str, Any]:
    """The card the graph raises for *asker*'s turn (graph_tool_worker)."""
    from kazma_core.agent.graph_tool_worker import _whose_card

    card: dict[str, Any] = {"type": "hitl_approval", "kind": "security", "tool": tool,
                            "args": {"path": "notes.txt"}, "tools": [{"name": tool, "args": {}}],
                            "message": "Approve?"}
    with bind_scope(space, asker):
        card.update(_whose_card([tool]))
    return card


class _PausedGraph:
    """A checkpoint paused at one approval interrupt (what LangGraph's
    ``aget_state`` gives the route)."""

    def __init__(self, card: dict[str, Any], interrupt_id: str) -> None:
        self.snapshot = SimpleNamespace(
            next=("tool_worker",),
            tasks=[SimpleNamespace(interrupts=[SimpleNamespace(id=interrupt_id, value=card)])],
            values={},
        )

    async def aget_state(self, _config: Any) -> Any:
        return self.snapshot


@contextmanager
def chat_paused(team: Any, *, asker: str = "reem") -> Iterator[SimpleNamespace]:
    """The approval routes over the real gate registry and chat store, with
    *asker*'s turn paused at a card in a chat of the shared space. The
    resumed drive is recorded with the scope it runs in."""
    from kazma_core.safety import hitl_gates

    from kazma_ui.hitl_gate_bridge import gate_pending_from_payload
    from kazma_ui.routes_direct import misc
    from kazma_ui.session_manager import get_session_manager
    from kazma_ui.sse_chat import _streaming

    account = team.accounts[asker]
    thread_id, session_id = f"t{secrets.token_hex(8)}", f"s{secrets.token_hex(8)}"
    card = _card(team.space, account)
    card["thread_id"] = thread_id
    card["interrupt_id"] = f"i{secrets.token_hex(6)}"
    resumed: list[tuple[str, str] | None] = []

    async def _drive(*_a: Any, **_k: Any) -> None:
        bound = scope.current_scope()
        resumed.append((bound.space_id, bound.asker) if bound else None)

    with pytest.MonkeyPatch.context() as patch, tempfile.TemporaryDirectory(
        ignore_cleanup_errors=True
    ) as tmp:
        patch.setattr(auth, "get_request_principal",
                      lambda request: team.principals.get(request.headers.get("x-test-person", "")))

        async def _no_secret() -> str:
            return ""

        patch.setattr(auth, "aget_kazma_secret", _no_secret)
        patch.setattr(hitl_gates, "_db_path_override", str(Path(tmp) / "hitl_gates.db"))
        patch.setattr(hitl_gates, "_schema_ready", False)
        patch.setattr(_streaming, "_drive_graph_to_journal", _drive)
        with bind_scope(team.space, account):
            store = get_session_manager()
            session = store.get_or_create(session_id, thread_id=thread_id)
            session.add_message("user", "write my notes")
            store.put(session)
            threads.record_thread(thread_id, team.space, account)
            asyncio.run(gate_pending_from_payload(card, session_id=session_id))
        holder = SimpleNamespace(
            app=None, templates=None, agent=None, gateway=None, chat_adapters=None,
            _init_errors=[], session_store=None, _graph_holder={},
            _hitl_state={"graph": _PausedGraph(card, card["interrupt_id"]), "checkpointer": object()},
        )
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        holder.app = FastAPI()
        holder.app.middleware("http")(auth.create_tenant_middleware())
        misc.register_misc_routes(holder)
        with TestClient(holder.app) as client:
            yield SimpleNamespace(client=client, thread=thread_id, session=session_id, card=card,
                                  gate=card["interrupt_id"], gates=hitl_gates, resumed=resumed,
                                  hitl_state=holder._hitl_state)


def _as(team: Any, who: str) -> dict[str, str]:
    return {"x-test-person": who, space_binding.SPACE_HEADER: team.space, **_XHR}


def _state(gates: Any, gate_id: str) -> str:
    row = gates.gate_for(gate_id)
    return row.state if row is not None else "missing"


def _approve(world: Any, team: Any, who: str) -> Any:
    return world.client.post(f"/api/approve/{world.thread}",
                             json={"action": "approve", "interrupt_id": world.gate},
                             headers=_as(team, who))


# ── the approve route ───────────────────────────────────────────────────


def test_the_card_names_its_asker_and_who_answers(team) -> None:
    card = _card(team.space, team.accounts["reem"])
    assert card["asker"] == team.accounts["reem"]
    assert card["answered_by"] == [team.accounts["reem"]]
    assert card["answered_by_names"] == ["reem-s205"]


def test_another_member_is_refused_and_the_gate_stays_pending(team) -> None:
    with chat_paused(team) as world:
        got = _approve(world, team, "omar")
        assert got.status_code == 403, got.text
        assert got.json()["code"] == "not_yours" and "reem-s205" in got.json()["error"]
        assert _state(world.gates, world.gate) == "pending"
        assert world.resumed == []
        # Reem answers her own card; her turn goes on, as her.
        mine = _approve(world, team, "reem")
        assert mine.status_code == 200, mine.text
        assert _state(world.gates, world.gate) != "pending"
        for _ in range(100):
            if world.resumed:
                break
            asyncio.run(asyncio.sleep(0.01))
    assert world.resumed == [(team.space, team.accounts["reem"])]


def test_control_the_thread_owner_rule_lets_another_member_answer(team, monkeypatch) -> None:
    """The rule before S2-05: a member of the chat's space answered any card."""
    from kazma_ui import hitl_answerers

    monkeypatch.setattr(hitl_answerers, "refusal_for_card", lambda *_a, **_k: (None, None))
    with chat_paused(team) as world:
        got = _approve(world, team, "omar")
        assert got.status_code == 200, got.text
        assert _state(world.gates, world.gate) != "pending"


def test_an_answer_for_someone_resumes_their_turn_as_them(team) -> None:
    """Reem's approver (N4) answers her card; her turn runs as her, never as
    the approver."""
    reem, omar = team.accounts["reem"], team.accounts["omar"]

    def set_approver(reg: dict[str, Any]) -> None:
        policy = dict(reg["policies"].get(reem) or {})
        policy[approvals.APPROVERS_POLICY] = [omar]
        reg["policies"][reem] = policy

    identity.update_registry(set_approver)
    with chat_paused(team) as world:
        assert world.card["answered_by"] == [omar]
        refused = _approve(world, team, "reem")
        assert refused.status_code == 403 and refused.json()["code"] == "not_yours", refused.text
        assert _state(world.gates, world.gate) == "pending"
        got = _approve(world, team, "omar")
        assert got.status_code == 200, got.text
        for _ in range(100):
            if world.resumed:
                break
            asyncio.run(asyncio.sleep(0.01))
    assert world.resumed == [(team.space, reem)]


def test_control_a_resume_bound_as_whoever_answered_runs_as_them(team, monkeypatch) -> None:
    """Without the binding, the approver's answer would run Reem's turn as
    the approver: their tools, their limits, their records."""
    from contextlib import nullcontext

    from kazma_ui import hitl_answerers

    reem, omar = team.accounts["reem"], team.accounts["omar"]

    def set_approver(reg: dict[str, Any]) -> None:
        reg["policies"][reem] = {approvals.APPROVERS_POLICY: [omar]}

    identity.update_registry(set_approver)
    monkeypatch.setattr(hitl_answerers, "as_the_asker", lambda _whose: nullcontext())
    with chat_paused(team) as world:
        assert _approve(world, team, "omar").status_code == 200
        for _ in range(100):
            if world.resumed:
                break
            asyncio.run(asyncio.sleep(0.01))
    assert world.resumed == [(team.space, omar)]


def test_the_pending_list_is_each_persons_own(team) -> None:
    with chat_paused(team) as world:
        omars = world.client.get("/api/pending-approvals", headers=_as(team, "omar"))
        reems = world.client.get("/api/pending-approvals", headers=_as(team, "reem"))
    assert omars.status_code == 200 and omars.json()["pending"] == [], omars.text
    assert [i["thread_id"] for i in reems.json()["pending"]] == [world.thread], reems.text
    assert reems.json()["pending"][0]["asker"] == team.accounts["reem"]


def test_clearing_takes_only_the_callers_own_cards(team, monkeypatch) -> None:
    from kazma_ui import hitl_approval

    deleted: list[str] = []
    paused: list[str] = []

    async def _paused(_graph: Any, _checkpointer: Any) -> list[dict[str, Any]]:
        return [{"thread_id": thread} for thread in paused]

    class _Checkpointer:
        async def adelete_thread(self, thread_id: str) -> None:
            deleted.append(thread_id)

    monkeypatch.setattr(hitl_approval, "_get_pending_approvals", _paused)
    with chat_paused(team) as world:
        paused.append(world.thread)
        world.hitl_state["checkpointer"] = _Checkpointer()
        omars = world.client.post("/api/pending-approvals/clear", headers=_as(team, "omar"))
        assert omars.status_code == 200 and omars.json()["cleared"] == 0, omars.text
        assert deleted == [] and _state(world.gates, world.gate) == "pending"
        reems = world.client.post("/api/pending-approvals/clear", headers=_as(team, "reem"))
        assert reems.json()["cleared"] == 1 and deleted == [world.thread], reems.text


# ── every surface that decides, and every place a card reaches ─────────


def test_the_decision_recorder_refuses_another_member(team) -> None:
    """The one writer every surface decides through (the chat apps' buttons
    included) refuses a person the card is not for."""
    from kazma_ui.hitl_decision import GateNotYours, record_gate_decision

    with chat_paused(team) as world:
        with bind_scope(team.space, team.accounts["omar"]), pytest.raises(GateNotYours):
            asyncio.run(record_gate_decision(world.thread, decision="approved", actor="omar",
                                             payload=world.card, interrupt_id=world.gate,
                                             require_durable=True))
        assert _state(world.gates, world.gate) == "pending"
        # The watchdog's timeout is nobody's answer: never refused.
        asyncio.run(record_gate_decision(world.thread, decision="timeout", actor="timeout",
                                         payload=world.card, interrupt_id=world.gate,
                                         require_durable=True))
        assert _state(world.gates, world.gate) != "pending"


def test_the_bell_shows_a_card_only_to_whom_it_waits_for(team) -> None:
    from kazma_ui.hitl_answerers import bell_items

    with chat_paused(team) as world:
        with bind_scope(team.space, team.accounts["omar"]):
            assert bell_items("en") == []
        with bind_scope(team.space, team.accounts["reem"]):
            reems = bell_items("en")
            arabic = bell_items("ar")
    assert [i["id"] for i in reems] == [f"approval-{world.gate}"]
    assert reems[0]["callback_id"] == f"link:/chat?s={world.session}"
    assert "notes.txt" not in json.dumps(reems), "a card's arguments never reach the bell"
    assert arabic[0]["title"].startswith("موافقة بانتظارك")


def test_web_push_reaches_only_the_people_a_card_asks(team, monkeypatch) -> None:
    from kazma_ui import push

    devices = [{"endpoint": "https://push.example/reem", "_account": team.accounts["reem"]},
               {"endpoint": "https://push.example/omar", "_account": team.accounts["omar"]}]
    monkeypatch.setattr(push, "turn_complete_notifications_on", lambda: True)
    monkeypatch.setattr(push, "list_subscriptions", lambda: list(devices))
    monkeypatch.setattr(push, "ensure_vapid_keys", lambda: ("pub", "priv"))
    card = _card(team.space, team.accounts["reem"])
    keys, subs = push._approval_plan(card)
    assert [s["endpoint"] for s in subs] == ["https://push.example/reem"]
    # Off: nothing is pushed for a pause, as before Spaces.
    monkeypatch.setattr(scope, "_enforced", False)
    assert push._approval_plan(card) is None


# ── the rule ────────────────────────────────────────────────────────────


def test_a_card_with_no_asker_is_answered_by_the_spaces_owners(team) -> None:
    found = approvals._answerers(team.space, "", ["file_write"])
    assert found.accounts == {OWNER_ACCOUNT_ID} and found.why == "space_owners"


def test_the_swarm_follows_the_one_rule(team) -> None:
    from kazma_core.swarm.single_operator import answered_by_caller

    reem, omar = team.accounts["reem"], team.accounts["omar"]
    with bind_scope(team.space, omar):
        assert not answered_by_caller(team.space, reem)
    with bind_scope(team.space, reem):
        assert answered_by_caller(team.space, reem)


def test_with_spaces_off_nothing_changes(team, monkeypatch) -> None:
    from kazma_core.agent.graph_tool_worker import _whose_card
    from kazma_ui.hitl_answerers import answerable, bell_items

    monkeypatch.setattr(scope, "_enforced", False)
    assert approvals.answer_refusal(team.space, team.accounts["reem"], ["file_write"],
                                    caller=team.accounts["omar"]) is None
    with bind_scope(team.space, team.accounts["reem"]):
        assert _whose_card(["file_write"]) == {}
        assert bell_items("en") == []
    items = [{"thread_id": "t1", "asker": team.accounts["reem"]}]
    assert answerable(items, caller=team.accounts["omar"]) == items
