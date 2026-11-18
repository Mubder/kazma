"""The matrix cases of Spaces S1-08: chats, checkpoints, Time Travel, drafts,
plans, receipts and the sockets (``tests/_space_matrix.py``).

Every case runs with Spaces enforced, as person A in ``matrix-a`` against the
ids of person B's ``matrix-b``, through the product's own routers behind the
real tenant middleware (:func:`world`) or the product's own tool functions.
It asserts that nothing of B is shown or changed, and that A's own rows are.

Ids are random and carry no marker: a route may echo the id it was asked for,
and that is not B's data. Markers are in titles and message text only.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import tempfile
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from kazma_core.spaces import scope, threads
from kazma_ui import auth, space_binding
from starlette.websockets import WebSocketDisconnect

from tests._space_matrix import Matrix, case

ITEM = "S1-08"


# ── the world: the chat routers behind the real middleware ───────────────


@dataclass
class Chat:
    """A planted chat: its ids, and the marker in its title and text."""

    session_id: str
    thread_id: str
    mark: str


class World:
    """The product's chat routers in one app, with two signed-in people."""

    def __init__(self, matrix: Matrix, client: TestClient, recorder: Any, held: dict[str, Any]) -> None:
        self.matrix = matrix
        self.client = client
        self.recorder = recorder
        self._held = held

    @property
    def manager(self) -> Any:
        """The checkpoint manager the world's routes use."""
        return self._held["manager"]

    @property
    def graph(self) -> Any:
        """The graph Time Travel restores and forks through. Its checkpoints
        are the product's own ``CheckpointManager``'s (the world's), so a
        restore or a fork meets the manager's thread guard too."""
        return self._held["graph"]

    # ── who asks ─────────────────────────────────────────────────────────

    def space_of(self, who: str) -> str:
        return {"a": Matrix.SPACE_A, "b": Matrix.SPACE_B}[who]

    def headers(self, who: str, *, space: str | None = None) -> dict[str, str]:
        return {"x-matrix-person": who, space_binding.SPACE_HEADER: space or self.space_of(who)}

    def call(self, who: str, method: str, path: str, **kwargs: Any) -> Any:
        space = kwargs.pop("space", None)
        return self.client.request(method, path, headers=self.headers(who, space=space), **kwargs)

    def as_(self, who: str) -> contextlib.AbstractContextManager[scope.Scope]:
        return self.matrix.as_a() if who == "a" else self.matrix.as_b()

    def account(self, who: str) -> str:
        return self.matrix.account_a if who == "a" else self.matrix.account_b

    # ── planting ─────────────────────────────────────────────────────────

    def chat(self, who: str, mark: str) -> Chat:
        """A chat of *who*'s space: a question and an answer that hold
        *mark*, a title that holds it, and its thread's record."""
        from kazma_ui.session_manager import get_session_manager

        made = Chat(session_id=f"s{secrets.token_hex(8)}", thread_id=f"t{secrets.token_hex(8)}", mark=mark)
        with self.as_(who):
            store = get_session_manager()
            session = store.get_or_create(made.session_id, thread_id=made.thread_id)
            session.add_message("user", f"question {mark}")
            session.add_message("assistant", f"answer {mark}")
            session.title = f"title {mark}"
            store.put(session)
            threads.record_thread(made.thread_id, self.space_of(who), self.account(who))
        return made

    def stored(self, who: str, chat: Chat) -> Any:
        """The chat as *who*'s space holds it now (None when it does not)."""
        from kazma_ui.session_manager import get_session_manager

        with self.as_(who):
            return get_session_manager().get(chat.session_id)

    def step(self, chat: Chat, n: int = 1) -> str:
        """A saved Time Travel step of *chat*; its snapshot id."""
        record = self.recorder.capture({
            "thread_id": chat.thread_id, "iteration": n, "created_at": f"turn-{chat.mark}",
            "messages": [{"role": "user", "content": f"question {chat.mark} step {n}"}],
        })
        assert record is not None
        return record.id

    def steps(self, chat: Chat) -> int:
        return len(self.recorder.list_snapshots(chat.thread_id))

    def checkpoint(self, who: str, chat: Chat) -> None:
        """A checkpoint of *chat*, written as *who* through the manager."""
        made = self.call(who, "POST", "/_matrix/checkpoint", json={"thread_id": chat.thread_id})
        assert made.status_code == 200, made.text

    def checkpoints(self, who: str, chat: Chat) -> bool:
        """Whether *who* reads a checkpoint of *chat* through the manager."""
        got = self.call(who, "GET", f"/_matrix/checkpoint/{chat.thread_id}")
        assert got.status_code == 200, got.text
        return bool(got.json()["found"])

    def pair(self, word: str) -> tuple[Chat, Chat]:
        marks = self.matrix.plant(word)
        return self.chat("a", marks.a), self.chat("b", marks.b)


