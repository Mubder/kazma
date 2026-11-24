"""Spaces S2-12: moving one's own chat between one's own space and a shared
one, through the real routes behind the real tenant middleware.

A person shares a chat of their own space into a shared space they are in,
and takes it back out. After each move the chat is in exactly one space: its
row (each space's own list), its checkpoints (each space's own file), its
thread's record (what everything kept by thread follows), the rows that name
the thread beside a space, and the chat apps' pointers. Taking a chat out
leaves the shared space a line -- who, when, the title -- that only the move
writes; a chat others wrote in cannot be taken out. A move that did not end
is settled at start, by the maintenance sweep and by the next move of the
chat. What memory does has its own file (``tests/test_chat_move_memory.py``).

Control: a "private" mark on the chat in the shared space -- the design the
plan turned down -- leaves it in the shared space's list for every other
member.
"""

from __future__ import annotations

import asyncio
import secrets
import sqlite3
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing, contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from kazma_core.config_store import get_config_store
from kazma_core.spaces import chat_moves, identity, people, scope, threads
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
from kazma_ui import auth, space_binding

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

    def chat(self, who: str, space: str, *, mark: str = "", authors: tuple[str, ...] = (),
             session_id: str = "", thread_id: str = "") -> tuple[str, str]:
        """A chat *who* opened in *space*: (session id, thread id)."""
        from kazma_ui.session_manager import get_session_manager

        session_id = session_id or f"s{secrets.token_hex(6)}"
        thread_id = thread_id or f"t{secrets.token_hex(6)}"
        account = self.ids[who]
        with scope.bind_scope(space, account):
            store = get_session_manager()
            session = store.get_or_create(session_id, thread_id=thread_id)
            for author in authors or (account,):
                session.add_message("user", f"question {mark}", author=author)
                session.add_message("assistant", f"answer {mark}")
            session.title = f"title {mark}"
            store.put(session)
        threads.record_thread(thread_id, space, account)
        return session_id, thread_id

    def listed(self, who: str, space: str) -> list[str]:
        got = self.call(who, "GET", "/api/chat/sessions", space=space)
        assert got.status_code == 200, got.text
        return [s["session_id"] for s in got.json()]

    def checkpoint(self, who: str, space: str, thread: str, messages: list[dict[str, Any]] | None = None) -> None:
        made = self.call(who, "POST", "/_t/checkpoint", space=space,
                         json={"thread_id": thread, "messages": messages or []})
        assert made.status_code == 200, made.text

    def messages(self, who: str, space: str, thread: str) -> list[dict[str, Any]]:
        got = self.call(who, "GET", f"/_t/checkpoint/{thread}", space=space)
        assert got.status_code == 200, got.text
        return got.json()["messages"]

    def has_checkpoint(self, who: str, space: str, thread: str) -> bool:
        got = self.call(who, "GET", f"/_t/checkpoint/{thread}", space=space)
        assert got.status_code == 200, got.text
        return bool(got.json()["found"])

    def move(self, who: str, space: str, session_id: str, to: str) -> Any:
        return self.call(who, "POST", f"/api/chat/sessions/{session_id}/move", space=space, json={"to_space": to})


@pytest.fixture
def world(monkeypatch):
    from kazma_gateway.stores.checkpoint import create_checkpoint_manager
    from kazma_ui.chat_move import create_chat_move_router
    from kazma_ui.sse_chat import create_sse_chat_router
    from langgraph.checkpoint.base import empty_checkpoint

    monkeypatch.setattr(scope, "_enforced", True)
    monkeypatch.setattr(threads, "_spaces", {})
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
    held: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        @asynccontextmanager
        async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
            held["manager"] = await create_checkpoint_manager(str(Path(tmp) / "checkpoints.db"))
            try:
                yield
            finally:
                await held["manager"].close()

        app = FastAPI(lifespan=lifespan)
        app.middleware("http")(auth.create_tenant_middleware())
        app.include_router(create_sse_chat_router(graph=None))
        held["graph"] = NoCardGraph()
        app.include_router(create_chat_move_router(graph_getter=lambda: held["graph"],
                                                   checkpoints_getter=lambda: held["manager"]))

        def _config(thread_id: str) -> dict[str, Any]:
            return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

        @app.post("/_t/checkpoint")
        async def _write(request: Request) -> dict[str, Any]:
            body = await request.json()
            checkpoint = empty_checkpoint()
            if body.get("messages"):
                checkpoint["channel_values"] = {"messages": body["messages"]}
            await held["manager"].aput(_config(body["thread_id"]), checkpoint,
                                       {"source": "input", "step": 0, "writes": {}}, {})
            return {"ok": True}

        @app.get("/_t/checkpoint/{thread_id}")
        async def _read(thread_id: str) -> dict[str, Any]:
            found = await held["manager"].aget_tuple(_config(thread_id))
            values = (found.checkpoint.get("channel_values") or {}) if found is not None else {}
            return {"found": found is not None, "messages": list(values.get("messages") or [])}

        try:
            with TestClient(app, backend_options={"loop_factory": asyncio.SelectorEventLoop}) as client:
                yield World(client, held, ids)
        finally:
            get_config_store().delete(identity.REGISTRY_KEY)
            get_config_store().delete(chat_moves.MOVES_KEY)
            from kazma_core.security.vault import reset_posture_cache

            reset_posture_cache()


# ── one space after each move ─────────────────────────────────────────────


def test_shared_then_taken_out_is_in_one_space_each_time(world) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="holiday plans")
    w.checkpoint("reem", i["mine"], thread)

    preview = w.call("reem", "GET", f"/api/chat/sessions/{session}/move", space=i["mine"]).json()
    assert preview == {"ok": True, "turns": 1, "others_wrote": False}

    shared = w.move("reem", i["mine"], session, i["family"])
    assert shared.status_code == 200, shared.text
    assert shared.json()["taken_out"] is False
    assert session not in w.listed("reem", i["mine"])
    assert session in w.listed("reem", i["family"]) and session in w.listed("omar", i["family"])
    assert threads.record_of(thread).space_id == i["family"]
    assert threads.record_of(thread).opener == i["reem"], "the opener stays"
    assert w.has_checkpoint("reem", i["family"], thread), "its history came with it"
    assert not w.has_checkpoint("reem", i["mine"], thread), "a read in the space it left finds nothing"
    with scope.bind_scope(i["family"], i["reem"]):
        from kazma_ui.session_manager import get_session_manager

        moved = get_session_manager().get(session)
    assert [m.get("author") for m in moved.messages if m["role"] == "user"] == [i["reem"]]
    assert chat_moves.moves_under_way() == {}, "the journal entry went"
    assert w.call("omar", "GET", "/api/chat/taken-out", space=i["family"]).json()["lines"] == []

    back = w.move("reem", i["family"], session, i["mine"])
    assert back.status_code == 200, back.text
    assert back.json()["taken_out"] is True
    assert session not in w.listed("omar", i["family"])
    assert session in w.listed("reem", i["mine"])
    assert threads.record_of(thread).space_id == i["mine"]
    assert w.has_checkpoint("reem", i["mine"], thread)
    lines = w.call("omar", "GET", "/api/chat/taken-out", space=i["family"]).json()["lines"]
    assert [(line["title"], line["by_name"]) for line in lines] == [("title holiday plans", "Reem")]
    # The line holds the title it had, never a word of the chat.
    assert "question" not in str(lines) and "answer" not in str(lines)


