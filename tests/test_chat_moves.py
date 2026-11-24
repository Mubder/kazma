"""Spaces S2-12: moving one's own chat between one's own space and a shared
one, through the real routes behind the real tenant middleware, on a real
LangGraph graph and the product's checkpoint manager.

A move is a fork into the other space and the ordinary delete here, under a
new session id and a new thread id. After each move the chat is in exactly
one space; the chat it was is a deleted chat, so a tab left open on it, a
message being sent, a queued write and a saved step name nothing that
exists. Nothing is lost: what lands while the chat is copied makes the move
refuse, and a move cut off at any point is settled -- at start, by the
maintenance sweep and by the next move of the chat -- into exactly one space.

Controls: a "private" mark in the shared space (the design the plan turned
down); a store that does not remember its deletes (an open tab and a message
being sent bring the chat back where it left); a handover that does not
compare (another member's words are lost); a journal no settling reads (a
killed move leaves the chat in two spaces).

What memory does has its own file (``tests/test_chat_move_memory.py``), and
what a deleted chat's late writers do ``tests/test_deleted_chat_stays_deleted.py``.
"""

from __future__ import annotations

import asyncio
import copy
import secrets
import sqlite3
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.config_store import get_config_store
from kazma_core.spaces import chat_moves, identity, people, scope, threads
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
from kazma_ui import auth, session_manager, space_binding

from tests import _tt_graph as tt
from tests._chat_move_space_cases import NoCardGraph


def _principal(account: str) -> dict[str, Any]:
    return {"source": "session", "role": "operator", "user_id": account, "actor": "login"}


class World:
    def __init__(self, client: TestClient, held: dict[str, Any], ids: dict[str, str]) -> None:
        self.client = client
        self.held = held
        self.ids = ids

    def call(self, who: str, method: str, path: str, *, space: str, **kwargs: Any) -> Any:
        headers = {"x-person": who, space_binding.SPACE_HEADER: space}
        return self.client.request(method, path, headers=headers, **kwargs)

    def run(self, space: str, who: str, fn: Callable[[], Any]) -> Any:
        """Run ``await fn()`` on the app's loop, bound to *space*."""
        async def bound() -> Any:
            with scope.bind_scope(space, self.ids[who]):
                return await fn()

        return self.client.portal.call(bound)

    def chat(self, who: str, space: str, *, mark: str = "", authors: tuple[str, ...] = (),
             session_id: str = "", thread_id: str = "") -> tuple[str, str]:
        """A chat *who* opened in *space*: (session id, thread id)."""
        session_id = session_id or f"s{secrets.token_hex(6)}"
        thread_id = thread_id or f"t{secrets.token_hex(6)}"
        account = self.ids[who]
        with scope.bind_scope(space, account):
            store = session_manager.get_session_manager()
            session = store.get_or_create(session_id, thread_id=thread_id)
            for author in authors or (account,):
                session.add_message("user", f"question {mark}", author=author)
                session.add_message("assistant", f"answer {mark}")
            session.title = f"title {mark}"
            store.put(session)
        threads.record_thread(thread_id, space, account)
        return session_id, thread_id

    def turn(self, who: str, space: str, thread: str, text: str) -> None:
        """A turn of the real graph."""
        async def run() -> None:
            await tt.run_turn(self.held["graph"], thread, text)

        self.run(space, who, run)

    def state(self, who: str, space: str, thread: str) -> list[tuple[str, str]]:
        async def read() -> Any:
            return await self.held["graph"].aget_state(tt.config(thread))

        values = self.run(space, who, read).values or {}
        return [(m["role"], m["content"]) for m in values.get("messages") or []]

    def listed(self, who: str, space: str) -> list[str]:
        got = self.call(who, "GET", "/api/chat/sessions", space=space)
        assert got.status_code == 200, got.text
        return [s["session_id"] for s in got.json()]

    def move(self, who: str, space: str, session_id: str, to: str) -> Any:
        return self.call(who, "POST", f"/api/chat/sessions/{session_id}/move", space=space, json={"to_space": to})

    def moved(self, who: str, space: str, session_id: str, to: str) -> str:
        """Move it, and the copy's session id."""
        answer = self.move(who, space, session_id, to)
        assert answer.status_code == 200, answer.text
        assert answer.json()["space"] == to
        return answer.json()["session_id"]

    def row(self, space: str, who: str, session_id: str) -> Any:
        with scope.bind_scope(space, self.ids[who]):
            return session_manager.get_session_manager().get(session_id)

    def restart(self) -> None:
        """What a new process holds: the stored chats, and no memory of the
        deletes."""
        old = session_manager.get_session_manager()
        session_manager.set_session_manager(session_manager.SessionManager(db_path=old.db_path))

    def settle(self) -> list[dict[str, str]]:
        from kazma_ui.chat_move import settle_chat_moves

        return self.client.portal.call(settle_chat_moves, self.held["manager"])

    def nothing_left(self, space: str, who: str, session: str, thread: str) -> None:
        """The deleted chat is nowhere in *space*."""
        assert session not in self.listed(who, space)
        assert threads.record_of(thread) is None, "its thread's record"

        async def rows() -> int:
            saver = await self.held["manager"]._saver_of(space)
            async with saver.conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
                                          (thread,)) as cur:
                return int((await cur.fetchone())[0])

        if not self.held["manager"]._shared_table():
            assert self.client.portal.call(rows) == 0, "its checkpoints"