@contextmanager
def world(matrix: Matrix) -> Iterator[World]:
    """Spaces enforced; the chat, replay, dashboard, A2A, voice and socket
    routers of the product behind the real tenant middleware. The person a
    request is signed in as is named by a test header (a query value on a
    socket): sign-in itself is S1-03's, checked by its own tests."""
    from kazma_core import time_travel
    from kazma_core.time_travel import SnapshotRecorder
    from kazma_gateway.stores.checkpoint import create_checkpoint_manager
    from kazma_ui import dashboard, routes_voice
    from kazma_ui.a2a_api import create_a2a_router
    from kazma_ui.replay_routes import create_replay_router
    from kazma_ui.routes.ws_chat import create_ws_chat_router
    from kazma_ui.routes_voice_ws import serve_voice_websocket
    from kazma_ui.sse_chat import create_sse_chat_router
    from langgraph.checkpoint.base import empty_checkpoint

    from tests import _tt_graph as tt

    people = {"a": matrix.principal_a, "b": matrix.principal_b}
    with pytest.MonkeyPatch.context() as patch, tempfile.TemporaryDirectory(
        ignore_cleanup_errors=True
    ) as tmp:
        patch.setattr(scope, "_enforced", True)
        patch.setattr(auth, "get_request_principal",
                      lambda request: people.get(request.headers.get("x-matrix-person", "")))
        for name in ("get_websocket_principal", "websocket_principal"):
            patch.setattr(auth, name, lambda ws: people.get(ws.query_params.get("person", "")))
        recorder = SnapshotRecorder(db_path=str(Path(tmp) / "snapshots.db"))
        # The chat delete forgets steps through the process's recorder.
        patch.setattr(time_travel, "_process_recorder", recorder)
        patch.setattr(time_travel, "_process_built_here", False)
        held: dict[str, Any] = {}

        @asynccontextmanager
        async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
            held["manager"] = await create_checkpoint_manager(str(Path(tmp) / "checkpoints.db"))
            held["graph"] = tt.build_graph(recorder, [], held["manager"])
            dashboard.set_dashboard_context(checkpoint_manager=held["manager"])
            try:
                yield
            finally:
                dashboard.set_dashboard_context(checkpoint_manager=None)
                await held["manager"].close()

        app = FastAPI(lifespan=lifespan)
        app.middleware("http")(auth.create_tenant_middleware())
        app.include_router(create_sse_chat_router(graph=None))
        app.include_router(create_replay_router(
            recorder_getter=lambda: recorder, graph_getter=lambda: held["graph"]))
        app.include_router(dashboard.router)
        app.include_router(create_a2a_router(lambda: None))
        app.include_router(create_ws_chat_router(graph=None))
        app.include_router(routes_voice.router)

        @app.websocket("/ws/voice")
        async def _voice(websocket: WebSocket) -> None:
            await serve_voice_websocket(websocket)

        def _config(thread_id: str) -> dict[str, Any]:
            return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

        @app.post("/_matrix/checkpoint")
        async def _write_checkpoint(request: Request) -> dict[str, Any]:
            body = await request.json()
            await held["manager"].aput(
                _config(body["thread_id"]), empty_checkpoint(),
                {"source": "input", "step": 0, "writes": {}}, {},
            )
            return {"ok": True}

        @app.get("/_matrix/checkpoint/{thread_id}")
        async def _read_checkpoint(thread_id: str) -> dict[str, Any]:
            return {"found": await held["manager"].aget_tuple(_config(thread_id)) is not None}

        try:
            # The server's own loop kind (kazma_core.eventloop): psycopg's
            # async pool refuses Windows' Proactor loop, and the checkpoint
            # manager would quietly fall back to SQLite on a real Postgres.
            with TestClient(app, backend_options={"loop_factory": asyncio.SelectorEventLoop}) as client:
                yield World(matrix, client, recorder, held)
        finally:
            close = getattr(recorder, "close", None)
            if callable(close):
                close()


def recorded_space(thread_id: str) -> str | None:
    """The space a thread is recorded in, read from the record store."""
    record = threads.record_of(thread_id)
    return record.space_id if record else None


def _text(response: Any) -> str:
    return response.text


def _in_world(run: Callable[[World], None]) -> Callable[[Matrix], None]:
    def with_world(matrix: Matrix) -> None:
        with world(matrix) as made:
            run(made)

    with_world.__name__ = run.__name__
    return with_world


def chat_case(target: str) -> Callable[[Callable[[World], None]], Callable[[Matrix], None]]:
    """Register a case that runs in :func:`world`."""

    def register(run: Callable[[World], None]) -> Callable[[Matrix], None]:
        return case(target, ITEM)(_in_world(run))

    return register


# ── the chat store's routes ──────────────────────────────────────────────


@chat_case("GET /api/chat/sessions")
def chat_list(w: World) -> None:
    mine, theirs = w.pair("list")
    listed = _text(w.call("a", "GET", "/api/chat/sessions"))
    assert mine.mark in listed and mine.session_id in listed
    assert theirs.mark not in listed and theirs.session_id not in listed
    assert theirs.thread_id not in listed