def test_control_a_private_mark_leaves_it_in_the_shared_space(world) -> None:
    """The design the plan turned down: the chat stays in the shared space
    with a mark. Every other member still lists it."""
    from kazma_ui.session_manager import get_session_manager

    w, i = world, world.ids
    session, _thread = w.chat("reem", i["family"], mark="my diary")
    with scope.bind_scope(i["family"], i["reem"]):
        store = get_session_manager()
        marked = store.get(session)
        marked.messages[0]["private"] = True
        store.put(marked)
    assert session in w.listed("omar", i["family"])


# ── who may move what ─────────────────────────────────────────────────────


def test_a_chat_others_wrote_in_cannot_be_taken_out(world) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["family"], mark="the trip", authors=(world.ids["reem"], world.ids["omar"]))
    preview = w.call("reem", "GET", f"/api/chat/sessions/{session}/move", space=i["family"]).json()
    assert preview["others_wrote"] is True
    refused = w.move("reem", i["family"], session, i["mine"])
    assert refused.status_code == 409 and refused.json()["error"] == "others_wrote"
    assert session in w.listed("omar", i["family"])
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


def test_a_chat_of_that_id_in_the_target_is_never_written_over(world) -> None:
    w, i = world, world.ids
    session, _thread = w.chat("reem", i["mine"], mark="hers")
    w.chat("reem", i["family"], mark="there", session_id=session)
    refused = w.move("reem", i["mine"], session, i["family"])
    assert refused.status_code == 409 and refused.json()["error"] == "already_there", refused.text
    from kazma_ui.chat_move import _session_in

    there = _session_in(i["family"], i["reem"], session)
    assert there.title == "title there" and chat_moves.moves_under_way() == {}


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


# ── everything kept beside a space follows; pointers go ───────────────────


def test_rows_beside_a_space_follow_and_pointers_go(world, monkeypatch) -> None:
    from kazma_core.agent import artifacts
    from kazma_core.agent.artifacts import get_artifact_store

    # A store an earlier test opened elsewhere: this one opens in this data dir.
    monkeypatch.setattr(artifacts, "_store", None)
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="draft chat")
    get_artifact_store().put_scratchpad(i["mine"], thread, "notes", "kept by the chat")
    get_config_store().set("active_thread.telegram:42", thread, category="session")
    assert w.move("reem", i["mine"], session, i["family"]).status_code == 200
    with closing(sqlite3.connect(_store("agent_artifacts.db"))) as conn:
        spaces = {r[0] for r in conn.execute("SELECT tenant_id FROM agent_artifacts WHERE thread_id = ?", (thread,))}
    assert spaces == {i["family"]}
    assert get_config_store().get("active_thread.telegram:42") is None


def _store(name: str) -> str:
    from kazma_core.spaces import space_data

    return str(dict(space_data.store_paths())[name])


# ── a move a stop cut off is settled at start ─────────────────────────────


def test_a_move_cut_off_before_its_switch_is_put_back(world) -> None:
    from kazma_ui.chat_move import _copy_row, _session_in, settle_chat_moves

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="cut before")
    source = _session_in(i["mine"], i["reem"], session)
    chat_moves.record_move("a1", {"thread": thread, "session": session, "source": i["mine"],
                                  "target": i["family"], "by": i["reem"], "taken_out": False,
                                  "title": "t", "stage": "copying"})
    _copy_row(source, i["family"], i["reem"], taken_out=False)
    settled = asyncio.run(settle_chat_moves(None))
    assert settled == [{"attempt": "a1", "outcome": "put_back"}]
    assert w.move("reem", i["mine"], session, i["family"]).status_code == 200, "nothing holds the next move"
    assert w.move("reem", i["family"], session, i["mine"]).status_code == 200
    assert session in w.listed("reem", i["mine"]) and session not in w.listed("omar", i["family"])
    assert chat_moves.moves_under_way() == {}


def test_a_move_cut_off_after_its_switch_is_finished(world) -> None:
    from kazma_ui.chat_move import _copy_row, _session_in, settle_chat_moves

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="cut after")
    source = _session_in(i["mine"], i["reem"], session)
    chat_moves.record_move("a2", {"thread": thread, "session": session, "source": i["mine"],
                                  "target": i["family"], "by": i["reem"], "taken_out": False,
                                  "title": "t", "stage": "switched"})
    _copy_row(source, i["family"], i["reem"], taken_out=False)
    threads.move_thread(thread, i["family"])
    settled = asyncio.run(settle_chat_moves(None))
    assert settled == [{"attempt": "a2", "outcome": "finished"}]
    assert session not in w.listed("reem", i["mine"]) and session in w.listed("omar", i["family"])
    assert chat_moves.moves_under_way() == {}


# ── every table that names a thread beside a space is declared ────────────


#: The columns that name a chat: its thread, or its session (the chat store's
#: rows and spool are kept by session).
_CHAT_COLUMNS = {"thread_id", "session_id"}


def _threaded_tables(stores: list[tuple[str, Path]]) -> set[tuple[str, str]]:
    from kazma_core.spaces import space_data

    found: set[tuple[str, str]] = set()
    for store, path in stores:
        with closing(sqlite3.connect(str(path))) as conn:
            for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
                columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
                if columns & _CHAT_COLUMNS and columns & set(space_data._SPACE_COLUMNS):
                    found.add((store, table))
    return found


def test_every_table_naming_a_thread_beside_a_space_moves_or_stays(monkeypatch, tmp_path) -> None:
    """Every table of a row store with a thread column and a space column is
    in ``MOVED_COLUMNS`` (it follows the chat), ``MOVED_BY_THEIR_CODE`` (it
    follows by its own code) or ``STAYS`` (with why): a new one fails here.
    Each moved table has the column it names."""
    from kazma_core.spaces import space_data

    from tests._space_world import plant_world

    gen = plant_world(monkeypatch, tmp_path)
    next(gen)
    try:
        stores = space_data.store_paths()
        found = _threaded_tables(stores)
        declared = _declared()
        assert found - declared == set(), "a table names a thread beside a space and is not declared"
        by_name = dict(stores)
        for (store, table), column in chat_moves.MOVED_COLUMNS.items():
            if store not in by_name:
                continue
            with closing(sqlite3.connect(str(by_name[store]))) as conn:
                columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
            assert {column} <= columns and columns & _CHAT_COLUMNS, (store, table)
    finally:
        gen.close()


def test_control_an_undeclared_table_fails_the_gate(tmp_path) -> None:
    planted = tmp_path / "planted.db"
    with closing(sqlite3.connect(planted)) as conn:
        conn.execute("CREATE TABLE notes (thread_id TEXT, tenant_id TEXT, body TEXT)")
    found = _threaded_tables([("planted.db", planted)])
    assert found - _declared() == {("planted.db", "notes")}


def _declared() -> set[tuple[str, str]]:
    return set(chat_moves.MOVED_COLUMNS) | set(chat_moves.MOVED_BY_THEIR_CODE) | set(chat_moves.STAYS)


# ── the chat move review (2026-11-23): each point fails on 36675f3fa ──────


