"""Spaces S2-12: what memory does when a person moves their chat between their
own space and a shared one (``kazma_ui.chat_move``). The owner's rule of
2026-11-24: moving a chat moves the conversation; what Kazma remembered from
it stays where it was said, and from now on it remembers what is said in the
new space; taking a chat out of a shared space makes that space forget it.

Through the real move on a private memory store and a private chat store:
the post-turn pipeline for the turns, turn reconcile, the past-chats search,
the forget ledger and journal. Sara moves her chat between her personal space
and a shared space she is in with Omar, in English and in Arabic.

1. Nothing is carried, retired or rebuilt: her memory is, byte for byte, what
   it was, and the shared space learns nothing from what was said before --
   turn reconcile, the past-chats search and the scan of old sessions alike
   (control: a copy whose thread does not say when it was copied).
2. What is said after the move is remembered, and a turn its live write
   missed is reconciled; a fork of the moved chat learns nothing either. A
   turn memory missed before the chat was shared is written where it was said.
3. A chat kept out of memory stays out in the space it moves to, and where it
   was under both its keys.
4. Taken out of the shared space, that space forgets it -- memories, the
   agent's notes, weekly summaries, lessons -- and nothing else.
5. Personal memory is never touched by a move in either direction, nor by an
   undo made in the other space.
6. A write still queued when the chat moves is the source's, which keeps it
   (her own space) or not (a shared space it was taken out of); a forget
   still pending there never reaches the chat shared again.
7. A forget that fails half way is finished by the forget convergence, and a
   memory database restored from before a take-out forgets it again.
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
from collections import OrderedDict
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.memory import forget, forget_journal
from kazma_core.spaces import scope, threads

from tests._chat_move_space_cases import Checkpoints, NoCardGraph
from tests.test_memory_speakers import OMAR, SARA, TEAM, _say, team  # noqa: F401

OWN = "sara-own"
T0 = 1_780_000_000.0

#: What Sara says in each language: (a home, an employer) and a word of each.
SAYINGS = {
    "en": (("I live in Lisbon.", "Lisbon"), ("I work at Initech.", "Initech")),
    "ar": (("أنا ساكنة في لشبونة", "لشبونة"), ("أشتغل في شركة النخيل", "النخيل")),
}
_REAL_CHAT_KEYS = forget.chat_keys


class Chat:
    """Sara's chat: its row in the real chat store, its turns remembered by
    the real post-turn pipeline where it is. ``session`` and ``thread`` are
    the ids it has now."""

    def __init__(self, team: Any) -> None:  # noqa: F811 -- the fixture
        self.team = team
        self.db: sqlite3.Connection = team.db
        self.session, self.thread = "s-first", "t-first"
        self.last = T0

    def store(self) -> Any:
        from kazma_ui.session_manager import get_session_manager

        return get_session_manager()

    def begin(self, space: str) -> None:
        threads.record_thread(self.thread, space, SARA)

    def row(self, space: str, text: str, *, at: float, author: str = SARA, answer: str = "Noted.") -> None:
        with scope.bind_scope(space, author):
            store = self.store()
            session = store.get_or_create(self.session, thread_id=self.thread)
            stamp = datetime.fromtimestamp(at, UTC).isoformat()
            session.add_message("user", text, author=author)
            session.messages[-1]["ts"] = stamp
            session.add_message("assistant", answer)
            session.messages[-1]["ts"] = stamp
            session.title = "a chat"
            store.put(session)
        self.last = max(self.last, at)

    def say(self, space: str, text: str, *, n: int, at: float, author: str = SARA) -> None:
        self.row(space, text, at=at, author=author)
        _say(author, self.thread, text, n=n, at=at, space=space)

    def move(self, frm: str, to: str) -> tuple[str, str]:
        """Move it; the ids it had."""
        from kazma_ui import chat_move

        had = self.session, self.thread
        with pytest.MonkeyPatch.context() as patch:
            # Right after what was said: the turns of these tests are dated.
            patch.setattr(chat_move, "time", SimpleNamespace(time=lambda: self.last + 1))
            answer = asyncio.run(chat_move._move_chat(session_id=self.session, from_space=frm, to_space=to, by=SARA,
                                                      graph=NoCardGraph(), checkpoints=Checkpoints()))
        with scope.bind_scope(to, SARA):
            self.session = answer["session_id"]
            self.thread = self.store().get(self.session).thread_id
        assert threads.thread_home(self.thread) == to and self.thread != had[1]
        return had

    def reconcile(self) -> dict[str, Any]:
        from kazma_core.memory.turn_reconcile import run_turn_reconcile_pass

        return run_turn_reconcile_pass()

    def held_row(self, space: str) -> Any:
        with scope.bind_scope(space, SARA):
            return self.store().get(self.session)

    def remembers(self, space: str) -> list[str]:
        return [str(r[0]) for r in self.db.execute(
            "SELECT user_text FROM episodes WHERE tenant_id = ? AND tier != 'forgotten' "
            "AND user_text != '' ORDER BY created_at", (space,))]

    def finds(self, space: str, word: str) -> list[str]:
        from kazma_core.memory.transcript_recall import search_transcripts

        return [h["session_id"] for h in search_transcripts(word, tenant_id=space)]

    def facts(self, space: str) -> set[str]:
        return {str(r[0]) for r in self.db.execute(
            "SELECT object FROM beliefs WHERE tenant_id = ? AND valid_until IS NULL AND invalidated_at IS NULL "
            "AND object != ''", (space,))}

    def memory(self, space: str) -> dict[str, list[tuple[Any, ...]]]:
        """Every row of *space* in the memory database but the ledger's (an
        entity's counts, kept for the page, are filled in whenever the
        database is next opened)."""
        self.db.rollback()
        held: dict[str, list[tuple[Any, ...]]] = {}
        for (table,) in self.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
            columns = [r[1] for r in self.db.execute(f'PRAGMA table_info("{table}")')]
            if "tenant_id" in columns and table != "memory_forgotten":
                names = ", ".join(f'"{c}"' for c in columns if c not in ("belief_count", "graph_degree"))
                held[table] = sorted((tuple(r) for r in self.db.execute(
                    f'SELECT {names} FROM "{table}" WHERE tenant_id = ?', (space,))), key=repr)
        return held

    def held(self, space: str) -> int:
        return sum(len(rows) for rows in self.memory(space).values())


@pytest.fixture
def chat(team, monkeypatch):  # noqa: F811 -- the fixture
    from kazma_core.config_store import get_config_store
    from kazma_core.spaces import chat_moves
    from kazma_ui import session_manager
    from kazma_ui.session_manager import SessionManager

    monkeypatch.setattr(threads, "_spaces", {})
    monkeypatch.setattr(threads, "_ended", OrderedDict())
    with threads._connection() as conn:  # the process's own record store
        conn.execute("DELETE FROM thread_scopes")
    monkeypatch.setattr(forget, "chat_keys", _REAL_CHAT_KEYS)
    monkeypatch.setattr(forget, "_schema_ready", set())
    store = SessionManager(db_path=str(team.tmp / "chat_sessions.db"))
    monkeypatch.setattr(session_manager, "_session_manager", store)
    get_config_store().delete(chat_moves.MOVES_KEY)
    yield Chat(team)
    get_config_store().delete(chat_moves.MOVES_KEY)
    store.close()


def _tell(chat: Chat, space: str, lang: str, *, first: int = 1) -> None:
    for n, (text, _word) in enumerate(SAYINGS[lang], start=first):
        chat.say(space, text, n=n, at=T0 + 60 * n)


# ── 1. the conversation moves; what was remembered stays ──────────────────


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_shared_chat_takes_no_memory_with_it(chat, lang) -> None:
    chat.begin(OWN)
    _tell(chat, OWN, lang)
    before = chat.memory(OWN)
    assert chat.remembers(OWN) == [text for text, _w in SAYINGS[lang]], "the turns were remembered"
    words = {word for _t, word in SAYINGS[lang]}
    assert any(w in fact for w in words for fact in chat.facts(OWN)), "and facts of theirs"

    chat.move(OWN, TEAM)

    assert chat.memory(OWN) == before, "her memory is what it was"
    assert chat.reconcile()["turns_written"] == 0
    assert chat.held(TEAM) == 0, "the shared space learned nothing from what was said before"
    assert chat.finds(TEAM, SAYINGS[lang][1][1]) == [], "nor does its past-chats search read it"
    assert [m["content"] for m in chat.held_row(TEAM).messages if m["role"] == "user"] == [
        text for text, _w in SAYINGS[lang]], "the conversation is there"
    assert chat.memory(OWN) == before, "nor did anything run on it since"
    queued = chat.team.ops.execute("SELECT COUNT(*) FROM memory_task_queue WHERE payload_json LIKE ?",
                                   (f"%{TEAM}%",)).fetchone()[0]
    assert queued == 0, "no pass was queued to extract them"


def _not_said_when(monkeypatch) -> None:
    """A copy whose thread does not say when it was copied."""
    real = threads.record_new_thread
    monkeypatch.setattr(threads, "record_new_thread", lambda thread, copied_at=None: real(thread))


def test_control_a_copy_that_does_not_say_when_it_was_made_is_learned(chat, monkeypatch) -> None:
    _not_said_when(monkeypatch)
    chat.begin(OWN)
    _tell(chat, OWN, "en")
    chat.move(OWN, TEAM)
    assert chat.reconcile()["turns_written"] == 2
    assert chat.finds(TEAM, "Initech") == [chat.session]


# ── 2. what is said after the move is remembered ─────────────────────────


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_what_is_said_in_the_new_space_is_remembered_there(chat, lang) -> None:
    (home, _w1), (work, word) = SAYINGS[lang]
    chat.begin(OWN)
    chat.say(OWN, home, n=1, at=T0)
    chat.move(OWN, TEAM)
    chat.say(TEAM, work, n=2, at=T0 + 60)
    assert chat.remembers(TEAM) == [work] and any(word in fact for fact in chat.facts(TEAM))
    # A turn its live write missed: reconcile writes that one, and no other --
    # the question she asked before the move, asked again, among them.
    chat.row(TEAM, home, at=T0 + 120)
    assert chat.reconcile()["turns_written"] == 1
    assert chat.remembers(TEAM) == [work, home]
    assert chat.remembers(OWN) == [home]
    assert chat.finds(TEAM, word) == [chat.session], "and its past chats are searched from there"


def test_what_was_said_before_is_never_counted_as_remembered_here(chat) -> None:
    """Slash commands, a message that is only an attachment, a question nobody
    answered, one with no time and the same question asked again here: what
    came with the chat is skipped, whatever it is, and never stands for a turn
    said here."""
    chat.begin(OWN)
    for n, text in enumerate(["/long on", "I live in Lisbon.", "a photo", "ok so", "I work at Initech."]):
        chat.row(OWN, text, at=T0 + 60 * n)
    with scope.bind_scope(OWN, SARA):
        held = chat.store().get(chat.session)
        held.messages[4]["content"] = [{"type": "image_url", "image_url": {"url": "x"}}]  # only an attachment
        del held.messages[7]  # "ok so" was never answered
        held.messages[2].pop("ts")
        chat.store().put(held)
    chat.move(OWN, TEAM)
    chat.say(TEAM, "I work at Initech.", n=6, at=T0 + 900)
    chat.row(TEAM, "I live in Lisbon.", at=T0 + 960)
    assert chat.reconcile()["turns_written"] == 1
    assert chat.remembers(TEAM) == ["I work at Initech.", "I live in Lisbon."]


def test_a_turn_with_no_time_is_not_known_to_have_been_said_here(chat) -> None:
    """Whatever the chat's own times say: its row was last written long ago."""
    chat.begin(OWN)
    chat.row(OWN, "I live in Lisbon.", at=T0)
    with scope.bind_scope(OWN, SARA):
        held = chat.store().get(chat.session)
        for message in held.messages:
            message.pop("ts")
        chat.store().put(held)
    chat.move(OWN, TEAM)
    with sqlite3.connect(chat.store().db_path) as conn:
        conn.execute("UPDATE sessions SET updated_at = ?, created_at = ?",
                     (datetime.fromtimestamp(T0 + 3600, UTC).isoformat(),) * 2)
    assert chat.reconcile()["turns_written"] == 0 and chat.remembers(TEAM) == []


