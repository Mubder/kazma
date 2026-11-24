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
   it was, and the shared space learns nothing from the old messages
   (control: without the keep-out, reconcile writes them there).
2. What is said after the move is remembered, in both.
3. A chat kept out of memory stays out in the space it moves to.
4. Taken out of the shared space, that space forgets it -- memories, the
   agent's notes, weekly summaries, lessons -- and nothing else.
5. Personal memory is never touched by a move in either direction, the next
   turns' facts included (control: plain ledger rows take them).
6. After the switch the space it left is written nothing; a thread record
   that cannot be read fails the write, so it is tried again.
7. A memory database restored from before the move keeps the turns out.
8. A forget that fails half way is finished by the forget convergence.
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any

import pytest
from kazma_core.memory import forget, forget_journal
from kazma_core.spaces import scope, threads

from tests._chat_move_space_cases import NoCardGraph
from tests.test_memory_speakers import OMAR, SARA, TEAM, _say, team  # noqa: F401

OWN = "sara-own"
SESSION, THREAD = "s-moved", "t-moved"
T0 = 1_780_000_000.0

#: What Sara says in each language: (a home, an employer) and a word of each.
SAYINGS = {
    "en": (("I live in Lisbon.", "Lisbon"), ("I work at Initech.", "Initech")),
    "ar": (("أنا ساكنة في لشبونة", "لشبونة"), ("أشتغل في شركة النخيل", "النخيل")),
}
_REAL_CHAT_KEYS = forget.chat_keys


class Chat:
    """Sara's chat: its row in the real chat store, its turns remembered by
    the real post-turn pipeline where it is."""

    def __init__(self, team: Any) -> None:  # noqa: F811 -- the fixture
        self.team = team
        self.db: sqlite3.Connection = team.db

    def row(self, space: str, text: str, *, at: float, author: str = SARA, answer: str = "Noted.") -> None:
        from kazma_ui.session_manager import get_session_manager

        with scope.bind_scope(space, author):
            store = get_session_manager()
            session = store.get_or_create(SESSION, thread_id=THREAD)
            stamp = datetime.fromtimestamp(at, UTC).isoformat()
            session.add_message("user", text, author=author)
            session.messages[-1]["ts"] = stamp
            session.add_message("assistant", answer)
            session.messages[-1]["ts"] = stamp
            session.title = "a chat"
            store.put(session)

    def say(self, space: str, text: str, *, n: int, at: float, author: str = SARA) -> None:
        self.row(space, text, at=at, author=author)
        _say(author, THREAD, text, n=n, at=at, space=space)

    def move(self, frm: str, to: str) -> None:
        from kazma_ui.chat_move import _move_chat

        asyncio.run(_move_chat(session_id=SESSION, from_space=frm, to_space=to, by=SARA, graph=NoCardGraph()))
        assert threads.thread_home(THREAD) == to

    def reconcile(self) -> dict[str, Any]:
        from kazma_core.memory.turn_reconcile import run_turn_reconcile_pass

        return run_turn_reconcile_pass()

    def remembers(self, space: str) -> list[str]:
        return [str(r[0]) for r in self.db.execute(
            "SELECT user_text FROM episodes WHERE tenant_id = ? AND session_id IN (?, ?) AND tier != 'forgotten' "
            "AND user_text != '' ORDER BY created_at", (space, SESSION, THREAD))]

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
    threads.record_thread(THREAD, OWN, SARA)
    yield Chat(team)
    get_config_store().delete(chat_moves.MOVES_KEY)
    store.close()


def _tell(chat: Chat, space: str, lang: str, *, first: int = 1) -> None:
    for n, (text, _word) in enumerate(SAYINGS[lang], start=first):
        chat.say(space, text, n=n, at=T0 + 60 * n)