@pytest.fixture
def world(monkeypatch, tmp_path):
    from kazma_gateway.stores.checkpoint import create_checkpoint_manager
    from kazma_ui import dashboard, live_connections
    from kazma_ui.chat_move import create_chat_move_router
    from kazma_ui.sse_chat import create_sse_chat_router

    monkeypatch.setattr(scope, "_enforced", True)
    monkeypatch.setattr(threads, "_spaces", {})
    monkeypatch.setattr(live_connections, "_GONE", type(live_connections._GONE)())
    get_config_store().delete(identity.REGISTRY_KEY)
    get_config_store().delete(chat_moves.MOVES_KEY)
    reem = people.create_person("Reem", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    omar = people.create_person("Omar", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    family = people.create_shared_space("Family", by=OWNER_ACCOUNT_ID)["id"]
    for person in (reem, omar):
        people.add_member(family, person["id"], by=OWNER_ACCOUNT_ID)
    ids = {"reem": reem["id"], "omar": omar["id"], "mine": reem["personal_space"],
           "omars": omar["personal_space"], "family": family}
    principals = {"reem": _principal(reem["id"]), "omar": _principal(omar["id"])}
    monkeypatch.setattr(auth, "get_request_principal",
                        lambda request: principals.get(request.headers.get("x-person", "")))
    before = session_manager.peek_session_manager()
    session_manager.set_session_manager(session_manager.SessionManager(db_path=str(tmp_path / "chats.db")))
    held: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        @asynccontextmanager
        async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
            held["manager"] = await create_checkpoint_manager(str(Path(tmp) / "checkpoints.db"))
            held["graph"] = tt.build_graph(None, [], held["manager"])
            monkeypatch.setattr(dashboard, "_checkpoint_manager", held["manager"])
            try:
                yield
            finally:
                await held["manager"].close()

        app = FastAPI(lifespan=lifespan)
        app.middleware("http")(auth.create_tenant_middleware())
        app.include_router(create_sse_chat_router(graph_getter=lambda: held["graph"]))
        app.include_router(create_chat_move_router(graph_getter=lambda: held["graph"],
                                                   checkpoints_getter=lambda: held["manager"]))
        try:
            with TestClient(app, backend_options={"loop_factory": asyncio.SelectorEventLoop}) as client:
                yield World(client, held, ids)
        finally:
            session_manager.set_session_manager(before)
            get_config_store().delete(identity.REGISTRY_KEY)
            get_config_store().delete(chat_moves.MOVES_KEY)
            from kazma_core.security.vault import reset_posture_cache

            reset_posture_cache()


def _fails(error: BaseException) -> Any:
    def raise_it(*_a: Any, **_k: Any) -> Any:
        raise error

    return raise_it


def _psycopg_error() -> Exception:
    psycopg = pytest.importorskip("psycopg")
    return psycopg.OperationalError("server closed the connection unexpectedly")


def _thread_of(w: World, space: str, who: str, session: str) -> str:
    return w.row(space, who, session).thread_id


# ── one space after each move, under new ids ──────────────────────────────


def test_shared_then_taken_out_is_in_one_space_each_time(world) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="holiday plans")
    w.turn("reem", i["mine"], thread, "question holiday plans")

    preview = w.call("reem", "GET", f"/api/chat/sessions/{session}/move", space=i["mine"]).json()
    assert preview == {"ok": True, "turns": 1, "others_wrote": False}

    shared = w.moved("reem", i["mine"], session, i["family"])
    assert shared != session, "a chat of its own"
    w.nothing_left(i["mine"], "reem", session, thread)
    assert shared in w.listed("reem", i["family"]) and shared in w.listed("omar", i["family"])
    there = w.row(i["family"], "omar", shared)
    assert there.thread_id not in (thread, session), "under a thread of its own"
    assert threads.record_of(there.thread_id).space_id == i["family"]
    assert threads.record_of(there.thread_id).opener == i["reem"], "the opener stays"
    assert there.title == "title holiday plans" and there.copied == 2
    assert [m.get("author") for m in there.messages if m["role"] == "user"] == [i["reem"]]
    assert [m["content"] for m in there.messages] == ["question holiday plans", "answer holiday plans"]
    assert chat_moves.moves_under_way() == {}, "the journal entry went"
    assert w.call("omar", "GET", "/api/chat/taken-out", space=i["family"]).json()["lines"] == []
    w.turn("reem", i["family"], there.thread_id, "and the hotel")
    assert w.state("reem", i["family"], there.thread_id) == [
        ("user", "question holiday plans"), ("assistant", "Answer to question holiday plans"),
        ("user", "and the hotel"), ("assistant", "Answer to and the hotel")]

    answer = w.move("reem", i["family"], shared, i["mine"])
    assert answer.status_code == 200 and answer.json()["taken_out"] is True, answer.text
    back = answer.json()["session_id"]
    assert back not in (session, shared)
    w.nothing_left(i["family"], "omar", shared, there.thread_id)
    assert back in w.listed("reem", i["mine"])
    lines = w.call("omar", "GET", "/api/chat/taken-out", space=i["family"]).json()["lines"]
    assert [(line["title"], line["by_name"]) for line in lines] == [("title holiday plans", "Reem")]
    # The line holds the title it had, never a word of the chat.
    assert "question" not in str(lines) and "answer" not in str(lines)


def test_control_a_private_mark_leaves_it_in_the_shared_space(world) -> None:
    """The design the plan turned down: the chat stays in the shared space
    with a mark. Every other member still lists it."""
    w, i = world, world.ids
    session, _thread = w.chat("reem", i["family"], mark="my diary")
    with scope.bind_scope(i["family"], i["reem"]):
        store = session_manager.get_session_manager()
        marked = store.get(session)
        marked.messages[0]["private"] = True
        store.put(marked)
    assert session in w.listed("omar", i["family"])


# ── the questions and the answers, nothing else ───────────────────────────


_SECRET = "pelican diagnosis"


def test_only_questions_and_answers_go(world) -> None:
    """Not the memory a turn was shown, nor what its tools answered, nor the
    turn's notes and "About me", nor the state of the space it ran in; nor the
    thread's journaled frames and saved steps."""
    from kazma_core.time_travel import process_recorder
    from kazma_core.turn_notes import TURN_NOTE_KINDS
    from kazma_ui.delivery import get_turn_broker

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="n3")
    w.turn("reem", i["mine"], thread, "question n3")
    call = {"id": "c1", "type": "function", "function": {"name": "memory_search", "arguments": "{}"}}

    async def remembered() -> None:
        await w.held["graph"].aupdate_state(tt.config(thread), {
            "messages": [
                {"role": "system", "content": TURN_NOTE_KINDS["memory_recall"][0] + f": {_SECRET}"},
                {"role": "system", "content": f"[kazma:about-user] Reem and her {_SECRET}."},
                {"role": "user", "content": [{"type": "text", "text": "question n3"},
                                             {"type": "image_url", "image_url": {"url": "data:," + _SECRET}}]},
                {"role": "assistant", "content": "", "tool_calls": [call]},
                {"role": "tool", "tool_call_id": "c1", "content": _SECRET},
                {"role": "assistant", "content": "answer n3"},
            ],
            "tool_results": {"c1": _SECRET}, "scratchpad": {"note": _SECRET}, "active_goal": _SECRET,
        }, as_node="respond")

    w.run(i["mine"], "reem", remembered)
    with scope.bind_scope(i["mine"], i["reem"]):
        store = session_manager.get_session_manager()
        held = store.get(session)
        held.messages[-1] |= {
            "parts": [{"type": "memory", "hits": [{"text": _SECRET}]}, {"type": "text", "text": "answer n3"}],
            "activity": [{"kind": "tool", "title": "memory_search", "detail": _SECRET}],
            "reasoning": _SECRET, "turn_id": "turn-1", "model": "m-1", "closed_at": "2026-11-24T08:00:00+00:00"}
        held.messages.append({"role": "assistant", "content": "", "parts": [{"type": "hitl", "args": _SECRET}]})
        store.put(held)
    broker = get_turn_broker()
    broker._journal.append(thread, {"type": "memory_explain", "data": {"episodes": [{"content": _SECRET}]}})
    recorder = process_recorder()
    recorder.capture({"thread_id": thread, "created_at": 1.0, "iteration": 0,
                      "messages": [{"role": "user", "content": _SECRET}]})

    shared = w.moved("reem", i["mine"], session, i["family"])
    there = w.row(i["family"], "omar", shared)
    assert [(m["role"], m["content"]) for m in there.messages] == [
        ("user", "question n3"), ("assistant", "answer n3")]
    assert set(there.messages[1]) == {"role", "content", "ts", "model", "closed_at"}
    assert there.copied == 2

    async def state() -> Any:
        return await w.held["graph"].aget_state(tt.config(there.thread_id))

    moved = w.run(i["family"], "omar", state)
    assert [(m["role"], m["content"]) for m in moved.values["messages"]] == [
        ("user", "question n3"), ("assistant", "answer n3")]
    assert moved.next == () and not moved.tasks, "nothing is left to run"
    assert _SECRET not in str(moved.values) and moved.values["tenant_id"] == i["family"]
    assert broker.resume(thread, 0)[0] == [] and broker.resume(there.thread_id, 0)[0] == []
    assert recorder.list_snapshots(thread) == [], "no saved step of it is left to restore"


def test_a_compacted_chat_goes_on_from_its_transcript(world) -> None:
    """Its state holds a summary in place of its first turns: no state is
    written, and the next turn starts from the row (``build_turn_messages``),
    as any chat with no checkpoint does."""
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="long")

    async def compacted() -> None:
        await w.held["graph"].aupdate_state(tt.config(thread), {"messages": [
            {"role": "system", "content": "[CONTEXT SUMMARY] They planned it. [/CONTEXT SUMMARY]"},
            {"role": "user", "content": "the last question"},
            {"role": "assistant", "content": "the last answer"}]}, as_node="respond")

    w.run(i["mine"], "reem", compacted)
    shared = w.moved("reem", i["mine"], session, i["family"])
    there = w.row(i["family"], "reem", shared)
    assert w.state("reem", i["family"], there.thread_id) == []
    from kazma_core.agent.turn_input import build_turn_messages

    async def built() -> list[dict[str, Any]]:
        return await build_turn_messages(w.held["graph"], tt.config(there.thread_id), user_text="go on",
                                         system_messages=[], fallback_history=there.messages)

    assert [m["content"] for m in w.run(i["family"], "reem", built)] == ["question long", "answer long", "go on"]


@pytest.mark.parametrize("shared_table", [False, True])
def test_a_real_graph_carries_on(world, monkeypatch, shared_table) -> None:
    """Two turns in her own space, the move, a turn after it; each space's
    own checkpoint file, and every space in one table as on Postgres."""
    from kazma_gateway.stores.checkpoint import CheckpointManager

    if shared_table:
        monkeypatch.setattr(CheckpointManager, "_shared_table", lambda self: True)
    _carries_on(world)


@pytest.mark.postgres
def test_a_real_graph_carries_on_on_real_postgres(world) -> None:
    if tt.postgres_dsn() is None:
        pytest.skip("needs a real Postgres (KAZMA_DATABASE_URL + KAZMA_TEST_ALLOW_REAL_DB=1)")
    assert world.held["manager"]._shared_table(), "the world's checkpoint store is not on Postgres"
    _carries_on(world)


def _carries_on(w: World) -> None:
    i = w.ids
    session, thread = w.chat("reem", i["mine"], mark="graph")
    for text in ("plan the garden", "and the fence"):
        w.turn("reem", i["mine"], thread, text)
    shared = w.moved("reem", i["mine"], session, i["family"])
    there = _thread_of(w, i["family"], "reem", shared)
    assert w.state("reem", i["mine"], thread) == [], "the chat it was has none"
    w.turn("reem", i["family"], there, "what did we plan?")
    assert [text for _role, text in w.state("reem", i["family"], there)] == [
        "plan the garden", "Answer to plan the garden", "and the fence", "Answer to and the fence",
        "what did we plan?", "Answer to what did we plan?"]


