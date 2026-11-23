"""Spaces S2-05, the second review: another member never steers, stops,
aborts, clears, compacts or cancels a member's turn.

Reem and Omar share a space (``tests/test_approvals_reach_the_asker.py``'s
world). Reem's turn runs or waits in her chat. Through the real routes:

* ``/api/chat/steer``, soft and hard, running and paused: only Reem steers
  her turn. Omar is refused before anything is saved or queued, with a line
  naming her. Her turn is hers whatever rows follow it: whose turn it is
  reads the message that OPENED the running turn, never a steer's row (the
  newest row was a steer, and another member's steer made the turn theirs).
* ``/api/chat/stop`` and ``/api/chat/abort``: only Reem or an owner of the
  space.
* ``/reset`` and ``/compact``: only the chat's opener or an owner, and
  never while another member's turn runs or waits.
* A new message (and a voice utterance) from Omar while Reem's turn runs is
  refused with a line saying the chat is answering her: it would cancel her
  turn. Reem's own new message supersedes it, as before.

Each with today's code as its control; nothing changes while the install
does not enforce Spaces.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.spaces import identity, scope
from kazma_core.spaces.scope import bind_scope

from tests.test_approvals_reach_the_asker import (  # noqa: F401 -- team is the shared fixture
    _as,
    chat_paused,
    team,
)

pytestmark = pytest.mark.real_features


class _Superseded(Exception):
    """Raised by a running turn when the route waits for it to end after
    cancelling it: the request superseded the turn."""


class _Running:
    """A turn running on the chat (what the stream route registers). The
    stream route cancels it and then waits for it: the second cancel raises
    ``_Superseded``, so a test sees the supersede without running a turn."""

    def __init__(self) -> None:
        self.cancels = 0

    def done(self) -> bool:
        return False

    def cancelled(self) -> bool:
        return False

    def cancel(self) -> bool:
        self.cancels += 1
        if self.cancels > 1:
            raise _Superseded
        return True

    def add_done_callback(self, _fn: Any) -> None:
        return None


class _NotPaused:
    """A checkpoint with nothing waiting."""

    snapshot = SimpleNamespace(next=(), tasks=[], values={})

    async def aget_state(self, _config: Any) -> Any:
        return self.snapshot


def _client(world: Any, graph: Any) -> Any:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth
    from kazma_ui.sse_chat import create_sse_chat_router

    app = FastAPI()
    app.middleware("http")(auth.create_tenant_middleware())
    app.include_router(create_sse_chat_router(graph_getter=lambda: graph))
    return TestClient(app)


def _with_admin(team: Any) -> None:
    team.principals["admin"] = {"source": "secret", "role": "admin", "username": "owner"}


def _messages(team: Any, world: Any) -> list[str]:
    from kazma_ui.session_manager import get_session_manager

    with bind_scope(team.space, team.accounts["reem"]):
        return [str(m.get("content")) for m in get_session_manager().get(world.session).messages]


def _running(world: Any) -> _Running:
    from kazma_ui.active_turns import register_turn

    turn = _Running()
    register_turn(world.thread, turn)
    return turn


@pytest.fixture(autouse=True)
def _no_turns_or_steers_left():
    from kazma_core.agent.steer import _soft_steer_buffers
    from kazma_ui.active_turns import reset_active_turns

    yield
    reset_active_turns()
    _soft_steer_buffers.clear()


def _no_check(monkeypatch: Any) -> None:
    """Today's routes: no one's turn is asked about."""
    from kazma_ui import sse_chat

    async def nobody(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(sse_chat, "_turn_control", nobody)
    monkeypatch.setattr(sse_chat, "_hard_steer_whose", nobody)


# ── whose turn it is: the message that opened it ────────────────────────


def _newest_user_row(rows: list[dict[str, Any]]) -> str:
    """Today's rule (the control): the newest user row's author."""
    for row in reversed(rows):
        if row.get("role") == "user":
            return str(row.get("author") or "")
    return ""


def _author_after(team: Any, rows: list[dict[str, Any]], rule: Any = None) -> str:
    import secrets

    from kazma_core.spaces import threads
    from kazma_ui import hitl_answerers
    from kazma_ui.session_manager import get_session_manager

    reem = team.accounts["reem"]
    thread, session_id = f"t{secrets.token_hex(6)}", f"s{secrets.token_hex(6)}"
    with bind_scope(team.space, reem):
        threads.record_thread(thread, team.space, reem)
        store = get_session_manager()
        session = store.get_or_create(session_id, thread_id=thread)
        session.messages.extend(rows)
        store.put(session)
    if rule is not None:
        session = SimpleNamespace(messages=rows)
        return rule(session.messages)
    return hitl_answerers.whose_turn(thread).asker


def _rows(team: Any) -> list[dict[str, Any]]:
    reem, omar = team.accounts["reem"], team.accounts["omar"]
    return [
        {"role": "user", "content": "an earlier question", "author": omar},
        {"role": "assistant", "content": "done", "turn_id": "T0", "lifecycle": "closed"},
        {"role": "user", "content": "write my notes", "author": reem},
        {"role": "assistant", "content": "", "turn_id": "T1", "lifecycle": "open"},
        {"role": "user", "content": "/steer! also the title", "author": omar},  # a steer from before the flag
        {"role": "user", "content": "/steer use bullets", "author": omar, "steer": True},
    ]


def test_the_turns_asker_wrote_the_message_that_opened_it(team) -> None:
    assert _author_after(team, _rows(team)) == team.accounts["reem"]


def test_control_the_newest_row_made_the_turn_the_steerers(team) -> None:
    assert _author_after(team, _rows(team), rule=_newest_user_row) == team.accounts["omar"]


# ── /api/chat/steer: soft and hard, running and paused ──────────────────


def _steer(team: Any, who: str, mode: str, *, paused: bool, monkeypatch: Any = None,
           check: bool = True) -> tuple[dict[str, Any], list[str], Any]:
    from kazma_core.agent.steer import _soft_steer_buffers

    if not check:
        _no_check(monkeypatch)
    with chat_paused(team) as world:
        graph = world.hitl_state["graph"] if paused else _NotPaused()
        if not paused:
            _running(world)
        with _client(world, graph) as client:
            got = client.post("/api/chat/steer", json={"session_id": world.session, "text": "use bullets",
                                                         "mode": mode}, headers=_as(team, who))
        assert got.status_code == 200, got.text
        return got.json(), _messages(team, world), list(_soft_steer_buffers.get(world.thread) or [])


@pytest.mark.parametrize("mode", ["soft", "hard"])
@pytest.mark.parametrize("paused", [False, True], ids=["running", "paused"])
def test_only_the_turns_asker_steers_it(team, mode: str, paused: bool) -> None:
    refused, saved, queued = _steer(team, "omar", mode, paused=paused)
    assert refused["ok"] is False and refused["reason"] == "not_your_turn", refused
    assert "reem-s205" in refused["message"], refused
    assert not any("use bullets" in m for m in saved), "nothing of the refused steer is saved"
    assert queued == [], "nothing of the refused steer is queued"
    taken, saved, queued = _steer(team, "reem", mode, paused=paused)
    assert taken["ok"] is True, taken
    assert any("use bullets" in m for m in saved) and queued, (saved, queued)


@pytest.mark.parametrize("mode", ["soft", "hard"])
def test_control_another_members_steer_was_taken(team, monkeypatch, mode: str) -> None:
    taken, saved, queued = _steer(team, "omar", mode, paused=False, monkeypatch=monkeypatch, check=False)
    assert taken["ok"] is True and any("use bullets" in m for m in saved) and queued, (taken, saved)


def test_a_steer_is_refused_when_whose_turn_cannot_be_read(team) -> None:
    from kazma_ui import hitl_answerers
    from kazma_ui.sse_chat import _hard_steer_whose

    def away(_thread_id: str) -> Any:
        raise identity.IdentityUnavailable("the identity store is away")

    with chat_paused(team) as world:
        with bind_scope(team.space, team.accounts["reem"]), pytest.MonkeyPatch.context() as patch:
            patch.setattr(hitl_answerers, "whose_turn", away)
            refused = asyncio.run(_hard_steer_whose(world.thread, "note"))
    assert refused["ok"] is False and refused["reason"] == "identity_unavailable", refused
    assert "could not check" in refused["message"]


def test_a_known_asker_whose_name_cannot_be_read_still_refuses_another(team) -> None:
    from kazma_ui.sse_chat import _hard_steer_whose

    def away() -> dict[str, Any]:
        raise identity.IdentityUnavailable("the identity store is away")

    with chat_paused(team) as world:
        with bind_scope(team.space, team.accounts["omar"]), pytest.MonkeyPatch.context() as patch:
            patch.setattr(identity, "read_registry", away)
            refused = asyncio.run(_hard_steer_whose(world.thread, "note"))
    assert refused["reason"] == "not_your_turn" and "another member" in refused["message"], refused


# ── /api/chat/stop and /api/chat/abort ──────────────────────────────────


def _stop(team: Any, who: str, route: str, monkeypatch: Any = None, check: bool = True) -> tuple[dict, _Running, list]:
    from kazma_ui import turn_abort

    if not check:
        _no_check(monkeypatch)
    _with_admin(team)
    aborted: list[str] = []

    async def _abort(thread_id: str, *_a: Any, **_k: Any) -> dict[str, Any]:
        aborted.append(thread_id)
        return {"ok": True}

    with chat_paused(team) as world, pytest.MonkeyPatch.context() as patch:
        patch.setattr(turn_abort, "abort_turn", _abort)
        turn = _running(world)
        with _client(world, _NotPaused()) as client:
            got = client.post(route, json={"session_id": world.session}, headers=_as(team, who))
        assert got.status_code == 200, got.text
        return got.json(), turn, aborted


def test_only_the_asker_or_an_owner_stops_the_turn(team) -> None:
    refused, turn, _ = _stop(team, "omar", "/api/chat/stop")
    assert refused["cancelled"] is False and refused["refused"] is True, refused
    assert "reem-s205" in refused["message"] and turn.cancels == 0
    for who in ("reem", "admin"):
        stopped, turn, _ = _stop(team, who, "/api/chat/stop")
        assert stopped["cancelled"] is True and turn.cancels == 1, (who, stopped)


def test_only_the_asker_or_an_owner_aborts_the_turn(team) -> None:
    refused, _turn, aborted = _stop(team, "omar", "/api/chat/abort")
    assert refused["ok"] is False and refused["reason"] == "not_your_turn" and aborted == [], refused
    assert "reem-s205" in refused["message"]
    for who in ("reem", "admin"):
        done, _turn, aborted = _stop(team, who, "/api/chat/abort")
        assert done == {"ok": True} and aborted, (who, done)


def test_control_another_member_stopped_and_aborted_the_turn(team, monkeypatch) -> None:
    stopped, turn, _ = _stop(team, "omar", "/api/chat/stop", monkeypatch, check=False)
    assert stopped["cancelled"] is True and turn.cancels == 1
    done, _turn, aborted = _stop(team, "omar", "/api/chat/abort", monkeypatch, check=False)
    assert done == {"ok": True} and aborted


# ── /reset and /compact ─────────────────────────────────────────────────


def _clear(team: Any, who: str, command: str, *, paused: bool, monkeypatch: Any = None,
           check: bool = True) -> tuple[str, list[str]]:
    if not check:
        _no_check(monkeypatch)
    _with_admin(team)
    with chat_paused(team) as world:
        graph = world.hitl_state["graph"] if paused else _NotPaused()
        with _client(world, graph) as client:
            got = client.post("/api/chat/stream", json={"message": command, "session_id": world.session},
                              headers=_as(team, who))
        assert got.status_code == 200, got.text
        return got.text, _messages(team, world)


@pytest.mark.parametrize("command", ["/reset", "/compact"])
def test_another_member_never_clears_or_compacts_while_her_card_waits(team, command: str) -> None:
    text, kept = _clear(team, "omar", command, paused=True)
    assert "event: error" in text and "reem-s205" in text and "busy" in text, text
    assert kept == ["write my notes"]


@pytest.mark.parametrize("command", ["/reset", "/compact"])
def test_only_the_chats_opener_or_an_owner_clears_it(team, command: str) -> None:
    text, kept = _clear(team, "omar", command, paused=False)
    assert "event: error" in text and "reem-s205" in text and "started this chat" in text, text
    assert kept == ["write my notes"]
    for who in ("reem", "admin"):
        text, _kept = _clear(team, who, command, paused=False)
        assert "started this chat" not in text and "busy" not in text, (who, text)


def test_the_owner_never_clears_while_a_members_card_waits(team) -> None:
    text, kept = _clear(team, "admin", "/reset", paused=True)
    assert "reem-s205" in text and kept == ["write my notes"], text


def test_control_another_member_cleared_the_chat(team, monkeypatch) -> None:
    text, kept = _clear(team, "omar", "/reset", paused=True, monkeypatch=monkeypatch, check=False)
    assert "Conversation cleared" in text and kept == [], text


# ── a new message while another member's turn runs ──────────────────────


def _send(team: Any, who: str, monkeypatch: Any, *, check: bool = True) -> tuple[Any, _Running, list[str]]:
    from kazma_ui import sse_chat

    if not check:
        _no_check(monkeypatch)
    # The supersede comes after the provider's key check; this world has none.
    monkeypatch.setattr(sse_chat, "_key_is_usable", lambda _key: True)
    with chat_paused(team) as world:
        turn = _running(world)
        with _client(world, _NotPaused()) as client:
            try:
                got: Any = client.post("/api/chat/stream", json={"message": "and also this",
                                                                  "session_id": world.session},
                                       headers=_as(team, who))
            except _Superseded:
                got = "superseded"
        return got, turn, _messages(team, world)


def test_another_members_message_never_cancels_her_running_turn(team, monkeypatch) -> None:
    got, turn, saved = _send(team, "omar", monkeypatch)
    assert got != "superseded" and turn.cancels == 0
    assert "event: error" in got.text and "answering reem-s205" in got.text, got.text
    assert all("and also this" not in m for m in saved)


def test_her_own_new_message_supersedes_as_before(team, monkeypatch) -> None:
    got, turn, _saved = _send(team, "reem", monkeypatch)
    assert got == "superseded" and turn.cancels >= 1


def test_control_another_members_message_cancelled_her_turn(team, monkeypatch) -> None:
    got, turn, _saved = _send(team, "omar", monkeypatch, check=False)
    assert got == "superseded" and turn.cancels >= 1


def test_a_voice_utterance_never_cancels_her_running_turn(team) -> None:
    from kazma_ui.voice_turn import run_voice_user_turn

    with chat_paused(team) as world, bind_scope(team.space, team.accounts["omar"]):
        turn = _running(world)
        result = asyncio.run(run_voice_user_turn(graph=_NotPaused(), session=None, thread_id=world.thread,
                                                 session_id=world.session, user_text="and also this"))
    assert result.error and "reem-s205" in result.error and turn.cancels == 0, result


# ── nothing changes while Spaces is off ─────────────────────────────────


def test_with_spaces_off_anyone_stops_as_before(team, monkeypatch) -> None:
    from kazma_ui import hitl_answerers

    def asked(*_a: Any, **_k: Any) -> None:
        raise AssertionError("whose turn is not read while Spaces is off")

    monkeypatch.setattr(hitl_answerers, "turn_control_refusal", asked)
    with chat_paused(team) as world:
        turn = _running(world)
        monkeypatch.setattr(scope, "_enforced", False)
        with _client(world, _NotPaused()) as client:
            got = client.post("/api/chat/stop", json={"session_id": world.session}, headers=_as(team, "omar"))
    assert got.json()["cancelled"] is True and turn.cancels == 1