def test_h2_a_failure_after_the_record_changed_keeps_the_journal_and_finishes(world, monkeypatch) -> None:
    """The switch's own bookkeeping fails once the record names the target:
    the chat is there, so the move finishes; the journal is never dropped
    with the chat in both spaces."""
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="h2")
    real = chat_moves.record_move

    def flaky(attempt: str, fields: dict[str, Any]) -> None:
        if fields == {"stage": "switched"}:
            raise sqlite3.OperationalError("database is locked")
        real(attempt, fields)

    monkeypatch.setattr(chat_moves, "record_move", flaky)
    moved = w.move("reem", i["mine"], session, i["family"])
    assert moved.status_code == 200, moved.text
    assert session not in w.listed("reem", i["mine"]) and session in w.listed("omar", i["family"])
    assert chat_moves.moves_under_way() == {}


def test_h3_the_moved_chats_sockets_and_streams_elsewhere_end(world, monkeypatch) -> None:
    """Every socket and stream of the chat open under another space than the
    one it moved to is closed or ended; one in its new space stays."""
    from kazma_ui import live_connections
    from kazma_ui.sse_chat import _streaming

    w, i = world, world.ids
    session, thread = w.chat("reem", i["family"], mark="h3")
    told: list[tuple[set[str], str]] = []
    with monkeypatch.context() as patch:
        patch.setattr(live_connections, "thread_moved", lambda chats, to: told.append((set(chats), to)) or 0)
        assert w.move("reem", i["family"], session, i["mine"]).status_code == 200
    assert told == [({session, thread}, i["mine"])]
    assert _streaming._moved_away(thread, i["family"]) and not _streaming._moved_away(thread, i["mine"])

    class _Socket:
        def __init__(self) -> None:
            self.closed: tuple[int, str] | None = None

        async def close(self, code: int, reason: str) -> None:
            self.closed = (code, reason)

    async def run() -> tuple[_Socket, _Socket]:
        omars, reems = _Socket(), _Socket()
        with live_connections.socket_watched(i["omar"], i["family"], omars, chat=session), \
                live_connections.socket_watched(i["reem"], i["mine"], reems, chat=session):
            assert live_connections.thread_moved({session, thread}, i["mine"]) == 1
            for _ in range(20):
                await asyncio.sleep(0.01)
        return omars, reems

    omars, reems = asyncio.run(run())
    assert omars.closed == (live_connections.CLOSE_CODE, "moved") and reems.closed is None

    async def streams() -> tuple[int, int]:
        omars, reems = asyncio.Queue(), asyncio.Queue()
        with live_connections.stream_watched(i["omar"], i["family"], omars, "wake", chat=thread),                 live_connections.stream_watched(i["reem"], i["mine"], reems, "wake", chat=thread):
            assert live_connections.thread_moved({session, thread}, i["mine"]) == 1
            assert await asyncio.wait_for(omars.get(), 10) == "wake"
        return omars.qsize(), reems.qsize()

    assert asyncio.run(streams()) == (0, 0), "and a stream in its new space is left to run"


def test_h6_a_chat_apps_chat_is_not_moved(world) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="telegram", session_id=f"gw-telegram-{secrets.token_hex(4)}",
                             thread_id=f"gw-telegram-{secrets.token_hex(4)}")
    refused = w.move("reem", i["mine"], session, i["family"])
    assert refused.status_code == 409 and refused.json()["error"] == "chat_app_chat", refused.text
    assert threads.record_of(thread).space_id == i["mine"]


def test_m1_turn_notes_and_time_travel_steps_do_not_move(world, monkeypatch) -> None:
    from kazma_core import time_travel
    from kazma_core.turn_notes import TURN_NOTE_KINDS

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="m1")
    note = next(iter(TURN_NOTE_KINDS.values()))[0] + " what the personal space recalled"
    w.checkpoint("reem", i["mine"], thread, [{"role": "system", "content": note},
                                              {"role": "user", "content": "question m1"}])
    forgot: list[list[str]] = []
    monkeypatch.setattr(time_travel, "forget_thread_snapshots", lambda keys, **_k: forgot.append(list(keys)) or 0)
    assert w.move("reem", i["mine"], session, i["family"]).status_code == 200
    assert w.messages("reem", i["family"], thread) == [{"role": "user", "content": "question m1"}]
    assert forgot and set(forgot[0]) == {session, thread}


def test_m5_the_approval_check_fails_closed_and_reads_the_registry(world) -> None:
    from kazma_core.safety import hitl_gates

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="m5")
    w.held["graph"] = None
    unknown = w.move("reem", i["mine"], session, i["family"])
    assert unknown.status_code == 503 and unknown.json()["error"] == "approval_unknown", unknown.text
    w.held["graph"] = NoCardGraph()
    hitl_gates.register_gate(hitl_gates.GateRow(gate_id=f"g{secrets.token_hex(4)}", thread_id=thread,
                                                tool="shell_exec", tenant_id=i["mine"]))
    waiting = w.move("reem", i["mine"], session, i["family"])
    assert waiting.status_code == 409 and waiting.json()["error"] == "approval_pending", waiting.text
    assert threads.record_of(thread).space_id == i["mine"]


# ── the move's re-review (2026-11-24): each point fails on 262c516c9 ──────


def _fails(error: Exception) -> Any:
    def raise_it(*_a: Any, **_k: Any) -> Any:
        raise error

    return raise_it


def _psycopg_error() -> Exception:
    psycopg = pytest.importorskip("psycopg")
    return psycopg.OperationalError("server closed the connection unexpectedly")


def _row(space: str, account: str, session_id: str) -> Any:
    from kazma_ui.session_manager import get_session_manager

    with scope.bind_scope(space, account):
        return get_session_manager().get(session_id)


_SECRET = "pelican diagnosis"
_CALL = {"id": "c1", "type": "function", "function": {"name": "memory_search", "arguments": "{}"}}


def test_n3_what_memory_answered_stays_where_it_was_said(world) -> None:
    """The row's "Memory used" part and row, and what its memory tools
    answered, do not move; nor does the state's copy of the answer, nor the
    thread's journaled frames. The conversation does, steps included."""
    from kazma_ui.delivery import get_turn_broker

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="n3")
    with scope.bind_scope(i["mine"], i["reem"]):
        from kazma_ui.session_manager import get_session_manager

        store = get_session_manager()
        held = store.get(session)
        answer = held.messages[-1]
        answer["parts"] = [
            {"type": "memory", "counts": {"turn": 1}, "hits": [{"kind": "turn", "text": _SECRET}]},
            {"type": "tool", "name": "memory_search", "call_id": "c1", "result": _SECRET, "state": "done"},
            {"type": "tool", "name": "web_search", "call_id": "c2", "result": "a page", "state": "done"},
            {"type": "text", "text": "answer n3"},
        ]
        answer["activity"] = [
            {"id": "memory", "kind": "memory", "title": "Memory used", "hits": [{"text": _SECRET}]},
            {"id": "tool#c1", "kind": "tool", "title": "memory_search", "detail": _SECRET, "state": "done"},
            {"id": "tool#c2", "kind": "tool", "title": "web_search", "detail": "a page", "state": "done"},
        ]
        store.put(held)
    w.checkpoint("reem", i["mine"], thread, [
        {"role": "user", "content": "question n3"},
        {"role": "assistant", "content": "", "tool_calls": [_CALL]},
        {"role": "tool", "tool_call_id": "c1", "content": _SECRET},
        {"role": "assistant", "content": "answer n3"},
    ])
    broker = get_turn_broker()
    broker._journal.append(thread, {"type": "memory_explain", "data": {"episodes": [{"content": _SECRET}]}})
    head = broker.head_seq(thread)

    assert w.move("reem", i["mine"], session, i["family"]).status_code == 200
    moved = _row(i["family"], i["omar"], session)
    assert _SECRET not in str(moved.messages)
    assert "answer n3" in str(moved.messages) and "a page" in str(moved.messages)
    assert [p["type"] for p in moved.messages[-1]["parts"]] == ["tool", "tool", "text"], "the steps stay"
    state = w.messages("omar", i["family"], thread)
    assert _SECRET not in str(state)
    assert [m["role"] for m in state] == ["user", "assistant", "tool", "assistant"], "the call keeps its answer"
    frames, gap, _head = broker.resume(thread, 0)
    assert frames == [] and not gap
    assert broker.resume(thread, head - 1 or 1)[0] == [], "nothing is replayed"
    assert broker.head_seq(thread) >= head, "a cursor from before meets a gap, never another frame's number"


