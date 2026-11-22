"""The S2-05/06 review's findings, each with today's code as its control.

Reem and Omar share a space (``tests/test_approvals_reach_the_asker.py``'s
world). Each finding is a way someone else decided Reem's card, ran her turn
as someone else, or stopped her turn:

1. an MCP server's sampling request was resolved before anyone asked who
   may answer it, and listed to everyone;
2. whose card it is came from a row the request could name, and two
   people's identical cards were merged into one row;
3. a clarify or confirm card's tools (``items[].tool``) were not read, so N21
   did not hold when it was answered;
4. a person whose cards go to approvers could turn them off for themself
   (YOLO, ``/unrestricted``, "Allow tool", a task grant); a chat app's swarm
   button asked only whose chat apps they are; Cancel and Retry of one's own
   task asked the approvers' rule;
5. the timeout watchdog recorded the timeout before reading whose card it
   was (a read that failed left the gate claimed), and resumed a turn whose
   asker had left;
6. with Spaces off, the Swarm decide route read the request first (the
   swarm's half is ``tests/test_swarm_spaces.py``);
8. another member's new message dropped a card waiting on Reem;
9. the long-task record (``/long``, ``/unrestricted``) was per chat.

Finding 7 (every resume runs as the turn's asker) is
``tests/test_resumes_run_as_the_asker.py``. Also here, the tests the review
found missing: the bridge-gate branch, the gateway's ``/hitl`` decision, the
identity-unavailable 503, the push wiring, the bell through its route, N21
through the route with the resume as the holder.
"""

from __future__ import annotations

import asyncio
import secrets
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.spaces import approvals, identity, people, scope, threads
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, bind_scope
from kazma_ui.hitl_answerers import whose_card as _whose_card_new

from tests.test_approvals_reach_the_asker import (  # noqa: F401 -- team is the shared fixture
    _approve,
    _as,
    _card,
    _state,
    chat_paused,
    team,
)

pytestmark = pytest.mark.real_features


def _set_approvers(account: str, approvers: list[str]) -> None:
    def change(reg: dict[str, Any]) -> None:
        policy = dict(reg["policies"].get(account) or {})
        policy[approvals.APPROVERS_POLICY] = approvers
        reg["policies"][account] = policy

    identity.update_registry(change)


def _wait(predicate: Any) -> None:
    for _ in range(200):
        if predicate():
            return
        asyncio.run(asyncio.sleep(0.01))


# ── 1. sampling ─────────────────────────────────────────────────────────


def _sampling_card(team: Any, thread: str, who: str = "reem") -> dict[str, Any]:
    from kazma_core.mcp import spec_client

    card = {"type": "hitl_approval", "kind": "mcp_sampling", "tool": "mcp_sampling",
            "tool_name": "mcp_sampling", "thread_id": thread,
            "args": {"server": "notes", "preview": "hello"}, "message": "sample?"}
    with bind_scope(team.space, team.accounts[who]):
        card.update(spec_client._sampling_card_who())
    return card


class _Sampling:
    """A sampling request waiting on *thread* (``spec_client``'s own table)."""

    def __init__(self, card: dict[str, Any]) -> None:
        from kazma_core.mcp import spec_client

        self.table = spec_client._sampling_pending
        self.loop = asyncio.new_event_loop()
        self.future = self.loop.create_future()
        self.thread = card["thread_id"]
        self.table[self.thread] = {"payload": card, "future": self.future, "thread_id": self.thread}

    def __enter__(self) -> _Sampling:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.table.pop(self.thread, None)
        self.loop.close()


def test_a_sampling_card_names_its_asker(team) -> None:
    card = _sampling_card(team, "t-sampling")
    assert card["asker"] == team.accounts["reem"] and card["space_id"] == team.space
    assert card["answered_by"] == [team.accounts["reem"]]


def _sampling_answered_by_omar(team: Any) -> tuple[Any, bool]:
    with chat_paused(team) as world, _Sampling(_sampling_card(team, world.thread)) as waiting:
        got = world.client.post(f"/api/approve/{world.thread}", json={"action": "approve"},
                                headers=_as(team, "omar"))
        decided = waiting.future.done()
        if not decided:
            mine = world.client.post(f"/api/approve/{world.thread}", json={"action": "approve"},
                                     headers=_as(team, "reem"))
            assert mine.status_code == 200 and mine.json().get("sampling") is True, mine.text
            assert waiting.future.result() is True
        return got, decided