@chat_case("GET /api/chat/sessions/archived")
def chat_archived_list(w: World) -> None:
    from kazma_ui.session_manager import get_session_manager

    mine, theirs = w.pair("archived")
    for who, chat in (("a", mine), ("b", theirs)):
        with w.as_(who):
            get_session_manager().set_archived(chat.session_id, True)
    listed = _text(w.call("a", "GET", "/api/chat/sessions/archived"))
    assert mine.mark in listed
    assert theirs.mark not in listed and theirs.session_id not in listed


@chat_case("GET /api/chat/sessions/{session_id}/messages")
def chat_messages(w: World) -> None:
    mine, theirs = w.pair("messages")
    assert mine.mark in _text(w.call("a", "GET", f"/api/chat/sessions/{mine.session_id}/messages"))
    for path in (f"/api/chat/sessions/{theirs.session_id}/messages",
                 f"/api/chat/sessions/{theirs.session_id}/messages?stats=1",
                 f"/api/chat/sessions/{theirs.thread_id}/messages"):
        shown = _text(w.call("a", "GET", path))
        assert theirs.mark not in shown and theirs.thread_id not in shown, path
    # Reading another space's id made nothing in this one.
    assert w.stored("a", theirs) is None


@chat_case("GET /api/chat/sessions/{session_id}/status")
def chat_status(w: World) -> None:
    from kazma_ui.active_turns import register_turn, unregister_turn

    mine, theirs = w.pair("status")

    class _Running:
        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            return None

    running = _Running()
    register_turn(theirs.thread_id, running)
    try:
        status = w.call("a", "GET", f"/api/chat/sessions/{theirs.session_id}/status").json()
        # B's turn is running; A is told nothing of it, not even its thread.
        assert not status.get("generating") and not status.get("paused"), status
        assert theirs.thread_id not in str(status)
        own = w.call("a", "GET", f"/api/chat/sessions/{mine.session_id}/status").json()
        assert own.get("thread_id") == mine.thread_id
    finally:
        unregister_turn(theirs.thread_id, running)


@chat_case("GET /api/chat/capacity")
def chat_capacity(w: World) -> None:
    mine, theirs = w.pair("capacity")
    for query in ({"session_id": theirs.session_id}, {"thread_id": theirs.thread_id}):
        refused = w.call("a", "GET", "/api/chat/capacity", params=query).json()
        assert refused == {"ok": False, "reason": "missing_session"}, refused
    assert w.call("a", "GET", "/api/chat/capacity", params={"session_id": mine.session_id}).json()["ok"]


@chat_case("POST /api/chat/sessions")
def chat_bind(w: World) -> None:
    _mine, theirs = w.pair("bind")
    # Naming B's chat id makes a new, empty chat of A's own, on another thread.
    bound = w.call("a", "POST", "/api/chat/sessions", json={"session_id": theirs.session_id})
    assert bound.status_code == 200, bound.text
    assert bound.json()["thread_id"] != theirs.thread_id
    made = w.stored("a", theirs)
    assert made is not None and not made.messages
    theirs_now = w.stored("b", theirs)
    assert theirs_now is not None and theirs.mark in str(theirs_now.messages)
    assert theirs_now.thread_id == theirs.thread_id


def _unchanged(w: World, theirs: Chat) -> None:
    now = w.stored("b", theirs)
    assert now is not None, "B's chat is gone"
    assert now.title == f"title {theirs.mark}" and not now.archived and not now.pinned
    assert theirs.mark in str(now.messages) and len(now.messages) == 2


@chat_case("PATCH /api/chat/sessions/{session_id}")
def chat_rename(w: World) -> None:
    mine, theirs = w.pair("rename")
    w.call("a", "PATCH", f"/api/chat/sessions/{theirs.session_id}", json={"title": "taken over"})
    _unchanged(w, theirs)
    assert w.stored("a", theirs) is None
    assert w.call("a", "PATCH", f"/api/chat/sessions/{mine.session_id}",
                  json={"title": "renamed"}).json()["status"] == "ok"
    assert w.stored("a", mine).title == "renamed"


@chat_case("DELETE /api/chat/sessions/{session_id}")
def chat_delete(w: World) -> None:
    mine, theirs = w.pair("delete")
    kept_step = w.step(theirs)
    # B's steps are also kept under the chat id on some paths: plant one there.
    w.recorder.capture({"thread_id": theirs.session_id, "iteration": 1, "created_at": "t",
                        "messages": [{"role": "user", "content": f"question {theirs.mark}"}]})
    w.checkpoint("b", theirs)
    for named in (theirs.session_id, theirs.thread_id):
        assert w.call("a", "DELETE", f"/api/chat/sessions/{named}").json() == {"status": "ok"}
    _unchanged(w, theirs)
    assert w.steps(theirs) == 1 and kept_step
    assert len(w.recorder.list_snapshots(theirs.session_id)) == 1
    assert w.checkpoints("b", theirs)
    assert recorded_space(theirs.thread_id) == Matrix.SPACE_B
    # A's own chat goes, with its steps, its checkpoints and its record.
    w.step(mine)
    w.checkpoint("a", mine)
    assert w.call("a", "DELETE", f"/api/chat/sessions/{mine.session_id}").json() == {"status": "ok"}
    assert w.stored("a", mine) is None and w.steps(mine) == 0
    assert recorded_space(mine.thread_id) is None


