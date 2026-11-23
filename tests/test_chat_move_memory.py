"""Spaces S2-12: what memory holds of a chat follows it when its person moves
it between their own space and a shared one (``memory.chat_move``), as agreed
with the memory session (2026-11-23).

Through the real writers on a private memory store: the post-turn pipeline
for the chat's turns, ``mutate_belief`` for the person's decisions, the
episode writer for the agent's notes, the changes log, the forget ledger and
journal, turn reconcile and the quick pass for the rebuild. Sara moves her
chat between her personal space and a shared space she is in with Omar.

1. The person's decisions are carried, not rebuilt: a fact at the user's
   word, at its own time and trust, filed as each space files the speaker
   (``user`` <-> ``member_<account>``); "Not me" is carried; a change still
   waiting is dropped.
2. The agent's notes are carried.
3a. The source keeps no tombstone of the chat: moved there and back, its
    turns are written again (control: tombstones kept, the move back writes
    nothing).
3b. What the person kept out of memory stays out in the target (control:
    no ledger copy, the forgotten turn comes back).
3c. The chat's turns and their quick-pass facts are in the target at once.
3d. What the chat's facts closed in the source is reopened, unless
    something else closed it since (control: nothing reopened).
3e. A writer queued before the move cannot write into the source (control:
    the guard taken out).
3g. The weeks the chat covers are summarized again in the target.
3h. The rebuild's deep pass is Kazma's own work, counted against no one.
A move a stop cut off is finished by the forget convergence, only while the
chat is still away.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from kazma_core.memory import chat_move, forget, forget_journal
from kazma_core.spaces import threads

from tests.test_memory_speakers import OMAR, SARA, TEAM, _say, team  # noqa: F401

OWN = "sara-own"
KEY = "chat-move-1"
T0 = 1_780_000_000.0
DAY = 86_400.0

#: Sara's chat: (question, answer, when).
TURNS = [
    ("I live in Lisbon.", "Noted.", T0),
    ("I work at Initech.", "Noted.", T0 + 60),
]


@pytest.fixture
def moved(team, monkeypatch):  # noqa: F811 -- the fixture
    """The team world, Sara's chat recorded in her own space, its turns
    written there by the real post-turn pipeline."""
    monkeypatch.setattr(threads, "_spaces", {})
    threads.record_thread(KEY, OWN, SARA)
    for n, (question, _answer, at) in enumerate(TURNS, start=1):
        _say(SARA, KEY, question, n=n, at=at, space=OWN)
    return team


def _store_chat(tmp: Path, tenant: str, turns: list[tuple[str, str, float]] = TURNS) -> None:
    """The chat row as the chat store keeps it, in *tenant* (the move copied
    it there before memory follows)."""
    messages: list[dict[str, Any]] = []
    for question, answer, at in turns:
        ts = datetime.fromtimestamp(at, UTC).isoformat()
        messages.append({"role": "user", "content": question, "ts": ts, "author": SARA})
        messages.append({"role": "assistant", "content": answer, "ts": ts})
    with closing(sqlite3.connect(tmp / "chat_sessions.db")) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS sessions (tenant_id TEXT, session_id TEXT, messages TEXT, "
                     "created_at TEXT, thread_id TEXT, PRIMARY KEY (tenant_id, session_id))")
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (KEY,))
        conn.execute("INSERT INTO sessions (tenant_id, session_id, thread_id, messages, created_at) "
                     "VALUES (?, ?, ?, ?, '')", (tenant, KEY, KEY, json.dumps(messages)))
        conn.commit()


def _move(team, to: str, *, frm: str, turns: list[tuple[str, str, float]] = TURNS,  # noqa: F811 -- the fixture
          journal: bool = True) -> dict[str, Any]:
    """The move's order: the chat row in the target, the thread's record
    switched, then memory follows."""
    _store_chat(team.tmp, to, turns)
    threads.move_thread(KEY, to)
    out = chat_move.move_chat_memory(KEY, from_space=frm, to_space=to, mover=SARA, conn=team.db, journal=journal)
    team.db.commit()
    assert out["ok"], out
    return out


def _turns(db: sqlite3.Connection, tenant: str) -> list[str]:
    return [str(r[0]) for r in db.execute(
        "SELECT user_text FROM episodes WHERE tenant_id = ? AND session_id = ? AND tier != 'forgotten' "
        "AND user_text != '' ORDER BY turn_number", (tenant, KEY))]


def _rows(db: sqlite3.Connection, tenant: str) -> int:
    return int(db.execute("SELECT COUNT(*) FROM episodes WHERE tenant_id = ? AND session_id = ?",
                          (tenant, KEY)).fetchone()[0])


def _current(db: sqlite3.Connection, tenant: str, predicate: str) -> dict[str, tuple[str, str]]:
    return {str(r["subject"]): (str(r["object"]), str(r["extraction_method"])) for r in db.execute(
        "SELECT subject, object, extraction_method FROM beliefs WHERE tenant_id = ? AND predicate = ? "
        "AND valid_until IS NULL AND invalidated_at IS NULL AND object != ''", (tenant, predicate))}


ENTRY = f"member_{SARA}"


# ── 3c. the chat's turns and their facts are in the target at once ────────


def test_the_turns_and_their_facts_are_rebuilt_in_the_target_at_once(moved) -> None:
    out = _move(moved, TEAM, frm=OWN)
    assert _turns(moved.db, TEAM) == [q for q, _a, _t in TURNS], "no reconcile pass was needed"
    assert _turns(moved.db, OWN) == [] and _rows(moved.db, OWN) == 0, "nothing of the chat left in the source"
    # The quick pass files Sara's own words under her entry in the shared space.
    assert _current(moved.db, TEAM, "lives_in")[ENTRY][0] == "Lisbon"
    assert "user" not in _current(moved.db, OWN, "lives_in")
    assert out["turns"] == 2


# ── 1. the person's decisions are carried ─────────────────────────────────


def _decide(db: sqlite3.Connection, subject: str, predicate: str, value: str, *, tenant: str, at: float) -> None:
    from kazma_core.memory.belief_mutation import mutate_belief

    mutate_belief(db, subject, predicate, value, extraction_method="user_explicit", tenant_id=tenant,
                  source_session=KEY, source_turn=1, now=at)
    db.commit()


def test_a_decision_at_the_users_word_is_carried_at_its_own_time_and_trust(moved) -> None:
    decided = T0 + 30
    _decide(moved.db, "user", "favorite_color", "teal", tenant=OWN, at=decided)
    _move(moved, TEAM, frm=OWN)
    assert _current(moved.db, TEAM, "favorite_color") == {ENTRY: ("teal", "user_explicit")}
    row = moved.db.execute("SELECT valid_from FROM beliefs WHERE tenant_id = ? AND subject = ? "
                           "AND predicate = 'favorite_color'", (TEAM, ENTRY)).fetchone()
    assert float(row[0]) == decided, "at its own time"
    assert _current(moved.db, OWN, "favorite_color") == {}
    # And back: her entry is "user" again in her own space.
    _move(moved, OWN, frm=TEAM)
    assert _current(moved.db, OWN, "favorite_color") == {"user": ("teal", "user_explicit")}
    assert _current(moved.db, TEAM, "favorite_color") == {}


def test_control_without_the_carry_the_decision_is_lost(moved, monkeypatch) -> None:
    monkeypatch.setattr(chat_move, "_carry_facts", lambda *_a, **_k: 0)
    _decide(moved.db, "user", "favorite_color", "teal", tenant=OWN, at=T0 + 30)
    _move(moved, TEAM, frm=OWN)
    assert _current(moved.db, TEAM, "favorite_color") == {}, "its words do not say it: only the carry keeps it"


def _changes(db: sqlite3.Connection, tenant: str, state: str) -> list[dict[str, Any]]:
    from kazma_core.memory import changes

    return changes.list_changes(db, tenant_id=tenant, states=(state,))


def _record(db: sqlite3.Connection, *, kind: str, state: str, value: str) -> None:
    """A change of the chat, as the changes log keeps it."""
    from kazma_core.memory import changes

    db.execute(
        "INSERT INTO memory_changes (id, tenant_id, kind, state, subject, predicate, old_value, new_value, "
        "source_session, source_turn, created_at, decided_at, decided_by) "
        "VALUES (?, ?, ?, ?, 'user', 'lives_in', 'Lisbon', ?, ?, 1, ?, ?, ?)",
        (changes._new_id(), OWN, kind, state, value, KEY, T0, T0 + 5 if state != "open" else None,
         "user" if state != "open" else None))
    db.commit()


def test_not_me_is_carried_and_a_waiting_change_is_dropped(moved) -> None:
    from kazma_core.memory import changes

    _record(moved.db, kind=changes.READING, state="dismissed", value="Porto")
    _record(moved.db, kind=changes.PENDING, state="open", value="Faro")
    _move(moved, TEAM, frm=OWN)
    assert changes.user_said_not_me(moved.db, tenant_id=TEAM, subject=ENTRY, predicate="lives_in", value="Porto")
    assert _changes(moved.db, TEAM, "open") == [] and _changes(moved.db, OWN, "open") == [], (
        "a change still waiting is dropped, not moved")


def test_control_without_carrying_not_me_the_deep_pass_may_file_it_again(moved, monkeypatch) -> None:
    from kazma_core.memory import changes

    monkeypatch.setattr(chat_move, "_carry_not_me", lambda *_a, **_k: 0)
    _record(moved.db, kind=changes.READING, state="dismissed", value="Porto")
    _move(moved, TEAM, frm=OWN)
    assert not changes.user_said_not_me(moved.db, tenant_id=TEAM, subject=ENTRY, predicate="lives_in",
                                        value="Porto")


# ── 2. the agent's notes are carried ──────────────────────────────────────


def _note(text: str, tenant: str) -> None:
    from kazma_core.memory.dual_write import mirror_episode

    assert mirror_episode(session_id="memory_store", turn_number=0, user_text=text, source="memory_store_tool",
                          tenant_id=tenant, metadata={"chat": KEY, "chat_turn": 1})


def _notes(db: sqlite3.Connection, tenant: str) -> list[str]:
    return [str(r[0]) for r in db.execute(
        "SELECT user_text FROM episodes WHERE tenant_id = ? AND session_id = 'memory_store' "
        "AND tier != 'forgotten' AND user_text != ''", (tenant,))]


def test_the_agents_notes_are_carried(moved) -> None:
    _note("Sara keeps her bike in the garage.", OWN)
    out = _move(moved, TEAM, frm=OWN)
    assert _notes(moved.db, TEAM) == ["Sara keeps her bike in the garage."]
    assert _notes(moved.db, OWN) == []
    assert out["notes_carried"] == 1


# ── 3a. no tombstone stays in the source: there and back ──────────────────


def test_moved_there_and_back_the_turns_are_written_again(moved) -> None:
    _move(moved, TEAM, frm=OWN)
    _move(moved, OWN, frm=TEAM)
    assert _turns(moved.db, OWN) == [q for q, _a, _t in TURNS]
    assert _turns(moved.db, TEAM) == [] and _rows(moved.db, TEAM) == 0
    assert _current(moved.db, OWN, "lives_in")["user"][0] == "Lisbon"


def test_control_with_the_tombstones_kept_the_move_back_writes_nothing(moved, monkeypatch) -> None:
    monkeypatch.setattr(chat_move, "_delete_moved_tombstones", lambda *_a, **_k: None)
    _move(moved, TEAM, frm=OWN)
    _move(moved, OWN, frm=TEAM)
    assert _turns(moved.db, OWN) == [], "the same ids, emptied: the writer says written and writes nothing"


# ── 3b. what the person kept out stays out ────────────────────────────────


def _forget_turn(db: sqlite3.Connection, tenant: str, turn: int) -> None:
    row = db.execute("SELECT id FROM episodes WHERE tenant_id = ? AND session_id = ? AND turn_number = ?",
                     (tenant, KEY, turn)).fetchone()
    out = forget.forget_episode(str(row[0]), tenant_id=tenant, conn=db, by="user")
    db.commit()
    assert out.get("ok"), out


def test_a_turn_the_person_forgot_stays_out_in_the_target(moved) -> None:
    _forget_turn(moved.db, OWN, 2)
    out = _move(moved, TEAM, frm=OWN)
    assert _turns(moved.db, TEAM) == ["I live in Lisbon."], "the forgotten turn is not rebuilt"
    assert forget.refuses_write(moved.db, tenant_id=TEAM, session_id=KEY, turn_number=2,
                                user_text="I work at Initech.")
    assert out["ledger_rows"] >= 1
    kinds = [e.kind for e in forget_journal.pending(moved.db)]
    assert "turn" not in kinds, "the copy is journaled and applied"


def test_control_without_the_ledger_copy_the_forgotten_turn_comes_back(moved, monkeypatch) -> None:
    monkeypatch.setattr(chat_move, "_copy_ledger", lambda *_a, **_k: 0)
    _forget_turn(moved.db, OWN, 2)
    _move(moved, TEAM, frm=OWN)
    assert "I work at Initech." in _turns(moved.db, TEAM)


# ── 3d. what the chat's facts closed is reopened ──────────────────────────

OTHER = "chat-move-0"


def _said_elsewhere(text: str, *, n: int, at: float) -> None:
    if threads.record_of(OTHER) is None:
        threads.record_thread(OTHER, OWN, SARA)
    _say(SARA, OTHER, text, n=n, at=at, space=OWN)


@pytest.fixture
def paris_first(team, monkeypatch):  # noqa: F811 -- the fixture
    """Another chat said Paris first; the moved chat's Lisbon closed it."""
    monkeypatch.setattr(threads, "_spaces", {})
    threads.record_thread(KEY, OWN, SARA)
    _said_elsewhere("I live in Paris.", n=1, at=T0 - 60)
    for n, (question, _answer, at) in enumerate(TURNS, start=1):
        _say(SARA, KEY, question, n=n, at=at, space=OWN)
    assert _current(team.db, OWN, "lives_in")["user"][0] == "Lisbon"
    return team