class _Snapshot:
    def __init__(self, *, cards: bool) -> None:
        self.next = ("tool_worker",)
        self.tasks = [type("Task", (), {"interrupts": ("card",) if cards else ()})()]


@pytest.mark.parametrize(("cards", "code"), [(False, "turn_unfinished"), (True, "approval_pending")])
def test_n6_a_turn_cut_off_half_way_or_paused_holds_the_move(world, cards, code) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="n6")
    w.held["graph"] = NoCardGraph(_Snapshot(cards=cards))
    refused = w.move("reem", i["mine"], session, i["family"])
    assert refused.status_code == 409 and refused.json()["error"] == code, refused.text
    assert threads.record_of(thread).space_id == i["mine"]


def test_m5_a_state_or_registry_that_cannot_be_read_holds_the_move(world, monkeypatch) -> None:
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
    w.held["graph"] = NoCardGraph()
    monkeypatch.setattr(hitl_gates, "live_gates", _fails(sqlite3.OperationalError("locked")))
    refused = w.move("reem", i["mine"], session, i["family"])
    assert refused.status_code == 503 and refused.json()["error"] == "approval_unknown", refused.text
    assert threads.record_of(thread).space_id == i["mine"]


def test_n8_a_step_postgres_refuses_does_not_hold_the_others_or_the_chat(world, monkeypatch) -> None:
    """The step that failed waits; every other step ran, the source's row
    among them. The sweep finishes it, without a restart."""
    from kazma_ui import chat_move

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="n8")
    w.checkpoint("reem", i["mine"], thread, [{"role": "user", "content": "question n8"}])
    get_config_store().set("active_thread.telegram:n8", thread, category="session")
    with monkeypatch.context() as patch:
        patch.setattr(chat_move, "_drop_pointers", _fails(_psycopg_error()))
        moved = w.move("reem", i["mine"], session, i["family"])
    assert moved.status_code == 200, moved.text
    assert session in w.listed("omar", i["family"]) and session not in w.listed("reem", i["mine"])
    assert not w.has_checkpoint("reem", i["mine"], thread)
    (entry,) = chat_moves.moves_under_way().values()
    assert "pointers" not in entry["done"] and "chat" in entry["done"]
    assert get_config_store().get("active_thread.telegram:n8") == thread

    # The maintenance sweep, as the app wires it (register_settler).
    from kazma_core.memory import worker_bootstrap

    labels = dict(worker_bootstrap._MAINTENANCE_SWEEPS)
    asked: list[str] = []
    chat_moves.register_settler(lambda: asked.append("settled"))
    try:
        labels["chat moves"]()
        assert asked == ["settled"]
        asyncio.run(chat_move.settle_chat_moves(None))
        assert get_config_store().get("active_thread.telegram:n8") is None
        assert chat_moves.moves_under_way() == {}
        labels["chat moves"]()
        assert asked == ["settled"], "with no move recorded it asks nobody"
    finally:
        chat_moves.register_settler(None)


def test_n8_the_next_move_of_the_chat_settles_what_the_last_one_left(world, monkeypatch) -> None:
    from kazma_ui import chat_move

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="n8 next")
    with monkeypatch.context() as patch:
        patch.setattr(chat_move, "_remove_row", _fails(_psycopg_error()))
        assert w.move("reem", i["mine"], session, i["family"]).status_code == 200
    assert len(chat_moves.moves_under_way()) == 1
    assert session in w.listed("reem", i["mine"]), "the row a step could not remove"
    back = w.move("reem", i["family"], session, i["mine"])
    assert back.status_code == 200, back.text
    assert threads.record_of(thread).space_id == i["mine"] and chat_moves.moves_under_way() == {}
    assert session not in w.listed("omar", i["family"])


def test_n8_one_move_that_cannot_be_settled_does_not_stop_the_others(world, monkeypatch) -> None:
    from kazma_ui import chat_move

    w, i = world, world.ids
    chats = [w.chat("reem", i["mine"], mark=f"settle {n}") for n in range(2)]
    for n, (session, thread) in enumerate(chats):
        chat_moves.record_move(f"a{n}", {"thread": thread, "session": session, "source": i["mine"],
                                         "target": i["family"], "by": i["reem"], "taken_out": False,
                                         "title": "t", "stage": "copying"})
    stuck, error, real = chats[0][1], _psycopg_error(), threads.thread_home

    def home(thread: str) -> str:
        if thread == stuck:
            raise error
        return real(thread)

    monkeypatch.setattr(threads, "thread_home", home)
    settled = asyncio.run(chat_move.settle_chat_moves(None))
    assert settled == [{"attempt": "a0", "outcome": "waiting"}, {"attempt": "a1", "outcome": "put_back"}]
    assert list(chat_moves.moves_under_way()) == ["a0"]


def test_n8_a_put_back_that_failed_keeps_its_entry(world, monkeypatch) -> None:
    """The copies a stopped move could not take back keep its journal: the
    next settling takes them (dropped, they stayed in the target for good)."""
    from kazma_ui import chat_move

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="put back")
    with monkeypatch.context() as patch:
        patch.setattr(chat_move, "_switch", _fails(_psycopg_error()))
        patch.setattr(chat_move, "_remove_row", _fails(_psycopg_error()))
        failed = w.move("reem", i["mine"], session, i["family"])
    assert failed.status_code == 503 and failed.json()["error"] == "move_failed", failed.text
    assert len(chat_moves.moves_under_way()) == 1 and session in w.listed("omar", i["family"])
    assert asyncio.run(chat_move.settle_chat_moves(None))[0]["outcome"] == "put_back"
    assert session not in w.listed("omar", i["family"]) and session in w.listed("reem", i["mine"])
    assert threads.record_of(thread).space_id == i["mine"]


_ABOUT = {"role": "system", "content": "[kazma:about-user] Reem is being treated for the pelican diagnosis."}


def _state_to_move() -> list[dict[str, Any]]:
    from kazma_core.turn_notes import TURN_NOTE_KINDS

    note = TURN_NOTE_KINDS["memory_recall"][0] + f": {_SECRET}"
    return [{"role": "system", "content": note}, _ABOUT, {"role": "user", "content": "question n13"}]