def test_a_sampling_request_is_answered_only_by_its_asker(team) -> None:
    got, decided = _sampling_answered_by_omar(team)
    assert got.status_code == 403 and got.json()["code"] == "not_yours", got.text
    assert not decided


def test_control_the_sampling_request_resolved_before_the_rule(team, monkeypatch) -> None:
    """Today's route resolved the request before asking who answers it."""
    from kazma_ui import hitl_answerers

    monkeypatch.setattr(hitl_answerers, "refusal_for_card", lambda *_a, **_k: (None, None))
    got, decided = _sampling_answered_by_omar(team)
    assert got.status_code == 200 and decided, got.text


def _sampling_listed(team: Any, monkeypatch: Any) -> tuple[list[Any], list[Any]]:
    from kazma_ui.routes_direct import misc

    monkeypatch.setattr(misc, "_sampling_shown_here", lambda: True)
    with chat_paused(team) as world, _Sampling(_sampling_card(team, world.thread)):
        def kinds(who: str) -> list[Any]:
            got = world.client.get("/api/pending-approvals", headers=_as(team, who))
            assert got.status_code == 200, got.text
            return [item.get("kind") for item in got.json()["pending"]]

        return kinds("omar"), kinds("reem")


def test_the_pending_list_keeps_a_sampling_request_to_its_asker(team, monkeypatch) -> None:
    omars, reems = _sampling_listed(team, monkeypatch)
    assert "mcp_sampling" not in omars and "mcp_sampling" in reems, (omars, reems)


def test_control_the_process_wide_sampling_list(team, monkeypatch) -> None:
    from kazma_ui import hitl_answerers

    monkeypatch.setattr(hitl_answerers, "cards_of_this_space", lambda items, **_k: list(items))
    omars, _reems = _sampling_listed(team, monkeypatch)
    assert "mcp_sampling" in omars


# ── 2. whose card comes from the live interrupt; twins of two people ────


def _register(card: dict[str, Any], session: str = "") -> None:
    from kazma_ui.hitl_gate_bridge import gate_pending_from_payload

    asyncio.run(gate_pending_from_payload(card, session_id=session))


def _twins(team: Any, tmp_path: Path) -> Any:
    """Omar's identical card on the chat first (an older pause, left
    pending), then Reem's: the row her interrupt's id finds."""
    from kazma_core.safety import hitl_gates

    hitl_gates.set_db_path_for_tests(str(tmp_path / "hitl_gates.db"))
    try:
        thread = f"t{secrets.token_hex(6)}"
        omars = {**_card(team.space, team.accounts["omar"]), "thread_id": thread, "interrupt_id": "i-omar"}
        reems = {**_card(team.space, team.accounts["reem"]), "thread_id": thread, "interrupt_id": "i-reem"}
        _register(omars)
        _register(reems)
        return hitl_gates.gate_for("i-reem")
    finally:
        hitl_gates.set_db_path_for_tests(None)


def test_two_peoples_identical_cards_are_two_gates(team, tmp_path) -> None:
    row = _twins(team, tmp_path)
    assert row is not None and row.asker == team.accounts["reem"], row


def test_control_twins_merged_across_people(team, tmp_path, monkeypatch) -> None:
    """Today's lookup: Reem's pause was recorded on Omar's row."""
    from kazma_core.safety import hitl_gates

    monkeypatch.setattr(hitl_gates, "_askers_differ", lambda *_a: False)
    row = _twins(team, tmp_path)
    assert row is None or row.asker == team.accounts["omar"], row


def _omar_names_his_own_row(team: Any) -> tuple[Any, str]:
    """Reem's card is live; Omar's row (another card of his on the chat)
    is named by Omar's request."""
    with chat_paused(team) as world:
        with bind_scope(team.space, team.accounts["omar"]):
            omars = {**_card(team.space, team.accounts["omar"], "file_read"),
                     "thread_id": world.thread, "interrupt_id": "i-omar-own"}
        _register(omars, world.session)
        got = world.client.post(f"/api/approve/{world.thread}",
                                json={"action": "approve", "interrupt_id": "i-omar-own"},
                                headers=_as(team, "omar"))
        return got, _state(world.gates, world.gate)