def _flag_case(suffix: str, before: Callable[[World, Chat, Chat], None],
               changed: Callable[[Any], bool]) -> None:
    @chat_case(f"POST /api/chat/sessions/{{session_id}}/{suffix}")
    def flag(w: World) -> None:
        mine, theirs = w.pair(suffix)
        before(w, mine, theirs)
        theirs_before = w.stored("b", theirs)
        state = (theirs_before.archived, theirs_before.pinned)
        w.call("a", "POST", f"/api/chat/sessions/{theirs.session_id}/{suffix}")
        theirs_now = w.stored("b", theirs)
        assert (theirs_now.archived, theirs_now.pinned) == state
        assert w.stored("a", theirs) is None
        done = w.call("a", "POST", f"/api/chat/sessions/{mine.session_id}/{suffix}")
        assert done.status_code == 200, done.text
        assert changed(w.stored("a", mine))

    flag.__name__ = f"chat_{suffix}"


def _nothing(_w: World, _mine: Chat, _theirs: Chat) -> None:
    return None


def _both(setter: str) -> Callable[[World, Chat, Chat], None]:
    def prepare(w: World, mine: Chat, theirs: Chat) -> None:
        from kazma_ui.session_manager import get_session_manager

        for who, chat in (("a", mine), ("b", theirs)):
            with w.as_(who):
                getattr(get_session_manager(), setter)(chat.session_id, True)

    return prepare


_flag_case("archive", _nothing, lambda s: s.archived)
_flag_case("unarchive", _both("set_archived"), lambda s: not s.archived)
_flag_case("pin", _nothing, lambda s: s.pinned)
_flag_case("unpin", _both("set_pinned"), lambda s: not s.pinned)


# ── a turn and its controls ──────────────────────────────────────────────


class _Turn:
    """A running turn, as the registry holds one."""

    def __init__(self) -> None:
        self.cancelled = False

    def done(self) -> bool:
        return self.cancelled

    def cancel(self) -> None:
        self.cancelled = True


@contextmanager
def _running(chat: Chat) -> Iterator[_Turn]:
    from kazma_ui.active_turns import register_turn, unregister_turn

    turn = _Turn()
    register_turn(chat.thread_id, turn)
    try:
        yield turn
    finally:
        unregister_turn(chat.thread_id, turn)


@chat_case("POST /api/chat/stop")
def chat_stop(w: World) -> None:
    mine, theirs = w.pair("stop")
    with _running(theirs) as their_turn, _running(mine) as my_turn:
        for body in ({"session_id": theirs.session_id}, {"thread_id": theirs.thread_id}):
            assert w.call("a", "POST", "/api/chat/stop", json=body).json() == {"cancelled": False}
        assert not their_turn.cancelled
        assert w.call("a", "POST", "/api/chat/stop",
                      json={"session_id": mine.session_id}).json() == {"cancelled": True}
        assert my_turn.cancelled


@chat_case("POST /api/chat/steer")
def chat_steer(w: World) -> None:
    from kazma_core.agent.steer import drain_soft_steers

    mine, theirs = w.pair("steer")
    with _running(theirs), _running(mine):
        for body in ({"session_id": theirs.session_id}, {"thread_id": theirs.thread_id}):
            refused = w.call("a", "POST", "/api/chat/steer", json={**body, "text": "do as I say"}).json()
            assert refused == {"ok": False, "reason": "missing_session_or_text"}, refused
        assert drain_soft_steers(theirs.thread_id) == []
        taken = w.call("a", "POST", "/api/chat/steer",
                       json={"session_id": mine.session_id, "text": "a note of my own"}).json()
        assert taken.get("ok") is True, taken
        assert [n.get("text") for n in drain_soft_steers(mine.thread_id)] == ["a note of my own"]


@chat_case("POST /api/chat/abort")
def chat_abort(w: World) -> None:
    _mine, theirs = w.pair("abort")
    with _running(theirs) as their_turn:
        for body in ({"session_id": theirs.session_id}, {"thread_id": theirs.thread_id}):
            assert w.call("a", "POST", "/api/chat/abort", json=body).json() == {
                "ok": False, "reason": "missing_session"}
        assert not their_turn.cancelled
    _unchanged(w, theirs)