def test_the_cut_holds_when_the_chat_is_cut_or_trimmed(chat) -> None:
    """An undo, a ``/reset`` and the store's own trim take from the front or
    the end; a new process and a spooled row say no more: what came with the
    chat is never read as said here."""
    from kazma_ui.session_manager import MAX_MESSAGES_PER_SESSION

    chat.begin(OWN)
    _tell(chat, OWN, "en")
    chat.move(OWN, TEAM)
    with scope.bind_scope(TEAM, SARA), chat.store().transact(chat.session) as held:
        held.messages = held.messages[1:]
    assert chat.reconcile()["turns_written"] == 0
    with scope.bind_scope(TEAM, SARA), chat.store().transact(chat.session) as held:
        for n in range(MAX_MESSAGES_PER_SESSION - len(held.messages) + 1):
            held.messages.append({"role": "user", "content": f"/note {n}",
                                  "ts": datetime.fromtimestamp(T0 + 900 + n, UTC).isoformat()})
        held.trim_messages()
    assert chat.reconcile()["turns_written"] == 0 and chat.remembers(TEAM) == []


def test_a_record_store_from_before_chats_moved_is_read(chat, monkeypatch, tmp_path) -> None:
    """A file an older build wrote has no such column: nothing in it was
    copied, and the column is added where the store opens it."""
    path = tmp_path / "older.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE thread_scopes (thread_id TEXT PRIMARY KEY, space_id TEXT NOT NULL, "
                 "opener TEXT NOT NULL, created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO thread_scopes VALUES ('t-old', 'sara-own', 'sara', '2026')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(threads, "thread_scopes_db", lambda: str(path))
    assert threads.copied_threads() == {} and threads.record_of("t-old").copied_at is None
    threads.record_thread("t-copy", TEAM, SARA, copied_at=T0)
    assert threads.copied_threads() == {"t-copy": T0} and threads.record_of("t-copy").copied_at == T0
    monkeypatch.setattr(threads, "thread_scopes_db", lambda: str(tmp_path / "none.db"))
    assert threads.copied_threads() == {} and not (tmp_path / "none.db").exists(), "asking makes no file"


