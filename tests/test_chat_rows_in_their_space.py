"""A chat's row is read and written as its own space's (S4-01 step 0a, piece 5:
rows 7, 17, 18, 37-39, 43-45 and the approvals query).

The spool's drain, the spooled chats of a list, a chat move's copy, a start's
listings and the history import ran with nothing bound, or bound to another
space. And a refused read was "no such chat": the chat was made anew, empty.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import sqlite3
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.db import pg_helpers
from kazma_core.spaces import identity, people, scope
from kazma_core.spaces.row_work import Door
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, NoSpaceBound, Scope, bind_scope, current_scope
from kazma_ui.session_manager import ChatSession, SessionManager

from tests._space_door import Calls, watched

REPO = Path(__file__).resolve().parent.parent
A, B = "s0a0a0a0a0a0a", "s0b0b0b0b0b0b"


@pytest.fixture
def enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)


def _messages(text: str) -> list[dict[str, Any]]:
    return [{"role": "user", "content": text, "ts": "2026-11-25T00:00:00+00:00"}]


def _manager(path: Path) -> SessionManager:
    return SessionManager(db_path=str(path / "chats.db"))


def _spooled(path: Path) -> None:
    """A's and B's chats, each with a saved row and a newer one in the spool."""
    made = _manager(path)
    for space in (A, B):
        session = ChatSession(session_id=f"chat-{space}", tenant_id=space, thread_id=f"t-{space}",
                              messages=_messages("saved"))
        made._upsert_db(session)
        session.messages = _messages("saved") + _messages("in the spool")
        made._spool.save(space, session.session_id, dataclasses.asdict(session), "refused")


_ROWS = ((SessionManager, "_load_one_from_primary"), (SessionManager, "_upsert_db"), (SessionManager, "_delete_row"))


def _tenant_of(call: Any) -> str:
    first = call.args[1]
    return first.tenant_id if isinstance(first, ChatSession) else first


# ── the spool (rows 17, 18) ──────────────────────────────────────────────


@pytest.mark.usefixtures("enforced")
def test_a_spooled_save_is_drained_as_its_own_spaces(tmp_path: Path) -> None:
    _spooled(tmp_path)
    with watched(*_ROWS) as calls:
        made = _manager(tmp_path)
    assert made._spool.keys() == set(), "drained"
    assert calls.of("_upsert_db") and {_tenant_of(c) for c in calls} == {A, B}
    assert [c.space for c in calls] == [_tenant_of(c) for c in calls]
    assert {c.scope for c in calls} == {None}


def test_off_a_drain_binds_nothing(tmp_path: Path) -> None:
    _spooled(tmp_path)
    with watched(*_ROWS) as calls:
        made = _manager(tmp_path)
    assert made._spool.keys() == set() and {c.scope for c in calls} == {None}


def _listed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refused: str = "") -> tuple[dict[str, Any], Calls]:
    """A list inside A, of a store whose spool holds A's and B's chats."""
    monkeypatch.setattr(SessionManager, "_drain_spool", lambda self: None)
    _spooled(tmp_path)
    made = _manager(tmp_path)
    real = made._load_one_from_primary

    def load(tenant_id: str, session_id: str, *, strict: bool = False) -> Any:
        if tenant_id == refused:
            raise NoSpaceBound("the database refused this")
        return real(tenant_id, session_id, strict=strict)

    dest: dict[str, Any] = {}
    monkeypatch.setattr(made, "_load_one_from_primary", load)
    with watched((made, "_load_one_from_primary")) as calls, bind_scope(A, "reem"):
        made._add_spooled_only(dest)
    return dest, calls


@pytest.mark.usefixtures("enforced")
def test_a_spooled_chat_of_another_space_is_read_as_its_own(tmp_path: Path, monkeypatch: Any) -> None:
    dest, calls = _listed(tmp_path, monkeypatch)
    assert {s.tenant_id for s in dest.values()} == {A, B}
    assert {(c.space, c.args[0]) for c in calls} == {(A, A), (B, B)}


@pytest.mark.usefixtures("enforced")
def test_one_spooled_chat_that_is_refused_hides_no_other(tmp_path: Path, monkeypatch: Any,
                                                         caplog: pytest.LogCaptureFixture) -> None:
    dest, _calls = _listed(tmp_path, monkeypatch, refused=B)
    assert {s.tenant_id for s in dest.values()} == {A}
    assert "could not be read" in caplog.text


# ── a chat's move (row 7) ────────────────────────────────────────────────


def _handed_over(tmp_path: Path, *, committed: bool) -> tuple[SessionManager, Calls]:
    made = _manager(tmp_path)
    chat = ChatSession(session_id="chat", tenant_id=A, thread_id="t", messages=_messages("the trip"))
    copy = ChatSession(session_id="copy", tenant_id=B, thread_id="t2", messages=_messages("the trip"))
    with bind_scope(A, "reem"):
        made.put(chat)
        with watched(*_ROWS) as calls:
            assert made.hand_over("chat", messages=_messages("the trip"), to=copy, commit=lambda: committed) is committed
    return made, calls


@pytest.mark.usefixtures("enforced")
@pytest.mark.parametrize("committed", [True, False], ids=["it stands", "it does not"])
def test_a_moved_chats_copy_is_written_in_the_space_it_goes_to(tmp_path: Path, committed: bool) -> None:
    made, calls = _handed_over(tmp_path, committed=committed)
    copied = [c for c in calls if c.name in ("_upsert_db", "_delete_row") and _tenant_of(c) == B]
    assert [c.name for c in copied] == (["_upsert_db"] if committed else ["_upsert_db", "_delete_row"])
    assert {c.space for c in copied} == {B} and {c.scope for c in copied} == {Scope(A, "reem")}
    assert (made._load_one_from_primary(B, "copy") is not None) is committed


def test_off_a_hand_over_is_as_before(tmp_path: Path) -> None:
    made, _calls = _handed_over(tmp_path, committed=True)
    assert made._load_one_from_primary(B, "copy") is not None


# ── a refused read is not "no such chat" (H2) ────────────────────────────


def _unreadable(tmp_path: Path, exc: BaseException, monkeypatch: pytest.MonkeyPatch) -> SessionManager:
    made = _manager(tmp_path)
    made.put(ChatSession(session_id="chat", tenant_id="default", thread_id="t", messages=_messages("kept")))
    made._sessions.clear()  # a restart

    class _Refuses:
        def execute(self, *_a: Any, **_k: Any) -> Any:
            raise exc

        execute_one = execute

    made._conn = _Refuses()
    monkeypatch.setattr(pg_helpers, "get_pool", _Refuses)  # on a Postgres install
    return made


@pytest.mark.usefixtures("enforced")
def test_a_refused_chat_is_not_made_anew(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    made = _unreadable(tmp_path, NoSpaceBound("the database refused this"), monkeypatch)
    with bind_scope("default", "owner"), pytest.raises(NoSpaceBound):
        made.get_or_create("chat", durable=False)
    assert made._sessions == {}, "no empty chat in its place"


@pytest.mark.parametrize("on", [True, False], ids=["an outage, enforced", "a refusal, Spaces off"])
def test_every_other_failed_read_is_as_before(tmp_path: Path, monkeypatch: Any, on: bool) -> None:
    made = _unreadable(tmp_path, sqlite3.OperationalError("away") if on else NoSpaceBound("refused"),
                       monkeypatch)
    monkeypatch.setattr(scope, "_enforced", on)
    with bind_scope("default", "owner"):
        assert made.get_or_create("chat", durable=False).messages == []


# ── a start's listings (rows 37, 38) and the approvals query ─────────────


class _Checkpoints:
    """A checkpointer that says through which door it is read."""

    def __init__(self) -> None:
        self.listed: list[Any] = []
        self.read: list[Any] = []

    async def alist(self, _config: Any, *, limit: int) -> Any:
        for n in range(2):
            self.listed.append(Door.now())
            yield SimpleNamespace(config={"configurable": {"thread_id": f"t{n}", "checkpoint_ns": "",
                                                           "checkpoint_id": f"c{n}"}})

    async def aget_state(self, _config: Any) -> Any:
        self.read.append(Door.now())
        return None


def _cut_turns(monkeypatch: pytest.MonkeyPatch) -> _Checkpoints:
    import time

    from kazma_core import checkpoint_retention
    from kazma_ui import turn_resume

    monkeypatch.setattr(checkpoint_retention, "checkpoint_time", lambda _id: time.time())
    made = _Checkpoints()
    assert asyncio.run(turn_resume._find_cut_turns(made, made)) == []
    return made


@pytest.mark.usefixtures("enforced")
def test_the_starts_listing_of_cut_turns_is_the_installs(monkeypatch: pytest.MonkeyPatch) -> None:
    made = _cut_turns(monkeypatch)
    assert len(made.listed) == 2 and all(d.reason and not d.space for d in made.listed)
    assert made.read == [None, None], "and only the listing"


def test_off_the_starts_listing_binds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    made = _cut_turns(monkeypatch)
    assert len(made.read) == 2 and current_scope() is None


@pytest.mark.usefixtures("enforced")
def test_the_watchdogs_listing_is_the_installs_and_a_pages_is_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.db import availability
    from kazma_core.safety import hitl
    from kazma_ui import hitl_approval, hitl_gate_bridge, hitl_timeout

    seen: list[Any] = []
    passes = iter([False, False, True])
    monkeypatch.setattr(hitl_timeout, "_SCAN_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(hitl_timeout, "is_shutting_down", lambda: next(passes, True))
    monkeypatch.setattr(hitl, "get_hitl_config", lambda: {"enabled": True, "approval_timeout_seconds": 300})

    async def answers(*_a: Any, **_k: Any) -> Any:
        return None

    async def pending(_graph: Any, _checkpointer: Any) -> list[Any]:
        seen.append(Door.now())
        return []

    monkeypatch.setattr(availability, "wait_while_away", answers)
    monkeypatch.setattr(hitl_gate_bridge, "pending_items_from_registry", answers)  # the registry is off
    monkeypatch.setattr(hitl_approval, "_get_pending_approvals", pending)
    asyncio.run(hitl_timeout._watchdog_loop(lambda: object(), lambda: object()))
    assert len(seen) == 1 and seen[0].reason and not seen[0].space

    async def page() -> None:
        with bind_scope(A, "reem"):
            await hitl_approval._get_pending_approvals(None, None)

    asyncio.run(page())
    assert seen[1] == Door(space=A), "a page's keeps its own space (H6)"


def test_waiting_approvals_are_read_from_each_threads_state_alone() -> None:
    """On Postgres the rows are dicts: the query that went raised on ``row[0]``."""
    from kazma_ui import hitl_approval

    ran: list[str] = []

    class _Cursor:
        async def execute(self, sql: str, *_a: Any) -> None:
            ran.append(sql)

        async def fetchall(self) -> list[dict[str, str]]:
            return [{"thread_id": "t1"}]

        async def __aenter__(self) -> _Cursor:
            return self

        async def __aexit__(self, *_a: Any) -> None: ...

    class _Conn(_Cursor):
        def cursor(self) -> _Cursor:
            return _Cursor()

    class AsyncConnectionPool:
        def connection(self) -> _Conn:
            return _Conn()

    class _Graph:
        read: list[str] = []

        async def aget_state(self, config: Any) -> Any:
            self.read.append(config["configurable"]["thread_id"])

    graph = _Graph()
    asyncio.run(hitl_approval._get_pending_approvals(graph, SimpleNamespace(conn=AsyncConnectionPool())))
    assert graph.read == ["t1"] and len(ran) == 1, "one listing, then each thread's own state"


# ── the history import's thread (row 39) ─────────────────────────────────


@pytest.fixture
def person(monkeypatch: pytest.MonkeyPatch) -> Any:
    from kazma_core.security.vault import reset_posture_cache

    monkeypatch.setattr(scope, "_enforced", True)
    get_config_store().delete(identity.REGISTRY_KEY)
    made = people.create_person("Reem", "a-long-enough-password", by=OWNER_ACCOUNT_ID)["id"]
    space = people.create_shared_space("Family", by=OWNER_ACCOUNT_ID)["id"]
    people.add_member(space, made, by=OWNER_ACCOUNT_ID)
    try:
        yield SimpleNamespace(id=made, space=space)
    finally:
        get_config_store().delete(identity.REGISTRY_KEY)
        reset_posture_cache()


def _imported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, space: str, who: str) -> tuple[list[Any], Any]:
    from kazma_core.memory import history_import
    from kazma_ui import history_import_api

    seen: list[Any] = []

    def import_history(_history: Any, **kw: Any) -> Any:
        seen.append((current_scope(), kw["tenant_id"]))
        return SimpleNamespace(stopped=False, as_dict=lambda: {})

    monkeypatch.setattr(history_import, "read_export", lambda _path: object())
    monkeypatch.setattr(history_import, "import_history", import_history)
    path = tmp_path / "upload"
    path.write_text("x")
    job = history_import_api._Job()
    thread = threading.Thread(target=history_import_api._run, args=("token", path, space, job, *([who] if who else [])))
    thread.start()
    thread.join(20)
    assert not path.exists()
    return seen, job


def test_an_imported_history_is_its_persons_in_their_space(person: Any, monkeypatch: Any, tmp_path: Path) -> None:
    seen, job = _imported(monkeypatch, tmp_path, person.space, person.id)
    assert seen == [(Scope(person.space, person.id), person.space)] and job.state == "done"


def test_a_history_of_someone_who_left_is_not_imported_as_anyone_else(person: Any, monkeypatch: Any,
                                                                      tmp_path: Path) -> None:
    identity.remove_member(person.space, person.id)
    seen, job = _imported(monkeypatch, tmp_path, person.space, person.id)
    assert seen == [] and (job.state, job.error) == ("failed", "AskerLeft")


def test_off_an_import_binds_nothing(monkeypatch: Any, tmp_path: Path) -> None:
    seen, job = _imported(monkeypatch, tmp_path, "default", "")
    assert seen == [(None, "default")] and job.state == "done"


def test_the_upload_names_who_brought_it(person: Any, monkeypatch: Any) -> None:
    """The request's person, kept with the file: the start may be another
    request, the thread is neither."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.memory import history_import
    from kazma_ui import history_import_api

    monkeypatch.setattr(history_import_api, "_UPLOADS", {})
    monkeypatch.setattr(history_import, "read_export", lambda _path: SimpleNamespace(summary=lambda: {"turns": 1}))
    app = FastAPI()

    @app.middleware("http")
    async def as_reem(request: Any, call_next: Any) -> Any:
        tokens = scope.set_scope(person.space, person.id)
        try:
            return await call_next(request)
        finally:
            scope.reset_scope(tokens)

    app.include_router(history_import_api.create_history_import_router())
    with TestClient(app) as client:
        token = client.post("/api/import/history/preview", content=b"x").json()["token"]
    held = history_import_api._UPLOADS[token]
    assert held[3:] == (person.space, person.id)
    held[0].unlink()