def test_a_request_naming_another_row_is_judged_by_the_live_card(team) -> None:
    got, reems_gate = _omar_names_his_own_row(team)
    assert got.status_code == 403 and got.json()["code"] == "not_yours", got.text
    assert reems_gate == "pending"


def _row_first(thread_id: str, gate_id: str = "", payload: dict | None = None, *, row: Any = None) -> Any:
    """Today's reading: the row's asker first, the row the request named."""
    from kazma_ui import hitl_answerers

    found = row if row is not None else hitl_answerers._row(gate_id)
    whose = _whose_card_new(thread_id, gate_id, payload, row=row)
    asker = str(getattr(found, "asker", "") or "") or whose.asker
    return hitl_answerers.GateWhose(whose.space_id, asker, whose.tools)


def test_control_a_row_first_reading_lets_the_named_row_decide(team, monkeypatch) -> None:
    """With the row first and the body's id (today's route), Omar's own row
    made Reem's card his: the check let him through."""
    from kazma_ui import hitl_answerers

    monkeypatch.setattr(hitl_answerers, "whose_card", _row_first)
    with chat_paused(team) as world:
        with bind_scope(team.space, team.accounts["omar"]):
            omars = {**_card(team.space, team.accounts["omar"], "file_read"),
                     "thread_id": world.thread, "interrupt_id": "i-omar-own"}
        _register(omars, world.session)
        refusal, whose = hitl_answerers.refusal_for_card(
            world.thread, "i-omar-own", world.card, caller=team.accounts["omar"])
    assert refusal is None and whose.asker == team.accounts["omar"]


def test_the_watchdog_resumes_as_the_live_cards_asker(team) -> None:
    resumed = _watchdog_after_a_row_says_omar(team)
    assert resumed == [(team.space, team.accounts["reem"])]


def test_control_a_row_first_watchdog_resumes_as_the_row(team, monkeypatch) -> None:
    from kazma_ui import hitl_answerers

    monkeypatch.setattr(hitl_answerers, "whose_card", _row_first)
    resumed = _watchdog_after_a_row_says_omar(team)
    assert resumed == [(team.space, team.accounts["omar"])]


def _watchdog_after_a_row_says_omar(team: Any) -> list[Any]:
    """Reem's live card, whose registry row (an older merge) says Omar: the
    timeout watchdog's resume, as whom it ran."""
    from kazma_ui import hitl_timeout

    with chat_paused(team) as world:
        conn = sqlite3.connect(world.gates._db_path_override)
        try:
            conn.execute("UPDATE hitl_gates SET asker = ? WHERE gate_id = ?",
                         (team.accounts["omar"], world.gate))
            conn.commit()
        finally:
            conn.close()
        graph = world.hitl_state["graph"]
        asyncio.run(hitl_timeout._auto_deny(graph, world.thread, 300.0))
        _wait(lambda: world.resumed)
        return list(world.resumed)


# ── 3. a semantic card's tools ──────────────────────────────────────────


@pytest.fixture
def project_team(team, tmp_path):
    """Reem holds Development in a shared space with a project folder."""
    folder = tmp_path / "project"
    folder.mkdir()
    space = people.create_shared_space("Dev", kind="host", project_folder=str(folder),
                                       by=OWNER_ACCOUNT_ID)["id"]
    for account in team.accounts.values():
        people.add_member(space, account, by=OWNER_ACCOUNT_ID)
    people.set_features(team.accounts["reem"], {"development": True}, by=OWNER_ACCOUNT_ID)
    principals = {**team.principals, "admin": {"source": "secret", "role": "admin", "username": "owner"}}
    accounts = {**team.accounts, "admin": OWNER_ACCOUNT_ID}
    return SimpleNamespace(space=space, principals=principals, accounts=accounts)


def _semantic_card(asker: str, tool: str) -> dict[str, Any]:
    return {"type": "hitl_approval", "kind": "semantic_clarify", "asker": asker,
            "items": [{"tool_call_id": "c1", "tool": tool, "question": "which folder?", "options": []}]}