def test_a_record_store_that_cannot_be_read_writes_nothing(chat, monkeypatch) -> None:
    """Which turns were said here is not known: reconcile waits."""
    chat.begin(OWN)
    _tell(chat, OWN, "en")
    chat.move(OWN, TEAM)
    monkeypatch.setattr(threads, "copied_threads", lambda: (_ for _ in ()).throw(
        sqlite3.OperationalError("database is locked")))
    assert chat.reconcile() == {"error": "chat store unreadable"}
    assert chat.remembers(TEAM) == [] and chat.finds(TEAM, "Initech") == []


def test_a_fork_of_a_moved_chat_learns_none_of_it(chat) -> None:
    """The fork's own time: neither what came with the chat nor what was
    forked is read as said in the fork. An ordinary chat's fork is as it was."""
    from kazma_ui import thread_rewind
    from kazma_ui.session_manager import ChatSession

    chat.begin(OWN)
    _tell(chat, OWN, "en")
    chat.move(OWN, TEAM)
    stamp = datetime.fromtimestamp(T0, UTC).isoformat()
    forked = [{"role": "user", "content": "We pack on Thursday."}, {"role": "assistant", "content": "Ok."}]
    ordinary = [{"role": "user", "content": "We leave on Friday.", "ts": stamp},
                {"role": "assistant", "content": "Ok.", "ts": stamp}]
    with scope.bind_scope(TEAM, SARA):
        messages = [{"role": m["role"], "content": m["content"]} for m in chat.store().get(chat.session).messages]
        threads.record_thread("plain", TEAM, SARA)
        chat.store().put(ChatSession(session_id="plain", thread_id="plain", messages=ordinary))
        thread_rewind._in_this_space(chat.thread, "fork-1")
        thread_rewind._in_this_space("plain", "fork-2")
        thread_rewind._save_fork_chat("fork-1", "a fork", messages)
        thread_rewind._save_fork_chat("fork-2", "another", forked)
    copied = threads.copied_threads()
    assert copied["fork-1"] > copied[chat.thread] and "fork-2" not in copied, "its own time"
    with scope.bind_scope(TEAM, SARA), chat.store().transact("fork-2") as held:
        for message in held.messages:
            message["ts"] = stamp
    chat.reconcile()
    assert set(chat.remembers(TEAM)) == {"We leave on Friday.", "We pack on Thursday."}
    assert chat.finds(TEAM, "Initech") == []


