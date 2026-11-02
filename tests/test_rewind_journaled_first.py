"""A rewind is journaled before it touches memory, and says what it did (FA-102).

The 2026-11 functional audit: ``_forget_rewound_turns`` opened the memory
database before it journaled anything. With the database locked, the rewind
returned ``{"ok": False}`` with an empty journal -- the maintenance pass had
nothing to apply although the WARNING said it would -- and ``/undo`` still
answered "Removed last assistant response, and what Kazma remembered from it."
(its error reply carried the raw exception text).

Now one whole-range ``rewind`` entry is appended first; a locked database
leaves it pending and one convergence pass applies it -- only to memories
made before the rewind, so the turns the chat goes on to write under the same
numbers stay. ``/undo`` and ``/edit`` say what happened to memory. The
negative control is the old order (open, then journal).
"""

from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from kazma_core.memory import forget, forget_journal
from tests.test_memory_rewind import (  # noqa: F401 - the fixture and its helpers
    MESSAGES,
    THREAD,
    _graph,
    _recall_holds_turn_two,
    _remember_both_turns,
    _seed,
    _turn_one_kept,
    mem,
)


def _locked(monkeypatch):
    real = forget._open
    calls = {"n": 0}

    def locked():
        calls["n"] += 1
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(forget, "_open", locked)
    return real, calls


async def _undo(graph, config) -> dict:
    from kazma_core.agent.rewind import drop_last_answer

    before = list((await graph.aget_state(config)).values["messages"])
    after = drop_last_answer(before)
    await graph.aupdate_state(config, {"messages": after})
    return await forget.rewind_memory(THREAD, before, after)


def test_a_locked_database_leaves_the_rewind_journaled_and_one_pass_applies_it(mem, monkeypatch):
    _remember_both_turns(mem)
    graph = _graph()
    real_open, calls = _locked(monkeypatch)

    async def run():
        return await _undo(graph, await _seed(graph))

    result = asyncio.run(run())
    assert result == {"ok": False, "pending": True} and calls["n"] == 1
    entries = [e for e in forget_journal._entries() if e.kind == "rewind"]
    assert len(entries) == 1 and entries[0].session_key == THREAD and entries[0].turn_number == 2
    assert _recall_holds_turn_two(mem), "nothing changed while the database was locked"

    monkeypatch.setattr(forget, "_open", real_open)
    done = forget_journal.apply_pending(mem.db)
    assert done["applied"] >= 1 and done["pending"] == 0
    assert not _recall_holds_turn_two(mem) and _turn_one_kept(mem)


def test_a_late_pass_keeps_the_turns_written_after_the_rewind(mem, monkeypatch):
    """The chat went on after the undo: its new turn 2 is not the rewound one."""
    from kazma_core.memory.dual_write import mirror_episode

    _remember_both_turns(mem)
    graph = _graph()
    real_open, _ = _locked(monkeypatch)

    async def run():
        return await _undo(graph, await _seed(graph))

    asyncio.run(run())
    monkeypatch.setattr(forget, "_open", real_open)
    # Written after the rewind (a second later than its entry).
    assert mirror_episode(session_id=THREAD, turn_number=2, user_text="what's the weather in Muscat",
                          assistant_text="Hot and humid.", created_at=time.time() + 1)
    forget_journal.apply_pending(mem.db)
    rows = mem.db.execute("SELECT user_text, tier FROM episodes WHERE session_id = ? AND turn_number = 2",
                          (THREAD,)).fetchall()
    kept = {r[0]: r[1] for r in rows}
    assert kept.get("what's the weather in Muscat") not in (None, forget.FORGOTTEN_TIER), kept
    assert not _recall_holds_turn_two(mem)


def test_the_old_order_journaled_nothing(mem, monkeypatch):
    """Negative control: opening the database first, as the rewind did."""
    _remember_both_turns(mem)
    _locked(monkeypatch)
    before = len(forget_journal._entries())
    with pytest.raises(sqlite3.OperationalError):
        forget._open()  # the old first step: nothing after it ran
    assert len(forget_journal._entries()) == before


@pytest.mark.parametrize(("answer", "said"), [
    ({"ok": True, "turns": 1}, "and forgot what Kazma remembered from it."),
    ({"ok": False, "pending": True}, "Memory could not be updated yet"),
    ({"ok": False, "pending": False}, "could not take back what it remembered"),
])
def test_undo_says_what_happened_to_memory(answer, said):
    from kazma_gateway.agent_handler.graph import _rewind_memory_note

    note = _rewind_memory_note(answer, "it")
    assert said in note
    assert "Traceback" not in note and "OperationalError" not in note


def test_the_gateway_replies_read_the_answer():
    """/undo and /edit word the memory note from the rewind's answer, and
    their failure replies carry no exception text."""
    import inspect

    from kazma_gateway.agent_handler import graph as gw

    src = inspect.getsource(gw)
    assert src.count("_rewind_memory_note(memory,") == 2
    assert 'f"⚠️ Could not undo: {exc}"' not in src and 'f"⚠️ Could not edit: {exc}"' not in src


def test_without_the_time_bound_the_later_turn_is_lost(mem, monkeypatch):
    """Negative control for the bound: the same late pass, its entry dated
    after the new turn (no bound in effect), forgets the turn the chat went on
    to write."""
    from kazma_core.memory.dual_write import mirror_episode

    _remember_both_turns(mem)
    graph = _graph()
    real_open, _ = _locked(monkeypatch)

    async def run():
        return await _undo(graph, await _seed(graph))

    asyncio.run(run())
    monkeypatch.setattr(forget, "_open", real_open)
    assert mirror_episode(session_id=THREAD, turn_number=2, user_text="what's the weather in Muscat",
                          assistant_text="Hot and humid.", created_at=time.time())
    new_id = mem.db.execute("SELECT id FROM episodes WHERE session_id = ? AND user_text = ?",
                            (THREAD, "what's the weather in Muscat")).fetchone()[0]
    conn = forget_journal._connect()
    try:
        conn.execute("UPDATE forget_journal SET at = ? WHERE kind = 'rewind'", (time.time() + 3600,))
    finally:
        conn.close()
    forget_journal.apply_pending(mem.db)
    tier = mem.db.execute("SELECT tier FROM episodes WHERE id = ?", (new_id,)).fetchone()[0]
    assert tier == forget.FORGOTTEN_TIER