@pytest.mark.postgres
def test_a_move_on_real_postgres_stores_the_copys_row_and_count(world) -> None:
    """The chat store's own Postgres table: the row, its count of copied
    messages, and the source's row gone."""
    if tt.postgres_dsn() is None:
        pytest.skip("needs a real Postgres (KAZMA_DATABASE_URL + KAZMA_TEST_ALLOW_REAL_DB=1)")
    from kazma_core.db.pg_helpers import get_pool

    w, i = world, world.ids
    assert session_manager.get_session_manager()._pg
    session, _thread = w.chat("reem", i["mine"], mark="pg")
    shared = w.moved("reem", i["mine"], session, i["family"])
    rows = get_pool().execute("SELECT tenant_id, session_id, copied FROM kazma_chat_sessions "
                              "WHERE session_id IN (%s, %s)", (session, shared))
    assert [(r["tenant_id"], r["session_id"], r["copied"]) for r in rows] == [(i["family"], shared, 2)]
    w.restart()
    assert w.row(i["family"], "reem", shared).copied == 2


# ── who may move what, and what holds a move ──────────────────────────────


def test_a_chat_others_wrote_in_cannot_be_taken_out(world) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["family"], mark="the trip", authors=(world.ids["reem"], world.ids["omar"]))
    preview = w.call("reem", "GET", f"/api/chat/sessions/{session}/move", space=i["family"]).json()
    assert preview["others_wrote"] is True
    refused = w.move("reem", i["family"], session, i["mine"])
    assert refused.status_code == 409 and refused.json()["error"] == "others_wrote"
    assert session in w.listed("omar", i["family"])
    assert threads.record_of(thread).space_id == i["family"] and chat_moves.moves_under_way() == {}


def test_a_message_there_that_names_no_author_is_not_known_to_be_theirs(world) -> None:
    w, i = world, world.ids
    session, _thread = w.chat("reem", i["family"], mark="unsigned")
    with scope.bind_scope(i["family"], i["reem"]):
        with session_manager.get_session_manager().transact(session) as held:
            held.messages[0].pop("author")
    assert w.move("reem", i["family"], session, i["mine"]).json()["error"] == "others_wrote"


def test_a_row_whose_thread_is_another_spaces_is_not_moved(world) -> None:
    """The thread's record says where a chat is: a row left beside a thread
    of another space moves nothing of that space."""
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="a row")
    threads.forget_thread(thread)
    threads.record_thread(thread, i["family"], i["reem"])
    assert w.move("reem", i["mine"], session, i["family"]).json()["error"] == "not_found"
    assert threads.record_of(thread).space_id == i["family"]


def test_only_its_opener_moves_a_chat(world) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["family"], mark="reem's chat")
    refused = w.move("omar", i["family"], session, i["omars"])
    assert refused.status_code == 403 and refused.json()["error"] == "not_your_chat"
    assert threads.record_of(thread).space_id == i["family"]


def test_a_move_is_between_ones_own_space_and_a_shared_one(world) -> None:
    w, i = world, world.ids
    session, _thread = w.chat("reem", i["mine"], mark="x")
    for to, code in ((i["omars"], "not_yours"), (i["mine"], "not_yours")):
        refused = w.move("reem", i["mine"], session, to)
        assert refused.json()["error"] == code, to
    # Another space's chat reads as no chat here.
    other, _t = w.chat("omar", i["omars"], mark="y")
    assert w.move("reem", i["mine"], other, i["family"]).json()["error"] == "not_found"


def test_a_chat_apps_chat_is_not_moved(world) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="x", session_id="gw-telegram-42", thread_id="gw-telegram-42")
    refused = w.move("reem", i["mine"], session, i["family"])
    assert refused.status_code == 409 and refused.json()["error"] == "chat_app_chat"
    assert threads.record_of(thread).space_id == i["mine"]


def test_a_running_turn_holds_the_move(world) -> None:
    from kazma_ui.active_turns import register_turn, unregister_turn

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="busy")

    class _Running:
        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            return None

    running = _Running()
    register_turn(thread, running)
    try:
        refused = w.move("reem", i["mine"], session, i["family"])
    finally:
        unregister_turn(thread, running)
    assert refused.status_code == 409 and refused.json()["error"] == "turn_running"
    assert threads.record_of(thread).space_id == i["mine"]


def test_a_card_holds_the_move_and_so_does_a_turn_cut_off(world) -> None:
    """A real paused turn, and one a restart cut off half way."""
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="card")
    w.turn("reem", i["mine"], thread, "file_write:notes")
    refused = w.move("reem", i["mine"], session, i["family"])
    assert refused.status_code == 409 and refused.json()["error"] == "approval_pending", refused.text

    class _CutOff:
        next = ("tool_worker",)
        tasks: tuple[Any, ...] = ()
        values: dict[str, Any] = {}

    other, other_thread = w.chat("reem", i["mine"], mark="cut")
    w.held["graph"] = NoCardGraph(_CutOff())
    refused = w.move("reem", i["mine"], other, i["family"])
    assert refused.status_code == 409 and refused.json()["error"] == "turn_unfinished", refused.text
    assert {threads.record_of(t).space_id for t in (thread, other_thread)} == {i["mine"]}


def test_a_state_or_registry_that_cannot_be_read_holds_the_move(world, monkeypatch) -> None:
    from kazma_core.safety import hitl_gates

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="m5b")

    class _Unreadable:
        def __init__(self, error: Exception) -> None:
            self.error = error

        async def aget_state(self, config: dict[str, Any]) -> Any:
            raise self.error

    for error in (_psycopg_error(), sqlite3.OperationalError("locked"), KeyError("channel")):
        w.held["graph"] = _Unreadable(error)
        refused = w.move("reem", i["mine"], session, i["family"])
        assert refused.status_code == 503 and refused.json()["error"] == "approval_unknown", (error, refused.text)
    w.held["graph"] = None
    assert w.move("reem", i["mine"], session, i["family"]).json()["error"] == "approval_unknown"
    w.held["graph"] = NoCardGraph()
    with monkeypatch.context() as patch:
        patch.setattr(hitl_gates, "live_gates", _fails(sqlite3.OperationalError("locked")))
        refused = w.move("reem", i["mine"], session, i["family"])
        assert refused.status_code == 503 and refused.json()["error"] == "approval_unknown", refused.text
    with monkeypatch.context() as patch:
        patch.setattr(hitl_gates, "live_gates", lambda _thread: ["a card"])
        refused = w.move("reem", i["mine"], session, i["family"])
        assert refused.status_code == 409 and refused.json()["error"] == "approval_pending", refused.text
    assert threads.record_of(thread).space_id == i["mine"] and chat_moves.moves_under_way() == {}


def test_moving_is_rate_limited(world, monkeypatch) -> None:
    from kazma_ui import rate_limit

    w, i = world, world.ids
    monkeypatch.setattr(rate_limit, "_enabled", lambda: True)
    monkeypatch.setattr(rate_limit, "_per_minute", lambda bucket, default: default)
    rate_limit._windows.clear()
    session, _thread = w.chat("reem", i["mine"], mark="rate")
    answers, (frm, to) = [], (i["mine"], i["family"])
    for _ in range(7):
        answer = w.move("reem", frm, session, to)
        answers.append(answer.status_code)
        session = answer.json().get("session_id", "")
        frm, to = to, frm
    rate_limit._windows.clear()
    assert answers == [200] * 6 + [429]


def test_two_moves_at_once_ask_again_under_the_lock(world) -> None:
    from kazma_ui.chat_move import _move_chat

    w, i = world, world.ids
    session, _thread = w.chat("reem", i["mine"], mark="twice")

    async def both() -> list[Any]:
        return await asyncio.gather(
            *(_move_chat(session_id=session, from_space=i["mine"], to_space=i["family"], by=i["reem"],
                         graph=w.held["graph"], checkpoints=w.held["manager"]) for _ in range(2)),
            return_exceptions=True)

    outcomes = w.run(i["mine"], "reem", both)
    refused = [o for o in outcomes if isinstance(o, chat_moves.ChatMoveRefused)]
    assert len(refused) == 1 and refused[0].code == "not_found", outcomes
    assert len(w.listed("omar", i["family"])) == 1


# ── the chat it was is a deleted chat (H1, H2) ────────────────────────────