def test_the_scan_of_old_sessions_takes_the_same_cut(chat) -> None:
    """``scripts/scan_old_sessions.py`` reads the chat store and the saved
    steps: what was said in another space is nobody's here."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "_scan_old_sessions_move", Path(__file__).resolve().parents[1] / "scripts" / "scan_old_sessions.py")
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)
    chat.begin(OWN)
    _tell(chat, OWN, "en")
    chat.move(OWN, TEAM)
    chat.row(TEAM, "I play the oud.", at=T0 + 900)
    copied = threads.copied_threads()
    (session,) = scan.load_sessions(chat.store().db_path)
    assert session["thread_id"] == chat.thread
    assert [(n, user) for n, user, _a in scan.turns_said_here(session, copied)] == [(3, "I play the oud.")]
    assert [n for n, _u, _a in scan.turns_said_here(session, {})] == [1, 2, 3], "an ordinary chat is read whole"
    # A saved step's messages name no time: none is known to have been said here.
    step = {"session_id": chat.thread, "thread_id": chat.thread, "messages": [
        {"role": "user", "content": "I live in Lisbon."}, {"role": "assistant", "content": "Noted."}]}
    assert scan.turns_said_here(step, copied) == []
    found = asyncio.run(scan.scan_session(chat.db, chat.team.ops, session, tenant_id=TEAM))
    assert found["turns"] == 1 and not any(w in f for w in ("Lisbon", "Initech") for f in chat.facts(TEAM))


def test_a_turn_memory_missed_is_written_where_it_was_said(chat) -> None:
    """No later pass would find the chat: it is reconciled before it goes."""
    chat.begin(OWN)
    chat.say(OWN, "I live in Lisbon.", n=1, at=T0)
    chat.row(OWN, "I work at Initech.", at=T0 + 60)  # its live write was lost
    chat.move(OWN, TEAM)
    assert chat.remembers(OWN) == ["I live in Lisbon.", "I work at Initech."] and chat.held(TEAM) == 0
    chat.reconcile()
    assert any("Initech" in f for f in chat.facts(OWN)) and chat.held(TEAM) == 0


@pytest.mark.parametrize(("age", "kept"), [(120.0, ["I work at Initech."]), (5.0, [])],
                         ids=["missed", "still the live pipeline's"])
def test_a_turn_missed_minutes_ago_is_not_left_for_a_pass_that_never_comes(chat, age, kept) -> None:
    """The regular pass leaves ten minutes to the live pipeline; a chat that
    leaves gives it one, since no pass would find the chat again."""
    import time

    chat.begin(OWN)
    chat.row(OWN, "I work at Initech.", at=time.time() - age)
    chat.move(OWN, TEAM)
    assert chat.remembers(OWN) == kept and chat.held(TEAM) == 0


def test_control_a_share_that_does_not_reconcile_loses_it(chat, monkeypatch) -> None:
    from kazma_core.memory import turn_reconcile

    monkeypatch.setattr(turn_reconcile, "reconcile_chat", lambda _row: 0)
    chat.begin(OWN)
    chat.row(OWN, "I work at Initech.", at=T0 + 60)
    chat.move(OWN, TEAM)
    chat.reconcile()
    assert chat.remembers(OWN) == []


# ── 3. a chat kept out of memory stays out ────────────────────────────────


def test_a_chat_kept_out_of_memory_is_kept_out_where_it_moves(chat) -> None:
    chat.begin(OWN)
    chat.row(OWN, "I live in Lisbon.", at=T0)
    assert forget.set_chat_remembered(chat.session, False, tenant_id=OWN)["ok"]
    chat.move(OWN, TEAM)
    assert not forget.chat_remembered(chat.session, tenant_id=TEAM)
    assert not forget.chat_remembered(chat.thread, tenant_id=TEAM)
    chat.say(TEAM, "I work at Initech.", n=2, at=T0 + 60)
    assert chat.remembers(TEAM) == [] and chat.facts(TEAM) == set()
    # Let back in there, what was said before is still not learned there.
    assert forget.set_chat_remembered(chat.session, True, tenant_id=TEAM)["ok"]
    assert chat.reconcile()["turns_written"] == 1
    assert chat.remembers(TEAM) == ["I work at Initech."]


def test_it_is_kept_out_before_there_is_a_row_to_read(chat, monkeypatch) -> None:
    """A pass running beside the move never meets the copy without it."""
    from kazma_ui import chat_move

    chat.begin(OWN)
    chat.row(OWN, "I live in Lisbon.", at=T0)
    forget.set_chat_remembered(chat.session, False, tenant_id=OWN)
    real, kept = chat_move._hand_over, []

    def hand_over(attempt: str, entry: dict[str, Any], *args: Any) -> bool:
        kept.append(forget.forgotten_in_chat(TEAM, [entry["new_session"], entry["new_thread"]])[0])
        return real(attempt, entry, *args)

    monkeypatch.setattr(chat_move, "_hand_over", hand_over)
    chat.move(OWN, TEAM)
    assert kept == [True]


@pytest.mark.parametrize("key", ["session", "thread"])
def test_a_chat_kept_out_under_one_key_is_kept_out_under_both(chat, key) -> None:
    """A write still queued names one of the keys; the deleted chat's store
    copy named both."""
    from kazma_core.memory.consolidator import run_deferred_turn_memory

    chat.begin(OWN)
    chat.row(OWN, "I live in Lisbon.", at=T0 + 60)
    kept = {"session": chat.session, "thread": chat.thread}[key]
    chat.db.execute("INSERT INTO memory_forgotten (tenant_id, session_key, turn_number, question_sha, episode_id, "
                    "forgotten_at) VALUES (?, ?, 0, '', NULL, ?)", (OWN, kept, T0))
    chat.db.commit()
    session, thread = chat.move(OWN, TEAM)
    for old in (session, thread):
        assert forget.forgotten_in_chat(OWN, [old])[0], old
    run_deferred_turn_memory(_queued(thread, OWN, "I live in Lisbon.", 1))
    assert chat.remembers(OWN) == []


def test_a_chat_not_kept_out_is_not_kept_out_where_it_moves(chat) -> None:
    chat.begin(OWN)
    chat.row(OWN, "I live in Lisbon.", at=T0)
    chat.move(OWN, TEAM)
    assert forget.chat_remembered(chat.session, tenant_id=TEAM)
    assert chat.db.execute("SELECT COUNT(*) FROM memory_forgotten").fetchone()[0] == 0, "no ledger row at all"


# ── 4. taken out, the shared space forgets it ─────────────────────────────


def _summary(chat: Chat, space: str, text: str) -> str:
    """A weekly summary written from the chat's first remembered turn."""
    episode = chat.db.execute("SELECT id FROM episodes WHERE tenant_id = ? AND session_id = ? LIMIT 1",
                              (space, chat.thread)).fetchone()[0]
    columns = {r[1] for r in chat.db.execute("PRAGMA table_info(memory_summaries)")}
    values = {"id": "sum-1", "tenant_id": space, "period_key": "2026-W20", "period_start": T0, "period_end": T0 + 9,
              "title": "Homes", "summary_text": text, "status": "active", "created_at": T0, "updated_at": T0,
              "level": "week"}
    names = [c for c in values if c in columns]
    chat.db.execute(f"INSERT INTO memory_summaries ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                    [values[c] for c in names])
    chat.db.execute("INSERT INTO memory_summary_sources (summary_id, episode_id) VALUES ('sum-1', ?)", (episode,))
    chat.db.commit()
    return text


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_chat_taken_out_is_forgotten_by_the_shared_space(chat, lang) -> None:
    from kazma_core.skills import self_improvement as si

    chat.begin(TEAM)
    _say(OMAR, "omars-chat", "I live in Doha.", n=1, at=T0)
    _tell(chat, TEAM, lang)
    words = {word for _t, word in SAYINGS[lang]}
    word = SAYINGS[lang][0][1]
    _summary(chat, TEAM, f"Sara's home is {word}.")
    chat.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, tier, created_at, metadata_json) "
        "VALUES ('note-1', ?, 'memory_store', 0, ?, 'recall', ?, json_object('chat', ?, 'chat_turn', 1))",
        (TEAM, f"Sara asked to keep {word}", T0, chat.thread))
    chat.db.commit()
    assert si.apply_agent_mutation("supervisor", f"Prefer {word} trains.", space=TEAM,
                                   source={"chat": chat.thread, "turn": 1})
    own_before = chat.memory(OWN)

    chat.move(TEAM, OWN)

    # The names left on the memory map are the memory session's own fix.
    held = {t: rows for t, rows in chat.memory(TEAM).items() if t != "entities"}
    said = repr(held) + repr(si._load_agent_evolution(TEAM))
    assert all(w not in said for w in words), "no row of the shared space holds what she said"
    assert "Doha" in chat.facts(TEAM), "Omar's own memory is there"
    assert chat.remembers(TEAM) == ["I live in Doha."] and all(chat.finds(TEAM, w) == [] for w in words)
    assert chat.memory(OWN) == own_before == {t: [] for t in own_before}
    assert chat.reconcile()["turns_written"] == 0, "nor does her own space learn it"
    assert chat.remembers(OWN) == [] and chat.finds(OWN, SAYINGS[lang][1][1]) == []
    assert chat.held_row(OWN).messages[0]["content"] == SAYINGS[lang][0][0], "the conversation is hers"


