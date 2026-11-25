"""A space erased and brought back in one process is what its bundle holds
(Spaces S4-06; found by the chat move, 25 Nov 2026).

What the process remembered of the space outlived it: a chat deleted there
stayed hidden and its thread ended until a restart, a chat still in the
process's cache showed what was said AFTER the export, and a copy held by a
late writer stored a chat of an erased space again.
"""

from __future__ import annotations

import copy
import sqlite3
import uuid
from types import SimpleNamespace

import pytest
from kazma_core.spaces import people, preconditions, scope, space_data, space_move, threads
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
from kazma_ui import session_manager

from tests._space_world import own_install
from tests.test_space_bundle_round_trip import ADMIN, PASSWORD, _client

LANGUAGES = {"en": ("the tent is blue", "said after the export"),
             "ar": ("الخيمة زرقاء", "قيلت بعد التصدير")}


@pytest.fixture(params=list(LANGUAGES))
def world(request, monkeypatch, tmp_path):
    monkeypatch.setattr(scope, "_enforced", True)
    monkeypatch.setattr(preconditions, "missing", lambda **_k: [])
    monkeypatch.setattr(threads, "_spaces", {})
    threads._ended.clear()
    before = session_manager.peek_session_manager()
    with own_install(monkeypatch, tmp_path / "one") as data:
        store = session_manager.SessionManager(db_path=str(data / "chat_sessions.db"))
        session_manager.set_session_manager(store)
        try:
            yield World(store, tmp_path / "b.kazmaspace", *LANGUAGES[request.param])
        finally:
            store.close()
            session_manager.set_session_manager(before)
            threads._ended.clear()


class World:
    def __init__(self, store, bundle, words, after) -> None:
        self.store, self.bundle, self.words, self.after = store, bundle, words, after
        self.person = people.create_person("mv", "a-long-enough-password", by=OWNER_ACCOUNT_ID)["id"]
        self.space = people.create_shared_space("Movers", by=OWNER_ACCOUNT_ID)["id"]
        self.other = people.create_shared_space("Others", by=OWNER_ACCOUNT_ID)["id"]

    def chat(self, space: str = "") -> SimpleNamespace:
        one = SimpleNamespace(sid=f"s{uuid.uuid4().hex[:8]}", thread=f"web-{uuid.uuid4().hex[:8]}",
                              space=space or self.space)
        with self.bound(one.space):
            row = self.store.get_or_create(one.sid, thread_id=one.thread)
            row.add_message("user", self.words, author=OWNER_ACCOUNT_ID)
            self.store.put(row)
        threads.record_thread(one.thread, one.space, OWNER_ACCOUNT_ID)
        return one

    def bound(self, space: str = "", who: str = OWNER_ACCOUNT_ID):
        return scope.bind_scope(space or self.space, who)

    def delete(self, chat) -> None:
        """A person's delete, as the route does it."""
        with self.bound(chat.space):
            self.store.mark_deleted(chat.sid, chat.thread)
            self.store.delete(chat.sid)
            threads.forget_thread(chat.thread)

    def say(self, chat, text: str) -> None:
        with self.bound(chat.space):
            row = self.store.get(chat.sid)
            row.add_message("user", text, author=OWNER_ACCOUNT_ID)
            self.store.put(row)

    def texts(self, chat) -> list[str] | None:
        with self.bound(chat.space):
            row = self.store.get(chat.sid)
            return None if row is None else [m["content"] for m in row.messages]

    def listed(self, space: str = "") -> set[str]:
        with self.bound(space):
            return {s.session_id for s in self.store.list_all()}

    def stored(self, space: str = "") -> int:
        return self.store._conn.execute("SELECT COUNT(*) FROM sessions WHERE tenant_id = ?",
                                        (space or self.space,)).fetchone()[0]

    def export(self) -> None:
        space_move.export_bundle(self.space, self.bundle, PASSWORD, by=OWNER_ACCOUNT_ID)

    def erase(self) -> None:
        with _client(ADMIN) as client:
            answer = client.delete(f"/api/spaces/{self.space}")
        assert answer.status_code == 200, answer.text

    def bring_in(self) -> None:
        with _client(ADMIN) as client, open(self.bundle, "rb") as handle:
            answer = client.post("/api/spaces/import", data={"password": PASSWORD, "owner": self.person},
                                 files={"bundle": ("a.zip", handle, "application/zip")})
        assert answer.status_code == 201, answer.text

    def runs(self, chat) -> bool:
        try:
            with self.bound(chat.space, self.person):
                threads.scope_for_turn(chat.thread)
        except threads.ThreadEnded:
            return False
        return True