def test_a_semantic_cards_tools_follow_n21(project_team) -> None:
    from kazma_ui.hitl_answerers import refusal_for_card

    reem = project_team.accounts["reem"]
    thread = f"t{secrets.token_hex(6)}"
    threads.record_thread(thread, project_team.space, reem)
    card = _semantic_card(reem, "request_path_access")
    assert refusal_for_card(thread, "", card, caller=reem)[0] == approvals.NOT_YOURS
    assert refusal_for_card(thread, "", card, caller=OWNER_ACCOUNT_ID)[0] is None


def test_control_a_semantic_card_read_without_its_items(project_team) -> None:
    """Today's reading took no tool from the items: Reem answered it."""
    from kazma_ui.hitl_answerers import refusal_for_card

    reem = project_team.accounts["reem"]
    thread = f"t{secrets.token_hex(6)}"
    threads.record_thread(thread, project_team.space, reem)
    card = {key: value for key, value in _semantic_card(reem, "request_path_access").items()
            if key != "items"}
    assert refusal_for_card(thread, "", card, caller=reem)[0] is None


# ── 4. N4: no standing approval for oneself, the button, one's own task ─


def _skips_own_cards(team: Any) -> dict[str, Any]:
    from kazma_core.agent.capacity_commands import apply_capacity_command
    from kazma_core.agent.long_task import long_task_status
    from kazma_core.safety.hitl_grants import grant_tool
    from kazma_core.safety.task_grants import grant_task
    from kazma_core.safety.yolo import YoloDisabledError, enable_yolo, yolo_status

    thread = f"t{secrets.token_hex(6)}"
    out: dict[str, Any] = {}
    with bind_scope(team.space, team.accounts["reem"]):
        try:
            enable_yolo(thread, actor="reem", force=True)
            out["yolo"] = "on"
        except YoloDisabledError as refused:
            out["yolo"] = str(refused)
        out["tool"] = grant_tool(thread, "file_write", actor="reem")
        out["task"] = grant_task(thread, actor="reem")
        out["unrestricted"] = apply_capacity_command(thread, "/unrestricted", actor="reem").reply
        out["long"] = long_task_status(thread).get("active")
        out["yolo_after"] = yolo_status(thread).get("active")
    # Omar, her approver, grants for her from the card he answered.
    with bind_scope(team.space, team.accounts["omar"]):
        out["approver"] = grant_tool(thread, "file_write", actor="omar", person=team.accounts["reem"])
    return out


def test_a_person_whose_cards_go_to_approvers_cannot_skip_them(team) -> None:
    _set_approvers(team.accounts["reem"], [team.accounts["omar"]])
    got = _skips_own_cards(team)
    assert "approve for you" in got["yolo"], got
    assert got["tool"]["error"] == approvals.APPROVERS_DECIDE and not got["tool"]["active"], got
    assert got["task"]["error"] == approvals.APPROVERS_DECIDE, got
    assert "not available" in got["unrestricted"] and not got["long"] and not got["yolo_after"], got
    assert got["approver"]["active"], got


def test_without_approvers_a_person_still_skips_their_own(team) -> None:
    got = _skips_own_cards(team)
    assert got["yolo"] == "on" and got["tool"]["active"] and got["task"]["active"], got


def test_control_no_grant_rule_lets_them_turn_their_cards_off(team, monkeypatch) -> None:
    _set_approvers(team.accounts["reem"], [team.accounts["omar"]])
    monkeypatch.setattr(approvals, "standing_grant_refusal", lambda *_a, **_k: None)
    got = _skips_own_cards(team)
    assert got["yolo"] == "on" and got["tool"]["active"] and got["task"]["active"], got


def _button_decides(monkeypatch: Any) -> bool:
    from kazma_core.swarm import shared_approvals

    monkeypatch.setattr(shared_approvals, "whose", lambda _rid: (OWNER_SPACE_ID, OWNER_ACCOUNT_ID))
    monkeypatch.setattr(shared_approvals, "get", lambda _rid: {"tool": "file_write"})
    return shared_approvals._a_chat_button_may_decide("req-1")


def test_a_chat_button_follows_the_one_rule(team, monkeypatch) -> None:
    assert _button_decides(monkeypatch)
    _set_approvers(OWNER_ACCOUNT_ID, [team.accounts["reem"]])
    assert not _button_decides(monkeypatch), "the owner's approvers answer the owner's request"