def test_control_a_take_out_that_forgets_nothing_leaves_it_there(chat, monkeypatch) -> None:
    from kazma_ui import chat_move

    monkeypatch.setattr(chat_move, "_before_the_delete", lambda _entry: None)
    chat.begin(TEAM)
    _tell(chat, TEAM, "en")
    chat.move(TEAM, OWN)
    assert "Lisbon" in chat.facts(TEAM)


def test_a_shared_chat_is_not_forgotten_where_it_was_said(chat, monkeypatch) -> None:
    """Only a take-out forgets: sharing never runs a forget."""
    chat.begin(OWN)
    _tell(chat, OWN, "en")
    monkeypatch.setattr(forget, "forget_chat", lambda *_a, **_k: pytest.fail("a forget ran"))
    chat.move(OWN, TEAM)


# ── 5. personal memory is never touched, in either direction ──────────────


def test_her_own_memory_is_untouched_there_and_back(chat) -> None:
    chat.begin(OWN)
    chat.say(OWN, "I live in Lisbon.", n=1, at=T0)
    chat.row(OWN, "/cost", at=T0 + 10)  # a message the chat store counts, and no turn
    before = chat.memory(OWN)
    chat.move(OWN, TEAM)
    chat.say(TEAM, "I work at Initech.", n=2, at=T0 + 60)
    chat.move(TEAM, OWN)
    assert chat.memory(OWN) == before
    forget.converge(chat.db)
    assert chat.reconcile()["turns_written"] == 0
    assert chat.memory(OWN) == before, "nor by the convergence and reconcile passes"
    assert chat.facts(TEAM) == set() and chat.remembers(TEAM) == []
    # The next turn in her own space, its facts too.
    chat.say(OWN, "My name is Sara.", n=3, at=T0 + 600)
    forget.converge(chat.db)
    assert "Sara" in chat.facts(OWN) and "Lisbon" in chat.facts(OWN)