def test_a_chat_deleted_after_the_export_is_back_with_its_space(world):
    chat, stays = world.chat(), world.chat()
    world.export()
    world.delete(chat)
    world.erase()
    world.bring_in()
    assert world.texts(chat) == [world.words]
    assert world.listed() == {chat.sid, stays.sid}
    assert world.runs(chat) and world.runs(stays)
    world.say(chat, "more")  # and it is written again
    world.store._sessions.clear()
    assert world.texts(chat) == [world.words, "more"]


def test_what_was_said_after_the_export_does_not_come_back(world):
    chat = world.chat()
    world.export()
    world.say(chat, world.after)
    world.erase()
    world.bring_in()
    assert world.texts(chat) == [world.words]
    world.say(chat, "more")
    world.store._sessions.clear()
    assert world.texts(chat) == [world.words, "more"], "nor with the next save"


def test_nothing_writes_a_chat_of_an_erased_space(world):
    chat = world.chat()
    with world.bound():
        held = copy.deepcopy(world.store.get(chat.sid))
    world.erase()
    held.add_message("assistant", world.after)
    with world.bound():
        world.store.put(held)
        with pytest.raises(session_manager.ChatDeleted):
            world.store.get_or_create(chat.sid, thread_id=chat.thread)
        with pytest.raises(session_manager.ChatDeleted):
            world.store.get_or_create("a-new-one")
    assert world.texts(chat) is None and world.stored() == 0 and world.listed() == set()
    assert not world.runs(chat)


def test_a_write_while_it_is_erased_goes_with_it(world, monkeypatch):
    """Between the erase of the rows and its end: a copy a writer held."""
    from kazma_ui.session_spool import spool_path_for

    chat, spooled = world.chat(), world.chat()
    with world.bound():
        held = copy.deepcopy(world.store.get(chat.sid))
        waiting = copy.deepcopy(world.store.get(spooled.sid))
    real = space_data._erase_secrets

    def late(space):
        with world.bound():
            world.store.put(held)
            world.store._spool_session(waiting, OSError("the database refused it"))
        assert world.stored() == 1
        return real(space)

    monkeypatch.setattr(space_data, "_erase_secrets", late)
    world.erase()
    assert world.stored() == 0
    assert not [k for k in world.store._spool.keys() if k[0] == world.space], spool_path_for(world.store.db_path)


def test_a_chat_deleted_in_another_space_stays_deleted(world):
    mine, theirs = world.chat(), world.chat(world.other)
    world.export()
    world.delete(theirs)
    world.erase()
    world.bring_in()
    assert world.texts(mine) == [world.words]
    assert world.texts(theirs) is None and theirs.sid not in world.listed(world.other)
    assert threads.thread_ended(theirs.thread)


def test_a_thread_ended_before_its_space_was_known_runs_again(world):
    chat = world.chat()
    world.export()
    with world.bound():
        world.store.delete(chat.sid)
    threads._spaces.clear()
    threads.forget_thread(chat.thread)
    world.erase()
    world.bring_in()
    assert world.runs(chat)