def test_control_the_owners_chat_apps_alone(team, monkeypatch) -> None:
    """Today's button asked only whose chat apps they are (O1)."""
    import kazma_core.swarm.single_operator as single_operator

    _set_approvers(OWNER_ACCOUNT_ID, [team.accounts["reem"]])
    monkeypatch.setattr(single_operator, "answered_by_caller", lambda *_a, **_k: True)
    assert _button_decides(monkeypatch)


def test_ones_own_task_is_cancelled_by_them_answered_by_approvers(team) -> None:
    from kazma_ui.swarm_panel.task_actions import caller_may_act_on

    reem, omar = team.accounts["reem"], team.accounts["omar"]
    _set_approvers(reem, [omar])
    task = {"metadata": {"tenant_id": team.space, "asker": reem}}
    with bind_scope(team.space, reem):
        assert caller_may_act_on(task, answering=False)
        assert not caller_may_act_on(task, answering=True)
    with bind_scope(team.space, omar):
        assert not caller_may_act_on(task, answering=False)
        assert caller_may_act_on(task, answering=True)


def test_control_the_approvers_rule_took_ones_own_task(team) -> None:
    """Today Cancel and Retry asked who answers: Reem lost her own task."""
    from kazma_core.swarm.single_operator import task_answered_by_caller

    reem = team.accounts["reem"]
    _set_approvers(reem, [team.accounts["omar"]])
    with bind_scope(team.space, reem):
        assert not task_answered_by_caller({"metadata": {"tenant_id": team.space, "asker": reem}})


# ── 5. the watchdog: nothing claimed on a failed read; a departed asker ─


def test_the_watchdog_claims_nothing_when_whose_card_cannot_be_read(team, monkeypatch) -> None:
    from kazma_ui import hitl_timeout

    with chat_paused(team) as world:
        def unreadable() -> dict[str, Any]:
            raise identity.IdentityUnavailable("the identity store could not be read")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(identity, "read_registry", unreadable)
            asyncio.run(hitl_timeout._auto_deny(world.hitl_state["graph"], world.thread, 300.0))
        assert _state(world.gates, world.gate) == "pending" and world.resumed == []
        # Readable again: the next pass times it out and resumes it as Reem.
        asyncio.run(hitl_timeout._auto_deny(world.hitl_state["graph"], world.thread, 300.0))
        _wait(lambda: world.resumed)
        assert world.resumed == [(team.space, team.accounts["reem"])]
        assert _state(world.gates, world.gate) != "pending"


def test_control_recorded_before_the_read_the_gate_is_left_claimed(team, monkeypatch) -> None:
    """Today's order: the timeout recorded, then the read that fails -- the
    gate is no longer pending and nothing resumes it."""
    from kazma_ui.hitl_decision import record_gate_decision

    with chat_paused(team) as world:
        asyncio.run(record_gate_decision(world.thread, decision="timeout", actor="timeout",
                                         payload=world.card, interrupt_id=world.gate,
                                         require_durable=True))
        assert _state(world.gates, world.gate) != "pending" and world.resumed == []


class _ClosableGraph:
    """A paused checkpoint that takes the turn's written end."""

    nodes = {"respond": object(), "supervisor": object()}
    checkpointer = object()

    def __init__(self, paused: Any) -> None:
        self.snapshot = paused.snapshot
        self.written: list[dict[str, Any]] = []

    async def aget_state(self, _config: Any) -> Any:
        return self.snapshot

    async def aupdate_state(self, _config: Any, update: dict[str, Any], as_node: str = "") -> None:
        self.written.append(update)
        self.snapshot = SimpleNamespace(next=(), tasks=[], values={**self.snapshot.values, **update})


def _departed(team: Any) -> tuple[list[Any], list[dict[str, Any]], str]:
    from kazma_ui import hitl_timeout

    with chat_paused(team) as world:
        reem = team.accounts["reem"]

        def leave(reg: dict[str, Any]) -> None:
            reg["memberships"] = [m for m in reg["memberships"]
                                  if not (m.get("account") == reem and m.get("space") == team.space)]

        identity.update_registry(leave)
        graph = _ClosableGraph(world.hitl_state["graph"])
        asyncio.run(hitl_timeout._auto_deny(graph, world.thread, 300.0))
        _wait(lambda: world.resumed or graph.written)
        return list(world.resumed), graph.written, _state(world.gates, world.gate)