# ── 1. the conversation moves; what was remembered stays ──────────────────


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_shared_chat_takes_no_memory_with_it(chat, lang) -> None:
    _tell(chat, OWN, lang)
    before = chat.memory(OWN)
    assert chat.remembers(OWN) == [text for text, _w in SAYINGS[lang]], "the turns were remembered"
    words = {word for _t, word in SAYINGS[lang]}
    assert any(w in fact for w in words for fact in chat.facts(OWN)), "and facts of theirs"

    chat.move(OWN, TEAM)

    assert chat.memory(OWN) == before, "her memory is what it was"
    assert chat.reconcile()["turns_written"] == 0
    assert chat.held(TEAM) == 0, "the shared space learned nothing from the old messages"
    assert all(chat.finds(TEAM, word) == [] for word in words), "its past-chats search reads none of them"
    assert chat.memory(OWN) == before, "nor did anything run on it since"
    queued = chat.team.ops.execute("SELECT COUNT(*) FROM memory_task_queue WHERE payload_json LIKE ?",
                                   (f"%{TEAM}%",)).fetchone()[0]
    assert queued == 0, "no pass was queued to extract them"


def test_control_without_the_keep_out_the_target_learns_the_old_messages(chat, monkeypatch) -> None:
    from kazma_ui import chat_move

    monkeypatch.setattr(chat_move, "_keep_out_of_memory", lambda *_a: None)
    _tell(chat, OWN, "en")
    chat.move(OWN, TEAM)
    assert chat.reconcile()["turns_written"] == 2
    assert chat.finds(TEAM, "Lisbon") == [SESSION]


# ── 2. what is said after the move is remembered ─────────────────────────


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_what_is_said_in_the_new_space_is_remembered_there(chat, lang) -> None:
    (home, _w1), (work, word) = SAYINGS[lang]
    chat.say(OWN, home, n=1, at=T0)
    chat.move(OWN, TEAM)
    chat.say(TEAM, work, n=2, at=T0 + 60)
    assert chat.remembers(TEAM) == [work] and any(word in fact for fact in chat.facts(TEAM))
    assert chat.finds(TEAM, word) == [SESSION]
    # A turn its live write missed: reconcile writes that one, and no other.
    chat.row(TEAM, "We leave on Friday.", at=T0 + 120)
    assert chat.reconcile()["turns_written"] == 1
    assert chat.remembers(TEAM) == [work, "We leave on Friday."]
    assert chat.remembers(OWN) == [home]


# ── 3. a chat kept out of memory stays out ────────────────────────────────


def test_the_turns_are_kept_out_before_the_row_is_there_to_read(chat, monkeypatch) -> None:
    """A reconcile pass running beside the move never meets the row without
    its ledger rows."""
    from kazma_ui import chat_move

    _tell(chat, OWN, "en")
    real, kept = chat_move._copy_row, []

    def copy_row(*args: Any, **kwargs: Any) -> None:
        kept.append(forget.forgotten_in_chat(TEAM, [SESSION, THREAD])[1])
        real(*args, **kwargs)

    monkeypatch.setattr(chat_move, "_copy_row", copy_row)
    chat.move(OWN, TEAM)
    assert len(kept) == 1 and len(kept[0]) == 2, kept


def test_a_chat_kept_out_of_memory_is_kept_out_where_it_moves(chat) -> None:
    chat.row(OWN, "I live in Lisbon.", at=T0)
    assert forget.set_chat_remembered(SESSION, False, tenant_id=OWN)["ok"]
    chat.move(OWN, TEAM)
    assert not forget.chat_remembered(SESSION, tenant_id=TEAM)
    chat.say(TEAM, "I work at Initech.", n=2, at=T0 + 60)
    assert chat.remembers(TEAM) == [] and chat.facts(TEAM) == set()
    # Let back in there, the old messages are still kept out of it.
    assert forget.set_chat_remembered(SESSION, True, tenant_id=TEAM)["ok"]
    assert chat.reconcile()["turns_written"] == 1
    assert chat.remembers(TEAM) == ["I work at Initech."]


def test_a_chat_not_kept_out_is_not_kept_out_where_it_moves(chat) -> None:
    chat.row(OWN, "I live in Lisbon.", at=T0)
    chat.move(OWN, TEAM)
    assert forget.chat_remembered(SESSION, tenant_id=TEAM)


