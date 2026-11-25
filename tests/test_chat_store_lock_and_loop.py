"""The chat store's one SQLite connection and its lock, and the event loop (the
fifth review of the chat move, Spaces S2-12; every install).

* **M1** ``SessionManager.held`` read the store's one connection without its
  lock: "no row" for a chat that is there, and for another chat read beside it.
  A gate: every public method of the store is used with its connection
  watched; the read of before is the control.
* **M3** asking whether a chat was deleted waited for other chats' saves, on
  the event loop. A gate: what an ``async def`` asks of the store itself waits
  for no save; a planted call, and the question under the store's lock, are the
  controls.
"""

from __future__ import annotations

import ast
import inspect
import sqlite3
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from kazma_core.spaces.scope import OWNER_SPACE_ID
from kazma_ui.session_manager import ChatSession, SessionManager

REPO = Path(__file__).resolve().parents[1]

# ── M1: the store's one connection, under its lock ────────────────────────

#: The sidebar's list reads outside the lock on purpose (FA-278: a slow query
#: never stalls a writer): its own statement, one refresh at a time.
_LIST_READ = "ORDER BY COALESCE(NULLIF(updated_at"


class _Watched:
    """The store's SQLite connection, noting each use without its lock."""

    def __init__(self, conn: sqlite3.Connection, lock: Any) -> None:
        self._conn, self._held, self.seen, self.bare = conn, lock._is_owned, 0, []

    def execute(self, sql: str, *args: Any) -> Any:
        self.seen += 1
        if not self._held() and _LIST_READ not in sql:
            self.bare.append(" ".join(sql.split())[:60])
        return self._conn.execute(sql, *args)

    def __enter__(self) -> Any:
        return self._conn.__enter__()

    def __exit__(self, *exc: Any) -> Any:
        return self._conn.__exit__(*exc)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


@pytest.fixture
def chats(tmp_path) -> Iterator[SessionManager]:
    made = SessionManager(db_path=str(tmp_path / "chats.db"))
    yield made
    made.close()


def _uses(s: SessionManager) -> dict[str, Callable[[], Any]]:
    """A use of each public method that may reach the database; most with an
    empty cache, so they do."""
    row = ChatSession(session_id="u-1", thread_id="u-t", messages=[{"role": "user", "content": "q"}])
    s.put(row)

    def cold(use: Callable[[], Any]) -> Callable[[], Any]:
        return lambda: (s._sessions.clear(), use())

    def transact() -> None:
        with s.transact("u-1") as held:
            held.title = "t"

    def hand_over() -> bool:
        s.put(ChatSession(session_id="u-1", thread_id="u-t", messages=[{"role": "user", "content": "q"}]))
        copied = ChatSession(session_id="u-2", thread_id="u-t2", tenant_id=OWNER_SPACE_ID)
        return s.hand_over("u-1", messages=s.held("u-1").messages, to=copied, commit=lambda: False)

    return {
        "get": cold(lambda: s.get("u-1")),
        "held": cold(lambda: s.held("u-1")),
        "snapshot": cold(lambda: s.snapshot("u-1")),
        "put": lambda: s.put(row),
        "transact": cold(transact),
        "get_or_create": cold(lambda: s.get_or_create("u-1")),
        "get_by_thread_id": cold(lambda: s.get_by_thread_id("u-t")),
        "thread_is_exclusive": cold(lambda: s.thread_is_exclusive("u-t")),
        "another_chat_on": cold(lambda: s.another_chat_on("u-t", "x")),
        "unmark_if_held": cold(lambda: s.unmark_if_held("u-1", "u-t")),
        "add_usage": cold(lambda: s.add_usage("u-1", 1, 0.0)),
        "rename": cold(lambda: s.rename("u-1", "r")),
        "set_archived": cold(lambda: s.set_archived("u-1", True)),
        "set_pinned": cold(lambda: s.set_pinned("u-1", True)),
        "update_from_dict": cold(lambda: s.update_from_dict("u-1", {"messages": []})),
        "list_all": lambda: (s._list_refreshed_at.clear(), s.list_all(include_empty=True)),
        "prune_empty_web_sessions": lambda: s.prune_empty_web_sessions(max_age_seconds=0.0),
        "hand_over": cold(hand_over),
        "delete_if": cold(lambda: s.delete_if("u-1", lambda _row: False)),
        "delete": cold(lambda: s.delete("u-1")),
        "clear": s.clear,
        "close": s.close,
    }


