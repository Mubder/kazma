"""Spaces S2-12: moving one's own chat between one's own space and a shared
one, through the real routes behind the real tenant middleware.

A person shares a chat of their own space into a shared space they are in,
and takes it back out. After each move the chat is in exactly one space: its
row (each space's own list), its checkpoints (each space's own file), its
thread's record (what everything kept by thread follows), the rows that name
the thread beside a space, and the chat apps' pointers. Taking a chat out
leaves the shared space a line -- who, when, the title -- that only the move
writes; a chat others wrote in cannot be taken out. A move a stop cut off is
settled at start. Memory's part has its own file
(``tests/test_chat_move_memory.py``).

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
from contextlib import asynccontextmanager, closing
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

_MEMORY = "kazma_ui.chat_move._memory_follows"


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
    # The memory part has its own tests; here it is what the move calls.
    followed: list[dict[str, Any]] = []
    monkeypatch.setattr(_MEMORY, lambda entry: followed.append(dict(entry)) or {"ok": True})
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
    held: dict[str, Any] = {"followed": followed}

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


def _bounds_clear(thread: str = "", by: str = "") -> None:
    """The daily bounds' record of moves, cleared for a test that moves a lot."""
    with closing(chat_moves._connection()) as conn:
        conn.execute("DELETE FROM chat_moves_done")


# ── one space after each move ─────────────────────────────────────────────


def test_shared_then_taken_out_is_in_one_space_each_time(world) -> None:
    w, i = world, world.ids
    _bounds_clear()
    session, thread = w.chat("reem", i["mine"], mark="holiday plans")
    w.checkpoint("reem", i["mine"], thread)

    preview = w.call("reem", "GET", f"/api/chat/sessions/{session}/move", space=i["mine"]).json()
    assert preview == {"ok": True, "turns": 1, "others_wrote": False, "bounded": ""}

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
    assert [f["target"] for f in w.held["followed"]] == [i["family"]], "memory follows it"
    assert chat_moves.moves_under_way() == {}, "the journal entry went"
    assert w.call("omar", "GET", "/api/chat/taken-out", space=i["family"]).json()["lines"] == []

    _bounds_clear()
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
    _bounds_clear()
    session, thread = w.chat("reem", i["family"], mark="the trip", authors=(world.ids["reem"], world.ids["omar"]))
    preview = w.call("reem", "GET", f"/api/chat/sessions/{session}/move", space=i["family"]).json()
    assert preview["others_wrote"] is True
    refused = w.move("reem", i["family"], session, i["mine"])
    assert refused.status_code == 409 and refused.json()["error"] == "others_wrote"
    assert session in w.listed("omar", i["family"])
    assert threads.record_of(thread).space_id == i["family"]


def test_only_its_opener_moves_a_chat(world) -> None:
    w, i = world, world.ids
    _bounds_clear()
    session, thread = w.chat("reem", i["family"], mark="reem's chat")
    refused = w.move("omar", i["family"], session, i["omars"])
    assert refused.status_code == 403 and refused.json()["error"] == "not_your_chat"
    assert threads.record_of(thread).space_id == i["family"]


def test_a_move_is_between_ones_own_space_and_a_shared_one(world) -> None:
    w, i = world, world.ids
    _bounds_clear()
    session, _thread = w.chat("reem", i["mine"], mark="x")
    for to, code in ((i["omars"], "not_yours"), (i["mine"], "not_yours")):
        refused = w.move("reem", i["mine"], session, to)
        assert refused.json()["error"] == code, to
    # Another space's chat reads as no chat here.
    other, _t = w.chat("omar", i["omars"], mark="y")
    assert w.move("reem", i["mine"], other, i["family"]).json()["error"] == "not_found"


def test_a_running_turn_holds_the_move(world) -> None:
    from kazma_ui.active_turns import register_turn, unregister_turn

    w, i = world, world.ids
    _bounds_clear()
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


def test_moves_are_bounded_per_person_and_per_chat(world) -> None:
    w, i = world, world.ids
    _bounds_clear()
    session, _thread = w.chat("reem", i["mine"], mark="one")
    assert w.move("reem", i["mine"], session, i["family"]).status_code == 200
    again = w.move("reem", i["family"], session, i["mine"])
    assert again.status_code == 429 and again.json()["error"] == "moved_today"
    for n in range(2):
        other, _t = w.chat("reem", i["mine"], mark=f"more {n}")
        assert w.move("reem", i["mine"], other, i["family"]).status_code == 200
    fourth, _t = w.chat("reem", i["mine"], mark="fourth")
    refused = w.move("reem", i["mine"], fourth, i["family"])
    assert refused.status_code == 429 and refused.json()["error"] == "moves_today"
    assert w.call("reem", "GET", f"/api/chat/sessions/{fourth}/move", space=i["mine"]).json()["bounded"] == "moves_today"