# ── 4. taken out, the shared space forgets it ─────────────────────────────


def _summary(chat: Chat, space: str, text: str) -> str:
    """A weekly summary written from the chat's first remembered turn."""
    episode = chat.db.execute("SELECT id FROM episodes WHERE tenant_id = ? AND session_id = ? LIMIT 1",
                              (space, THREAD)).fetchone()[0]
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

    threads.move_thread(THREAD, TEAM)
    _say(OMAR, "omars-chat", "I live in Doha.", n=1, at=T0)
    _tell(chat, TEAM, lang)
    words = {word for _t, word in SAYINGS[lang]}
    word = SAYINGS[lang][0][1]
    _summary(chat, TEAM, f"Sara's home is {word}.")
    chat.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, tier, created_at, metadata_json) "
        "VALUES ('note-1', ?, 'memory_store', 0, ?, 'recall', ?, json_object('chat', ?, 'chat_turn', 1))",
        (TEAM, f"Sara asked to keep {word}", T0, THREAD))
    chat.db.commit()
    assert si.apply_agent_mutation("supervisor", f"Prefer {word} trains.", space=TEAM,
                                   source={"chat": THREAD, "turn": 1})
    own_before = chat.memory(OWN)

    chat.move(TEAM, OWN)

    said = repr(chat.memory(TEAM)) + repr(si._load_agent_evolution(TEAM))
    assert all(w not in said for w in words), "no row of the shared space holds what she said"
    assert "Doha" in chat.facts(TEAM), "Omar's own memory is there"
    assert chat.remembers(TEAM) == [] and all(chat.finds(TEAM, w) == [] for w in words)
    assert chat.memory(OWN) == own_before == {t: [] for t in own_before}
    assert chat.reconcile()["turns_written"] == 0, "nor does her own space learn it"
    assert all(chat.finds(OWN, w) == [] for w in words)
    assert chat.remembers(OWN) == []


def test_control_a_take_out_that_forgets_nothing_leaves_it_there(chat, monkeypatch) -> None:
    from kazma_ui import chat_move

    monkeypatch.setattr(chat_move, "_forget_where_it_was", lambda _entry: None)
    threads.move_thread(THREAD, TEAM)
    _tell(chat, TEAM, "en")
    chat.move(TEAM, OWN)
    assert "Lisbon" in chat.facts(TEAM)


def test_a_shared_chat_is_not_forgotten_where_it_was_said(chat, monkeypatch) -> None:
    """Only a take-out forgets: sharing never runs a forget."""
    _tell(chat, OWN, "en")
    monkeypatch.setattr(forget, "forget_chat", lambda *_a, **_k: pytest.fail("a forget ran"))
    chat.move(OWN, TEAM)


# ── 5. personal memory is never touched, in either direction ──────────────


def _there_and_back(chat: Chat) -> dict[str, list[tuple[Any, ...]]]:
    """Said in her own space, shared, more said there, taken back out: her
    memory as it was before the first move."""
    chat.say(OWN, "I live in Lisbon.", n=1, at=T0)
    chat.row(OWN, "/cost", at=T0 + 10)  # a message the chat store counts, and no turn
    before = chat.memory(OWN)
    chat.move(OWN, TEAM)
    chat.say(TEAM, "I work at Initech.", n=2, at=T0 + 60)
    chat.move(TEAM, OWN)
    return before


def test_her_own_memory_is_untouched_there_and_back(chat) -> None:
    before = _there_and_back(chat)
    assert chat.memory(OWN) == before
    forget.converge(chat.db)
    assert chat.reconcile()["turns_written"] == 0
    assert chat.memory(OWN) == before, "nor by the convergence and reconcile passes"
    assert chat.finds(OWN, "Lisbon") == [SESSION], "what she said there is still found"
    assert chat.finds(OWN, "Initech") == [], "what she said in the shared space is not"
    assert chat.facts(TEAM) == set()