def test_what_the_chats_facts_closed_is_reopened(paris_first) -> None:
    out = _move(paris_first, TEAM, frm=OWN)
    assert _current(paris_first.db, OWN, "lives_in")["user"][0] == "Paris"
    assert out["reopened"] == 1


def test_a_fact_closed_since_by_something_else_stays_closed(paris_first) -> None:
    _said_elsewhere("I live in Rome.", n=2, at=T0 + 600)
    _move(paris_first, TEAM, frm=OWN)
    assert _current(paris_first.db, OWN, "lives_in")["user"][0] == "Rome"


def test_control_without_the_reopen_the_source_loses_the_home(paris_first, monkeypatch) -> None:
    monkeypatch.setattr(chat_move, "_reopen", lambda *_a, **_k: [])
    _move(paris_first, TEAM, frm=OWN)
    assert "user" not in _current(paris_first.db, OWN, "lives_in")


# ── 3e. a writer queued before the move cannot write into the source ──────


def test_a_queued_write_for_the_source_is_refused_after_the_switch(moved) -> None:
    from kazma_core.memory.dual_write import write_episode

    _move(moved, TEAM, frm=OWN)
    assert forget.refuses_write(moved.db, tenant_id=OWN, session_id=KEY, turn_number=3, user_text="I have a cat.")
    status, _eid = write_episode(session_id=KEY, turn_number=3, user_text="I have a cat.", assistant_text="Noted.",
                                 tenant_id=OWN, source="turn_reconcile", created_at=T0 + 120)
    assert status == "refused" and _rows(moved.db, OWN) == 0
    assert not forget.refuses_write(moved.db, tenant_id=TEAM, session_id=KEY, turn_number=3,
                                    user_text="I have a cat."), "its home takes it"