def test_an_import_that_is_refused_changes_nothing_remembered(world):
    chat = world.chat()
    world.export()
    world.delete(chat)
    with _client(ADMIN) as client, open(world.bundle, "rb") as handle:
        answer = client.post("/api/spaces/import", data={"password": PASSWORD, "owner": world.person},
                             files={"bundle": ("a.zip", handle, "application/zip")})
    assert answer.status_code == 409, answer.text  # the space is here
    assert world.texts(chat) is None and not world.runs(chat)


# ── the review's M3, L3 and L4 (25 Nov 2026) ───────────────────────────────


def _write(world, chat, text: str) -> bool:
    """An answer stored as a closing turn stores it."""
    from kazma_ui.reply_sink import upsert_reply

    with world.bound(chat.space):
        return upsert_reply(chat.sid, f"t-{uuid.uuid4().hex[:6]}", text)


def test_its_chats_are_written_while_its_threads_cannot_be_read(world, monkeypatch, caplog):
    chat = world.chat()
    world.export()
    world.delete(chat)
    threads.end_thread("web-of-nobody-known")  # a thread whose space was not known: the store is asked
    world.erase()

    def busy(_space):
        raise sqlite3.OperationalError("database is locked")

    real = space_move.import_bundle

    def imported(*args, **kwargs):  # the import itself read it
        taken = real(*args, **kwargs)
        monkeypatch.setattr(threads, "space_threads", busy)
        return taken

    monkeypatch.setattr(space_move, "import_bundle", imported)
    world.bring_in()  # and the import is not said to have failed
    assert "could not be read" in caplog.text
    assert world.runs(chat)
    assert _write(world, chat, "an answer") is True
    world.store._sessions.clear()
    assert world.texts(chat) == [world.words, "an answer"]


def test_no_answer_is_called_saved_that_was_not(world, monkeypatch):
    """Between the import's own end and the chat store being told."""
    chat = world.chat()
    world.export()
    world.erase()
    real, said = space_move.import_bundle, []

    def imported(*args, **kwargs):
        taken = real(*args, **kwargs)
        with world.bound():
            assert world.store.get(chat.sid) is not None  # the space is live
        said.append(_write(world, chat, "an answer"))
        return taken

    monkeypatch.setattr(space_move, "import_bundle", imported)
    world.bring_in()
    stored = world.texts(chat)
    assert said == [False] and stored == [world.words]
    assert _write(world, chat, "another") is True


def test_a_transaction_that_was_not_written_raises(world):
    chat = world.chat()
    with world.bound(), pytest.raises(session_manager.ChatDeleted):
        with world.store.transact(chat.sid) as row:
            row.add_message("assistant", "late")
            world.store.mark_deleted(chat.sid, chat.thread)


def test_a_copy_held_since_before_is_not_written_over_the_bundle(world):
    chat = world.chat()
    world.export()
    world.say(chat, world.after)
    with world.bound():
        held = world.store.get(chat.sid)
        copied = copy.deepcopy(held)
    world.erase()
    world.bring_in()
    with world.bound():
        for late in (held, copied):
            late.add_message("assistant", "later still")
            world.store.put(late)
    assert world.texts(chat) == [world.words]
    world.store._sessions.clear()
    assert world.texts(chat) == [world.words]
    world.say(chat, "more")  # control: one read now is written
    world.store._sessions.clear()
    assert world.texts(chat) == [world.words, "more"]


def test_a_list_read_across_the_arrival_caches_nothing_of_before(world, monkeypatch):
    chat = world.chat()
    world.export()
    world.say(chat, world.after)
    real = world.store._load_all_from_db

    def across(limit=None, target=None):
        real(limit=limit, target=target)
        monkeypatch.setattr(world.store, "_load_all_from_db", real)
        world.erase()
        world.bring_in()

    world.store._sessions.clear()
    monkeypatch.setattr(world.store, "_load_all_from_db", across)
    with world.bound():
        world.store._merge_missing_from_db()
    assert world.texts(chat) == [world.words]
    world.say(chat, "more")
    world.store._sessions.clear()
    assert world.texts(chat) == [world.words, "more"]