def _next_turn(chat: Chat) -> None:
    """The next turn in her own space: turn 3 to the live writer, and the
    chat store's fourth message -- the number the move kept out is 3."""
    chat.say(OWN, "I am allergic to penicillin. My name is Sara.", n=3, at=time.time())
    forget.converge(chat.db)


def test_the_next_turns_facts_stay(chat) -> None:
    _there_and_back(chat)
    _next_turn(chat)
    assert "Sara" in chat.facts(OWN)


def test_control_plain_ledger_rows_take_the_next_turns_facts(chat, monkeypatch) -> None:
    """The keep-out written as any forgotten turn's: the convergence reads
    "turn 3" as her next turn's number and takes back its facts."""
    def plain(session: str, *, keys: Any, turns: Any, tenant_id: str) -> int:
        return sum(forget.keep_turn_out(session, n, q, tenant_id=tenant_id) for n, q in turns)

    monkeypatch.setattr(forget, "keep_moved_turns_out", plain)
    _there_and_back(chat)
    _next_turn(chat)
    assert "Sara" not in chat.facts(OWN)


def test_a_move_takes_back_no_turn_the_space_holds(chat) -> None:
    """The rows a move writes keep turns out; they never take one back, the
    very turn they name included."""
    _tell(chat, OWN, "en")
    forget._write_moved_turns(chat.db, OWN, [SESSION, THREAD],
                              [(n, forget.question_sha(text)) for n, (text, _w) in enumerate(SAYINGS["en"], 1)],
                              at=time.time())
    chat.db.commit()
    before = chat.memory(OWN)
    forget.converge(chat.db)
    assert chat.memory(OWN) == before and chat.remembers(OWN) == [t for t, _w in SAYINGS["en"]]


def test_nothing_of_a_move_waits_in_the_forget_journal(chat) -> None:
    _tell(chat, OWN, "en")
    chat.move(OWN, TEAM)
    chat.move(TEAM, OWN)
    assert forget_journal.pending(chat.db) == []


# ── 6. the space it left is written nothing ───────────────────────────────


def test_after_the_switch_the_space_it_left_is_written_nothing(chat) -> None:
    chat.say(OWN, "I live in Lisbon.", n=1, at=T0)
    chat.move(OWN, TEAM)
    _say(SARA, THREAD, "I work at Initech.", n=2, at=T0 + 60, space=OWN)  # queued before the move
    assert chat.remembers(OWN) == ["I live in Lisbon."] and chat.facts(OWN) == {"Lisbon"}


def _unreadable(monkeypatch) -> None:
    def record_of(_thread: str) -> Any:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(threads, "_spaces", {})
    monkeypatch.setattr(threads, "record_of", record_of)


def test_an_unreadable_thread_record_fails_the_write_so_it_is_tried_again(chat, monkeypatch) -> None:
    from kazma_core.memory.consolidator import _run_turn_memory
    from kazma_core.memory.dual_write import write_episode

    chat.row(OWN, "I live in Lisbon.", at=T0)
    turn = [{"role": "user", "content": "I live in Lisbon."}, {"role": "assistant", "content": "Noted."}]
    with monkeypatch.context() as patch:
        _unreadable(patch)
        assert write_episode(session_id=THREAD, turn_number=1, user_text="I live in Lisbon.",
                             tenant_id=OWN)[0] == "failed"
        assert _run_turn_memory(turn, session_id=THREAD, turn=1, tenant_id=OWN, at=T0, said_by=SARA) is False, (
            "the queue's task fails and is retried")
        assert chat.remembers(OWN) == [] and chat.facts(OWN) == set()
        after = chat.reconcile()
        assert after == {"error": "chat store unreadable"}, "and reconcile waits before it"
    assert chat.reconcile()["turns_written"] == 1, "readable again, it is written"
    assert chat.remembers(OWN) == ["I live in Lisbon."]


# ── 7. a restored memory database keeps them out ──────────────────────────