# ── everything kept beside a space follows; pointers go ───────────────────


def test_rows_beside_a_space_follow_and_pointers_go(world, monkeypatch) -> None:
    from kazma_core.agent import artifacts
    from kazma_core.agent.artifacts import get_artifact_store

    # A store an earlier test opened elsewhere: this one opens in this data dir.
    monkeypatch.setattr(artifacts, "_store", None)
    w, i = world, world.ids
    _bounds_clear()
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
    assert [f["target"] for f in w.held["followed"]] == [i["family"]]
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
    _bounds_clear()
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
    assert [f["target"] for f in w.held["followed"]] == [i["family"]], "memory follows it"
    assert chat_moves.moves_under_way() == {}


def test_h3_the_moved_chats_sockets_and_streams_elsewhere_end(world, monkeypatch) -> None:
    """Every socket and stream of the chat open under another space than the
    one it moved to is closed or ended; one in its new space stays."""
    from kazma_ui import live_connections
    from kazma_ui.sse_chat import _streaming

    w, i = world, world.ids
    _bounds_clear()
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


def test_h6_a_chat_apps_chat_is_not_moved(world) -> None:
    w, i = world, world.ids
    _bounds_clear()
    session, thread = w.chat("reem", i["mine"], mark="telegram", session_id=f"gw-telegram-{secrets.token_hex(4)}",
                             thread_id=f"gw-telegram-{secrets.token_hex(4)}")
    refused = w.move("reem", i["mine"], session, i["family"])
    assert refused.status_code == 409 and refused.json()["error"] == "chat_app_chat", refused.text
    assert threads.record_of(thread).space_id == i["mine"]


def test_m1_turn_notes_and_time_travel_steps_do_not_move(world, monkeypatch) -> None:
    from kazma_core import time_travel
    from kazma_core.turn_notes import TURN_NOTE_KINDS

    w, i = world, world.ids
    _bounds_clear()
    session, thread = w.chat("reem", i["mine"], mark="m1")
    note = next(iter(TURN_NOTE_KINDS.values()))[0] + " what the personal space recalled"
    w.checkpoint("reem", i["mine"], thread, [{"role": "system", "content": note},
                                              {"role": "user", "content": "question m1"}])
    forgot: list[list[str]] = []
    monkeypatch.setattr(time_travel, "forget_thread_snapshots", lambda keys, **_k: forgot.append(list(keys)) or 0)
    assert w.move("reem", i["mine"], session, i["family"]).status_code == 200
    assert w.messages("reem", i["family"], thread) == [{"role": "user", "content": "question m1"}]
    assert forgot and set(forgot[0]) == {session, thread}


def test_m4_the_daily_bound_is_claimed_atomically(world) -> None:
    """Two moves at once with one move left today: only one moves."""
    from kazma_ui.chat_move import _move_chat

    w, i = world, world.ids
    _bounds_clear()
    with closing(chat_moves._connection()) as conn:
        for n in range(chat_moves.MOVES_PER_DAY - 1):
            conn.execute("INSERT INTO chat_moves_done (thread_id, moved_by, from_space, to_space, moved_at) "
                         "VALUES (?, ?, ?, ?, strftime('%s','now'))", (f"old{n}", i["reem"], i["mine"], i["family"]))
    one, _t1 = w.chat("reem", i["mine"], mark="m4 one")
    two, _t2 = w.chat("reem", i["mine"], mark="m4 two")

    async def both() -> list[Any]:
        return await asyncio.gather(
            *(_move_chat(session_id=s, from_space=i["mine"], to_space=i["family"], by=i["reem"],
                         graph=NoCardGraph()) for s in (one, two)),
            return_exceptions=True)

    outcomes = asyncio.run(both())
    refused = [o for o in outcomes if isinstance(o, chat_moves.ChatMoveRefused)]
    assert len(refused) == 1 and refused[0].code == "moves_today", outcomes


def test_m5_the_approval_check_fails_closed_and_reads_the_registry(world) -> None:
    from kazma_core.safety import hitl_gates

    w, i = world, world.ids
    _bounds_clear()
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
