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


@pytest.mark.usefixtures("enforced")
def test_a_moves_commit_is_not_the_targets(tmp_path: Path) -> None:
    """Only the copy's row is the target's: the journal's step and the chat
    here are the move's own."""
    made = _manager(tmp_path)
    copy = ChatSession(session_id="copy", tenant_id=B, thread_id="t2", messages=_messages("the trip"))
    doors: list[Any] = []

    def commit() -> bool:
        doors.append(Door.now())
        return True

    with bind_scope(A, "reem"):
        made.put(ChatSession(session_id="chat", tenant_id=A, thread_id="t", messages=_messages("the trip")))
        assert made.hand_over("chat", messages=_messages("the trip"), to=copy, commit=commit)
    assert doors == [Door(space=A)]


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


@pytest.mark.usefixtures("enforced")
def test_a_refused_chat_is_not_missing_by_its_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    made = _unreadable(tmp_path, NoSpaceBound("the database refused this"), monkeypatch)
    with bind_scope("default", "owner"), pytest.raises(NoSpaceBound):
        made.get_by_thread_id("t")


@pytest.mark.parametrize("on", [True, False], ids=["an outage, enforced", "a refusal, Spaces off"])
def test_every_other_thread_lookup_is_as_before(tmp_path: Path, monkeypatch: Any, on: bool) -> None:
    made = _unreadable(tmp_path, sqlite3.OperationalError("away") if on else NoSpaceBound("refused"),
                       monkeypatch)
    monkeypatch.setattr(scope, "_enforced", on)
    with bind_scope("default", "owner"):
        assert made.get_by_thread_id("t") is None


@pytest.mark.parametrize("on", [True, False], ids=["enforced", "Spaces off"])
def test_a_spooled_chat_that_fails_otherwise_is_raised_as_before(tmp_path: Path, monkeypatch: Any, on: bool) -> None:
    monkeypatch.setattr(SessionManager, "_drain_spool", lambda self: None)
    _spooled(tmp_path)
    made = _manager(tmp_path)
    monkeypatch.setattr(scope, "_enforced", on)

    def fails(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("a bug")

    monkeypatch.setattr(made, "_load_one_from_db", fails)
    with pytest.raises(RuntimeError, match="a bug"):
        made._add_spooled_only({})


@pytest.mark.parametrize("on", [True, False], ids=["enforced", "Spaces off"])
def test_a_chat_apps_turn_that_is_refused_is_said_to_be_lost(monkeypatch: Any, on: bool,
                                                             caplog: pytest.LogCaptureFixture) -> None:
    """It was a DEBUG line. With Spaces off it still is."""
    from kazma_core.observability import ops_alerts
    from kazma_core.sessions import directory
    from kazma_gateway.agent_handler import graph

    def refused(_thread: str) -> Any:
        raise NoSpaceBound("the database refused this")

    sent: list[str] = []
    monkeypatch.setattr(scope, "_enforced", on)
    monkeypatch.setattr(directory, "canonical_web_session", refused)
    monkeypatch.setattr(ops_alerts, "alert", lambda key, *_a, **_k: sent.append(key))
    caplog.set_level("WARNING", logger="kazma_gateway.agent_handler.graph")
    graph._sync_platform_session_to_web("gw-telegram-1", "telegram", {}, [])
    assert sent == (["reply.persist_failed"] if on else [])
    assert ("not saved to the transcript" in caplog.text) is on


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
    assert seen == [] and (job.state, job.error) == ("failed", "left_space")


def test_a_person_who_leaves_while_it_runs_ends_it(person: Any, monkeypatch: Any, tmp_path: Path) -> None:
    from kazma_core.memory import history_import
    from kazma_ui import history_import_api

    monkeypatch.setattr(history_import_api, "_ACCOUNTS_ASK_S", 0.0)
    turns: list[bool] = []

    def import_history(_history: Any, *, should_stop: Any, **_kw: Any) -> Any:
        for n in range(5):
            if n == 2:
                identity.remove_member(person.space, person.id)
            if should_stop():
                return SimpleNamespace(stopped=True, as_dict=lambda: {})
            turns.append(True)
        return SimpleNamespace(stopped=False, as_dict=lambda: {})

    monkeypatch.setattr(history_import, "read_export", lambda _path: object())
    monkeypatch.setattr(history_import, "import_history", import_history)
    path = tmp_path / "upload"
    path.write_text("x")
    job = history_import_api._Job()
    history_import_api._run("token", path, person.space, job, person.id)
    assert len(turns) == 2 and (job.state, job.error) == ("failed", "left_space")


def test_an_import_waits_while_the_accounts_cannot_be_read(person: Any, monkeypatch: Any, tmp_path: Path) -> None:
    """An outage failed it and deleted the uploaded file."""
    from kazma_ui import history_import_api

    monkeypatch.setattr(history_import_api, "_ACCOUNTS_ASK_S", 0.01)
    real, asked = identity.read_registry, []

    def away_twice() -> Any:
        asked.append((tmp_path / "upload").exists())
        if len(asked) <= 2:
            raise identity.IdentityUnavailable("the settings store is away")
        return real()

    monkeypatch.setattr(identity, "read_registry", away_twice)
    seen, job = _imported(monkeypatch, tmp_path, person.space, person.id)
    assert asked[:2] == [True, True], "the file is kept meanwhile"
    assert len(seen) == 1 and job.state == "done"


def test_an_import_that_waited_in_vain_says_so_in_a_code(person: Any, monkeypatch: Any, tmp_path: Path) -> None:
    from kazma_ui import history_import_api

    monkeypatch.setattr(history_import_api, "_ACCOUNTS_ASK_S", 0.01)
    monkeypatch.setattr(history_import_api, "_ACCOUNTS_WAIT_S", 0.05)

    def away() -> Any:
        raise identity.IdentityUnavailable("the settings store is away")

    monkeypatch.setattr(identity, "read_registry", away)
    _seen, job = _imported(monkeypatch, tmp_path, person.space, person.id)
    assert (job.state, job.error) == ("failed", "accounts_unavailable")


def test_every_code_of_a_failed_import_has_words_in_both_languages() -> None:
    from kazma_ui import history_import_api
    from kazma_ui.i18n.catalog.history_import import TRANSLATIONS

    source = Path(history_import_api.__file__).read_text(encoding="utf-8")
    why = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == "_why")
    codes = {n.value for r in ast.walk(why) if isinstance(r, ast.Return) for n in ast.walk(r)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert codes == {"left_space", "accounts_unavailable", "not_an_export", "failed"}
    page = (REPO / "kazma-ui/kazma_ui/static/js/history_import.js").read_text(encoding="utf-8")
    for code in codes:
        words = TRANSLATIONS[f"memory.import.why_{code}"]
        assert words["en"] and words["ar"] and f"memory.import.why_{code}" in page


def test_a_started_import_is_its_uploaders(person: Any, monkeypatch: Any, tmp_path: Path) -> None:
    """The upload's person reaches the thread, whoever's request starts it."""
    from kazma_ui import history_import_api

    ran: list[Any] = []
    done = threading.Event()
    monkeypatch.setattr(history_import_api, "_JOBS", {})
    monkeypatch.setattr(history_import_api, "_run", lambda *args: (ran.append(args[2:]), done.set()))
    with bind_scope(person.space, OWNER_ACCOUNT_ID):
        assert history_import_api._claim(person.space, "token", (tmp_path, 0.0, {}, person.space, person.id))
    assert done.wait(5)
    assert ran[0][0] == person.space and ran[0][2] == person.id


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

_FETCH_ONE, _FETCH_MANY = {"fetchone", "execute_one"}, {"fetchall", "fetchmany"}


def _last(call: ast.AST) -> str:
    return call.func.attr if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) else ""