def _no_memory_of_deletes(monkeypatch) -> None:
    monkeypatch.setattr(session_manager.SessionManager, "_remember_deleted", lambda *_a: None)


def _send(w: World, who: str, space: str, session: str, text: str) -> Any:
    return w.call(who, "POST", "/api/chat/stream", space=space, json={"message": text, "session_id": session})


@pytest.mark.parametrize(("lang", "word"), [("en", "was deleted or moved"), ("ar", "حُذفت هذه المحادثة")])
def test_h1_a_tab_left_open_never_brings_the_chat_back(world, monkeypatch, lang, word) -> None:
    from kazma_ui import i18n

    monkeypatch.setattr(i18n, "current_language", lambda: lang)
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="h1")
    w.moved("reem", i["mine"], session, i["family"])
    sent = _send(w, "reem", i["mine"], session, "and one more thing")
    assert sent.status_code == 200 and '"code": "chat_deleted"' in sent.text, sent.text
    assert word in sent.text
    bound = w.call("reem", "POST", "/api/chat/sessions", space=i["mine"], json={"session_id": session})
    assert bound.status_code == 404, bound.text
    w.nothing_left(i["mine"], "reem", session, thread)


def test_h2_a_message_being_sent_never_puts_its_copy_back(world) -> None:
    """The request read the chat, then the move ended: its own copy, whole
    transcript and all, is not stored again."""
    from kazma_ui.sse_chat._persistence import _mutate_session

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="h2")
    read = copy.deepcopy(w.row(i["mine"], "reem", session))
    w.moved("reem", i["mine"], session, i["family"])
    with scope.bind_scope(i["mine"], i["reem"]):
        assert _mutate_session(session, lambda s: s.messages.append({"role": "user", "content": "x"}), read) is None
        session_manager.get_session_manager().put(read)
    w.nothing_left(i["mine"], "reem", session, thread)


def test_control_a_store_that_forgets_its_deletes_brings_it_back(world, monkeypatch) -> None:
    from kazma_ui.sse_chat._persistence import _mutate_session

    _no_memory_of_deletes(monkeypatch)
    w, i = world, world.ids
    session, _thread = w.chat("reem", i["mine"], mark="came back")
    open_tab, _t = w.chat("reem", i["mine"], mark="open tab")
    read = copy.deepcopy(w.row(i["mine"], "reem", session))
    for chat in (session, open_tab):
        w.moved("reem", i["mine"], chat, i["family"])
    bound = w.call("reem", "POST", "/api/chat/sessions", space=i["mine"], json={"session_id": open_tab})
    assert bound.status_code == 200, "H1: the open tab's id is a chat again"
    with scope.bind_scope(i["mine"], i["reem"]):
        _mutate_session(session, lambda s: s.messages.append({"role": "user", "content": "x"}), read)
    assert [m["content"] for m in w.row(i["mine"], "reem", session).messages][:2] == [
        "question came back", "answer came back"], "H2: the whole conversation is back in the space it left"


def test_the_moved_chats_sockets_and_streams_end(world, monkeypatch) -> None:
    """Every socket and stream of the chat in the space it left is closed or
    ended; one of a chat of the same session id in another space stays."""
    from kazma_ui import live_connections

    w, i = world, world.ids
    session, thread = w.chat("reem", i["family"], mark="h3")
    told: list[dict[str, str]] = []
    real = live_connections.chat_gone
    with monkeypatch.context() as patch:
        patch.setattr(live_connections, "chat_gone", lambda **k: told.append(k) or real(**k))
        w.moved("reem", i["family"], session, i["mine"])
    assert told == [{"session_id": session, "thread_id": thread, "space": i["family"]}]
    assert live_connections.is_gone(thread) and not live_connections.is_gone("t-other")

    class _Socket:
        def __init__(self) -> None:
            self.closed: tuple[int, str] | None = None

        async def close(self, code: int, reason: str) -> None:
            self.closed = (code, reason)

    async def run() -> tuple[_Socket, _Socket, int, int]:
        omars, reems = _Socket(), _Socket()
        theirs, hers = asyncio.Queue(), asyncio.Queue()
        with live_connections.socket_watched(i["omar"], i["family"], omars, chat=session), \
                live_connections.socket_watched(i["reem"], i["mine"], reems, chat=session), \
                live_connections.stream_watched(i["omar"], i["family"], theirs, "wake", chat=thread), \
                live_connections.stream_watched(i["reem"], i["mine"], hers, "wake", chat=thread):
            assert live_connections.chat_gone(session_id=session, thread_id=thread, space=i["family"]) == 2
            assert await asyncio.wait_for(theirs.get(), 10) == "wake"
            for _ in range(200):
                if omars.closed:
                    break
                await asyncio.sleep(0.01)
        return omars, reems, theirs.qsize(), hers.qsize()

    omars, reems, theirs, hers = asyncio.run(run())
    assert omars.closed == (live_connections.CLOSE_CODE, "moved") and reems.closed is None
    assert (theirs, hers) == (0, 0)


def test_an_attached_stream_ends_when_its_chat_moves(world, monkeypatch) -> None:
    """The real attach generator in the space the chat leaves: registered
    under its thread, woken by the move, and ended by its own check."""
    from kazma_ui import live_connections
    from kazma_ui.delivery import get_turn_broker
    from kazma_ui.sse_chat import _streaming

    w, i = world, world.ids
    _session, thread = w.chat("reem", i["family"], mark="attached")
    monkeypatch.setattr(_streaming, "is_thread_paused", lambda _t: True)

    async def run() -> list[str]:
        seen: list[str] = []
        with scope.bind_scope(i["family"], i["omar"]):
            stream = _streaming._sse_attach_stream(thread, "s", 0, replay_is_history=True)
            seen.append(await anext(stream))
            waiting = asyncio.ensure_future(anext(stream))
            for _ in range(1000):
                if live_connections._STREAMS:
                    break
                await asyncio.sleep(0.01)
            assert [entry[5] for entry in live_connections._STREAMS.values()] == [thread]
            assert live_connections.chat_gone(session_id="s", thread_id=thread, space=i["family"]) == 1
            get_turn_broker()._journal.append(thread, {"type": "token", "data": {"content": _SECRET}})
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(waiting, 5)
        return seen

    assert _SECRET not in str(asyncio.run(run()))


def test_a_chat_socket_is_watched_by_its_chat(world, monkeypatch) -> None:
    from kazma_ui import live_connections
    from kazma_ui.routes import ws_chat

    w, i = world, world.ids
    session, _thread = w.chat("omar", i["family"], mark="socket")
    watched: list[tuple[Any, ...]] = []

    @contextmanager
    def note(asker: Any, space: Any, _websocket: Any, *, chat: str = "") -> Any:
        watched.append((asker, space, chat))
        yield

    monkeypatch.setattr(live_connections, "socket_watched", note)
    app = FastAPI()
    app.include_router(ws_chat.create_ws_chat_router())
    binding = space_binding.Binding(space_id=i["family"], asker=i["omar"])
    monkeypatch.setattr(space_binding, "space_for_socket", lambda *_a, **_k: binding)
    monkeypatch.setattr(auth, "get_websocket_principal", lambda _ws: _principal(i["omar"]))
    with TestClient(app) as client, client.websocket_connect(f"/ws/chat/{session}") as socket:
        socket.close()
    assert watched == [(i["omar"], i["family"], session)]


def test_chat_app_pointers_to_it_go(world) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="pointed")
    get_config_store().set("active_thread.telegram:42", thread, category="session")
    get_config_store().set("active_thread.telegram:43", "another", category="session")
    w.moved("reem", i["mine"], session, i["family"])
    assert get_config_store().get("active_thread.telegram:42") is None
    assert get_config_store().get("active_thread.telegram:43") == "another"


# ── what lands while the chat is copied (H3) ──────────────────────────────


def _omar_writes_while(w: World, monkeypatch, session: str) -> None:
    """Omar's message lands after the chat was read and before it is handed
    over, as a real message does (the session's transaction)."""
    from kazma_ui import thread_rewind

    i = w.ids
    real = thread_rewind.carry_conversation

    async def carry(*args: Any, **kwargs: Any) -> int:
        carried = await real(*args, **kwargs)

        def write() -> None:
            with scope.bind_scope(i["family"], i["omar"]):
                with session_manager.get_session_manager().transact(session) as chat:
                    chat.messages.append({"role": "user", "content": "wait, me too", "author": i["omar"]})

        await asyncio.to_thread(write)
        return carried

    monkeypatch.setattr(thread_rewind, "carry_conversation", carry)