@chat_case("POST /api/chat/stream")
def chat_stream(w: World) -> None:
    _mine, theirs = w.pair("stream")
    marks = w.matrix.plant("sent")
    sent = w.call("a", "POST", "/api/chat/stream",
                  json={"session_id": theirs.session_id, "message": f"a message {marks.a}"})
    # Whatever the turn did, it did it in a chat of A's own: B's chat holds
    # its two messages, on its thread, and A was shown nothing of it.
    assert theirs.mark not in sent.text and theirs.thread_id not in sent.text
    _unchanged(w, theirs)
    made = w.stored("a", theirs)
    assert made is None or made.thread_id != theirs.thread_id
    assert recorded_space(theirs.thread_id) == Matrix.SPACE_B


# ── the sockets ──────────────────────────────────────────────────────────


def _socket_close(w: World, path: str) -> int | None:
    """The code the server closes a socket with at once, or None when it
    stays open (the first frame, if any, is read and dropped)."""
    try:
        with w.client.websocket_connect(path) as socket:
            socket.receive_json()
            return None
    except WebSocketDisconnect as closed:
        return closed.code


@chat_case("WEBSOCKET /ws/chat/{session_id}")
def chat_socket(w: World) -> None:
    mine, theirs = w.pair("socket")
    # B's chat, asked for from A's space: no such chat here.
    assert _socket_close(w, f"/ws/chat/{theirs.session_id}?person=a&space={Matrix.SPACE_A}") == 4004
    # B's space, asked for by A: not a member, said at the handshake.
    assert _socket_close(w, f"/ws/chat/{theirs.session_id}?person=a&space={Matrix.SPACE_B}") == 4003
    # No space named, with Spaces enforced and nobody signed in: refused.
    assert _socket_close(w, f"/ws/chat/{theirs.session_id}?space={Matrix.SPACE_B}") == 4003
    # A's own chat: connected, and the last answer is A's.
    with w.client.websocket_connect(
        f"/ws/chat/{mine.session_id}?person=a&space={Matrix.SPACE_A}"
    ) as socket:
        frame = socket.receive_json()
    assert mine.mark in str(frame) and theirs.mark not in str(frame)


@chat_case("WEBSOCKET /ws/voice")
def voice_socket(w: World) -> None:
    refused = _socket_close(w, f"/ws/voice?person=a&space={Matrix.SPACE_B}")
    assert refused == 4003, refused
    with w.client.websocket_connect(f"/ws/voice?person=a&space={Matrix.SPACE_A}") as socket:
        assert socket.receive_json() == {"type": "ready"}


# ── Time Travel ──────────────────────────────────────────────────────────


@chat_case("GET /api/replay/threads")
def replay_threads(w: World) -> None:
    mine, theirs = w.pair("threads")
    w.step(mine)
    w.step(theirs)
    listed = w.call("a", "GET", "/api/replay/threads")
    assert listed.json()["threads"] == [mine.thread_id]
    assert theirs.mark not in listed.text and theirs.thread_id not in listed.text


@chat_case("GET /api/replay/snapshots/{thread_id}")
def replay_steps(w: World) -> None:
    mine, theirs = w.pair("steps")
    w.step(mine)
    w.step(theirs)
    refused = w.call("a", "GET", f"/api/replay/snapshots/{theirs.thread_id}")
    assert refused.status_code == 404 and theirs.mark not in refused.text
    own = w.call("a", "GET", f"/api/replay/snapshots/{mine.thread_id}")
    assert own.status_code == 200 and mine.thread_id in own.text


@chat_case("GET /api/replay/snapshots/{thread_id}/{snapshot_id}")
def replay_step(w: World) -> None:
    mine, theirs = w.pair("step")
    my_step, their_step = w.step(mine), w.step(theirs)
    for thread in (theirs.thread_id, mine.thread_id):
        # B's step, under its own thread and under A's.
        shown = w.call("a", "GET", f"/api/replay/snapshots/{thread}/{their_step}")
        assert theirs.mark not in shown.text, thread
        assert shown.status_code == 404, thread
    own = w.call("a", "GET", f"/api/replay/snapshots/{mine.thread_id}/{my_step}")
    assert own.status_code == 200 and mine.mark in own.text


@chat_case("DELETE /api/replay/threads/{thread_id}")
def replay_clear(w: World) -> None:
    mine, theirs = w.pair("clear")
    w.step(mine)
    w.step(theirs)
    assert w.call("a", "DELETE", f"/api/replay/threads/{theirs.thread_id}").status_code == 404
    assert w.steps(theirs) == 1
    assert w.call("a", "DELETE", f"/api/replay/threads/{mine.thread_id}").status_code == 200
    assert w.steps(mine) == 0 and w.steps(theirs) == 1


@chat_case("POST /api/replay/compare")
def replay_compare(w: World) -> None:
    mine, theirs = w.pair("compare")
    their_a, their_b = w.step(theirs, 1), w.step(theirs, 2)
    my_a, my_b = w.step(mine, 1), w.step(mine, 2)
    refused = w.call("a", "POST", "/api/replay/compare",
                     json={"thread_id": theirs.thread_id, "a": their_a, "b": their_b})
    assert refused.status_code == 404 and theirs.mark not in refused.text
    # B's steps named under A's own thread compare nothing either.
    mixed = w.call("a", "POST", "/api/replay/compare",
                   json={"thread_id": mine.thread_id, "a": my_a, "b": their_b})
    assert theirs.mark not in mixed.text
    own = w.call("a", "POST", "/api/replay/compare",
                 json={"thread_id": mine.thread_id, "a": my_a, "b": my_b})
    assert own.status_code == 200 and "diff" in own.json()