def test_no_delete_comes_between_an_unmarks_question_and_its_answer(chats, monkeypatch) -> None:
    """A move put back asks whether the chat is there, and takes the mark
    away: a person's delete between the two was undone."""
    chats.put(ChatSession(session_id="u-1", thread_id="u-t", messages=[{"role": "user", "content": "q"}]))
    chats.mark_deleted("u-1", "u-t")
    real, deletes = SessionManager.held, []

    def held(self: SessionManager, session_id: str) -> ChatSession | None:
        found = real(self, session_id)
        if not deletes:
            deletes.append(threading.Thread(target=self.delete, args=("u-1",)))
            deletes[0].start()
            deletes[0].join(timeout=0.5)
        return found

    monkeypatch.setattr(SessionManager, "held", held)
    chats.unmark_if_held("u-1", "u-t")
    deletes[0].join(timeout=30)
    assert real(chats, "u-1") is None and chats.chat_deleted("u-1")


#: What is asked of the store on the event loop: memory only (M3).
MEMORY_ONLY: dict[str, Callable[[SessionManager], Any]] = {
    "chat_deleted": lambda s: s.chat_deleted("s", "t"),
    "begin_again": lambda s: s.begin_again("gw-telegram-1"),
    "mark_deleted": lambda s: s.mark_deleted("s-marked", "t-marked"),
    "unmark_deleted": lambda s: s.unmark_deleted("s-marked", "t-marked"),
}


def _methods() -> set[str]:
    return {name for name, _fn in inspect.getmembers(SessionManager, inspect.isfunction) if not name.startswith("_")}


def _without_the_lock(s: SessionManager) -> list[str]:
    uses = _uses(s)
    watched = s._conn = _Watched(s._conn, s._lock)
    bare: list[str] = []
    for name, use in uses.items():
        use()
        bare += [f"{name}: {sql}" for sql in watched.bare]
        watched.bare.clear()
    assert watched.seen > len(uses), "the uses reach the database"
    return bare


def test_every_use_of_the_stores_connection_holds_its_lock(chats) -> None:
    assert set(_uses(chats)) | set(MEMORY_ONLY) == _methods(), "a method the gate does not use"
    assert _without_the_lock(chats) == []


def test_control_the_read_of_before_is_seen(chats, monkeypatch) -> None:
    def held(self: SessionManager, session_id: str) -> ChatSession | None:
        return self._load_one_from_primary(OWNER_SPACE_ID, session_id, strict=True)

    monkeypatch.setattr(SessionManager, "held", held)
    assert any(line.startswith("held: SELECT") for line in _without_the_lock(chats))


def test_a_chat_that_is_there_is_never_read_as_gone(chats) -> None:
    """What went wrong: a read beside another's answered "no row", and raised
    "bad parameter or other API misuse"."""
    chats.put(ChatSession(session_id="u-1", thread_id="u-t", messages=[{"role": "user", "content": "q"}]))
    wrong: list[str] = []

    def read() -> None:
        for _ in range(2000):
            try:
                if chats.held("u-1") is None:
                    wrong.append("no row")
            except (sqlite3.Error, SystemError) as exc:
                wrong.append(repr(exc))

    readers = [threading.Thread(target=read) for _ in range(4)]
    for reader in readers:
        reader.start()
    for reader in readers:
        reader.join()
    assert wrong == []


# ── M3: what is asked on the event loop waits for no save ─────────────────


