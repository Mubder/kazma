"""Restore drills for memory, with what comes back (RPO) and how long it takes
(RTO) -- Memory audit Stage 4.

Each drill runs the product's own steps on private stores: chats saved in
the chat store, turned into memories by turn reconcile, the native memory
backup taken, more turns and a forget after it, the memory database lost,
the backup restored the way ``POST /api/system/memory/restore`` restores it,
and the passes a start runs (forget convergence, then turn reconcile). It
then measures:

* RPO: which turns are memories again. Every turn the chat store holds is
  rebuilt -- the chat store, not the backup, bounds what is lost; only what
  never was a chat turn (an agent's saved note) is lost since the backup;
* forgets made after the backup stay forgotten;
* RTO: the seconds from the restore to the last pass, at
  ``KAZMA_TEST_MEMORY_RESTORE_DRILL_TURNS`` turns (default 40; the guide's
  figures were taken at the live size).

Found by the first run (2026-10-14): turn reconcile kept its cursor in the
settings store, which a memory restore does not roll back -- the cursor had
already passed the chats changed after the backup, so their newer turns were
never memories again. The cursor now lives in ``memory_state.db`` itself
(``memory_reconcile_state``), so a restored copy carries the cursor of its
own moment. The negative control puts the newer cursor back.

The Postgres-primary profile is ``test_memory_restore_drill_pg.py``.
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

TURNS = max(4, int(os.environ.get("KAZMA_TEST_MEMORY_RESTORE_DRILL_TURNS", "40")))
DAY = 86400.0


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _turn(i: int, at: float) -> list[dict]:
    return [{"role": "user", "content": f"drill question {i} about the {_word(i)} plan", "ts": _iso(at)},
            {"role": "assistant", "content": f"Answer {i}."}]


def _word(i: int) -> str:
    # One unusual word per turn, so a keyword recall names exactly one memory.
    return "qz" + "".join("abcdefghij"[int(d)] for d in str(i).zfill(4))


@pytest.fixture()
def drill(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write, turn_reconcile
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    for var in ("KAZMA_MEMORY_ENFORCE_TENANT", "KAZMA_PRODUCTION", "KAZMA_MULTI_USER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setenv("KAZMA_BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    # The deep pass is the queue's (a model call): the drill measures the turns.
    monkeypatch.setattr("kazma_core.memory.task_queue.enqueue_task", lambda *a, **k: "queued")
    dual_write._reset_mirror()
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    ensure_primary_schema(db)
    db.close()
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    clock = SimpleNamespace(now=time.time())
    monkeypatch.setattr(turn_reconcile, "time", SimpleNamespace(time=lambda: clock.now, monotonic=time.monotonic))
    yield SimpleNamespace(dir=tmp_path, clock=clock)
    dual_write._reset_mirror()


def _save(drill, session_id: str, messages: list[dict]) -> None:
    from kazma_ui.session_manager import ChatSession, SessionManager

    store = SessionManager(db_path=str(drill.dir / "chat_sessions.db"))
    try:
        store.put(ChatSession(session_id=session_id, tenant_id="default", thread_id="", messages=messages))
    finally:
        store.close()


def _reconcile(drill) -> dict:
    """A reconcile pass an hour on: every saved chat has settled."""
    from kazma_core.memory import turn_reconcile

    drill.clock.now += 3600
    return turn_reconcile.run_turn_reconcile_pass(time_budget_s=600)


def _questions(drill, *, tier_not: str = "forgotten") -> set[str]:
    db = sqlite3.connect(str(drill.dir / "memory_state.db"))
    try:
        return {r[0] for r in db.execute("SELECT user_text FROM episodes WHERE tier != ?", (tier_not,))}
    finally:
        db.close()


def _scenario(drill, monkeypatch, *, carry_newer_cursor: bool = False) -> dict:
    """The drill. Returns what came back and how long the restore took."""
    from kazma_core.memory import forget
    from kazma_core.memory.backup import perform_native_backups, restore_memory_backup
    from kazma_core.memory.recall import recall
    from kazma_core.memory.turn_reconcile import read_state

    base = time.time() - 30 * DAY
    half = TURNS // 2
    # Before the backup: two chats, half the turns.
    chat_a = [m for i in range(0, half, 2) for m in _turn(i, base + i * 60)]
    chat_b = [m for i in range(1, half, 2) for m in _turn(i, base + i * 60)]
    _save(drill, "chat-a", chat_a)
    _save(drill, "chat-b", chat_b)
    assert _reconcile(drill)["turns_written"] == half
    backup = next(p for p in perform_native_backups() if p.name.startswith("memory_state_"))

    # After the backup: chat A goes on, a new chat C, and a forget.
    later = [m for i in range(half, TURNS - 2) for m in _turn(i, base + i * 60)]
    _save(drill, "chat-a", chat_a + later)
    _save(drill, "chat-c", [m for i in range(TURNS - 2, TURNS) for m in _turn(i, base + i * 60)])
    assert _reconcile(drill)["turns_written"] == TURNS - half
    db = sqlite3.connect(str(drill.dir / "memory_state.db"))
    db.row_factory = sqlite3.Row
    forgotten_q = "drill question 2 about the " + _word(2) + " plan"
    eid = db.execute("SELECT id FROM episodes WHERE user_text = ?", (forgotten_q,)).fetchone()[0]
    db.close()
    assert forget.forget_episode(eid, tenant_id="default")["ok"]
    newer_cursor = read_state(None)

    # The restore, as POST /api/system/memory/restore does it, an hour on.
    if carry_newer_cursor:  # negative control: the cursor of after the backup
        monkeypatch.setattr("kazma_core.memory.turn_reconcile.read_state", lambda _conn: newer_cursor)
    drill.clock.now += 3600
    caught_up = restore_memory_backup(backup)

    remembered = _questions(drill)
    expected = {f"drill question {i} about the {_word(i)} plan" for i in range(TURNS)} - {forgotten_q}
    found = recall(f"{_word(TURNS - 1)} plan", tenant_id="default", local_only=True)
    return {
        "missing": sorted(expected - remembered),
        "forgotten_back": forgotten_q in remembered,
        "recall_finds_newest": any(_word(TURNS - 1) in e.content for e in found.episodes),
        "rto_s": caught_up["seconds"],
        "rebuilt": caught_up["turns_rebuilt"],
        "forgets_applied": caught_up["forgets_applied"],
    }


def test_a_restored_backup_comes_back_whole_and_keeps_its_forgets(drill, monkeypatch, capsys):
    out = _scenario(drill, monkeypatch)
    with capsys.disabled():
        print(f"\n[memory restore drill, local] turns={TURNS} rebuilt={out['rebuilt']} "
              f"missing={len(out['missing'])} rto_s={out['rto_s']}")
    assert out["missing"] == [], out["missing"][:5]  # RPO: every chat turn is a memory again
    assert not out["forgotten_back"]  # the forget after the backup holds
    assert out["recall_finds_newest"]  # a turn from after the backup answers
    assert out["rebuilt"] == TURNS - TURNS // 2  # every turn of after the backup
    assert out["forgets_applied"] == 1  # at the restore, not at the next pass


def test_a_cursor_from_after_the_backup_loses_the_newer_turns(drill, monkeypatch):
    """Negative control: the cursor where it was (the settings store, before
    2026-10-14) had passed the chats changed after the backup."""
    out = _scenario(drill, monkeypatch, carry_newer_cursor=True)
    assert out["missing"], "the drill must see the lost turns"


def test_an_upgraded_database_goes_on_from_the_old_facts_cursor(drill, monkeypatch):
    """No cursor row yet (an older build): the facts cursor continues from the
    settings store's, capped at the database's newest memory; none at all
    starts from the beginning."""
    import sqlite3 as _sqlite3

    from kazma_core.config_store import get_config_store
    from kazma_core.memory import turn_reconcile

    db = _sqlite3.connect(str(drill.dir / "memory_state.db"))
    for i in range(3):
        db.execute("INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, tier, created_at, "
                   "metadata_json) VALUES (?, 'default', 's', ?, ?, 'episodic', 0, '{}')", (f"x{i}", i + 1, f"q{i}"))
    db.commit()
    db.close()
    assert turn_reconcile.read_state(None)["facts_after"] == 0
    get_config_store().set(turn_reconcile.STATE_KEY, {"facts_after": 2}, category="memory")
    assert turn_reconcile.read_state(None)["facts_after"] == 2
    get_config_store().set(turn_reconcile.STATE_KEY, {"facts_after": 99}, category="memory")
    assert turn_reconcile.read_state(None)["facts_after"] == 3  # never past this database
    get_config_store().delete(turn_reconcile.STATE_KEY)