class _Saver:
    """The raw saver of a running world, reached on its own loop."""

    def __init__(self, w: World) -> None:
        self.w = w

    def rows(self, table: str, thread: str) -> list[Any]:
        async def read() -> list[Any]:
            saver = self.w.held["manager"]._saver
            if hasattr(saver.conn, "connection"):
                async with saver.conn.connection() as conn:
                    cur = await conn.execute(f"SELECT * FROM {table} WHERE thread_id = %s", (thread,))
                    return list(await cur.fetchall())
            async with saver.conn.execute(f"SELECT * FROM {table} WHERE thread_id = ?", (thread,)) as cur:
                return list(await cur.fetchall())

        return self.w.client.portal.call(read)

    def write(self, thread: str) -> None:
        """A pending write of the newest checkpoint, as a node leaves one."""
        async def put() -> None:
            saver = self.w.held["manager"]._saver
            config = {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
            found = await saver.aget_tuple(config)
            await saver.aput_writes(found.config, [("messages", _state_to_move())], "task-1")

        self.w.client.portal.call(put)

    def aged(self, thread: str) -> None:
        """An older checkpoint of the thread, with the space's notes in it."""
        async def put() -> None:
            from langgraph.checkpoint.base import empty_checkpoint

            saver = self.w.held["manager"]._saver
            checkpoint = empty_checkpoint()
            version = saver.get_next_version(None, None)
            checkpoint["channel_values"] = {"messages": [*_state_to_move(), {"role": "user", "content": _SECRET}]}
            checkpoint["channel_versions"] = {"messages": version}
            await saver.aput({"configurable": {"thread_id": thread, "checkpoint_ns": ""}}, checkpoint,
                             {"source": "input", "step": 0, "writes": {}}, {"messages": version})

        self.w.client.portal.call(put)

    def newest(self, thread: str) -> None:
        async def put() -> None:
            from langgraph.checkpoint.base import empty_checkpoint

            saver = self.w.held["manager"]._saver
            config = {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
            before = await saver.aget_tuple(config)
            checkpoint = empty_checkpoint()
            versions = {"messages": saver.get_next_version(before.checkpoint["channel_versions"]["messages"], None),
                        "active_goal": saver.get_next_version(None, None),
                        "_post_turn_memory": saver.get_next_version(None, None)}
            checkpoint["channel_values"] = {"messages": _state_to_move(), "active_goal": "plan the trip",
                                            "_post_turn_memory": {"tenant_id": "a", "turn": 1}}
            checkpoint["channel_versions"] = versions
            await saver.aput(before.config, checkpoint, {"source": "loop", "step": 1, "writes": {}}, versions)

        self.w.client.portal.call(put)


def _one_shared_table(w: World, thread: str) -> None:
    saver = _Saver(w)
    saver.aged(thread)
    saver.newest(thread)
    saver.write(thread)


def _cleaned_in_place(w: World, thread: str) -> None:
    saver = _Saver(w)
    assert len(saver.rows("checkpoints", thread)) == 1, "the older states are not there to be read"
    assert _SECRET not in str(saver.rows("checkpoints", thread))
    assert w.messages("omar", w.ids["family"], thread) == [{"role": "user", "content": "question n13"}]


def test_n13_on_one_shared_table_the_state_is_cleaned_in_place(world, monkeypatch) -> None:
    """Every space's checkpoints in one table keyed by thread, as on the live
    install's Postgres: what the source space recalled is not in the thread's
    state, its older states or its pending writes."""
    from kazma_gateway.stores.checkpoint import CheckpointManager

    monkeypatch.setattr(CheckpointManager, "_shared_table", lambda self: True)
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="n13")
    _one_shared_table(w, thread)
    assert len(_Saver(w).rows("checkpoints", thread)) == 2 and _Saver(w).rows("writes", thread)
    assert w.move("reem", i["mine"], session, i["family"]).status_code == 200
    _cleaned_in_place(w, thread)
    assert _Saver(w).rows("writes", thread) == []


@pytest.mark.postgres
def test_n13_on_real_postgres_the_state_is_cleaned_in_place(world) -> None:
    from tests import _tt_graph as tt

    if tt.postgres_dsn() is None:
        pytest.skip("needs a real Postgres (KAZMA_DATABASE_URL + KAZMA_TEST_ALLOW_REAL_DB=1)")
    w, i = world, world.ids
    assert w.held["manager"]._shared_table(), "the world's checkpoint store is not on Postgres"
    session, thread = w.chat("reem", i["mine"], mark="n13 pg")
    _one_shared_table(w, thread)
    saver = _Saver(w)
    old = {r["version"] for r in saver.rows("checkpoint_blobs", thread)}
    assert len(saver.rows("checkpoints", thread)) == 2 and saver.rows("checkpoint_writes", thread) and old
    assert w.move("reem", i["mine"], session, i["family"]).status_code == 200
    _cleaned_in_place(w, thread)
    assert saver.rows("checkpoint_writes", thread) == []
    blobs = saver.rows("checkpoint_blobs", thread)
    assert all(_SECRET.encode() not in bytes(r["blob"] or b"") for r in blobs), blobs
    assert {r["channel"] for r in blobs} == {"messages"}, "the last turn's memory hand-off went too"
    assert old.isdisjoint(r["version"] for r in blobs)


def test_n13_each_spaces_own_file_gets_the_cleaned_state_alone(world) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="n13 files")
    w.checkpoint("reem", i["mine"], thread, [*_state_to_move(), {"role": "user", "content": _SECRET}])
    w.checkpoint("reem", i["mine"], thread, _state_to_move())

    async def rows(space: str, table: str) -> list[Any]:
        saver = await w.held["manager"]._saver_of(space)
        async with saver.conn.execute(f"SELECT * FROM {table} WHERE thread_id = ?", (thread,)) as cur:
            return list(await cur.fetchall())

    async def pending() -> None:
        saver = await w.held["manager"]._saver_of(i["mine"])
        found = await saver.aget_tuple({"configurable": {"thread_id": thread, "checkpoint_ns": ""}})
        await saver.aput_writes(found.config, [("messages", _state_to_move())], "task-1")

    call = w.client.portal.call
    call(pending)
    assert len(call(rows, i["mine"], "writes")) == 1
    assert w.move("reem", i["mine"], session, i["family"]).status_code == 200
    assert len(call(rows, i["family"], "checkpoints")) == 1 and call(rows, i["family"], "writes") == []
    assert _SECRET not in str(call(rows, i["family"], "checkpoints"))
    assert w.messages("omar", i["family"], thread) == [{"role": "user", "content": "question n13"}]
    assert call(rows, i["mine"], "checkpoints") == [] and call(rows, i["mine"], "writes") == []


def test_the_state_of_an_earlier_attempt_is_not_left_beside_this_one(world) -> None:
    w, i = world, world.ids
    _session, thread = w.chat("reem", i["mine"], mark="attempts")

    async def move_state() -> bool:
        return await w.held["manager"].amove_state(thread, from_space=i["mine"], to_space=i["family"], clean=dict)

    async def there() -> int:
        saver = await w.held["manager"]._saver_of(i["family"])
        async with saver.conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", (thread,)) as cur:
            return int((await cur.fetchone())[0])

    call = w.client.portal.call
    assert call(move_state) is False, "a chat with no state yet has none to move"
    for text in ("the first attempt", "and a turn after it"):
        w.checkpoint("reem", i["mine"], thread, [{"role": "user", "content": text}])
        assert call(move_state) is True
    assert call(there) == 1