def test_control_without_the_guard_the_queued_write_lands_in_the_source(moved, monkeypatch) -> None:
    from kazma_core.memory.dual_write import write_episode

    monkeypatch.setattr(forget, "_moved_elsewhere", lambda *_a: False)
    _move(moved, TEAM, frm=OWN)
    status, _eid = write_episode(session_id=KEY, turn_number=3, user_text="I have a cat.", assistant_text="Noted.",
                                 tenant_id=OWN, source="turn_reconcile", created_at=T0 + 120)
    assert status == "written" and _rows(moved.db, OWN) == 1


def test_with_spaces_off_the_guard_reads_nothing(moved, monkeypatch) -> None:
    from kazma_core.spaces import scope

    monkeypatch.setattr(scope, "_enforced", False)
    monkeypatch.setattr(threads, "record_of", lambda *_a: pytest.fail("the record was read"))
    assert forget._moved_elsewhere([KEY], OWN) is False


# ── 3g. the weeks are summarized again; 3h. the rebuild is Kazma's own ────


def test_the_weeks_the_chat_covers_are_summarized_again_in_the_target(moved) -> None:
    from kazma_core.memory.topic_summaries import _week_of

    key, start, end = _week_of(T0)
    moved.db.execute("INSERT INTO memory_summary_periods (tenant_id, period_key, period_start, period_end, "
                     "status, attempts) VALUES (?, ?, ?, ?, 'done', 2)", (TEAM, key, start, end))
    moved.db.commit()
    out = _move(moved, TEAM, frm=OWN)
    status, attempts = moved.db.execute("SELECT status, attempts FROM memory_summary_periods "
                                        "WHERE tenant_id = ? AND period_key = ?", (TEAM, key)).fetchone()
    assert (status, attempts) == ("queued", 0) and out["weeks"] == 1