def test_a_departed_askers_card_closes_their_turn(team) -> None:
    resumed, written, gate = _departed(team)
    assert resumed == [], "a departed person's turn is resumed as nobody"
    [end] = written
    text = end["messages"][-1]["content"]
    assert "reem-s205" in text and "no longer a member" in text and end["turn_failed"], text
    assert gate == "settled"


def test_control_a_departed_askers_turn_resumed(team, monkeypatch) -> None:
    """Today's watchdog resumed it, as the person who left."""
    monkeypatch.setattr(identity, "asker_left", lambda *_a: False)
    resumed, written, _gate = _departed(team)
    assert resumed == [(team.space, team.accounts["reem"])] and written == []


# ── 8. another member's new message never drops Reem's card ─────────────


def _waiting_for(team: Any, who: str, *, rule: Any = None) -> Any:
    from kazma_ui import hitl_answerers
    from kazma_ui.sse_chat import _card_waits_on_someone_else

    with chat_paused(team) as world, bind_scope(team.space, team.accounts[who]):
        with pytest.MonkeyPatch.context() as patch:
            if rule is not None:
                patch.setattr(hitl_answerers, "waiting_for_someone_else", rule)
            return asyncio.run(_card_waits_on_someone_else(world.thread, world.hitl_state["graph"]))


def test_another_members_message_is_refused_while_her_card_waits(team) -> None:
    from kazma_ui.sse_chat import _waiting_text

    assert _waiting_for(team, "omar") == "reem-s205"
    assert _waiting_for(team, "reem") is None, "her own message goes on as before"
    assert "reem-s205" in _waiting_text("Can you check this?", "reem-s205")
    assert "reem-s205" in _waiting_text("هل يمكنك؟", "reem-s205") and "موافقة" in _waiting_text("هل؟", "reem-s205")


def test_control_any_message_started_a_new_turn(team) -> None:
    assert _waiting_for(team, "omar", rule=lambda *_a, **_k: None) is None


def test_a_voice_utterance_is_refused_too(team) -> None:
    from kazma_ui.voice_turn import run_voice_user_turn

    with chat_paused(team) as world, bind_scope(team.space, team.accounts["omar"]):
        result = asyncio.run(run_voice_user_turn(
            graph=world.hitl_state["graph"], session=None, thread_id=world.thread,
            session_id=world.session, user_text="hello"))
    assert result.error and "reem-s205" in result.error, result