def _carries_on(w: World) -> None:
    """A real graph, two turns in Reem's own space, the move, a turn after it."""
    from tests import _tt_graph as tt

    i = w.ids
    session, thread = w.chat("reem", i["mine"], mark="graph")
    graph = tt.build_graph(None, [], w.held["manager"])
    w.held["graph"] = graph
    call = w.client.portal.call

    def turn(space: str, text: str) -> None:
        async def run() -> None:
            with scope.bind_scope(space, i["reem"]):
                await tt.run_turn(graph, thread, text)

        call(run)

    async def state() -> Any:
        return await graph.aget_state(tt.config(thread))

    for text in ("plan the garden", "and the fence"):
        turn(i["mine"], text)
    assert w.move("reem", i["mine"], session, i["family"]).status_code == 200
    moved = call(state)
    assert moved.next == () and not moved.tasks, "nothing is left to run in the space it moved to"
    turn(i["family"], "what did we plan?")
    said = [m["content"] for m in call(state).values["messages"]]
    assert said == ["plan the garden", "Answer to plan the garden", "and the fence", "Answer to and the fence",
                    "what did we plan?", "Answer to what did we plan?"]
    assert call(state).next == ()


def test_a_real_graph_carries_on_from_the_state_that_moved(world) -> None:
    _carries_on(world)


def test_a_real_graph_carries_on_on_one_shared_table(world, monkeypatch) -> None:
    from kazma_gateway.stores.checkpoint import CheckpointManager

    monkeypatch.setattr(CheckpointManager, "_shared_table", lambda self: True)
    _carries_on(world)


@pytest.mark.postgres
def test_a_real_graph_carries_on_on_real_postgres(world) -> None:
    from tests import _tt_graph as tt

    if tt.postgres_dsn() is None:
        pytest.skip("needs a real Postgres (KAZMA_DATABASE_URL + KAZMA_TEST_ALLOW_REAL_DB=1)")
    _carries_on(world)


def test_n18_nothing_counts_moves_so_a_crash_leaves_nothing_to_release(world) -> None:
    """The daily bound went with the rebuild's model calls (the route's rate
    limit stands): a move a crash cut off never holds the next one."""
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="n18")
    chat_moves.record_move("crashed", {"thread": thread, "session": session, "source": i["mine"],
                                       "target": i["family"], "by": i["reem"], "taken_out": False,
                                       "title": "t", "stage": "copying"})
    for source, target in ((i["mine"], i["family"]), (i["family"], i["mine"])) * 2:
        assert w.move("reem", source, session, target).status_code == 200, (source, target)
    with closing(chat_moves._connection()) as conn:
        assert "chat_moves_done" not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}


def _shared_file(w: World, space: str, thread: str, text: str) -> str:
    """A file Kazma shared in the chat *thread*: its id."""
    from kazma_core.chat_files import share_file

    with tempfile.TemporaryDirectory() as tmp, scope.bind_scope(space, w.ids["reem"]):
        path = Path(tmp) / "report.txt"
        path.write_text(text, encoding="utf-8")
        return share_file(path, thread_id=thread).id


def _link(w: World, space: str, session: str, file_id: str) -> None:
    from kazma_ui.session_manager import get_session_manager

    with scope.bind_scope(space, w.ids["reem"]):
        store = get_session_manager()
        held = store.get(session)
        held.messages[-1]["content"] += f" [report](/api/chat/files/{file_id})"
        store.put(held)


def _opens(space: str, account: str, file_id: str) -> tuple[str, str] | None:
    """What the link answers in *space*: (the file's text, the chat it is of)."""
    from kazma_core.chat_files import load_shared

    with scope.bind_scope(space, account):
        found = load_shared(file_id)
    return (found[1].read_text(encoding="utf-8"), found[0].thread_id) if found is not None else None


def test_n19_the_chats_shared_files_move_with_it(world) -> None:
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
    assert w.move("reem", i["mine"], session, i["family"]).status_code == 200
    assert _opens(i["family"], i["omar"], mine) == ("the report", thread)
    assert _opens(i["family"], i["omar"], borrowed) == ("a chart", thread), "its chat's members open it"
    assert _opens(i["mine"], i["reem"], mine) is None and _opens(i["mine"], i["reem"], unlinked) is None
    assert _opens(i["mine"], i["reem"], borrowed) == ("a chart", other)


def test_n19_a_move_put_back_leaves_no_file_behind(world, monkeypatch) -> None:
    from kazma_ui import chat_move

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="n19 back")
    file_id = _shared_file(w, i["mine"], thread, "the report")
    with monkeypatch.context() as patch:
        patch.setattr(chat_move, "_switch", _fails(sqlite3.OperationalError("locked")))
        assert w.move("reem", i["mine"], session, i["family"]).status_code == 503
    assert _opens(i["family"], i["omar"], file_id) is None
    assert _opens(i["mine"], i["reem"], file_id) == ("the report", thread)


def test_n19_files_the_target_has_no_room_for_refuse_the_move(world, monkeypatch) -> None:
    from kazma_core.spaces import space_limits

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="n19 full")
    file_id = _shared_file(w, i["mine"], thread, "the report")
    monkeypatch.setattr(space_limits, "disk_refusal", lambda space, incoming=0: "full" if space == i["family"] else None)
    refused = w.move("reem", i["mine"], session, i["family"])
    assert refused.status_code == 507 and refused.json()["error"] == "space_limit", refused.text
    assert threads.record_of(thread).space_id == i["mine"] and session not in w.listed("omar", i["family"])
    assert _opens(i["family"], i["omar"], file_id) is None and chat_moves.moves_under_way() == {}


# ── the single-step reverts the re-review found unnoticed ─────────────────


def _remembering(w: World, space: str, session: str) -> None:
    """The chat's last answer, with the memory it used; its question names no
    author (as a chat from before Spaces)."""
    from kazma_ui.session_manager import get_session_manager

    with scope.bind_scope(space, w.ids["reem"]):
        store = get_session_manager()
        held = store.get(session)
        held.messages[0].pop("author", None)
        held.messages[-1]["parts"] = [
            {"type": "memory", "counts": {"turn": 1}, "hits": [{"kind": "turn", "text": _SECRET}]},
            {"type": "tool", "name": "memory_search", "call_id": "c1", "result": _SECRET, "state": "done"},
        ]
        store.put(held)


def test_a_move_put_back_leaves_the_chat_exactly_as_it_was(world, monkeypatch) -> None:
    import copy

    from kazma_ui import chat_move

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="back")
    _remembering(w, i["mine"], session)
    w.checkpoint("reem", i["mine"], thread, _state_to_move())
    before = copy.deepcopy(_row(i["mine"], i["reem"], session).messages)
    with monkeypatch.context() as patch:
        patch.setattr(chat_move, "_switch", _fails(sqlite3.OperationalError("locked")))
        assert w.move("reem", i["mine"], session, i["family"]).status_code == 503

    async def states(space: str) -> int:
        saver = await w.held["manager"]._saver_of(space)
        async with saver.conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", (thread,)) as cur:
            return int((await cur.fetchone())[0])

    assert _row(i["mine"], i["reem"], session).messages == before, "what was copied is a copy"
    assert w.client.portal.call(states, i["mine"]) == 1 and w.client.portal.call(states, i["family"]) == 0
    assert chat_moves.moves_under_way() == {}