def test_the_rebuilds_deep_pass_is_kazmas_own_work(moved) -> None:
    from kazma_core.memory.task_queue import REBUILD_CHAT_MOVE, _task_binding

    _move(moved, TEAM, frm=OWN)
    payloads = [json.loads(r[0]) for r in moved.ops.execute(
        "SELECT payload_json FROM memory_task_queue WHERE task_type = 'micro_consolidation'")]
    rebuilt = [p for p in payloads if p.get("rebuild") == REBUILD_CHAT_MOVE]
    assert len(rebuilt) == 2, payloads
    binding = _task_binding("micro_consolidation", rebuilt[0])
    assert binding.install, "not counted against the mover's limit"


def test_control_a_deep_pass_with_no_mark_runs_as_its_person(moved) -> None:
    from kazma_core.memory.task_queue import _task_binding

    _move(moved, TEAM, frm=OWN)
    row = moved.db.execute("SELECT id FROM episodes WHERE tenant_id = ? AND session_id = ? LIMIT 1",
                           (TEAM, KEY)).fetchone()
    binding = _task_binding("micro_consolidation", {"episode_id": str(row[0])})
    assert not binding.install


# ── a move a stop cut off ─────────────────────────────────────────────────


def _cut_off(team, monkeypatch) -> None:  # noqa: F811 -- the fixture
    """The move journaled and the chat switched; a stop before memory followed."""
    _store_chat(team.tmp, TEAM)
    threads.move_thread(KEY, TEAM)
    with monkeypatch.context() as patch:
        patch.setattr(chat_move, "_copy_ledger", lambda *_a, **_k: (_ for _ in ()).throw(sqlite3.OperationalError(
            "database is locked")))
        with pytest.raises(sqlite3.OperationalError):
            chat_move.move_chat_memory(KEY, from_space=OWN, to_space=TEAM, mover=SARA, conn=team.db)
    team.db.commit()
    assert [e.kind for e in forget_journal.pending(team.db)] == ["chat_moved"]


def test_a_move_cut_off_is_finished_by_the_convergence(moved, monkeypatch) -> None:
    _cut_off(moved, monkeypatch)
    assert _turns(moved.db, OWN) != [], "nothing moved yet"
    forget.converge(moved.db)
    moved.db.commit()
    assert _turns(moved.db, TEAM) == [q for q, _a, _t in TURNS] and _rows(moved.db, OWN) == 0
    assert forget_journal.pending(moved.db) == []


def test_a_chat_moved_back_since_is_not_moved_again(moved, monkeypatch) -> None:
    _cut_off(moved, monkeypatch)
    threads.move_thread(KEY, OWN)
    forget.converge(moved.db)
    moved.db.commit()
    assert _turns(moved.db, OWN) == [q for q, _a, _t in TURNS], "it is home: the source keeps it"