# ── a job's address (row 45) ─────────────────────────────────────────────


def test_a_jobs_address_reads_no_chat() -> None:
    """A chat's row holds no address: the read gave none, and a refused one
    was a DEBUG line."""
    from kazma_core.cron import scheduler

    assert not hasattr(scheduler, "_web_session_for_thread")
    fields = set(ChatSession.__dataclass_fields__)
    assert not fields & {"platform", "chat_id"}, "a chat that holds its address is worth asking again"


# ── no row of a Postgres pool is read by its place (gate e) ──────────────


def rows_by_place(source: str) -> set[int]:
    """Lines that take a Postgres row by its place: inside a pool's
    ``connection()`` block, outside an ``isinstance(row, dict)`` choice
    (every Kazma pool answers dicts, and a plain cursor may stand in a test)."""
    found: set[int] = set()
    for block in ast.walk(ast.parse(source)):
        if not (isinstance(block, ast.With | ast.AsyncWith)
                and any(".connection(" in ast.unparse(item.context_expr) for item in block.items)):
            continue
        inside = [n for stmt in block.body for n in ast.walk(stmt)]
        chosen = {id(n) for c in inside if isinstance(c, ast.If | ast.IfExp) and "isinstance" in ast.unparse(c.test)
                  for n in ast.walk(c)}
        fetched = {t.id for n in inside if isinstance(n, ast.Assign) and "fetch" in ast.unparse(n.value)
                   for t in n.targets if isinstance(t, ast.Name)}
        rows = fetched | {n.target.id for n in inside
                          if isinstance(n, ast.For | ast.comprehension) and isinstance(n.target, ast.Name)
                          and ("fetch" in ast.unparse(n.iter)
                               or fetched & {x.id for x in ast.walk(n.iter) if isinstance(x, ast.Name)})}
        found |= {n.lineno for n in inside if isinstance(n, ast.Subscript) and id(n) not in chosen
                  and isinstance(n.value, ast.Name) and n.value.id in rows
                  and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, int)}
    return found


def test_no_postgres_row_is_read_by_its_place() -> None:
    files = subprocess.run(["git", "ls-files", "--", "kazma-*/*.py"], cwd=REPO, capture_output=True, text=True,
                           check=True).stdout.splitlines()
    found: list[str] = []
    for rel in files:
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        if ".connection(" in text:
            found += [f"{rel}:{line}" for line in rows_by_place(text)]
    assert found == [], "read it by its name: the pool's rows are dicts"


def test_a_planted_row_by_place_is_found() -> None:
    planted = ("async def f(conn):\n    async with conn.connection() as c:\n        rows = await c.fetchall()\n"
               "        return [row[0] for row in rows]\n")
    assert rows_by_place(planted) == {4}
    assert rows_by_place(planted.replace("row[0] for", "row['thread_id'] for")) == set()
    assert rows_by_place(planted.replace("row[0] for", "row['t'] if isinstance(row, dict) else row[0] for")) == set()