@chat_case("POST /api/replay/restore")
def replay_restore(w: World) -> None:
    from tests import _tt_graph as tt

    mine, theirs = w.pair("restore")
    their_step, my_step = w.step(theirs), w.step(mine)

    def state_of(chat: Chat) -> str:
        # Read as Kazma's own work (nothing bound): the thread's own space.
        return str(tt.run(w.graph.aget_state(tt.config(chat.thread_id))).values)

    for body in ({"thread_id": theirs.thread_id, "snapshot_id": their_step},
                 {"thread_id": mine.thread_id, "snapshot_id": their_step}):
        refused = w.call("a", "POST", "/api/replay/restore", json=body)
        assert refused.status_code in (404, 409), refused.text
        assert theirs.mark not in refused.text
    assert theirs.mark not in state_of(theirs) and theirs.mark not in state_of(mine)
    assert w.steps(theirs) == 1
    done = w.call("a", "POST", "/api/replay/restore",
                  json={"thread_id": mine.thread_id, "snapshot_id": my_step})
    assert done.status_code == 200, done.text
    assert mine.mark in state_of(mine)


@chat_case("POST /api/replay/fork")
def replay_fork(w: World) -> None:
    mine, theirs = w.pair("fork")
    their_step, my_step = w.step(theirs), w.step(mine)
    for body in ({"thread_id": theirs.thread_id, "snapshot_id": their_step},
                 {"thread_id": mine.thread_id, "snapshot_id": their_step}):
        refused = w.call("a", "POST", "/api/replay/fork", json=body)
        assert refused.status_code in (404, 409), refused.text
        assert theirs.mark not in refused.text
    # No copy of B's chat was made in A's space.
    assert theirs.mark not in _text(w.call("a", "GET", "/api/chat/sessions"))
    forked = w.call("a", "POST", "/api/replay/fork",
                    json={"thread_id": mine.thread_id, "snapshot_id": my_step})
    assert forked.status_code == 200, forked.text
    new_thread = forked.json()["new_thread_id"]
    # The fork is a chat of A's space, recorded there before anything was written.
    assert recorded_space(new_thread) == Matrix.SPACE_A
    assert new_thread not in _text(w.call("b", "GET", "/api/chat/sessions"))


# ── the checkpointed threads (the Dashboard's table) ─────────────────────


@chat_case("GET /api/sessions")
def sessions_list(w: World) -> None:
    mine, theirs = w.pair("sessions")
    w.checkpoint("a", mine)
    w.checkpoint("b", theirs)
    listed = w.call("a", "GET", "/api/sessions")
    assert listed.status_code == 200, listed.text
    assert mine.thread_id in listed.text
    assert theirs.thread_id not in listed.text and theirs.mark not in listed.text


@chat_case("DELETE /api/sessions/{thread_id}")
def sessions_delete(w: World) -> None:
    mine, theirs = w.pair("sessionsdelete")
    w.checkpoint("a", mine)
    w.checkpoint("b", theirs)
    w.step(theirs)
    refused = w.call("a", "DELETE", f"/api/sessions/{theirs.thread_id}")
    assert refused.status_code == 404, refused.text
    _unchanged(w, theirs)
    assert w.checkpoints("b", theirs) and w.steps(theirs) == 1
    assert recorded_space(theirs.thread_id) == Matrix.SPACE_B
    done = w.call("a", "DELETE", f"/api/sessions/{mine.thread_id}")
    assert done.status_code == 200 and done.json()["deleted"] is True, done.text
    assert not w.checkpoints("a", mine) and w.stored("a", mine) is None
    assert recorded_space(mine.thread_id) is None


@chat_case("POST /api/sessions/clear-all")
def sessions_clear_all(w: World) -> None:
    mine, theirs = w.pair("clearall")
    w.checkpoint("a", mine)
    w.checkpoint("b", theirs)
    assert w.call("a", "POST", "/api/sessions/clear-all").status_code == 200
    assert not w.checkpoints("a", mine)
    assert w.checkpoints("b", theirs)
    _unchanged(w, theirs)


# ── A2A ──────────────────────────────────────────────────────────────────