@pytest.mark.parametrize("where", ["rows", "spool"])
def test_an_erase_that_could_not_take_the_chats_is_run_again(world, monkeypatch, where):
    """Its rows stayed and the second DELETE was refused: the space had left."""
    chat, late = world.chat(), world.chat()
    with world.bound():
        held, waiting = copy.deepcopy(world.store.get(chat.sid)), copy.deepcopy(world.store.get(late.sid))
    real_secrets = space_data._erase_secrets

    def written_late(space):
        with world.bound():
            world.store.put(held)
            world.store._spool_session(waiting, OSError("refused"))
        return real_secrets(space)

    monkeypatch.setattr(space_data, "_erase_secrets", written_late)
    if where == "rows":
        real = world.store._let_go

        def stuck(_space):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(world.store, "_let_go", stuck)
        undo = lambda: monkeypatch.setattr(world.store, "_let_go", real)  # noqa: E731
    else:
        spool_discard = world.store._spool.discard

        def stuck_spool(*_a):
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(world.store._spool, "discard", stuck_spool)
        undo = lambda: monkeypatch.setattr(world.store._spool, "discard", spool_discard)  # noqa: E731
    with _client(ADMIN) as client:
        client_ = client
        with pytest.raises(sqlite3.OperationalError):
            client_.delete(f"/api/spaces/{world.space}")
    monkeypatch.setattr(space_data, "_erase_secrets", real_secrets)
    undo()
    world.erase()  # answers 200
    assert world.stored() == 0
    assert not [k for k in world.store._spool.keys() if k[0] == world.space]


# ── controls: each call taken out ─────────────────────────────────────────


def test_control_without_the_arrival(world, monkeypatch):
    monkeypatch.setattr(session_manager.SessionManager, "space_arrived", lambda *_a: None)
    with pytest.raises(AssertionError):
        test_a_chat_deleted_after_the_export_is_back_with_its_space(world)


def test_control_the_cached_copy_kept(world, monkeypatch):
    monkeypatch.setattr(session_manager.SessionManager, "space_erased", lambda *_a: None)
    monkeypatch.setattr(session_manager.SessionManager, "_let_go", lambda *_a: None)
    with pytest.raises(AssertionError):
        test_what_was_said_after_the_export_does_not_come_back(world)


def test_control_a_late_write_without_the_erase_s_mark(world, monkeypatch):
    monkeypatch.setattr(session_manager.SessionManager, "space_erased", lambda *_a: None)
    chat = world.chat()
    with world.bound():
        held = copy.deepcopy(world.store.get(chat.sid))
    world.erase()
    with world.bound():
        world.store.put(held)
    assert world.stored() == 1, "the fault: an erased chat is stored again"


def test_every_caller_of_an_erase_or_an_import_tells_the_store():
    """The gate: whoever erases or brings in a space tells the chat store."""
    import ast
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    listed = subprocess.run(["git", "ls-files", "kazma-*"], cwd=root, capture_output=True, text=True,
                            check=True).stdout.split()
    sources = {p: (root / p).read_text(encoding="utf-8") for p in listed if p.endswith(".py")}
    sources["planted.py"] = "def wipe(s):\n    space_data.erase_space(s, by='x')\n"
    untold = []
    for path, source in sources.items():
        if "space_data.erase_space" not in source and "space_move.import_bundle" not in source:
            continue
        for function in (n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef)):
            called = {ast.unparse(c.func) for c in ast.walk(function) if isinstance(c, ast.Call)}
            for asked, told in (("space_data.erase_space", "space_erased"), ("space_move.import_bundle", "space_arrived")):
                if asked in called and not any(name.endswith(told) for name in called):
                    untold.append(path)
    assert untold == ["planted.py"]
