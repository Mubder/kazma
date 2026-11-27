"""The world a chat is moved in (Spaces S2-12): the real chat routes behind
the real tenant middleware, a real LangGraph graph, the product's checkpoint
manager, Reem and Omar with their own spaces and one they share."""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import secrets
import sqlite3
import tempfile
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
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
        threads._ended.clear()
        old = session_manager.get_session_manager()
        session_manager.set_session_manager(session_manager.SessionManager(
            db_path=old.db_path, backend=None if old._pg else "sqlite"))  # the same store, as it was named

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
def world(monkeypatch, tmp_path, request):
    from kazma_gateway.stores.checkpoint import create_checkpoint_manager
    from kazma_ui import dashboard
    from kazma_ui.chat_move import create_chat_move_router
    from kazma_ui.sse_chat import create_sse_chat_router

    monkeypatch.setattr(scope, "_enforced", True)
    monkeypatch.setattr(threads, "_spaces", {})
    monkeypatch.setattr(threads, "_ended", OrderedDict())
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
    # The test's own choice: a test marked postgres keeps its chats and
    # checkpoints on the real Postgres, every other one in this folder's
    # files, whatever the run's address (the Postgres job sets it for every
    # test, and a SQLite case read its own file).
    backend = None if request.node.get_closest_marker("postgres") else "sqlite"
    session_manager.set_session_manager(session_manager.SessionManager(db_path=str(tmp_path / "chats.db"),
                                                                       backend=backend))
    held: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        @asynccontextmanager
        async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
            held["manager"] = await create_checkpoint_manager(str(Path(tmp) / "checkpoints.db"), backend=backend)
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
        app.include_router(dashboard.router)
        try:
            with TestClient(app, backend_options={"loop_factory": asyncio.SelectorEventLoop}) as client:
                yield World(client, held, ids)
        finally:
            session_manager.set_session_manager(before)
            get_config_store().delete(identity.REGISTRY_KEY)
            get_config_store().delete(chat_moves.MOVES_KEY)
            from kazma_core.security.vault import reset_posture_cache

            reset_posture_cache()


def fails(error: BaseException) -> Any:
    def raise_it(*_a: Any, **_k: Any) -> Any:
        raise error

    return raise_it


def psycopg_error() -> Exception:
    """The database's own error. SQLite's where psycopg is not installed (CI's
    test job): skipped for it here, a test was skipped from inside the request
    it makes, and failed."""
    said = "server closed the connection unexpectedly"
    if importlib.util.find_spec("psycopg") is None:
        return sqlite3.OperationalError(said)
    return pytest.importorskip("psycopg").OperationalError(said)


def entries_of(monkeypatch) -> list[dict[str, Any]]:
    """The journal's entry of each move that starts, with its new ids."""
    seen: list[dict[str, Any]] = []
    real = chat_moves.record_move

    def record(attempt: str, fields: dict[str, Any]) -> None:
        if "thread" in fields:
            seen.append(dict(fields))
        real(attempt, fields)

    monkeypatch.setattr(chat_moves, "record_move", record)
    return seen


def left(w: World, attempt: str, mark: str, *, out: bool = False) -> tuple[str, str, dict[str, Any]]:
    """A move a stop cut off before its handover: session, thread, entry."""
    from kazma_ui import chat_move

    i = w.ids
    frm, to = (i["family"], i["mine"]) if out else (i["mine"], i["family"])
    session, thread = w.chat("reem", frm, mark=mark)
    held = w.row(frm, "reem", session)
    entry = chat_move._entry(held, session, frm, to, i["reem"], out)
    entry.update(checkpoint="", **chat_move._copied_is(chat_move._rows_to_copy(held, i["reem"])))
    chat_moves.record_move(attempt, entry)
    chat_move._record_copy(entry)
    return session, thread, entry


def handed_over(w: World, attempt: str, mark: str, *, out: bool = False) -> tuple[str, str, dict[str, Any]]:
    """A move a stop cut off right after its handover."""
    from kazma_ui import chat_move

    session, thread, entry = left(w, attempt, mark, out=out)
    held = w.row(entry["source"], "reem", session)
    rows = chat_move._rows_to_copy(held, w.ids["reem"])
    assert chat_move._hand_over(attempt, entry, copy.deepcopy(held.messages), rows) is True
    return session, thread, entry


def say(w: World, space: str, who: str, session: str, text: str) -> None:
    """*who* writes in the chat, as a message does."""
    with scope.bind_scope(space, w.ids[who]), session_manager.get_session_manager().transact(session) as chat:
        chat.add_message("user", text, author=w.ids[who])


def shared_file(w: World, space: str, thread: str, text: str) -> str:
    """A file Kazma shared in the chat *thread*: its id."""
    from kazma_core.chat_files import share_file

    with tempfile.TemporaryDirectory() as tmp, scope.bind_scope(space, w.ids["reem"]):
        path = Path(tmp) / "report.txt"
        path.write_text(text, encoding="utf-8")
        return share_file(path, thread_id=thread).id


def files_of(space: str, account: str, thread: str) -> list[str]:
    """What the files of a chat hold in *space*."""
    from kazma_core import chat_files

    with scope.bind_scope(space, account):
        found = [chat_files.load_shared(i) for i in chat_files._records_of(chat_files._shared_dir(), thread)]
    return sorted(f[1].read_text(encoding="utf-8") for f in found if f is not None)


def opens(space: str, account: str, file_id: str) -> tuple[str, str] | None:
    """What a link answers in *space*: (the file's text, the chat it is of)."""
    from kazma_core.chat_files import load_shared

    with scope.bind_scope(space, account):
        found = load_shared(file_id)
    return (found[1].read_text(encoding="utf-8"), found[0].thread_id) if found is not None else None


def link(w: World, space: str, session: str, file_id: str) -> None:
    with scope.bind_scope(space, w.ids["reem"]):
        store = session_manager.get_session_manager()
        held = store.get(session)
        held.messages[-1]["content"] += f" [report](/api/chat/files/{file_id})"
        store.put(held)