def test_an_undo_in_the_shared_space_leaves_her_own_memory(chat) -> None:
    """A rewind takes its turns back from the chat it is made in: what was
    remembered of them where they were said is another chat's."""
    chat.begin(OWN)
    chat.say(OWN, "I live in Lisbon.", n=1, at=T0)
    chat.move(OWN, TEAM)
    chat.say(TEAM, "I work at Initech.", n=2, at=T0 + 60)
    before = chat.memory(OWN)
    old = [{"role": "user", "content": "I live in Lisbon."}, {"role": "assistant", "content": "Noted."},
           {"role": "user", "content": "I work at Initech."}, {"role": "assistant", "content": "Noted."}]
    asyncio.run(forget.rewind_memory(chat.thread, old, [], tenant_id=TEAM))
    assert chat.remembers(TEAM) == [] and chat.facts(TEAM) == set()
    assert chat.memory(OWN) == before


# ── 6. what is still waiting when the chat moves ──────────────────────────


def _queued(thread: str, space: str, text: str, n: int) -> dict[str, Any]:
    """What the durable queue holds of a turn whose write is still waiting."""
    return {"user_text": text, "assistant_text": "Noted.", "session_id": thread, "turn": n,
            "tenant_id": space, "at": T0 + 60 * n, "said_by": SARA}


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_turn_queued_when_its_chat_is_shared_is_still_hers(chat, lang) -> None:
    from kazma_core.memory.consolidator import run_deferred_turn_memory

    (home, word), _work = SAYINGS[lang]
    chat.begin(OWN)
    chat.row(OWN, home, at=T0 + 60)
    _session, thread = chat.move(OWN, TEAM)
    assert not forget.turn_kept_out(thread, 1, home, tenant_id=OWN), "its lesson is hers too"
    assert run_deferred_turn_memory(_queued(thread, OWN, home, 1))
    assert chat.remembers(OWN) == [home] and any(word in fact for fact in chat.facts(OWN))
    assert chat.held(TEAM) == 0
    # And when it came back meanwhile.
    chat.move(TEAM, OWN)
    assert run_deferred_turn_memory(_queued(thread, OWN, "I like the sea.", 2))
    assert chat.remembers(OWN) == [home, "I like the sea."]