def rows_by_place(source: str) -> set[int]:
    """Lines that take a Postgres row by its place: the shared pool's and the
    checkpointer's rows are dicts, so it raises or reads the wrong thing.

    What it reads: a row of a pool's ``connection()`` block, or of
    ``pool.execute`` / ``execute_one`` (``get_pool()``, a name with ``pool``
    in it): indexed by a number, or unpacked. Through ``await``, a name, a
    walrus, a loop or a comprehension, and after the block too, in the same
    function. Not a choice by ``isinstance`` or ``hasattr``, nor the rows of
    SQLite (``_conn``, ``sqlite``). What it does not: a row handed to another
    function, or kept on an object."""
    found: set[int] = set()
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        nodes = list(ast.walk(fn))
        in_block = {id(n) for b in nodes if isinstance(b, ast.With | ast.AsyncWith)
                    and any(".connection(" in ast.unparse(i.context_expr) for i in b.items)
                    for stmt in b.body for n in ast.walk(stmt)}
        chosen = {id(n) for c in nodes if isinstance(c, ast.If | ast.IfExp)
                  and ("isinstance" in ast.unparse(c.test) or "hasattr" in ast.unparse(c.test))
                  for n in ast.walk(c)}
        cursors = {i.optional_vars.id for b in nodes if isinstance(b, ast.With | ast.AsyncWith) for i in b.items
                   if isinstance(i.optional_vars, ast.Name) and ".cursor(" in ast.unparse(i.context_expr)
                   and (id(b) in in_block or any(".connection(" in ast.unparse(x.context_expr) for x in b.items))}
        #: name -> its assignments: (line, "one" | "many" | "").
        named: dict[str, dict[int, str]] = {}

        def is_pg(call: ast.Call) -> bool:
            owner = ast.unparse(call.func.value)
            if "sqlite" in owner or "_conn" in owner:
                return False
            return id(call) in in_block or "pool" in owner.lower()

        def kind(node: ast.AST) -> str:
            if isinstance(node, ast.Await):
                return kind(node.value)
            if isinstance(node, ast.NamedExpr):
                return kind(node.value)
            if isinstance(node, ast.BoolOp):
                return next((k for k in map(kind, node.values) if k), "")
            if isinstance(node, ast.Name):
                if node.id in cursors:
                    return "many"
                # The newest assignment before: a name may hold SQLite's row
                # in one branch and Postgres's in the other.
                before = [line for line in named.get(node.id, {}) if line <= node.lineno]
                return named[node.id][max(before)] if before else ""
            if isinstance(node, ast.Call) and _last(node) and is_pg(node):
                if _last(node) in _FETCH_ONE:
                    return "one"
                if _last(node) in _FETCH_MANY or (_last(node) == "execute" and "pool" in ast.unparse(node.func.value)):
                    return "many"
            if isinstance(node, ast.Subscript) and kind(node.value) == "many" and not isinstance(node.slice, ast.Slice):
                return "one"
            return ""

        for _ in range(3):  # names of names
            for node in nodes:
                pairs: list[tuple[ast.AST, str]] = []
                if isinstance(node, ast.Assign):
                    pairs = [(t, kind(node.value)) for t in node.targets]
                elif isinstance(node, ast.NamedExpr):
                    pairs = [(node.target, kind(node.value))]
                elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
                    pairs = [(node.target, "one" if kind(node.iter) == "many" else "")]
                for target, what in pairs:
                    if isinstance(target, ast.Name):
                        named.setdefault(target.id, {})[node.lineno if hasattr(node, "lineno")
                                                        else target.lineno] = what
                    if isinstance(target, ast.Tuple | ast.List) and what == "one" and id(node) not in chosen:
                        found.add(target.lineno)
        for node in nodes:
            if isinstance(node, ast.Subscript) and id(node) not in chosen and kind(node.value) == "one":
                place = node.slice.operand if isinstance(node.slice, ast.UnaryOp) else node.slice
                if isinstance(place, ast.Constant) and isinstance(place.value, int):
                    found.add(node.lineno)
    return found