def test_a_memory_database_restored_from_before_the_move_keeps_the_turns_out(chat) -> None:
    _tell(chat, OWN, "en")
    chat.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    path = chat.team.tmp / "memory_state.db"
    shutil.copyfile(path, chat.team.tmp / "backup.db")
    chat.move(OWN, TEAM)
    restored = sqlite3.connect(str(chat.team.tmp / "backup.db"))
    try:
        assert restored.execute("SELECT COUNT(*) FROM memory_forgotten").fetchone()[0] == 0
        out = forget_journal.apply_pending(restored)
        assert out["failed"] == []
        rows = restored.execute("SELECT DISTINCT tenant_id, turn_number, episode_id FROM memory_forgotten "
                                "WHERE session_key = ? ORDER BY turn_number", (THREAD,)).fetchall()
        assert rows == [(TEAM, 1, forget.MOVED_IN), (TEAM, 2, forget.MOVED_IN)]
        assert restored.execute("SELECT COUNT(*) FROM episodes WHERE tenant_id = ? AND tier != 'forgotten'",
                                (OWN,)).fetchone()[0] == 2, "her own turns are as they were"
    finally:
        restored.close()
    journal = sqlite3.connect(str(chat.team.tmp / "memory_forget_journal.db"))
    try:
        assert "Lisbon" not in repr(journal.execute("SELECT * FROM forget_journal").fetchall()), "no text in it"
    finally:
        journal.close()


# ── 8. a forget that fails half way ───────────────────────────────────────


def test_a_forget_that_fails_is_finished_by_the_convergence(chat, monkeypatch) -> None:
    from kazma_core.spaces import chat_moves
    from kazma_ui.chat_move import settle_chat_moves

    threads.move_thread(THREAD, TEAM)
    _tell(chat, TEAM, "en")
    with monkeypatch.context() as patch:
        patch.setattr(forget, "_forget_chat_facts", lambda *_a, **_k: (_ for _ in ()).throw(
            sqlite3.OperationalError("database is locked")))
        chat.move(TEAM, OWN)
    (entry,) = chat_moves.moves_under_way().values()
    assert "memory" not in entry["done"] and "chat" in entry["done"], "the chat itself is moved"
    forget.converge(chat.db)
    assert chat.remembers(TEAM) == [] and "Lisbon" not in chat.facts(TEAM)
    asyncio.run(settle_chat_moves(None))
    assert chat_moves.moves_under_way() == {}


def test_a_forget_that_answers_not_done_is_asked_again(chat, monkeypatch) -> None:
    from kazma_core.spaces import chat_moves
    from kazma_ui.chat_move import settle_chat_moves

    threads.move_thread(THREAD, TEAM)
    _tell(chat, TEAM, "en")
    with monkeypatch.context() as patch:
        patch.setattr(forget, "forget_chat", lambda *_a, **_k: {"ok": False, "error": "busy"})
        chat.move(TEAM, OWN)
    (entry,) = chat_moves.moves_under_way().values()
    assert "memory" not in entry["done"] and "Lisbon" in chat.facts(TEAM)
    asyncio.run(settle_chat_moves(None))
    assert chat_moves.moves_under_way() == {} and chat.facts(TEAM) == set()


def test_forgetting_a_chat_moved_away_leaves_its_new_spaces_steps(chat, monkeypatch) -> None:
    """Time Travel steps are kept by thread alone: a forget replayed for the
    space a chat left never deletes the steps it made since."""
    from kazma_core import time_travel

    threads.move_thread(THREAD, TEAM)
    _tell(chat, TEAM, "en")
    chat.move(TEAM, OWN)
    asked: list[Any] = []
    monkeypatch.setattr(time_travel, "forget_thread_snapshots", lambda keys, **_k: asked.append(keys) or 0)
    assert forget.forget_chat(SESSION, tenant_id=TEAM, keys=[SESSION, THREAD], journal=False)["ok"]
    assert asked == []
    assert forget.forget_chat(SESSION, tenant_id=OWN, keys=[SESSION, THREAD], journal=False)["ok"]
    assert len(asked) == 1