def test_a_turn_queued_when_its_chat_is_taken_out_is_not_the_teams(chat) -> None:
    from kazma_core.memory.consolidator import run_deferred_turn_memory

    chat.begin(TEAM)
    chat.row(TEAM, "I live in Lisbon.", at=T0 + 60)
    _session, thread = chat.move(TEAM, OWN)
    assert forget.turn_kept_out(thread, 1, "I live in Lisbon.", tenant_id=TEAM), "nor does it teach a lesson"
    run_deferred_turn_memory(_queued(thread, TEAM, "I live in Lisbon.", 1))
    assert chat.held(TEAM) == 0 and chat.held(OWN) == 0


def test_a_forget_still_pending_never_reaches_the_chat_shared_again(chat, monkeypatch) -> None:
    """Taken out while the shared space's memory could not be written, then
    shared again: the pending forget is the old chat's."""
    chat.begin(TEAM)
    _tell(chat, TEAM, "en")
    with monkeypatch.context() as patch:
        patch.setattr(forget, "_forget_chat_facts", lambda *_a, **_k: (_ for _ in ()).throw(
            sqlite3.OperationalError("database is locked")))
        chat.move(TEAM, OWN)
    chat.move(OWN, TEAM)
    chat.say(TEAM, "My name is Sara.", n=3, at=T0 + 600)
    forget.converge(chat.db)
    chat.reconcile()
    assert forget_journal.pending(chat.db) == []
    assert chat.facts(TEAM) == {"Sara"} and chat.remembers(TEAM) == ["My name is Sara."]