def _entry_of(monkeypatch) -> list[dict[str, Any]]:
    """The journal's entry of the move that runs, with its new ids."""
    seen: list[dict[str, Any]] = []
    real = chat_moves.record_move

    def record(attempt: str, fields: dict[str, Any]) -> None:
        if "thread" in fields:
            seen.append(dict(fields))
        real(attempt, fields)

    monkeypatch.setattr(chat_moves, "record_move", record)
    return seen


def _no_copy_left(w: World, entry: dict[str, Any]) -> None:
    w.nothing_left(entry["target"], "reem", entry["new_session"], entry["new_thread"])
    assert chat_moves.moves_under_way() == {}


def test_h3_another_members_words_hold_the_move(world, monkeypatch) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["family"], mark="h3")
    w.turn("reem", i["family"], thread, "question h3")
    entries = _entry_of(monkeypatch)
    with monkeypatch.context() as patch:
        _omar_writes_while(w, patch, session)
        refused = w.move("reem", i["family"], session, i["mine"])
    assert refused.status_code == 409 and refused.json()["error"] == "turn_running", refused.text
    here = w.row(i["family"], "omar", session)
    assert here.messages[-1]["content"] == "wait, me too", "the chat stays, with his words"
    assert threads.record_of(thread).space_id == i["family"] and w.state("reem", i["family"], thread)
    (entry,) = entries
    _no_copy_left(w, entry)
    assert chat_moves.taken_out_chats(i["family"]) == []
    # Asked again, they are his words: it cannot be taken out at all.
    assert w.move("reem", i["family"], session, i["mine"]).json()["error"] == "others_wrote"


def test_control_a_handover_that_does_not_compare_loses_them(world, monkeypatch) -> None:
    w, i = world, world.ids
    session, _thread = w.chat("reem", i["family"], mark="lost words")
    real = session_manager.SessionManager.hand_over

    def careless(self: Any, session_id: str, *, messages: Any, **kwargs: Any) -> bool:
        return real(self, session_id, messages=self.get(session_id).messages, **kwargs)

    monkeypatch.setattr(session_manager.SessionManager, "hand_over", careless)
    _omar_writes_while(w, monkeypatch, session)
    taken = w.moved("reem", i["family"], session, i["mine"])
    assert "wait, me too" not in str(w.row(i["mine"], "reem", taken).messages)
    assert w.row(i["family"], "omar", session) is None, "and his words are nowhere"


def test_a_message_waits_for_the_handover_and_is_told(world, monkeypatch) -> None:
    """A message sent as the chat is handed over waits for it, and is told
    the chat moved: not stored in the space it left, nor lost in silence."""
    import threading

    from kazma_ui.sse_chat._persistence import _mutate_session

    w, i = world, world.ids
    session, _thread = w.chat("reem", i["mine"], mark="waits")
    real = chat_moves.advance_move
    answered: list[Any] = []
    senders: list[threading.Thread] = []
    read = copy.deepcopy(w.row(i["mine"], "reem", session))

    def advance(*args: Any, **kwargs: Any) -> bool:
        def send() -> None:
            with scope.bind_scope(i["mine"], i["reem"]):
                answered.append(_mutate_session(session, lambda s: s.messages.append({"role": "user"}), read))

        senders.append(threading.Thread(target=send))
        senders[-1].start()
        senders[-1].join(0.2)
        assert senders[-1].is_alive() and answered == [], "it waits for the handover"
        return real(*args, **kwargs)

    monkeypatch.setattr(chat_moves, "advance_move", advance)
    shared = w.moved("reem", i["mine"], session, i["family"])
    senders[0].join(10)
    assert answered == [None]
    assert len(w.row(i["family"], "reem", shared).messages) == 2


def test_a_rename_meanwhile_is_kept(world, monkeypatch) -> None:
    from kazma_ui import thread_rewind

    w, i = world, world.ids
    session, _thread = w.chat("reem", i["mine"], mark="renamed")
    real = thread_rewind.carry_conversation

    async def carry(*args: Any, **kwargs: Any) -> int:
        await asyncio.to_thread(rename)
        return await real(*args, **kwargs)

    def rename() -> None:
        with scope.bind_scope(i["mine"], i["reem"]):
            session_manager.get_session_manager().rename(session, "The trip")
            session_manager.get_session_manager().set_pinned(session, True)

    monkeypatch.setattr(thread_rewind, "carry_conversation", carry)
    there = w.row(i["family"], "reem", w.moved("reem", i["mine"], session, i["family"]))
    assert there.title == "The trip" and there.pinned


# ── the files Kazma shared in it ──────────────────────────────────────────


def _shared_file(w: World, space: str, thread: str, text: str) -> str:
    """A file Kazma shared in the chat *thread*: its id."""
    from kazma_core.chat_files import share_file

    with tempfile.TemporaryDirectory() as tmp, scope.bind_scope(space, w.ids["reem"]):
        path = Path(tmp) / "report.txt"
        path.write_text(text, encoding="utf-8")
        return share_file(path, thread_id=thread).id


def _link(w: World, space: str, session: str, file_id: str) -> None:
    with scope.bind_scope(space, w.ids["reem"]):
        store = session_manager.get_session_manager()
        held = store.get(session)
        held.messages[-1]["content"] += f" [report](/api/chat/files/{file_id})"
        store.put(held)


def _opens(space: str, account: str, file_id: str) -> tuple[str, str] | None:
    """What the link answers in *space*: (the file's text, the chat it is of)."""
    from kazma_core.chat_files import load_shared

    with scope.bind_scope(space, account):
        found = load_shared(file_id)
    return (found[1].read_text(encoding="utf-8"), found[0].thread_id) if found is not None else None


def test_the_chats_shared_files_move_with_it(world) -> None:
    """Its own files, and a file of another chat its messages link to (a
    fork keeps its source's links): no broken link in the target. What the
    chat shared is gone from the source; the other chat's file stays there."""
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="n19")
    _other_session, other = w.chat("reem", i["mine"], mark="n19 fork source")
    mine, borrowed = _shared_file(w, i["mine"], thread, "the report"), _shared_file(w, i["mine"], other, "a chart")
    unlinked = _shared_file(w, i["mine"], thread, "shared, its link edited away")
    for file_id in (mine, borrowed):
        _link(w, i["mine"], session, file_id)
    there = _thread_of(w, i["family"], "reem", w.moved("reem", i["mine"], session, i["family"]))
    assert _opens(i["family"], i["omar"], mine) == ("the report", there)
    assert _opens(i["family"], i["omar"], borrowed) == ("a chart", there), "its chat's members open it"
    assert _opens(i["family"], i["omar"], unlinked) == ("shared, its link edited away", there)
    assert _opens(i["mine"], i["reem"], mine) is None and _opens(i["mine"], i["reem"], unlinked) is None
    assert _opens(i["mine"], i["reem"], borrowed) == ("a chart", other)


def test_files_the_target_has_no_room_for_refuse_the_move(world, monkeypatch) -> None:
    from kazma_core.spaces import space_limits

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="n19 full")
    file_id = _shared_file(w, i["mine"], thread, "the report")
    entries = _entry_of(monkeypatch)
    monkeypatch.setattr(space_limits, "disk_refusal", lambda space, incoming=0: "full" if space == i["family"] else None)
    refused = w.move("reem", i["mine"], session, i["family"])
    assert refused.status_code == 507 and refused.json()["error"] == "space_limit", refused.text
    assert session in w.listed("reem", i["mine"]) and _opens(i["mine"], i["reem"], file_id)
    _no_copy_left(w, entries[0])


# ── a move killed at any point is in exactly one space ────────────────────


class _Killed(Exception):
    """The process went: nothing after this ran (no handler of the move's
    catches it)."""


def _wrap(monkeypatch, owner: Any, name: str, *, before: bool) -> None:
    """Kill the process before, or right after, *owner.name* runs."""
    real = getattr(owner, name)
    if asyncio.iscoroutinefunction(real):
        async def killed(*args: Any, **kwargs: Any) -> Any:
            if not before:
                await real(*args, **kwargs)
            raise _Killed(name)
    else:
        def killed(*args: Any, **kwargs: Any) -> Any:
            if not before:
                real(*args, **kwargs)
            raise _Killed(name)

    monkeypatch.setattr(owner, name, killed)


