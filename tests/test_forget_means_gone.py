"""Forget means gone (the memory framework audit's part D: F5 and F11, 2026-11-21).

What the audit measured, and what each test here holds:

- **A forgotten fact kept its subject and predicate.** "Sara was diagnosed
  with celiac disease", forgotten, read ``sara`` / ``diagnosed_with`` and an
  empty value, and the search index still answered "diagnosed". The planted
  word goes in a fact's subject, its predicate and its value, every kind of
  forget runs, and every column of every table of both memory databases and
  the forget journal is read for it. Negative control: today's scrub (the
  value alone) leaves it in the subject, the predicate and the index.
- **"Forget everything that mentions X" left X.** It compared current facts'
  values with SQLite's ``LIKE`` (ASCII case only), left an ended fact's value,
  read no summary, entity, archived fact or legacy table, and was not
  journaled: a restore brought back what it removed. Here the word is found
  in any case and an Arabic word in two spellings; a restore of a copy taken
  before is forgotten again. Negative controls: a fold that only lowers the
  case misses the Arabic spellings; an entry without ids replays nothing.
- **The journal covered only some forgets.** Keeping a turn out wrote the
  ledger and no entry, so a restored copy learned the turn again; a chat
  forgotten and then deleted came back under its other key, because the
  replay asked the chat store for its keys; one failing entry held every
  forget behind it and memory health said nothing. Negative controls: each
  old behaviour, inline.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from kazma_core.memory import forget, forget_journal, memory_stores
from tests._memory_plant import ANSWER, QUESTION, WORD, _everywhere, _plant, open_mem

REPO = Path(__file__).resolve().parents[1]

NAME_SUBJECT = f"{WORD.lower()}_project"
NAME_PREDICATE = f"{WORD.lower()}_status"


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    yield from open_mem(tmp_path, monkeypatch)


def _plant_names(mem) -> str:
    """A fact of the planted turn with the word in its subject and its
    predicate (the audit's "sara" / "diagnosed_with")."""
    from kazma_core.memory.belief_mutation import mutate_belief

    out = mutate_belief(mem.db, NAME_SUBJECT, NAME_PREDICATE, "active", ops_conn=mem.ops,
                        predicate_type="state", extraction_method="user_stated",
                        source_session="th1", source_turn=1)
    mem.db.commit()
    assert out.get("belief_id"), out
    return str(out["belief_id"])


def _plant_all(mem) -> dict:
    planted = _plant(mem)
    planted["named"] = _plant_names(mem)
    before = _everywhere(mem)
    for where in ("beliefs.subject", "beliefs.predicate", "beliefs.object", "beliefs_fts_data.block"):
        assert where in before, (where, sorted(before))
    return planted


_FORGETS = {
    "turn": lambda mem, p: forget.forget_episode(p["eid"], tenant_id="default"),
    "chat by its thread": lambda mem, p: forget.forget_chat("th1", tenant_id="default"),
    "chat by its session": lambda mem, p: forget.forget_chat("s1", tenant_id="default"),
    "everything that mentions it": lambda mem, p: forget.forget_mentions(WORD, tenant_id="default"),
    "everything that mentions it, in capitals": lambda mem, p: forget.forget_mentions(
        WORD.upper(), tenant_id="default"),
    "the source of one of its facts": lambda mem, p: forget.forget_belief_source(
        p["named"], tenant_id="default"),
    "a rewind of the turn": lambda mem, p: forget._forget_rewound_turns(
        "th1", [{"role": "user", "content": QUESTION}, {"role": "assistant", "content": ANSWER}],
        from_turn=1, tenant_id="default"),
}


@pytest.mark.parametrize("kind", sorted(_FORGETS))
def test_every_kind_of_forget_leaves_the_word_nowhere(mem, kind):
    planted = _plant_all(mem)
    out = _FORGETS[kind](mem, planted)
    assert out.get("ok") is not False, out
    assert _everywhere(mem) == set()
    row = mem.db.execute("SELECT subject, predicate, object FROM beliefs WHERE id = ?",
                         (planted["named"],)).fetchone()
    assert tuple(row) == ("", "", "")


def test_negative_control_the_old_scrub_leaves_the_subject_and_predicate(mem, monkeypatch):
    planted = _plant_all(mem)
    monkeypatch.setattr(forget, "_EMPTIED_FACT_COLUMNS", ("object",))
    forget.forget_episode(planted["eid"], tenant_id="default")
    left = _everywhere(mem)
    for where in ("beliefs.subject", "beliefs.predicate"):
        assert where in left, sorted(left)
    assert any(w.startswith("beliefs_fts") for w in left), sorted(left)


# ── "Forget everything that mentions X" ──────────────────────────────────


def _summary(mem, text: str) -> str:
    now = time.time()
    sid = "sum-" + str(abs(hash(text)))[:8]
    mem.db.execute(
        "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, title, "
        "summary_text, status, created_at, updated_at) VALUES (?, 'default', '2026-W46', ?, ?, ?, ?, "
        "'active', ?, ?)", (sid, now - 7 * 86400, now, f"Notes on {text}", f"The week talked about {text}.", now, now))
    mem.db.commit()
    return sid


def test_it_reaches_ended_facts_summaries_and_entities(mem):
    from kazma_core.memory.belief_mutation import mutate_belief

    first = mutate_belief(mem.db, "user", "codeword", f"{WORD}-1", predicate_type="functional",
                          extraction_method="user_stated", source_session="c9", source_turn=4)
    mutate_belief(mem.db, "user", "codeword", "plain", predicate_type="functional",
                  extraction_method="user_stated", source_session="c9", source_turn=5)
    mem.db.commit()
    ended = mem.db.execute("SELECT valid_until FROM beliefs WHERE id = ?", (first["belief_id"],)).fetchone()[0]
    assert ended is not None, "the first value is history now"
    sid = _summary(mem, WORD)
    _plant_names(mem)
    # An entity the extractor named after it (a person: not retired with its
    # last fact), and a grouping on the graph that holds it.
    mem.db.execute("INSERT INTO entities (id, tenant_id, type, name) VALUES (?, 'default', 'person', ?)",
                   (NAME_SUBJECT, f"{WORD} Project"))
    mem.db.execute("INSERT INTO graph_associations (id, tenant_id, group_root, member, created_at) "
                   "VALUES ('assoc_1', 'default', 'user', ?, 1)", (NAME_SUBJECT,))
    mem.db.commit()

    out = forget.forget_mentions(WORD.lower(), tenant_id="default")

    assert out["ok"] and first["belief_id"] in out["facts_invalidated"]
    assert sid in out["summaries_forgotten"] and out["entities_removed"] == [NAME_SUBJECT]
    assert _everywhere(mem) == set()
    row = mem.db.execute("SELECT status, title FROM memory_summaries WHERE id = ?", (sid,)).fetchone()
    assert tuple(row) == ("forgotten", "")


def _arabic_world(mem) -> dict:
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.dual_write import mirror_episode

    plain = mirror_episode(session_id="ar1", turn_number=1,
                           user_text="سجل عندك: مدرسه الامل في الجهراء", assistant_text="تم.")
    vowelled = mirror_episode(session_id="ar2", turn_number=1,
                              user_text="كيف حال مَدْرَسَة الأَمَل اليوم؟", assistant_text="بخير.")
    other = mirror_episode(session_id="ar3", turn_number=1,
                           user_text="ما هي أفضل حديقة في الكويت؟", assistant_text="حديقة الشهيد.")
    fact = mutate_belief(mem.db, "user", "school", "مدرسة الأمل", predicate_type="functional",
                         extraction_method="user_stated", source_session="ar4", source_turn=1)
    mem.db.commit()
    return {"plain": plain, "vowelled": vowelled, "other": other, "fact": fact["belief_id"]}


def _tier(mem, eid: str) -> str:
    return str(mem.db.execute("SELECT tier FROM episodes WHERE id = ?", (eid,)).fetchone()[0])


def test_an_arabic_word_is_found_in_two_spellings(mem):
    w = _arabic_world(mem)
    out = forget.forget_mentions("مدرسة الأمل", tenant_id="default")
    assert out["ok"]
    assert _tier(mem, w["plain"]) == forget.FORGOTTEN_TIER
    assert _tier(mem, w["vowelled"]) == forget.FORGOTTEN_TIER
    assert _tier(mem, w["other"]) != forget.FORGOTTEN_TIER
    row = mem.db.execute("SELECT object FROM beliefs WHERE id = ?", (w["fact"],)).fetchone()
    assert row[0] == ""


def test_negative_control_a_fold_that_only_lowers_case_misses_them(mem, monkeypatch):
    from kazma_core.memory import mention_match

    w = _arabic_world(mem)
    monkeypatch.setattr(mention_match, "_fold", lambda text: str(text or "").casefold())
    forget.forget_mentions("مدرسة الأمل", tenant_id="default")
    assert _tier(mem, w["plain"]) != forget.FORGOTTEN_TIER
    assert _tier(mem, w["vowelled"]) != forget.FORGOTTEN_TIER


def test_kazmas_own_names_for_the_person_are_never_a_mention(mem):
    """"Forget everything that mentions user" is not every fact about the user."""
    from kazma_core.memory.belief_mutation import mutate_belief

    kept = mutate_belief(mem.db, "user", "lives_in", "Lisbon", predicate_type="functional",
                         extraction_method="user_stated", source_session="c1", source_turn=1)
    mem.db.commit()
    out = forget.forget_mentions("user", tenant_id="default")
    assert out["error"] == "nothing_holds_it"
    row = mem.db.execute("SELECT subject, object FROM beliefs WHERE id = ?", (kept["belief_id"],)).fetchone()
    assert tuple(row) == ("user", "Lisbon")


def test_about_me_is_named_and_never_changed(mem):
    from kazma_core.memory.profile import get_about, set_about

    set_about("default", f"My codename is {WORD}.")
    out = forget.forget_mentions(WORD, tenant_id="default")
    assert out["error"] == "nothing_holds_it" and out["about_me_holds_it"] is True
    assert WORD in str(get_about("default"))


# ── A restore of a copy taken before ─────────────────────────────────────


def _snapshot(mem) -> Path:
    copy = mem.dir / "memory_state.before.db"
    dst = sqlite3.connect(str(copy))
    try:
        mem.db.backup(dst)
    finally:
        dst.close()
    return copy


def _restore(mem, copy: Path) -> None:
    """The backup put back over the live database, whole (as
    ``restore_memory_backup`` does; through SQLite, since the writers keep
    the file open)."""
    src = sqlite3.connect(str(copy))
    try:
        src.backup(mem.db)
    finally:
        src.close()


def test_a_restore_forgets_everything_that_mentions_it_again(mem):
    _plant_all(mem)
    _summary(mem, WORD)
    copy = _snapshot(mem)
    assert forget.forget_mentions(WORD, tenant_id="default")["ok"]
    entry = [e for e in forget_journal._entries() if e.kind == "mentions"]
    assert len(entry) == 1 and WORD.lower() not in entry[0].detail.lower()
    _restore(mem, copy)
    assert _everywhere(mem), "the copy holds it again"

    forget.converge()

    assert _everywhere(mem) == set()


def test_negative_control_an_entry_that_names_nothing_replays_nothing(mem, monkeypatch):
    _plant_all(mem)
    copy = _snapshot(mem)
    monkeypatch.setattr(forget, "_journal_detail", lambda found, tenant: {})
    forget.forget_mentions(WORD, tenant_id="default")
    _restore(mem, copy)
    monkeypatch.undo()
    forget.converge()
    assert "beliefs.subject" in _everywhere(mem)


ASKED = f"Forget every memory that mentions {WORD}"


def _kept_out(mem) -> bool:
    conn = forget._open()
    try:
        return forget.refuses_write(conn, tenant_id="default", session_id="th9", turn_number=3,
                                    user_text=ASKED, keys=["th9"])
    finally:
        conn.close()


def test_a_turn_kept_out_stays_out_after_a_restore(mem):
    copy = _snapshot(mem)
    assert forget.keep_turn_out("th9", 3, ASKED, tenant_id="default")
    _restore(mem, copy)
    assert not _kept_out(mem), "the copy predates the keep-out"
    forget.converge()
    assert _kept_out(mem)


def test_negative_control_a_keep_out_with_no_entry_is_lost_by_a_restore(mem, monkeypatch):
    copy = _snapshot(mem)
    monkeypatch.setattr(forget_journal, "append", lambda *a, **k: None)
    forget.keep_turn_out("th9", 3, ASKED, tenant_id="default")
    monkeypatch.undo()
    _restore(mem, copy)
    forget.converge()
    assert not _kept_out(mem)


def _delete_chat(mem) -> None:
    from kazma_ui.session_manager import SessionManager

    store = SessionManager(db_path=str(mem.dir / "chat_sessions.db"))
    try:
        store.delete("s1")
    finally:
        store.close()
    assert forget.chat_keys("s1") == ["s1"], "the store no longer names its thread"


def test_a_chat_forgotten_then_deleted_stays_forgotten_after_a_restore(mem):
    planted = _plant(mem)
    copy = _snapshot(mem)
    assert forget.forget_chat("s1", tenant_id="default")["ok"]
    _delete_chat(mem)
    _restore(mem, copy)
    assert _tier(mem, planted["eid"]) != forget.FORGOTTEN_TIER
    forget.converge()
    assert _tier(mem, planted["eid"]) == forget.FORGOTTEN_TIER


def test_negative_control_without_the_recorded_keys_it_comes_back(mem, monkeypatch):
    planted = _plant(mem)
    copy = _snapshot(mem)
    monkeypatch.setattr(forget_journal, "keys_detail", lambda keys: "")
    forget.forget_chat("s1", tenant_id="default")
    _delete_chat(mem)
    _restore(mem, copy)
    forget.converge()
    assert _tier(mem, planted["eid"]) != forget.FORGOTTEN_TIER


# ── One failing forget holds nothing else; memory health says so ─────────


def _three_entries() -> tuple[int, int, int]:
    a = forget_journal.append("turn", tenant_id="default", session_key="chat-a", turn_number=1,
                              question_sha="a" * 32)
    b = forget_journal.append("turn", tenant_id="default", session_key="chat-b", turn_number=1,
                              question_sha="b" * 32)
    c = forget_journal.append("keep_out", tenant_id="default", session_key="chat-a")
    return a, b, c


def _failing_for_chat_a(monkeypatch) -> None:
    real = forget.replay_turn

    def replay(conn, entry):
        if entry.session_key == "chat-a":
            raise sqlite3.OperationalError("database is locked")
        return real(conn, entry)

    monkeypatch.setattr(forget, "replay_turn", replay)


def _pending(mem) -> set[int]:
    conn = forget._open()
    try:
        return {e.seq for e in forget_journal.pending(conn)}
    finally:
        conn.close()


def test_one_failing_forget_does_not_hold_the_others(mem, monkeypatch):
    from kazma_core.memory.health import _forget_journal_component

    a, b, c = _three_entries()
    _failing_for_chat_a(monkeypatch)
    out = forget_journal.apply_pending()
    assert out["applied"] == 1 and out["failed"] == [a]
    # chat-b's forget is applied; chat-a's and the keep-out after it wait.
    assert _pending(mem) == {a, c}
    state = forget_journal.health()
    assert state["pending"] == 2 and state["failing"][0]["seq"] == a
    assert state["failing"][0]["error"] == "OperationalError"
    comp = _forget_journal_component()
    assert comp["status"] == "warn" and comp["fix"] and "OperationalError" in comp["detail"]


def test_negative_control_the_old_loop_stopped_at_the_first_failure(mem, monkeypatch):
    a, b, c = _three_entries()
    _failing_for_chat_a(monkeypatch)
    conn = forget._open()
    try:
        for e in forget_journal.pending(conn):  # the loop as it was until 2026-11-21
            try:
                forget_journal._apply(conn, e)
                forget_journal.mark_applied(conn, e.seq)
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                break
    finally:
        conn.close()
    assert _pending(mem) == {a, b, c}


def test_memory_health_is_quiet_when_every_forget_is_applied(mem):
    from kazma_core.memory.health import _forget_journal_component

    planted = _plant(mem)
    forget.forget_episode(planted["eid"], tenant_id="default")
    comp = _forget_journal_component()
    assert comp["status"] == "ok" and comp["meta"]["pending"] == 0


# ── Facts forgotten before 2026-11-21 ────────────────────────────────────


def _old_tombstone(mem) -> str:
    from kazma_core.memory.belief_mutation import mutate_belief

    fact = mutate_belief(mem.db, "sara", "diagnosed_with", "celiac disease", predicate_type="set",
                         extraction_method="user_stated", source_session="c2", source_turn=1)
    bid = fact["belief_id"]
    mem.db.execute(
        "UPDATE beliefs SET object = '', embedding = NULL, invalidated_at = 1, "
        "metadata_json = '{\"forgotten\": {\"at\": 1}}' WHERE id = ?", (bid,))
    mem.db.commit()
    return bid


def _index_says_diagnosed(mem) -> bool:
    return bool(mem.db.execute(
        "SELECT COUNT(*) FROM beliefs_fts WHERE beliefs_fts MATCH 'diagnosed*'").fetchone()[0])


def test_an_older_tombstone_loses_its_subject_and_predicate(mem):
    bid = _old_tombstone(mem)
    assert _index_says_diagnosed(mem), "the audit's finding: the index still answers"
    forget.converge()
    row = mem.db.execute("SELECT subject, predicate, object FROM beliefs WHERE id = ?", (bid,)).fetchone()
    assert tuple(row) == ("", "", "")
    assert not _index_says_diagnosed(mem)
    blocks = b"".join(r[0] or b"" for r in mem.db.execute("SELECT block FROM beliefs_fts_data"))
    assert b"diagnosed" not in blocks.lower()


def test_negative_control_without_the_step_the_older_tombstone_keeps_them(mem, monkeypatch):
    bid = _old_tombstone(mem)
    monkeypatch.setattr(forget, "finish_old_tombstones", lambda conn, **k: 0)
    forget.converge()
    row = mem.db.execute("SELECT subject, predicate FROM beliefs WHERE id = ?", (bid,)).fetchone()
    assert tuple(row) == ("sara", "diagnosed_with")


# ── The copies Forget cannot reach are named ─────────────────────────────


def test_the_horizon_names_every_copy_the_audit_found():
    horizon = " ".join(what for what, _until in memory_stores.ERASURE_HORIZON)
    guide = (REPO / "docs" / "docs" / "guide" / "memory-and-rag.md").read_text(encoding="utf-8")
    section = guide[guide.index("**What Forget cannot reach.**"):]
    section = section[: section.index("\n## ")]
    for copy, in_guide in (("Postgres dumps", "Postgres dumps"), ("write-ahead log", "write-ahead log"),
                           ("Neo4j", "Neo4j"), ("migration bundles", "migration bundles"),
                           ("folder the owner chose", "folder you chose")):
        assert copy in horizon, copy
        assert in_guide in section, in_guide


def test_the_journal_never_holds_what_was_forgotten(mem):
    _plant_all(mem)
    forget.forget_mentions(WORD, tenant_id="default")
    conn = sqlite3.connect(str(mem.dir / "memory_forget_journal.db"))
    try:
        rows = conn.execute("SELECT kind, session_key, detail FROM forget_journal").fetchall()
    finally:
        conn.close()
    assert any(r[0] == "mentions" for r in rows)
    assert WORD.lower() not in json.dumps(rows).lower()