# ── 7. a forget that fails half way; a restore ────────────────────────────


def test_a_forget_that_fails_is_finished_by_the_convergence(chat, monkeypatch) -> None:
    from kazma_core.spaces import chat_moves
    from kazma_ui.chat_move import settle_chat_moves

    chat.begin(TEAM)
    _tell(chat, TEAM, "en")
    with monkeypatch.context() as patch:
        patch.setattr(forget, "_forget_chat_facts", lambda *_a, **_k: (_ for _ in ()).throw(
            sqlite3.OperationalError("database is locked")))
        chat.move(TEAM, OWN)
    (entry,) = chat_moves.moves_under_way().values()
    assert entry["done"] == ["handed_over"], "the chat waits, unseen, until what memory must hold is done"
    with scope.bind_scope(TEAM, SARA):
        assert chat.store().get(entry["session"]) is None and chat.store().held(entry["session"]) is not None
    forget.converge(chat.db)
    assert chat.remembers(TEAM) == [] and "Lisbon" not in chat.facts(TEAM)
    asyncio.run(settle_chat_moves(Checkpoints()))
    assert chat_moves.moves_under_way() == {}


def test_a_forget_that_answers_not_done_is_asked_again(chat, monkeypatch) -> None:
    from kazma_core.spaces import chat_moves
    from kazma_ui.chat_move import settle_chat_moves

    chat.begin(TEAM)
    _tell(chat, TEAM, "en")
    with monkeypatch.context() as patch:
        patch.setattr(forget, "forget_chat", lambda *_a, **_k: {"ok": False, "error": "busy"})
        chat.move(TEAM, OWN)
    (entry,) = chat_moves.moves_under_way().values()
    assert entry["done"] == ["handed_over"] and "Lisbon" in chat.facts(TEAM)
    asyncio.run(settle_chat_moves(Checkpoints()))
    assert chat_moves.moves_under_way() == {} and chat.facts(TEAM) == set()


def test_a_memory_database_restored_from_before_a_take_out_forgets_it_again(chat) -> None:
    chat.begin(TEAM)
    _tell(chat, TEAM, "en")
    chat.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    path = chat.team.tmp / "memory_state.db"
    shutil.copyfile(path, chat.team.tmp / "backup.db")
    chat.move(TEAM, OWN)
    restored = sqlite3.connect(str(chat.team.tmp / "backup.db"))
    restored.row_factory = sqlite3.Row
    try:
        assert restored.execute("SELECT COUNT(*) FROM episodes WHERE tier != 'forgotten'").fetchone()[0] == 2
        assert forget_journal.apply_pending(restored)["failed"] == []
        assert restored.execute("SELECT COUNT(*) FROM episodes WHERE tier != 'forgotten'").fetchone()[0] == 0
        assert restored.execute("SELECT COUNT(*) FROM beliefs WHERE object LIKE '%Lisbon%'").fetchone()[0] == 0
    finally:
        restored.close()
    # The copy in her own space is no reconcile's to learn, whatever the memory holds.
    assert chat.reconcile()["turns_written"] == 0 and chat.remembers(OWN) == []