#: Files whose Postgres connections answer tuples: the memory mirror and
#: pgvector have connections of their own (``memory/backends.py``).
TUPLES = ("kazma-core/kazma_core/memory/",)

#: Places that read both backends' rows in one function: SQLite's by place.
BOTH_BACKENDS: dict[str, str] = {}


def test_no_postgres_row_is_read_by_its_place() -> None:
    files = subprocess.run(["git", "ls-files", "--", "kazma-*/*.py"], cwd=REPO, capture_output=True, text=True,
                           check=True).stdout.splitlines()
    found: set[str] = set()
    for rel in files:
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        if not rel.startswith(TUPLES) and (".connection(" in text or "pool" in text):
            found |= {f"{rel}:{line}" for line in rows_by_place(text)}
    places = {f.rsplit(":", 1)[0] for f in found}
    assert {f for f in found if f.rsplit(":", 1)[0] not in BOTH_BACKENDS} == set(), \
        "read it by its name: the pool's rows are dicts"
    assert set(BOTH_BACKENDS) - places == set(), "declared, and no longer there"


def _planted(body: str) -> str:
    return "async def f(self, conn):\n    async with conn.connection() as c, c.cursor() as cur:\n" + "".join(
        f"        {line}\n" for line in body.split("\n"))


@pytest.mark.parametrize("body", [
    "rows = await cur.fetchall()\nreturn [row[0] for row in rows]",
    "return (await cur.fetchone())[0]",
    "return cur.fetchone()[0]",
    "rows = cur.fetchall()\nfor a, b in rows:\n    use(a, b)",
    "a, b = cur.fetchone()",
    "for r in cur.fetchall():\n    use(r[-1])",
    "for r in cur:\n    use(r[0])",
    "if (row := cur.fetchone()):\n    use(row[0])",
    "rows = cur.fetchall()\nreturn rows[0][0]",
])
def test_a_planted_row_by_place_is_found(body: str) -> None:
    assert rows_by_place(_planted(body)), body


@pytest.mark.parametrize("source", [
    "def f(pool):\n    row = pool.execute_one(q)\n    return row[0]\n",
    "def f():\n    for r in get_pool().execute(q):\n        use(r[0])\n",
    "def f(c):\n    with c.connection() as conn:\n        rows = conn.execute(q).fetchall()\n    return rows[0][0]\n",
])
def test_a_planted_row_of_a_pool_or_after_the_block_is_found(source: str) -> None:
    assert rows_by_place(source), source


@pytest.mark.parametrize("body", [
    "rows = cur.fetchall()\nreturn rows[0]['n']",
    "row = cur.fetchone()\nreturn row['n']",
    "rows = cur.fetchall()\nreturn [r['t'] if isinstance(r, dict) else r[0] for r in rows]",
    "row = self._conn.execute(q).fetchone()\nreturn row[0]",
    "name = path.split('.')[0]",
])
def test_a_right_read_is_not_a_finding(body: str) -> None:
    assert rows_by_place(_planted(body)) == set(), body
