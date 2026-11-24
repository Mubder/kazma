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
   it was, and the shared space learns nothing from what was said before
   (control: a copy that does not say how many messages came with it).
2. What is said after the move is remembered, and a turn its live write
   missed is reconciled; a fork of the moved chat learns nothing either.
3. A chat kept out of memory stays out in the space it moves to.
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
from datetime import UTC, datetime
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

    def say(self, space: str, text: str, *, n: int, at: float, author: str = SARA) -> None:
        self.row(space, text, at=at, author=author)
        _say(author, self.thread, text, n=n, at=at, space=space)

    def move(self, frm: str, to: str) -> tuple[str, str]:
        """Move it; the ids it had."""
        from kazma_ui.chat_move import _move_chat

        had = self.session, self.thread
        answer = asyncio.run(_move_chat(session_id=self.session, from_space=frm, to_space=to, by=SARA,
                                        graph=NoCardGraph(), checkpoints=Checkpoints()))
        with scope.bind_scope(to, SARA):
            self.session = answer["session_id"]
            self.thread = self.store().get(self.session).thread_id
        assert threads.thread_home(self.thread) == to and self.thread != had[1]
        return had

    def reconcile(self) -> dict[str, Any]:
        from kazma_core.memory.turn_reconcile import run_turn_reconcile_pass

        return run_turn_reconcile_pass()

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
    assert chat.finds(TEAM, SAYINGS[lang][1][1]) == [chat.session], "the conversation is there"
    assert chat.memory(OWN) == before, "nor did anything run on it since"
    queued = chat.team.ops.execute("SELECT COUNT(*) FROM memory_task_queue WHERE payload_json LIKE ?",
                                   (f"%{TEAM}%",)).fetchone()[0]
    assert queued == 0, "no pass was queued to extract them"


def test_control_a_copy_that_does_not_say_what_came_with_it_is_learned(chat, monkeypatch) -> None:
    from kazma_ui import session_manager

    real = session_manager.SessionManager.hand_over

    def silent(self: Any, session_id: str, *, to: Any, **kwargs: Any) -> bool:
        to.copied = 0
        return real(self, session_id, to=to, **kwargs)

    monkeypatch.setattr(session_manager.SessionManager, "hand_over", silent)
    chat.begin(OWN)
    _tell(chat, OWN, "en")
    chat.move(OWN, TEAM)
    assert chat.reconcile()["turns_written"] == 2


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


def test_what_came_with_it_is_counted_as_reconcile_counts(chat) -> None:
    """Slash commands, a message that is only an attachment and a question
    nobody answered are turns to one count and none to another: reconcile
    starts after the messages that came, whatever they were."""
    chat.begin(OWN)
    for n, text in enumerate(["/long on", "I live in Lisbon.", "a photo", "ok so", "I work at Initech."]):
        chat.row(OWN, text, at=T0 + 60 * n)
    with scope.bind_scope(OWN, SARA):
        held = chat.store().get(chat.session)
        held.messages[4]["content"] = [{"type": "image_url", "image_url": {"url": "x"}}]  # only an attachment
        del held.messages[7]  # "ok so" was never answered
        chat.store().put(held)
    chat.move(OWN, TEAM)
    chat.row(TEAM, "We leave on Friday.", at=T0 + 900)
    assert chat.reconcile()["turns_written"] == 1
    assert chat.remembers(TEAM) == ["We leave on Friday."]