def _points() -> dict[str, tuple[Callable[[], Any], str, bool]]:
    """Where a move is killed: (what is wrapped, before or after it)."""
    from kazma_core import chat_files
    from kazma_core.memory import forget
    from kazma_ui import chat_delete, chat_move, thread_rewind

    store = session_manager.SessionManager
    return {
        "journaled": (lambda: chat_move, "_copy", True),
        "record": (lambda: chat_move, "_record_copy", False),
        "state": (lambda: thread_rewind, "carry_conversation", False),
        "files": (lambda: chat_files, "copy_shared_files", False),
        "before the handover": (lambda: chat_move, "_hand_over", True),
        "the copy's row stored": (lambda: chat_moves, "advance_move", True),
        "the journal says copied": (lambda: chat_moves, "advance_move", False),
        "the chat's row gone": (lambda: store, "hand_over", False),
        "before the steps": (lambda: chat_move, "_finish", True),
        "row asked again": (lambda: chat_move, "_remove_row", False),
        "pointers": (lambda: chat_move, "_drop_pointers", False),
        "kept out": (lambda: forget, "set_chat_remembered", False),
        "forgotten": (lambda: forget, "forget_chat", False),
        "line": (lambda: chat_moves, "record_taken_out", False),
        "source files": (lambda: chat_move, "_remove_files", False),
        "checkpoints": (lambda: chat_delete, "remove_chat_remains", False),
        "journal entry": (lambda: chat_moves, "settle_move", True),
    }


@pytest.mark.parametrize("out", [False, True], ids=["shared", "taken-out"])
@pytest.mark.parametrize("point", sorted(_points()))
def test_a_move_killed_anywhere_is_settled_into_one_space(world, monkeypatch, point, out) -> None:
    """Both ways, killed at each point, then a new process: exactly one
    space holds the chat, whole; the other holds nothing of it."""
    w, i = world, world.ids
    frm, to = (i["family"], i["mine"]) if out else (i["mine"], i["family"])
    session, thread = w.chat("reem", frm, mark="killed")
    w.turn("reem", frm, thread, "question killed")
    file_id = _shared_file(w, frm, thread, "the report")
    entries = _entry_of(monkeypatch)
    killed = False
    with monkeypatch.context() as patch:
        owner, name, before = _points()[point]
        _wrap(patch, owner(), name, before=before)
        if name == "advance_move":
            # Killed inside the handover: not even its own undo runs.
            patch.setattr(session_manager.SessionManager, "_delete_row", lambda *_a: None)
        from kazma_ui.chat_move import _move_chat

        async def move() -> Any:
            return await _move_chat(session_id=session, from_space=frm, to_space=to, by=i["reem"],
                                    graph=w.held["graph"], checkpoints=w.held["manager"])

        try:
            w.run(frm, "reem", move)
        except _Killed:
            killed = True
    (entry,) = entries
    if not killed:
        assert point in ("kept out", "forgotten", "line") and not out, "a step of a take-out alone"
    w.restart()
    w.settle()
    assert chat_moves.moves_under_way() == {}, "and nothing of the move is left"
    here, there = w.row(frm, "reem", session), w.row(to, "reem", entry["new_session"])
    assert (here is None) != (there is None), "in exactly one space"
    stands, space, at, other, gone = ((here, frm, thread, to, (entry["new_session"], entry["new_thread"])) if here
                                      else (there, to, entry["new_thread"], frm, (session, thread)))
    assert [m["content"] for m in stands.messages] == ["question killed", "answer killed"]
    assert threads.record_of(at).space_id == space
    assert [text for _role, text in w.state("reem", space, at)] == ["question killed", "Answer to question killed"]
    assert _opens(space, i["reem"], file_id) == ("the report", at) and _opens(other, i["reem"], file_id) is None
    w.nothing_left(other, "reem", *gone)
    if out:
        assert len(chat_moves.taken_out_chats(i["family"])) == (1 if there else 0)
    # And it moves on from there.
    w.moved("reem", space, stands.session_id, frm if space == to else to)


def test_control_a_killed_move_nothing_settles_is_in_two_spaces(world, monkeypatch) -> None:
    w, i = world, world.ids
    session, _thread = w.chat("reem", i["mine"], mark="two")
    with monkeypatch.context() as patch:
        _wrap(patch, chat_moves, "advance_move", before=True)
        # Killed for good: not even the handover's own undo runs.
        patch.setattr(session_manager.SessionManager, "_delete_row", lambda *_a: None)
        from kazma_ui.chat_move import _move_chat

        async def move() -> Any:
            return await _move_chat(session_id=session, from_space=i["mine"], to_space=i["family"], by=i["reem"],
                                    graph=w.held["graph"], checkpoints=w.held["manager"])

        with pytest.raises(_Killed):
            w.run(i["mine"], "reem", move)
    w.restart()
    assert session in w.listed("reem", i["mine"]) and len(w.listed("omar", i["family"])) == 1
    w.settle()
    assert session in w.listed("reem", i["mine"]) and w.listed("omar", i["family"]) == [], "settled, it is in one"


def test_a_journal_write_whose_answer_was_lost_puts_the_chat_back(world, monkeypatch) -> None:
    """``copied`` landed and its answer did not: the handover took the copy
    back, so the settling must not finish a delete whose copy is gone."""
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="lost answer")
    real = chat_moves.advance_move

    def lost(attempt: str, *, at: str, to: str) -> bool:
        advanced = real(attempt, at=at, to=to)
        if to == "copied":
            raise _psycopg_error()
        return advanced

    with monkeypatch.context() as patch:
        patch.setattr(chat_moves, "advance_move", lost)
        refused = w.move("reem", i["mine"], session, i["family"])
    assert refused.status_code == 503 and refused.json()["error"] == "move_failed", refused.text
    assert session in w.listed("reem", i["mine"]) and w.listed("omar", i["family"]) == []
    assert threads.record_of(thread).space_id == i["mine"] and chat_moves.moves_under_way() == {}
    w.restart()
    assert session in w.listed("reem", i["mine"])


def test_a_row_the_store_could_not_delete_is_a_deleted_chat_all_the_same(world, monkeypatch) -> None:
    """The copy stands and the delete of the chat's row failed: the move goes
    on, nothing is written into the chat it was, and the row goes later."""
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="stuck row")
    real = session_manager.SessionManager._delete_row

    def stuck(self: Any, tenant: str, session_id: str) -> None:
        if session_id == session:
            raise _psycopg_error()
        real(self, tenant, session_id)

    with monkeypatch.context() as patch:
        patch.setattr(session_manager.SessionManager, "_delete_row", stuck)
        w.moved("reem", i["mine"], session, i["family"])
        assert w.row(i["mine"], "reem", session) is None, "read as deleted"
        assert '"code": "chat_deleted"' in _send(w, "reem", i["mine"], session, "more").text
        (entry,) = chat_moves.moves_under_way().values()
        assert "chat" not in entry["done"] and "remains" in entry["done"], "the other steps ran"
    assert w.settle()[0]["outcome"] == "finished"
    w.restart()
    w.nothing_left(i["mine"], "reem", session, thread)


# ── a request that goes away; a step that fails ───────────────────────────


def test_a_cancelled_request_does_not_cut_the_move_off(world, monkeypatch) -> None:
    from kazma_ui import chat_move

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="cancel")
    real = chat_move._copy

    async def cancelled() -> None:
        copied, go = asyncio.Event(), asyncio.Event()

        async def copy_slowly(*args: Any) -> None:
            await real(*args)
            copied.set()
            await go.wait()

        monkeypatch.setattr(chat_move, "_copy", copy_slowly)
        move = asyncio.ensure_future(chat_move._move_chat(
            session_id=session, from_space=i["mine"], to_space=i["family"], by=i["reem"],
            graph=w.held["graph"], checkpoints=w.held["manager"]))
        await copied.wait()
        move.cancel()
        with pytest.raises(asyncio.CancelledError):
            await move
        go.set()
        for _ in range(1000):
            if not await asyncio.to_thread(chat_moves.moves_under_way):
                break
            await asyncio.sleep(0.01)

    w.run(i["mine"], "reem", cancelled)
    assert chat_moves.moves_under_way() == {} and len(w.listed("omar", i["family"])) == 1
    w.nothing_left(i["mine"], "reem", session, thread)


def test_a_refusal_is_the_requests_and_never_the_tasks_error(world, caplog) -> None:
    w, i = world, world.ids
    session, _thread = w.chat("reem", i["family"], mark="quiet", authors=(i["omar"],))
    with caplog.at_level("WARNING"):
        assert w.move("reem", i["family"], session, i["mine"]).json()["error"] == "others_wrote"
    assert [r for r in caplog.records if r.name == "kazma_core.background"] == []