def test_a_shared_chats_own_messages_name_their_author(world) -> None:
    w, i = world, world.ids
    session, _thread = w.chat("reem", i["mine"], mark="author")
    _remembering(w, i["mine"], session)
    assert w.move("reem", i["mine"], session, i["family"]).status_code == 200
    moved = _row(i["family"], i["omar"], session)
    assert [m.get("author") for m in moved.messages if m["role"] == "user"] == [i["reem"]]
    assert all("author" not in m for m in moved.messages if m["role"] == "assistant")


def test_only_a_take_out_leaves_a_line(world) -> None:
    w, i = world, world.ids
    session, _thread = w.chat("reem", i["mine"], mark="line")
    assert w.move("reem", i["mine"], session, i["family"]).status_code == 200
    assert chat_moves.taken_out_chats(i["mine"]) == []
    assert w.move("reem", i["family"], session, i["mine"]).status_code == 200
    assert [line["by"] for line in chat_moves.taken_out_chats(i["family"])] == [i["reem"]]


def test_no_frame_is_there_at_the_switch_and_none_made_in_it_stays(world, monkeypatch) -> None:
    """A member of the target attaching the moment the record names it finds
    no frame of the source's; one journaled while the switch ran goes too."""
    from kazma_ui import chat_move
    from kazma_ui.delivery import get_turn_broker

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="frames")
    broker = get_turn_broker()
    frame = {"type": "memory_explain", "data": {"episodes": [{"content": _SECRET}]}}
    broker._journal.append(thread, frame)
    real, at_the_switch = chat_move._switch, []

    def switch(attempt: str, entry: dict[str, Any]) -> None:
        at_the_switch.extend(broker.resume(thread, 0)[0])
        broker._journal.append(thread, frame)
        real(attempt, entry)

    monkeypatch.setattr(chat_move, "_switch", switch)
    assert w.move("reem", i["mine"], session, i["family"]).status_code == 200
    assert at_the_switch == [] and broker.clear(thread) == 0, "none is left to drop"


def test_a_cancelled_move_is_put_back(world, monkeypatch) -> None:
    from kazma_ui import chat_move

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="cancel")
    real = chat_move._copy

    async def cancelled() -> None:
        copied = asyncio.Event()

        async def copy(*args: Any) -> None:
            await real(*args)
            copied.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(chat_move, "_copy", copy)
        move = asyncio.ensure_future(chat_move._move_chat(
            session_id=session, from_space=i["mine"], to_space=i["family"], by=i["reem"], graph=NoCardGraph()))
        await copied.wait()
        assert session in w.listed("omar", i["family"]), "copied"
        move.cancel()
        with pytest.raises(asyncio.CancelledError):
            await move

    asyncio.run(cancelled())
    assert chat_moves.moves_under_way() == {} and threads.record_of(thread).space_id == i["mine"]
    assert session not in w.listed("omar", i["family"]) and session in w.listed("reem", i["mine"])


def test_a_step_done_is_not_done_again(world, monkeypatch) -> None:
    from kazma_ui import chat_move

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="done")
    ran: list[str] = []
    real = chat_move._remove_row
    with monkeypatch.context() as patch:
        patch.setattr(chat_move, "_remove_row", _fails(_psycopg_error()))
        assert w.move("reem", i["mine"], session, i["family"]).status_code == 200
    (entry,) = chat_moves.moves_under_way().values()
    assert "chat" not in entry["done"] and "pointers" in entry["done"]
    monkeypatch.setattr(chat_move, "_remove_row", lambda *a: ran.append("chat") or real(*a))
    for name in ("_drop_pointers", "_forget_steps", "_remove_files"):
        monkeypatch.setattr(chat_move, name, lambda *_a, n=name: ran.append(n))
    assert asyncio.run(chat_move.settle_chat_moves(None))[0]["outcome"] == "finished"
    assert ran == ["chat"] and chat_moves.moves_under_way() == {}


def test_the_sources_row_is_the_last_to_go(world, monkeypatch) -> None:
    """A finish cut off anywhere before its end still has the source's row."""
    from kazma_ui import chat_move

    w, i = world, world.ids
    session, _thread = w.chat("reem", i["mine"], mark="order")
    ran: list[str] = []
    for name in ("_drop_pointers", "_forget_steps", "_remove_files", "_remove_row"):
        real = getattr(chat_move, name)
        monkeypatch.setattr(chat_move, name, lambda *a, n=name, r=real: ran.append(n) or r(*a))
    assert w.move("reem", i["mine"], session, i["family"]).status_code == 200
    assert ran == ["_drop_pointers", "_forget_steps", "_remove_files", "_remove_row"]


def _left(w: World, attempt: str, mark: str) -> tuple[str, str]:
    """A move a stop cut off before its switch: its session and thread."""
    from kazma_ui.chat_move import _copy_row, _session_in

    i = w.ids
    session, thread = w.chat("reem", i["mine"], mark=mark)
    chat_moves.record_move(attempt, {"thread": thread, "session": session, "source": i["mine"],
                                     "target": i["family"], "by": i["reem"], "taken_out": False,
                                     "title": "t", "stage": "copying"})
    _copy_row(_session_in(i["mine"], i["reem"], session), i["family"], i["reem"], taken_out=False)
    return session, thread


def test_settling_waits_for_the_move_that_runs_and_reads_again(world) -> None:
    """The sweep beside a move whose request still runs: it waits for the
    thread's lock, and leaves alone what that request settled itself."""
    from kazma_ui.chat_move import settle_chat_moves
    from kazma_ui.thread_locks import thread_mutation

    w, i = world, world.ids
    session, thread = _left(w, "running", "beside")

    async def beside() -> list[Any]:
        async with thread_mutation(thread):
            settling = asyncio.ensure_future(settle_chat_moves(None))
            done, _pending = await asyncio.wait({settling}, timeout=0.5)
            assert not done and _row(i["family"], i["reem"], session) is not None, "it waits for the lock"
            chat_moves.settle_move("running")  # the move's own request ended it
        return await settling

    assert asyncio.run(beside()) == []
    assert _row(i["family"], i["reem"], session) is not None, "and touches nothing of it"


def test_an_error_settling_one_move_does_not_stop_the_next(world, monkeypatch) -> None:
    from kazma_ui import chat_move

    w, i = world, world.ids
    _left(w, "a-first", "first")
    second, _thread = _left(w, "b-second", "second")
    real = chat_move._settle_one

    async def settle_one(attempt: str, entry: dict[str, Any], checkpoints: Any) -> str:
        if attempt == "a-first":
            raise _psycopg_error()
        return await real(attempt, entry, checkpoints)

    monkeypatch.setattr(chat_move, "_settle_one", settle_one)
    settled = asyncio.run(chat_move.settle_chat_moves(None))
    assert settled == [{"attempt": "a-first", "outcome": "waiting"}, {"attempt": "b-second", "outcome": "put_back"}]
    assert _row(i["family"], i["reem"], second) is None and set(chat_moves.moves_under_way()) == {"a-first"}


def test_an_entry_that_names_no_chat_is_dropped(world) -> None:
    from kazma_ui.chat_move import settle_chat_moves

    chat_moves.record_move("odd", {"stage": "copying"})
    assert asyncio.run(settle_chat_moves(None)) == [{"attempt": "odd", "outcome": "dropped"}]
    assert chat_moves.moves_under_way() == {}