def test_the_stream_route_refuses_another_members_message(team) -> None:
    """Through the real chat stream route: Omar's message is refused with the
    line, and nothing of it is saved."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth
    from kazma_ui.session_manager import get_session_manager
    from kazma_ui.sse_chat import create_sse_chat_router

    with chat_paused(team) as world:
        graph = world.hitl_state["graph"]
        app = FastAPI()
        app.middleware("http")(auth.create_tenant_middleware())
        app.include_router(create_sse_chat_router(graph_getter=lambda: graph))
        with TestClient(app) as client:
            got = client.post("/api/chat/stream", json={"message": "and also this", "session_id": world.session},
                              headers=_as(team, "omar"))
        assert got.status_code == 200, got.text
        assert "reem-s205" in got.text and "event: error" in got.text, got.text
        with bind_scope(team.space, team.accounts["reem"]):
            saved = get_session_manager().get(world.session)
        assert all("and also this" not in str(m.get("content")) for m in saved.messages)


# ── 9. the long-task record is each person's ────────────────────────────


def _long_seen_by_omar(team: Any) -> bool:
    from kazma_core.agent.long_task import enable_long_task, long_task_status

    thread = f"t{secrets.token_hex(6)}"
    with bind_scope(team.space, team.accounts["reem"]):
        assert enable_long_task(thread, actor="reem", mode="mission")["active"]
    with bind_scope(team.space, team.accounts["omar"]):
        return bool(long_task_status(thread).get("active"))


def test_one_members_long_task_is_theirs_alone(team) -> None:
    assert not _long_seen_by_omar(team)


def test_control_a_long_task_kept_per_chat(team, monkeypatch) -> None:
    from kazma_core.safety import grant_holder

    monkeypatch.setattr(grant_holder, "holder", lambda thread_id, person=None: str(thread_id or ""))
    assert _long_seen_by_omar(team)


def test_with_spaces_off_the_long_task_key_is_the_chats(team, monkeypatch) -> None:
    from kazma_core.agent.long_task import _record_key

    monkeypatch.setattr(scope, "_enforced", False)
    assert _record_key("t-1") == "long_task.t-1"


# ── the tests the review found missing ─────────────────────────────────


def _bridge_gate(team: Any, world: Any) -> str:
    from kazma_core.safety.hitl_gates import GateRow, register_gate

    thread = f"bridge-{secrets.token_hex(4)}"
    with bind_scope(team.space, team.accounts["reem"]):
        register_gate(GateRow(gate_id=f"g-{thread}", thread_id=thread, tenant_id=team.space,
                              mechanism="bus_bridge", tool="file_write", args_json='{"path": "b.txt"}',
                              asker=team.accounts["reem"]), ttl_seconds=60)
    return thread


def test_the_bridge_gate_is_answered_only_by_its_asker(team) -> None:
    with chat_paused(team) as world:
        thread = _bridge_gate(team, world)
        omars = world.client.post(f"/api/approve/{thread}", json={"action": "approve"},
                                  headers=_as(team, "omar"))
        assert omars.status_code == 403 and omars.json()["code"] == "not_yours", omars.text
        assert _state(world.gates, f"g-{thread}") == "pending"
        reems = world.client.post(f"/api/approve/{thread}", json={"action": "approve"},
                                  headers=_as(team, "reem"))
        assert reems.status_code == 200 and reems.json().get("bridge") is True, reems.text


def test_control_the_bridge_gate_without_the_rule(team, monkeypatch) -> None:
    from kazma_ui import hitl_answerers

    monkeypatch.setattr(hitl_answerers, "refusal_for_card", lambda *_a, **_k: (None, None))
    with chat_paused(team) as world:
        thread = _bridge_gate(team, world)
        omars = world.client.post(f"/api/approve/{thread}", json={"action": "approve"},
                                  headers=_as(team, "omar"))
        assert omars.status_code == 200, omars.text


def test_an_unreadable_identity_store_answers_503_and_the_card_waits(team, monkeypatch) -> None:
    with chat_paused(team) as world:
        def unreadable() -> dict[str, Any]:
            raise identity.IdentityUnavailable("the identity store could not be read")

        monkeypatch.setattr(identity, "read_registry", unreadable)
        got = _approve(world, team, "reem")
        monkeypatch.undo()
        assert got.status_code == 503 and got.json()["code"] == "identity_unavailable", got.text
        assert _state(world.gates, world.gate) == "pending" and world.resumed == []


def test_a_card_frame_pushes_to_the_people_it_asks(team, monkeypatch) -> None:
    """``delivery.py``: an ``approval_required`` frame naming who answers it
    is pushed to them; a card naming no one (Spaces off) is not."""
    from kazma_ui import push
    from kazma_ui.delivery import TurnBroker

    pushed: list[dict[str, Any]] = []

    async def record(card: dict[str, Any]) -> None:
        pushed.append(card)

    monkeypatch.setattr(push, "notify_push_approval", record)

    async def emit(card: dict[str, Any]) -> None:
        broker = TurnBroker()
        await broker.emit("t-push", {"type": "approval_required", "data": card})
        for _ in range(100):
            if pushed:
                return
            await asyncio.sleep(0.01)

    card = _card(team.space, team.accounts["reem"])
    asyncio.run(emit(card))
    assert [c.get("answered_by") for c in pushed] == [[team.accounts["reem"]]]
    pushed.clear()
    asyncio.run(emit({k: v for k, v in card.items() if k not in ("answered_by", "answered_by_names")}))
    assert pushed == []


def test_the_bell_route_shows_a_card_to_whom_it_waits_for(team) -> None:
    with chat_paused(team) as world:
        def ids(who: str) -> list[str]:
            got = world.client.get("/api/alerts/recent", headers=_as(team, who))
            assert got.status_code == 200, got.text
            body = got.json()
            items = body if isinstance(body, list) else body.get("alerts", body.get("items", []))
            return [str(i.get("id")) for i in items if isinstance(i, dict)]

        assert f"approval-{world.gate}" in ids("reem")
        assert f"approval-{world.gate}" not in ids("omar")


def test_n21_through_the_route_the_admin_answers_and_the_holder_resumes(project_team) -> None:
    with chat_paused(project_team, tool="request_path_access") as world:
        assert world.card["answered_by"] == [OWNER_ACCOUNT_ID]
        holders = _approve(world, project_team, "reem")
        assert holders.status_code == 403 and holders.json()["code"] == "not_yours", holders.text
        admins = _approve(world, project_team, "admin")
        assert admins.status_code == 200, admins.text
        _wait(lambda: world.resumed)
    assert world.resumed == [(project_team.space, project_team.accounts["reem"])]


def test_the_gateways_hitl_decision_asks_the_one_rule(team, monkeypatch) -> None:
    """A chat app's ``/hitl approve`` (the gateway, the owner's until S3-04)
    goes through the one decision writer: with the owner's approvers set it
    is refused, said in the chat, and the gate stays pending."""
    sent, gate = _gateway_decides(team, monkeypatch)
    assert any("not yours" in text for text in sent), sent
    assert gate == "pending"


def test_control_the_gateways_decision_without_the_rule(team, monkeypatch) -> None:
    from kazma_ui import hitl_answerers

    monkeypatch.setattr(hitl_answerers, "refusal_for_card", lambda *_a, **_k: (None, None))
    sent, gate = _gateway_decides(team, monkeypatch)
    assert not any("not yours" in text for text in sent) and gate != "pending", (sent, gate)


def _gateway_decides(team: Any, monkeypatch: Any) -> tuple[list[str], str]:
    from unittest.mock import AsyncMock

    from kazma_core.safety import hitl_gates
    from kazma_core.sessions.directory import record_thread_owner
    from kazma_gateway.agent_handler import hitl
    from kazma_gateway.gateway import IncomingMessage

    _set_approvers(OWNER_ACCOUNT_ID, [team.accounts["reem"]])
    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "telegram:42")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        hitl_gates.set_db_path_for_tests(str(Path(tmp) / "hitl_gates.db"))
        try:
            thread = f"telegram-{secrets.token_hex(4)}"
            threads.record_thread(thread, OWNER_SPACE_ID, OWNER_ACCOUNT_ID)
            record_thread_owner(thread, "telegram:42")
            card = {**_card(OWNER_SPACE_ID, OWNER_ACCOUNT_ID), "thread_id": thread, "interrupt_id": "i-gw"}
            _register(card)

            async def pending(*_a: Any) -> dict[str, Any]:
                return card

            monkeypatch.setattr(hitl, "_check_graph_interrupt", pending)
            monkeypatch.setattr(hitl, "_approval_card_is_stale", lambda *_a: False)
            sent: list[str] = []

            async def send(message: Any) -> bool:
                sent.append(str(message.text))
                return True

            manager = SimpleNamespace(send=send)
            store = SimpleNamespace(get=AsyncMock(return_value={}))
            graph = SimpleNamespace(aget_state=AsyncMock(), ainvoke=AsyncMock(), astream=AsyncMock())
            msg = IncomingMessage("telegram", "telegram:42", f"/hitl approve {thread}",
                                  context_metadata={"chat_id": 42})

            async def decide() -> None:
                with bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
                    try:
                        await hitl._handle_hitl_resume(msg, graph, {}, thread, store, manager)
                    except Exception:  # noqa: BLE001 -- the resume after the decision is not under test
                        pass

            asyncio.run(decide())
            return sent, _state(hitl_gates, "i-gw")
        finally:
            hitl_gates.set_db_path_for_tests(None)


def test_with_spaces_off_nothing_here_changes(team, monkeypatch) -> None:
    from kazma_ui.hitl_answerers import cards_of_this_space, waiting_for_someone_else

    monkeypatch.setattr(scope, "_enforced", False)
    items = [{"thread_id": "t", "asker": team.accounts["reem"]}]
    assert cards_of_this_space(items, caller=team.accounts["omar"]) == items
    assert waiting_for_someone_else("t", items[0], caller=team.accounts["omar"]) is None
    with bind_scope(team.space, team.accounts["reem"]):
        _set_approvers(team.accounts["reem"], [team.accounts["omar"]])
        assert approvals.standing_grant_refusal() is None