def test_a_step_that_fails_does_not_hold_the_others_or_the_chat(world, monkeypatch) -> None:
    """The step that failed waits; every other step ran. The sweep finishes
    it, without a restart, and a step done is not done again."""
    from kazma_ui import chat_move

    w, i = world, world.ids
    session, thread = w.chat("reem", i["family"], mark="n8")
    get_config_store().set("active_thread.telegram:n8", thread, category="session")
    with monkeypatch.context() as patch:
        patch.setattr(chat_move, "_drop_pointers", _fails(_psycopg_error()))
        w.moved("reem", i["family"], session, i["mine"])
    w.nothing_left(i["family"], "omar", session, thread)
    (entry,) = chat_moves.moves_under_way().values()
    assert set(entry["done"]) == {name for name, _step in chat_move._STEPS} - {"pointers"}
    assert get_config_store().get("active_thread.telegram:n8") == thread
    lines = chat_moves.taken_out_chats(i["family"])

    # The maintenance sweep, as the app wires it (register_settler).
    from kazma_core.memory import worker_bootstrap

    sweep = dict(worker_bootstrap._MAINTENANCE_SWEEPS)["chat moves"]
    asked: list[str] = []
    chat_moves.register_settler(lambda: asked.append("settled"))
    ran: list[str] = []
    try:
        sweep()
        assert asked == ["settled"]
        (attempt,) = chat_moves.moves_under_way()
        with monkeypatch.context() as patch:
            for name in ("_remove_row", "_forget_where_it_was", "_remove_files"):
                patch.setattr(chat_move, name, lambda *_a, n=name: ran.append(n))
            assert w.settle() == [{"attempt": attempt, "outcome": "finished"}]
        assert ran == [], "a step done is not done again"
        assert get_config_store().get("active_thread.telegram:n8") is None
        assert chat_moves.moves_under_way() == {}
        sweep()
        assert asked == ["settled"], "with no move recorded it asks nobody"
    finally:
        chat_moves.register_settler(None)
    assert chat_moves.taken_out_chats(i["family"]) == lines


def test_something_of_the_chat_that_cannot_be_deleted_is_tried_again(world, monkeypatch) -> None:
    from kazma_core import time_travel

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="remains")
    with monkeypatch.context() as patch:
        patch.setattr(time_travel, "forget_thread_snapshots", _fails(time_travel.SnapshotStoreError("locked")))
        w.moved("reem", i["mine"], session, i["family"])
    (entry,) = chat_moves.moves_under_way().values()
    assert "remains" not in entry["done"] and threads.record_of(thread).space_id == i["mine"], (
        "its record stays while a copy of it does")
    assert w.settle()[0]["outcome"] == "finished"
    w.nothing_left(i["mine"], "reem", session, thread)


def test_a_put_back_that_failed_keeps_its_entry(world, monkeypatch) -> None:
    """The copy a stopped move could not take back keeps its journal: the
    next settling takes it."""
    from kazma_ui import chat_move

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="put back")
    entries = _entry_of(monkeypatch)
    with monkeypatch.context() as patch:
        patch.setattr(chat_move, "_hand_over", _fails(_psycopg_error()))
        patch.setattr(chat_move, "_remove_files", _fails(_psycopg_error()))
        failed = w.move("reem", i["mine"], session, i["family"])
        assert failed.status_code == 503 and failed.json()["error"] == "move_failed", failed.text
        again = w.move("reem", i["mine"], session, i["family"])
        assert (again.status_code, again.json()["error"]) == (409, "move_in_progress"), again.text
    assert [e["stage"] for e in chat_moves.moves_under_way().values()] == ["putting_back"]
    assert w.settle()[0]["outcome"] == "put_back"
    _no_copy_left(w, entries[0])
    assert session in w.listed("reem", i["mine"]) and threads.record_of(thread).space_id == i["mine"]
    w.moved("reem", i["mine"], session, i["family"])


def test_the_next_move_of_the_chat_settles_what_the_last_one_left(world, monkeypatch) -> None:
    from kazma_ui import chat_move

    w, i = world, world.ids
    session, _thread = w.chat("reem", i["mine"], mark="next")
    with monkeypatch.context() as patch:
        patch.setattr(chat_move, "_hand_over", _fails(_psycopg_error()))
        patch.setattr(chat_move, "_put_back", _fails(_psycopg_error()))
        assert w.move("reem", i["mine"], session, i["family"]).status_code == 503
    assert len(chat_moves.moves_under_way()) == 1
    w.moved("reem", i["mine"], session, i["family"])
    assert chat_moves.moves_under_way() == {} and len(w.listed("omar", i["family"])) == 1


def test_a_kept_out_mark_is_taken_back_with_the_copy(world, monkeypatch) -> None:
    from kazma_core.memory import forget
    from kazma_ui import chat_move

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="private")
    forget.set_chat_remembered(session, False, tenant_id=i["mine"], keys=[session, thread])
    entries = _entry_of(monkeypatch)
    with monkeypatch.context() as patch:
        patch.setattr(chat_move, "_hand_over", _fails(_psycopg_error()))
        assert w.move("reem", i["mine"], session, i["family"]).status_code == 503
    keys = [entries[0]["new_session"], entries[0]["new_thread"]]
    assert entries[0]["kept_out"] and not forget.forgotten_in_chat(i["family"], keys)[0]
    assert forget.forgotten_in_chat(i["mine"], [session, thread])[0], "here it is still kept out"


# ── settling beside a move that runs ──────────────────────────────────────


def _left(w: World, attempt: str, mark: str) -> tuple[str, str, dict[str, Any]]:
    """A move a stop cut off before its handover: session, thread, entry."""
    from kazma_ui import chat_move

    i = w.ids
    session, thread = w.chat("reem", i["mine"], mark=mark)
    held = w.row(i["mine"], "reem", session)
    entry = chat_move._entry(held, session, i["mine"], i["family"], i["reem"], False)
    chat_moves.record_move(attempt, entry)
    chat_move._record_copy(entry)
    return session, thread, entry


def test_a_handover_and_a_settling_never_both_decide(world) -> None:
    """The settling took the move first: the handover changes nothing. And
    the other way round: a move that is handed over is finished, not undone."""
    from kazma_ui import chat_move

    w, i = world, world.ids
    session, _thread, entry = _left(w, "first", "settled first")
    assert chat_moves.advance_move("first", at="copying", to="putting_back")
    held = w.row(i["mine"], "reem", session)
    rows = chat_move._rows_to_copy(held, i["reem"])
    assert chat_move._hand_over("first", entry, copy.deepcopy(held.messages), rows) is False
    assert session in w.listed("reem", i["mine"]) and w.listed("omar", i["family"]) == []

    session, _thread, entry = _left(w, "second", "handed over first")
    held = w.row(i["mine"], "reem", session)
    rows = chat_move._rows_to_copy(held, i["reem"])
    assert chat_move._hand_over("second", entry, copy.deepcopy(held.messages), rows) is True
    assert not chat_moves.advance_move("second", at="copying", to="putting_back")
    assert {s["attempt"]: s["outcome"] for s in w.settle()} == {"first": "put_back", "second": "finished"}
    assert w.listed("omar", i["family"]) == [entry["new_session"]]


def test_a_move_handed_over_as_it_is_settled_is_finished(world, monkeypatch) -> None:
    """The settling read ``copying`` and the move was handed over before its
    own step (another process): what it finds then is what it settles."""
    from kazma_ui import chat_move

    w, i = world, world.ids
    session, _thread, entry = _left(w, "both", "both at once")
    held = w.row(i["mine"], "reem", session)
    rows = chat_move._rows_to_copy(held, i["reem"])
    real = chat_moves.advance_move

    def advance(attempt: str, *, at: str, to: str) -> bool:
        if to == "putting_back":
            monkeypatch.setattr(chat_moves, "advance_move", real)
            assert chat_move._hand_over(attempt, entry, copy.deepcopy(held.messages), rows) is True
        return real(attempt, at=at, to=to)

    monkeypatch.setattr(chat_moves, "advance_move", advance)
    assert w.settle() == [{"attempt": "both", "outcome": "finished"}]
    assert w.listed("omar", i["family"]) == [entry["new_session"]]
    assert session not in w.listed("reem", i["mine"])


def _copy_of(session: str, space: str, held: Any) -> Any:
    return session_manager.ChatSession(session_id=f"{session}-copy", thread_id=f"{session}-copy-t", tenant_id=space,
                                       messages=copy.deepcopy(held.messages), copied=len(held.messages))