def test_a_row_a_move_left_behind_is_not_moved_again(world, monkeypatch) -> None:
    """The source's row a failed step left: the chat is recorded elsewhere,
    so there is no such chat in that space."""
    from kazma_ui import chat_move

    w, i = world, world.ids
    session, _thread = w.chat("reem", i["mine"], mark="behind")
    monkeypatch.setattr(chat_move, "_remove_row", _fails(_psycopg_error()))
    assert w.move("reem", i["mine"], session, i["family"]).status_code == 200
    assert session in w.listed("reem", i["mine"]), "left behind"
    again = w.move("reem", i["mine"], session, i["family"])
    assert (again.status_code, again.json()["error"]) == (404, "not_found"), again.text


def test_a_chat_whose_last_move_cannot_be_settled_says_so(world, monkeypatch) -> None:
    from kazma_ui import chat_move

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="unsettled")
    monkeypatch.setattr(chat_move, "_switch", _fails(sqlite3.OperationalError("locked")))
    monkeypatch.setattr(chat_moves, "settle_move", _fails(_psycopg_error()))
    assert w.move("reem", i["mine"], session, i["family"]).json()["error"] == "move_failed"
    again = w.move("reem", i["mine"], session, i["family"])
    assert (again.status_code, again.json()["error"]) == (409, "move_in_progress"), again.text
    assert len(chat_moves.moves_under_way()) == 1 and threads.record_of(thread).space_id == i["mine"]


def test_moving_is_rate_limited(world, monkeypatch) -> None:
    """Its only bound: a move makes no model call (the daily bound went)."""
    from kazma_ui import rate_limit

    w, i = world, world.ids
    monkeypatch.setattr(rate_limit, "_enabled", lambda: True)
    monkeypatch.setattr(rate_limit, "_per_minute", lambda bucket, default: default)
    rate_limit._windows.clear()
    session, _thread = w.chat("reem", i["mine"], mark="rate")
    answers, (frm, to) = [], (i["mine"], i["family"])
    for _ in range(7):
        answers.append(w.move("reem", frm, session, to).status_code)
        frm, to = to, frm
    rate_limit._windows.clear()
    assert answers == [200] * 6 + [429]


def test_the_maintenance_sweep_settles_on_the_servers_loop(world) -> None:
    """The sweep runs in a worker thread; the thread locks and checkpoints
    live on the server's loop, where the settling runs."""
    from kazma_core.memory import worker_bootstrap
    from kazma_ui.chat_move import settler

    w, i = world, world.ids
    session, _thread = _left(w, "swept", "sweep")
    sweep = dict(worker_bootstrap._MAINTENANCE_SWEEPS)["chat moves"]

    async def server() -> None:
        chat_moves.register_settler(settler(asyncio.get_running_loop(), lambda: None))
        try:
            await asyncio.to_thread(sweep)
        finally:
            chat_moves.register_settler(None)

    sweep()  # no app running: nothing settles it, and nothing fails
    assert set(chat_moves.moves_under_way()) == {"swept"}
    asyncio.run(server())
    assert chat_moves.moves_under_way() == {} and session not in w.listed("omar", i["family"])


def test_the_moved_state_is_the_conversation_in_the_target() -> None:
    """What was the last turn's alone, and what is about the space it ran in,
    does not move; the state names the space and the folder it runs in now."""
    values = {
        "messages": [*_state_to_move(), {"role": "tool", "name": "memory_read", "content": _SECRET}],
        "active_goal": "plan the trip", "tenant_id": "a", "workspace_root": "C:/a",
        "_post_turn_memory": {"tenant_id": "a", "turn": 1}, "memory_explain": {"episodes": [_SECRET]},
        "snapshot_id": "s1", "snapshot_iteration": 3, "tool_results": {"c1": _SECRET},
        "tool_calls_done": [{"content": _SECRET}], "tool_calls_pending": [{"id": "c9"}],
        "active_attachments": ["C:/a/secret.pdf"],
    }
    held = dict(values)
    moved = chat_moves.moved_state(values, to_space="b", workspace_root="C:/b")
    assert moved == {
        "messages": [{"role": "user", "content": "question n13"},
                     {"role": "tool", "name": "memory_read", "content": chat_moves.MEMORY_LEFT}],
        "active_goal": "plan the trip", "tenant_id": "b", "workspace_root": "C:/b",
        "tool_results": {}, "tool_calls_done": [], "tool_calls_pending": [], "active_attachments": [],
    }
    assert values == held, "the source's state is not touched"
    assert "workspace_root" not in chat_moves.moved_state(values, to_space="b")


def test_the_line_is_written_once_however_often_its_step_runs() -> None:
    import time

    began = time.time()
    line = {"space_id": "once", "thread_id": "t-once", "title": "a chat", "by": "reem"}
    for _ in range(2):
        chat_moves.record_taken_out(**line, since=began)
    assert len(chat_moves.taken_out_chats("once")) == 1
    chat_moves.record_taken_out(**line, since=began + 3600)  # a later move of the same chat
    assert len(chat_moves.taken_out_chats("once")) == 2


def test_the_owners_rows_from_before_spaces_follow_too(world, monkeypatch) -> None:
    """Rows that name no space are the owner's (N19)."""
    from kazma_core.agent import artifacts
    from kazma_core.agent.artifacts import get_artifact_store

    monkeypatch.setattr(artifacts, "_store", None)
    w, i = world, world.ids
    for thread, mark in (("t-before", ""), ("t-hers", i["mine"])):
        get_artifact_store().put_scratchpad(mark or scope.OWNER_SPACE_ID, thread, "notes", "kept")
    with closing(sqlite3.connect(_store("agent_artifacts.db"))) as conn, conn:
        conn.execute("UPDATE agent_artifacts SET tenant_id = '' WHERE thread_id = 't-before'")

    def space_of(thread: str) -> set[str]:
        with closing(sqlite3.connect(_store("agent_artifacts.db"))) as conn:
            return {r[0] for r in conn.execute("SELECT tenant_id FROM agent_artifacts WHERE thread_id = ?", (thread,))}

    chat_moves.move_thread_rows("t-before", from_space=i["mine"], to_space=i["family"])
    assert space_of("t-before") == {""}, "not another space's to move"
    chat_moves.move_thread_rows("t-before", from_space=scope.OWNER_SPACE_ID, to_space=i["family"])
    assert space_of("t-before") == {i["family"]} and space_of("t-hers") == {i["mine"]}


def test_two_moves_at_once_ask_again_under_the_lock(world) -> None:
    from kazma_ui.chat_move import _move_chat

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="twice")

    async def both() -> list[Any]:
        return await asyncio.gather(
            *(_move_chat(session_id=session, from_space=i["mine"], to_space=i["family"], by=i["reem"],
                         graph=NoCardGraph()) for _ in range(2)), return_exceptions=True)

    outcomes = asyncio.run(both())
    refused = [o for o in outcomes if isinstance(o, chat_moves.ChatMoveRefused)]
    assert len(refused) == 1 and refused[0].code == "not_found", outcomes
    assert threads.record_of(thread).space_id == i["family"]


def test_an_attached_stream_is_watched_by_its_chat_and_ends_when_it_moves(world, monkeypatch) -> None:
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
            threads.move_thread(thread, i["mine"])
            assert live_connections.thread_moved({thread}, i["mine"]) == 1
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