def _answers_while_a_save_runs(s: SessionManager, ask: Callable[[SessionManager], Any], seconds: float) -> bool:
    """Whether *ask* answers while another thread holds the store's lock, as
    a save does while the database is slow."""
    saving, done = threading.Event(), threading.Event()

    def save() -> None:
        with s._lock:
            saving.set()
            done.wait(30)

    holder = threading.Thread(target=save)
    holder.start()
    assert saving.wait(10)
    asked = threading.Thread(target=lambda: ask(s))
    asked.start()
    asked.join(seconds)
    answered = not asked.is_alive()
    done.set()
    holder.join()
    asked.join()
    return answered


@pytest.mark.parametrize("name", sorted(MEMORY_ONLY))
def test_it_answers_while_a_save_runs(chats, name) -> None:
    assert _answers_while_a_save_runs(chats, MEMORY_ONLY[name], 10.0)


def test_control_under_the_stores_lock_it_waited(chats, monkeypatch) -> None:
    def chat_deleted(self: SessionManager, session_id: str = "", thread_id: str = "") -> bool:
        with self._lock:
            return self._is_deleted(OWNER_SPACE_ID, session_id, thread_id)

    monkeypatch.setattr(SessionManager, "chat_deleted", chat_deleted)
    assert not _answers_while_a_save_runs(chats, MEMORY_ONLY["chat_deleted"], 0.5)


_STORE_GETTERS = {"get_session_manager", "peek_session_manager", "_get_store", "_store", "_sessions"}
_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _own_nodes(fn: Any) -> Iterator[ast.AST]:
    """The nodes of *fn* itself, not of a function made inside it."""
    stack = [n for n in fn.body if not isinstance(n, _NESTED)]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(c for c in ast.iter_child_nodes(node) if not isinstance(c, _NESTED))


def _is_store(node: ast.AST, names: set[str]) -> bool:
    if isinstance(node, ast.Call):
        return getattr(node.func, "id", getattr(node.func, "attr", "")) in _STORE_GETTERS
    return isinstance(node, ast.Name) and node.id in names


def _store_calls(fn: Any, methods: set[str]) -> set[str]:
    names = {t.id for n in _own_nodes(fn) if isinstance(n, ast.Assign) and _is_store(n.value, set())
             for t in n.targets if isinstance(t, ast.Name)}
    return {n.func.attr for n in _own_nodes(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in methods and _is_store(n.func.value, names)}


def _asked_on_the_loop(source: str, methods: set[str]) -> set[str]:
    """The store's methods an ``async def`` of *source* calls itself, or
    through a plain function of the same file it calls itself."""
    tree = ast.parse(source)
    plain = {f.name: _store_calls(f, methods) for f in tree.body if isinstance(f, ast.FunctionDef)}
    found: set[str] = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.AsyncFunctionDef):
            found |= _store_calls(fn, methods)
            for node in _own_nodes(fn):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    found |= plain.get(node.func.id, set())
    return found


def test_nothing_else_of_the_chat_store_is_asked_on_the_event_loop() -> None:
    """The gateway's ``/reset`` listed and saved chats there."""
    found: dict[str, set[str]] = {}
    for package in ("kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway", "kazma-core/kazma_core"):
        for path in (REPO / package).rglob("*.py"):
            source = path.read_text(encoding="utf-8", errors="replace")
            if "session_manager" in source or "_get_store" in source:
                asked = _asked_on_the_loop(source, _methods()) - set(MEMORY_ONLY)
                if asked:
                    found[path.relative_to(REPO).as_posix()] = asked
    assert found == {}, "await asyncio.to_thread(...) it"


def test_control_a_planted_call_on_the_loop_is_caught() -> None:
    planted = ("from kazma_ui.session_manager import get_session_manager\n"
               "def helper():\n    store = get_session_manager()\n    return store.get('x')\n"
               "async def a():\n    get_session_manager().put(1)\n"
               "async def b():\n    return helper()\n"
               "async def fine():\n    await asyncio.to_thread(helper)\n"
               "    def later():\n        return get_session_manager().list_all()\n")
    assert _asked_on_the_loop(planted, _methods()) == {"put", "get"}