@chat_case("POST /api/a2a")
def a2a_tasks(w: World) -> None:
    from kazma_core.a2a_protocol import ENABLED_KEY
    from kazma_core.config_store import get_config_store

    marks = w.matrix.plant("a2a")
    get_config_store().set(ENABLED_KEY, True)
    try:
        def rpc(who: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
            return w.call(who, "POST", "/api/a2a",
                          json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).json()

        sent = rpc("b", "message/send", {
            "message": {"role": "user", "messageId": secrets.token_hex(4),
                        "parts": [{"kind": "text", "text": f"a request {marks.b}"}]},
            "configuration": {"blocking": True},
        })
        task_id = sent["result"]["id"]
        for method in ("tasks/get", "tasks/cancel"):
            answer = rpc("a", method, {"id": task_id})
            assert "error" in answer and marks.b not in str(answer), answer
        assert rpc("b", "tasks/get", {"id": task_id})["result"]["id"] == task_id
    finally:
        get_config_store().delete(ENABLED_KEY)


# ── routes that keep nothing of their own: the boundary is their case ────


def _boundary(method: str, path: str, **kwargs: Any) -> Callable[[Matrix], None]:
    """A route with no rows of its own (a page, a transcription): it runs in
    the space the request named, and a request for another's space never
    reaches it."""

    def run(matrix: Matrix) -> None:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(scope, "_enforced", True)
            with matrix.requests_as("a") as client:
                refused = client.request(method, path, headers=matrix.headers_b, **kwargs)
                assert refused.status_code == 403 and refused.json()["code"] == "not_a_member"
                assert client.reached == []  # type: ignore[attr-defined]
                unnamed = client.request(method, path, **kwargs)
                assert unnamed.status_code == 200 and unnamed.json()["space"] == Matrix.SPACE_A
                own = client.request(method, path, headers=matrix.headers_a, **kwargs)
                assert own.json() == {"space": Matrix.SPACE_A, "asker": matrix.account_a}

    return run


for _method, _path in (("GET", "/chat"), ("GET", "/replay"),
                       ("POST", "/api/voice/stt"), ("POST", "/api/voice/tts")):
    case(f"{_method} {_path}", ITEM)(_boundary(_method, _path))


@chat_case("POST /api/voice/livekit/token")
def voice_room(w: World) -> None:
    """A live-voice room carries audio: it is its space's, whatever chat id
    the request names."""
    _mine, theirs = w.pair("room")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("LIVEKIT_URL", "wss://voice.example.test")
        patch.setenv("LIVEKIT_API_KEY", "demo-key")
        patch.setenv("LIVEKIT_API_SECRET", "demo-secret-demo-secret-demo-secret")
        body = {"session_id": theirs.session_id}
        theirs_room = w.call("b", "POST", "/api/voice/livekit/token", json=body)
        if theirs_room.status_code == 404:
            pytest.skip("LiveKit tokens cannot be made here")
        mine_room = w.call("a", "POST", "/api/voice/livekit/token", json=body)
        assert mine_room.status_code == 200, mine_room.text
        assert mine_room.json()["room"] != theirs_room.json()["room"]


# ── tools ────────────────────────────────────────────────────────────────


_REGISTRY: list[Any] = []


def _registry() -> Any:
    """The registry a running Kazma builds, as the matrix lists its tools."""
    if not _REGISTRY:
        from kazma_core.agent.tool_builtins import register_builtin_tools
        from kazma_core.agent.tool_registry import LocalToolRegistry

        registry = LocalToolRegistry()
        register_builtin_tools(registry)
        _REGISTRY.append(registry)
    return _REGISTRY[0]


def _call(name: str, **arguments: Any) -> str:
    """Run the product's own tool function, in the caller's bound scope."""
    tool = _registry().get_tool(name)
    assert tool is not None, f"the tool {name} was not found"
    result = tool.func(**arguments)
    if asyncio.iscoroutine(result):
        # A coroutine runs in a copy of this context: the scope travels.
        result = asyncio.run(result)
    return str(result)


@contextmanager
def _turn(thread_id: str) -> Iterator[None]:
    """A tool call's thread, as the tool worker binds it."""
    from kazma_core.agent.turn_input import _scratchpad_thread_ctx
    from kazma_core.safety.hitl import _current_thread_id

    token = _current_thread_id.set(thread_id)
    pad = _scratchpad_thread_ctx.set(thread_id)
    try:
        yield
    finally:
        _scratchpad_thread_ctx.reset(pad)
        _current_thread_id.reset(token)


@contextmanager
def _enforced() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scope, "_enforced", True)
        yield


def _thread_of(matrix: Matrix, who: str) -> str:
    thread_id = f"t{secrets.token_hex(8)}"
    space = Matrix.SPACE_A if who == "a" else Matrix.SPACE_B
    account = matrix.account_a if who == "a" else matrix.account_b
    threads.record_thread(thread_id, space, account)
    return thread_id


@case("tool:save_proposal", ITEM)
def tool_save_proposal(matrix: Matrix) -> None:
    marks = matrix.plant("draft")
    with _enforced():
        thread_b = _thread_of(matrix, "b")
        with matrix.as_b(), _turn(thread_b):
            saved = _call("save_proposal", kind="notes", items=[f"draft {marks.b}"])
        assert marks.b in saved or "proposal" in saved.lower()
        # A saving under B's thread writes a draft of A's space, never B's.
        with matrix.as_a(), _turn(thread_b):
            _call("save_proposal", kind="notes", items=[f"draft {marks.a}"])
        with matrix.as_b(), _turn(thread_b):
            listed = _call("list_proposals")
        assert marks.b in listed and marks.a not in listed