def test_the_handover_is_one_step(world) -> None:
    """The store's own promise: the copy's row in and the chat's row out, or
    neither."""
    w, i = world, world.ids
    store = session_manager.get_session_manager()

    def stored(session: str) -> int:
        with sqlite3.connect(store.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM sessions WHERE session_id = ?", (session,)).fetchone()[0]

    session, _thread = w.chat("reem", i["mine"], mark="one step")
    held = copy.deepcopy(w.row(i["mine"], "reem", session))
    there = _copy_of(session, i["family"], held)
    with scope.bind_scope(i["mine"], i["reem"]):
        assert store.hand_over(session, messages=held.messages, to=there, commit=lambda: False) is False
        assert (stored(session), stored(there.session_id)) == (1, 0)
        with pytest.raises(sqlite3.OperationalError):
            store.hand_over(session, messages=held.messages, to=there,
                            commit=_fails(sqlite3.OperationalError("database is locked")))
        assert (stored(session), stored(there.session_id)) == (1, 0)
        seen: list[tuple[int, int]] = []
        assert store.hand_over(session, messages=held.messages, to=there,
                               commit=lambda: not seen.append((stored(session), stored(there.session_id)))) is True
        assert seen == [(1, 1)], "the copy is stored when the journal says so"
        assert (stored(session), stored(there.session_id)) == (0, 1)


def test_the_handover_waits_for_every_writer_and_they_for_it(world) -> None:
    """A transaction under way ends first -- and what it wrote makes the move
    refuse -- and a plain ``put`` waits until the handover ended."""
    import threading

    w, i = world, world.ids
    store = session_manager.get_session_manager()
    session, _thread = w.chat("reem", i["mine"], mark="writers")
    held = copy.deepcopy(w.row(i["mine"], "reem", session))
    there = _copy_of(session, i["family"], held)
    inside, go_on, answers = threading.Event(), threading.Event(), []

    def writes() -> None:
        with scope.bind_scope(i["mine"], i["reem"]), store.transact(session) as now:
            inside.set()
            go_on.wait(10)
            now.add_message("user", "one more", author=i["reem"])

    def hands_over() -> None:
        with scope.bind_scope(i["mine"], i["reem"]):
            answers.append(store.hand_over(session, messages=held.messages, to=there, commit=lambda: True))

    writer, mover = threading.Thread(target=writes), threading.Thread(target=hands_over)
    writer.start()
    assert inside.wait(10)
    mover.start()
    mover.join(0.3)
    assert mover.is_alive() and answers == [], "it waits for the transaction"
    go_on.set()
    writer.join(10)
    mover.join(10)
    assert answers == [False] and len(w.row(i["mine"], "reem", session).messages) == 3

    # A plain put -- a chat app's own save -- meanwhile.
    now = copy.deepcopy(w.row(i["mine"], "reem", session))
    late = copy.deepcopy(now)
    late.add_message("user", "late", author=i["reem"])
    putters: list[threading.Thread] = []

    def puts() -> None:
        with scope.bind_scope(i["mine"], i["reem"]):
            store.put(late)

    def commit() -> bool:
        putters.append(threading.Thread(target=puts))
        putters[-1].start()
        putters[-1].join(0.3)
        assert putters[-1].is_alive(), "it waits for the handover"
        return True

    with scope.bind_scope(i["mine"], i["reem"]):
        assert store.hand_over(session, messages=now.messages, to=there, commit=commit) is True
    putters[0].join(10)
    assert w.row(i["mine"], "reem", session) is None and session not in w.listed("reem", i["mine"])


def test_settling_waits_for_the_move_that_runs_and_reads_again(world) -> None:
    """The sweep beside a move whose request still runs: it waits for the
    thread's lock, and leaves alone what that request settled itself."""
    from kazma_ui.chat_move import settle_chat_moves
    from kazma_ui.thread_locks import thread_mutation

    w, _i = world, world.ids
    _session, thread, entry = _left(w, "running", "beside")

    async def beside() -> list[Any]:
        async with thread_mutation(thread):
            settling = asyncio.ensure_future(settle_chat_moves(None))
            done, _pending = await asyncio.wait({settling}, timeout=0.5)
            assert not done and threads.record_of(entry["new_thread"]) is not None, "it waits for the lock"
            chat_moves.settle_move("running")  # the move's own request ended it
        return await settling

    assert asyncio.run(beside()) == []
    assert threads.record_of(entry["new_thread"]) is not None, "and touches nothing of it"


def test_an_error_settling_one_move_does_not_stop_the_next(world, monkeypatch) -> None:
    from kazma_ui import chat_move

    w, _i = world, world.ids
    _left(w, "a-first", "first")
    _session, _thread, second = _left(w, "b-second", "second")
    real = chat_move._settle_one

    async def settle_one(attempt: str, checkpoints: Any) -> str:
        if attempt == "a-first":
            raise _psycopg_error()
        return await real(attempt, checkpoints)

    monkeypatch.setattr(chat_move, "_settle_one", settle_one)
    assert w.settle() == [{"attempt": "a-first", "outcome": "waiting"}, {"attempt": "b-second", "outcome": "put_back"}]
    assert threads.record_of(second["new_thread"]) is None and set(chat_moves.moves_under_way()) == {"a-first"}


def test_an_entry_that_names_no_chat_is_dropped(world) -> None:
    get_config_store().set(chat_moves.MOVES_KEY, {"odd": {"stage": "copying"}}, category="system")
    assert world.settle() == [{"attempt": "odd", "outcome": "dropped"}]
    assert chat_moves.moves_under_way() == {}


def test_an_update_of_a_move_that_ended_records_nothing() -> None:
    chat_moves.record_move("ended", {"done": ["chat"]})
    assert "ended" not in chat_moves.moves_under_way()
    assert chat_moves.advance_move("ended", at="copying", to="copied") is False


def test_the_maintenance_sweep_settles_on_the_servers_loop(world) -> None:
    """The sweep runs in a worker thread; the thread locks and checkpoints
    live on the server's loop, where the settling runs."""
    from kazma_core.memory import worker_bootstrap
    from kazma_ui.chat_move import settler

    w = world
    _session, _thread, entry = _left(w, "swept", "sweep")
    sweep = dict(worker_bootstrap._MAINTENANCE_SWEEPS)["chat moves"]

    async def server() -> None:
        chat_moves.register_settler(settler(asyncio.get_running_loop(), lambda: w.held["manager"]))
        try:
            await asyncio.to_thread(sweep)
        finally:
            chat_moves.register_settler(None)

    sweep()  # no app running: nothing settles it, and nothing fails
    assert set(chat_moves.moves_under_way()) == {"swept"}
    w.client.portal.call(server)
    assert chat_moves.moves_under_way() == {} and threads.record_of(entry["new_thread"]) is None


# ── the line a shared space keeps ─────────────────────────────────────────


def test_only_a_take_out_leaves_a_line(world) -> None:
    w, i = world, world.ids
    session, _thread = w.chat("reem", i["mine"], mark="line")
    shared = w.moved("reem", i["mine"], session, i["family"])
    assert chat_moves.taken_out_chats(i["mine"]) == []
    w.moved("reem", i["family"], shared, i["mine"])
    assert [line["by"] for line in chat_moves.taken_out_chats(i["family"])] == [i["reem"]]


def test_the_line_is_written_once_however_often_its_step_runs() -> None:
    began = time.time()
    line = {"space_id": "once", "thread_id": "t-once", "title": "a chat", "by": "reem"}
    for _ in range(2):
        chat_moves.record_taken_out(**line, since=began)
    assert len(chat_moves.taken_out_chats("once")) == 1
    # Taken out again, shared in between: a line of its own.
    chat_moves.record_taken_out(**line, since=began + 3600)
    assert len(chat_moves.taken_out_chats("once")) == 2


def test_a_shared_chats_own_messages_name_their_author(world) -> None:
    """Her own messages from before Spaces name nobody; in a shared space
    every message names its author."""
    w, i = world, world.ids
    session, _thread = w.chat("reem", i["mine"], mark="author")
    with scope.bind_scope(i["mine"], i["reem"]):
        store = session_manager.get_session_manager()
        held = store.get(session)
        held.messages[0].pop("author", None)
        store.put(held)
    there = w.row(i["family"], "omar", w.moved("reem", i["mine"], session, i["family"]))
    assert [m.get("author") for m in there.messages] == [i["reem"], None]
    # Taken out, they are as they were written.
    back = w.row(i["mine"], "reem", w.moved("reem", i["family"], there.session_id, i["mine"]))
    assert [m.get("author") for m in back.messages] == [i["reem"], None]


# ── the ordinary delete removes the same things ───────────────────────────


def test_the_delete_route_and_a_move_remove_the_same_things(world) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="deleted")
    w.turn("reem", i["mine"], thread, "question deleted")
    assert w.call("reem", "DELETE", f"/api/chat/sessions/{session}", space=i["mine"]).json() == {"status": "ok"}
    w.nothing_left(i["mine"], "reem", session, thread)
    assert '"code": "chat_deleted"' in _send(w, "reem", i["mine"], session, "more").text