def test_the_count_holds_when_the_chat_is_cut_or_trimmed(chat) -> None:
    """It never reads a message that came with the chat as one said here: an
    undo, a ``/reset``, a ``/compact`` and the store's own trim only take
    from the front or the end."""
    from kazma_ui.session_manager import MAX_MESSAGES_PER_SESSION

    chat.begin(OWN)
    _tell(chat, OWN, "en")
    chat.move(OWN, TEAM)
    with scope.bind_scope(TEAM, SARA):
        store = chat.store()

        def count(cut: Any) -> tuple[int, int]:
            with store.transact(chat.session) as held:
                cut(held)
            held = store.get(chat.session)
            return held.copied, len(held.messages)

        assert count(lambda s: s.messages.extend({"role": "assistant", "content": f"m{n}"} for n in range(2))) == (4, 6)
        assert count(lambda s: setattr(s, "messages", s.messages[:3])) == (3, 3), "undone past the move"
        full = [{"role": "user", "content": f"m{n}"} for n in range(MAX_MESSAGES_PER_SESSION)]
        copied, kept = count(lambda s: [s.add_message(m["role"], m["content"]) for m in full])
        assert copied == 0 and kept == MAX_MESSAGES_PER_SESSION, "trimmed from the front"
        assert count(lambda s: setattr(s, "messages", [])) == (0, 0)


def test_the_count_is_the_rows_own(chat) -> None:
    """A new process reads it wherever the row is read."""
    from kazma_ui.session_manager import SessionManager

    chat.begin(OWN)
    _tell(chat, OWN, "en")
    chat.move(OWN, TEAM)
    path = chat.store().db_path
    again = SessionManager(db_path=path)
    with scope.bind_scope(TEAM, SARA):
        assert again.get(chat.session).copied == 4
        assert [s.copied for s in again.list_all()] == [4]
    again.close()
    other = SessionManager(db_path=path)
    with scope.bind_scope(TEAM, SARA):
        assert other.get_by_thread_id(chat.thread).copied == 4
    other.close()


def test_a_chat_store_from_before_chats_moved_is_read(chat) -> None:
    """A file an older build wrote has no such column: nothing in it came
    with a move, and the column is added where the store opens it."""
    from kazma_core.memory.chat_history import sessions_changed_since
    from kazma_ui.session_manager import SessionManager

    path = chat.team.tmp / "older.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sessions (tenant_id TEXT, session_id TEXT, messages TEXT, created_at TEXT, "
                 "total_cost REAL, total_tokens INTEGER, thread_id TEXT, updated_at TEXT, "
                 "PRIMARY KEY (tenant_id, session_id))")
    conn.execute("INSERT INTO sessions VALUES (?, 's', '[{\"role\": \"user\", \"content\": \"hi\"}]', "
                 "'', 0, 0, 't', '2026')", (OWN,))
    conn.commit()
    conn.close()

    def counts() -> list[int]:
        return [r["copied"] for r in sessions_changed_since(sqlite_path=path)]

    assert counts() == [0]
    store = SessionManager(db_path=str(path))
    with scope.bind_scope(OWN, SARA):
        held = store.get("s")
        assert held.copied == 0
        held.copied = 1
        store.put(held)
    store.close()
    assert counts() == [1]


def test_a_fork_of_a_moved_chat_learns_none_of_it(chat) -> None:
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
        for key in ("fork-1", "plain", "fork-2"):
            threads.record_thread(key, TEAM, SARA)
        thread_rewind._save_fork_chat("fork-1", "a fork", messages, chat.thread)
        chat.store().put(ChatSession(session_id="plain", thread_id="plain", messages=ordinary))
        thread_rewind._save_fork_chat("fork-2", "another", forked, "plain")
        for key in ("fork-1", "fork-2"):
            with chat.store().transact(key) as held:
                for message in held.messages:
                    message["ts"] = stamp
    chat.reconcile()
    assert set(chat.remembers(TEAM)) == {"We leave on Friday.", "We pack on Thursday."}, (
        "an ordinary chat's fork is as it was")


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
    assert chat.remembers(OWN) == [] and chat.finds(OWN, SAYINGS[lang][1][1]) == [chat.session]


def test_control_a_take_out_that_forgets_nothing_leaves_it_there(chat, monkeypatch) -> None:
    from kazma_ui import chat_move

    monkeypatch.setattr(chat_move, "_forget_where_it_was", lambda _entry: None)
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
    assert "memory" not in entry["done"] and "remains" in entry["done"], "the chat itself is moved"
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
    assert "memory" not in entry["done"] and "Lisbon" in chat.facts(TEAM)
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