@case("tool:list_proposals", ITEM)
def tool_list_proposals(matrix: Matrix) -> None:
    marks = matrix.plant("drafts")
    with _enforced():
        thread_a, thread_b = _thread_of(matrix, "a"), _thread_of(matrix, "b")
        with matrix.as_a(), _turn(thread_a):
            _call("save_proposal", kind="notes", items=[f"draft {marks.a}"])
        with matrix.as_b(), _turn(thread_b):
            _call("save_proposal", kind="notes", items=[f"draft {marks.b}"])
        for thread in (thread_a, thread_b):
            with matrix.as_a(), _turn(thread):
                listed = _call("list_proposals")
            assert marks.b not in listed, thread
        with matrix.as_a(), _turn(thread_a):
            assert marks.a in _call("list_proposals")


@case("tool:discard_proposal", ITEM)
def tool_discard_proposal(matrix: Matrix) -> None:
    import re

    marks = matrix.plant("discard")
    with _enforced():
        thread_b = _thread_of(matrix, "b")
        with matrix.as_b(), _turn(thread_b):
            saved = _call("save_proposal", kind="notes", items=[f"draft {marks.b}"])
        found = re.search(r"proposal[_ ]id[\"'`:= ]+([A-Za-z0-9_.:-]+)", saved)
        assert found, saved
        proposal_id = found.group(1).strip("\"'`.,")
        with matrix.as_a(), _turn(thread_b):
            try:
                _call("discard_proposal", proposal_id=proposal_id)
            except Exception:  # noqa: BLE001 -- a refusal is the tool's to word
                pass
        with matrix.as_b(), _turn(thread_b):
            listed = _call("list_proposals")
        assert marks.b in listed and "discarded" not in listed.lower()


@case("tool:update_scratchpad", ITEM)
def tool_update_scratchpad(matrix: Matrix) -> None:
    from kazma_core.agent.artifacts import get_artifact_store

    marks = matrix.plant("pad")
    with _enforced():
        thread_b = _thread_of(matrix, "b")
        with matrix.as_b(), _turn(thread_b):
            _call("update_scratchpad", key="finding", finding=f"note {marks.b}")
        with matrix.as_a(), _turn(thread_b):
            _call("update_scratchpad", key="finding", finding=f"note {marks.a}")
            mine = str(get_artifact_store().list_scratchpad(thread_b))
        with matrix.as_b():
            theirs = str(get_artifact_store().list_scratchpad(thread_b))
        assert marks.b in theirs and marks.a not in theirs
        assert marks.b not in mine


@case("tool:task_ledger_update", ITEM)
def tool_task_ledger_update(matrix: Matrix) -> None:
    from kazma_core.agent.task_ledger import get_ledger_store
    from kazma_core.agent.tool_refusal import RefusedBeforeEffect

    marks = matrix.plant("plan")
    with _enforced():
        thread_a, thread_b = _thread_of(matrix, "a"), _thread_of(matrix, "b")
        with matrix.as_b(), _turn(thread_b):
            _call("task_ledger_update", goal=f"goal {marks.b}")
        with matrix.as_a(), _turn(thread_b):
            with pytest.raises(RefusedBeforeEffect):
                _call("task_ledger_update", goal=f"goal {marks.a}", complete=True)
            assert get_ledger_store().active_for(thread_b) is None
            assert get_ledger_store().history(thread_b) == []
        with matrix.as_b(), _turn(thread_b):
            plan = get_ledger_store().active_for(thread_b)
            assert plan is not None and plan.goal == f"goal {marks.b}"
        with matrix.as_a(), _turn(thread_a):
            _call("task_ledger_update", goal=f"goal {marks.a}")
            assert get_ledger_store().active_for(thread_a).goal == f"goal {marks.a}"


def _session_messages_case(tool: str, **arguments: Any) -> Callable[[Matrix], None]:
    """A tool that reads the running turn's own messages: each turn sees its
    own, and a turn of another space, in a context of its own, sees none."""

    def run(matrix: Matrix) -> None:
        import contextvars

        from kazma_core.tools.export_session import _current_session_messages

        marks = matrix.plant(tool)

        def as_b() -> str:
            _current_session_messages.set([{"role": "user", "content": f"question {marks.b}"}])
            with matrix.as_b():
                return _call(tool, **arguments)

        def as_a() -> str:
            with matrix.as_a():
                return _call(tool, **arguments)

        with _enforced():
            assert marks.b in contextvars.copy_context().run(as_b) or tool == "context_info"
            assert marks.b not in contextvars.copy_context().run(as_a)

    return run


case("tool:export_session", ITEM)(_session_messages_case("export_session", format="json"))
case("tool:context_info", ITEM)(_session_messages_case("context_info", details=True))
