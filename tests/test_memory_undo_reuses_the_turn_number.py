"""After an undo the chat's next turn has the same number, and the forget
ledger names a turn by its number (2026-11-24).

``enforce_ledger`` -- the maintenance pass, every 15 minutes -- took every
fact of a turn number the ledger holds, so what the new turn taught was
forgotten at the next pass: "I live in Paris" undone, "I live in Lisbon" said
instead, and the home gone within the quarter hour. The ledger's word reaches
what was written by the time of the forget, and -- while no other turn has the
number -- what was stated by then (a fact pass that had read the turn, audit
2026-10-26 AUD-010).

Through the product, in both languages: the post-turn function writes the
turns, the real rewind takes the first back, ``enforce_ledger`` runs. Negative
control: the old pass.
"""

from __future__ import annotations

import sqlite3
import time

import pytest
from kazma_core.memory import forget
from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

CHAT = "chat-undo"

#: What was said and undone, what was said instead, the two values.
SAID = [
    ("I live in Paris.", "I live in Lisbon.", "Paris", "Lisbon"),
    ("أسكن في باريس.", "أسكن في لشبونة.", "باريس", "لشبونة"),
]


@pytest.fixture
def db(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write

    for var in ("KAZMA_PRODUCTION", "KAZMA_MULTI_USER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    monkeypatch.setattr("kazma_core.memory.forget.chat_keys", lambda key: [key])
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"), isolation_level=None)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    dual_write._reset_mirror()
    yield conn
    dual_write._reset_mirror()
    ops.close()
    conn.close()


def _messages(*said: str) -> list[dict]:
    out: list[dict] = []
    for text in said:
        out += [{"role": "user", "content": text}, {"role": "assistant", "content": "Noted."}]
    return out


def _turn(text: str, *, turn: int = 2) -> None:
    from kazma_core.memory.consolidator import _run_turn_memory

    assert _run_turn_memory(_messages("hello there, how are you", text)[:2 * turn], session_id=CHAT, turn=turn,
                            tenant_id="default", at=time.time())
    time.sleep(0.02)


def _undo(text: str) -> None:
    out = forget._forget_rewound_turns(CHAT, _messages("hello there, how are you", text), from_turn=2,
                                       tenant_id="default")
    assert out.get("ok"), out
    time.sleep(0.02)


def _held(conn: sqlite3.Connection) -> list[str]:
    return [str(r[0]) for r in conn.execute(
        "SELECT object FROM beliefs WHERE source_session = ? AND object != '' AND invalidated_at IS NULL "
        "AND valid_until IS NULL AND predicate != 'related_to'", (CHAT,))]


def _old_pass(conn: sqlite3.Connection) -> None:
    """``enforce_ledger``'s facts as they were taken: by the turn number."""
    for tenant, key, turn in conn.execute(
            "SELECT DISTINCT f.tenant_id, f.session_key, f.turn_number FROM memory_forgotten f "
            "JOIN beliefs b ON b.tenant_id = f.tenant_id AND b.source_session = f.session_key "
            "AND b.source_turn = f.turn_number WHERE f.turn_number > 0 AND b.object != ''").fetchall():
        forget._forget_turn_facts(conn, tenant_id=tenant, keys=[key], turn=int(turn), now=time.time())


@pytest.mark.parametrize(("undone", "instead", "gone", "kept"), SAID)
def test_what_is_said_instead_of_an_undone_turn_stays(db, undone, instead, gone, kept):
    _turn(undone)
    assert _held(db) == [gone]
    _undo(undone)
    assert _held(db) == []
    _turn(instead)
    assert _held(db) == [kept]
    for _pass in range(2):
        forget.enforce_ledger(db)
        assert _held(db) == [kept]


@pytest.mark.parametrize(("undone", "instead", "gone", "kept"), SAID)
def test_control_the_pass_by_the_turn_number_forgets_it(db, undone, instead, gone, kept):
    _turn(undone)
    _undo(undone)
    _turn(instead)
    _old_pass(db)
    assert _held(db) == []


@pytest.mark.parametrize(("undone", "instead", "gone", "kept"), SAID)
def test_what_the_undone_turn_left_behind_is_still_taken(db, undone, instead, gone, kept):
    """A fact of the undone turn that the forget missed (a restore, a fact
    pass that had read the turn): taken, beside the new turn's, which stays."""
    _turn(undone)
    left = dict(db.execute("SELECT * FROM beliefs WHERE object = ?", (gone,)).fetchone())
    _undo(undone)
    _turn(instead)
    left["id"] = "b_left"
    db.execute(f"INSERT INTO beliefs ({', '.join(left)}) VALUES ({', '.join('?' for _ in left)})",
               list(left.values()))
    assert sorted(_held(db)) == sorted([gone, kept])
    assert forget.enforce_ledger(db)["facts"] == 1
    assert _held(db) == [kept]


@pytest.mark.parametrize(("undone", "instead", "gone", "kept"), SAID)
def test_a_fact_pass_that_had_read_the_turn_is_taken_back(db, undone, instead, gone, kept):
    """Written after the forget landed, stated before it; no other turn has
    the number (audit 2026-10-26 AUD-010)."""
    from kazma_core.memory.belief_extractor import extract_and_apply_beliefs_sync

    stated = time.time()
    _turn(undone)
    _undo(undone)
    ops = sqlite3.connect(":memory:")
    ensure_ops_schema(ops)
    extract_and_apply_beliefs_sync(db, ops, undone, session_id=CHAT, turn=2, tenant_id="default", now=stated,
                                   private=True)
    assert _held(db) == [gone]
    forget.enforce_ledger(db)
    assert _held(db) == []


@pytest.mark.parametrize(("undone", "instead", "gone", "kept"), SAID)
def test_asking_again_for_the_undone_turn_takes_nothing_of_the_new_one(db, undone, instead, gone, kept):
    """A second Forget of the undone memory, and the pass that finds an old
    copy of it (a restored row)."""
    _turn(undone)
    old = str(db.execute("SELECT id FROM episodes WHERE session_id = ? AND turn_number = 2", (CHAT,)).fetchone()[0])
    _undo(undone)
    _turn(instead)
    assert forget.forget_episode(old, conn=db, tenant_id="default")["already"]
    assert _held(db) == [kept]
    db.execute("UPDATE episodes SET tier = 'episodic', user_text = ?, assistant_text = 'Noted.' WHERE id = ?",
               (undone, old))
    assert forget.enforce_ledger(db)["memories"] == 1
    assert _held(db) == [kept]
    assert db.execute("SELECT tier FROM episodes WHERE id = ?", (old,)).fetchone()[0] == "forgotten"


@pytest.mark.parametrize(("event", "first", "again"), [
    ("the pottery exhibition", "The pottery exhibition was last Saturday.", "The pottery exhibition was on the 23rd."),
    ("معرض الفخار", "معرض الفخار كان السبت اللي فات.", "معرض الفخار كان يوم ٢٣."),
])
def test_the_day_the_new_turn_gives_stays(db, event, first, again):
    """The day half (``_ledgered_days``)."""
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2
    from kazma_core.memory.fact_days import metadata_of

    ops = sqlite3.connect(":memory:")
    ensure_ops_schema(ops)

    def told(day: str | None, text: str, turn: int) -> None:
        fact = {"subject": "user", "predicate": "attended", "predicate_type": "set", "object": event,
                "confidence": 0.9, **({"on": day} if day else {})}
        _apply_beliefs_to_v2([fact], db, ops, session_id=CHAT, turn=turn, tenant_id="default", now=time.time(),
                             private=True, user_text=text, stats={"source": "llm", "applied": 0, "rejected": 0,
                                                                  "actions": [], "skipped_filler": False})
        time.sleep(0.02)

    def day() -> str:
        return str(metadata_of(db.execute("SELECT metadata_json FROM beliefs WHERE predicate = 'attended' "
                                          "AND valid_until IS NULL").fetchone()[0]).get("started") or "")

    told(None, event, 1)
    _turn(first)
    told("2026-05-30", first, 2)
    assert day() == "2026-05-30"
    _undo(first)
    assert day() == ""
    _turn(again)
    told("2026-05-23", again, 2)
    forget.enforce_ledger(db)
    assert day() == "2026-05-23"
