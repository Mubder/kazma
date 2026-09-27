"""Forgetting holds (plan U1, ``kazma_core/memory/forget.py``).

"Nothing lost" protects memories from accidents. A user who takes one back has
decided otherwise, and the paths that rebuild memory must not bring it back:
turn reconcile writes an episode for every stored turn without one, the
recovery pass refills an episode whose text is gone, the legacy restore puts
stranded copies back, and the past-chats search reads the chat store itself.
Each is run here after a forget, and each check has a negative control that
shows the path WOULD have brought the memory back without the ledger.

The last test holds the source: every product site that inserts an episode is
declared, and the ones that write live memory consult the ledger.
"""

from __future__ import annotations

import ast
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from kazma_core.memory import forget, turn_reconcile
from kazma_core.memory.schema_v2 import ensure_primary_schema

REPO = Path(__file__).resolve().parents[1]

WORK_Q = "where do I work these days"
COLOUR_Q = "what is my favourite colour"
OFFICE_Q = "which city is the office in"


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    dual_write._reset_mirror()
    yield SimpleNamespace(dir=tmp_path, db=db)
    dual_write._reset_mirror()
    db.close()


def _iso(days_ago: float) -> str:
    return datetime.fromtimestamp(time.time() - days_ago * 86400, UTC).isoformat()


def _u(text, ts=None):
    return {"role": "user", "content": text, **({"ts": ts} if ts else {})}


def _a(text):
    return {"role": "assistant", "content": text}


def _chat(mem, *, session="s1", thread="th1"):
    """One web chat (two keys), three settled turns, a title that says nothing."""
    from kazma_ui.session_manager import ChatSession, SessionManager

    store = SessionManager(db_path=str(mem.dir / "chat_sessions.db"))
    try:
        store.put(ChatSession(
            session_id=session, tenant_id="default", thread_id=thread, title="Notes",
            messages=[
                _u(WORK_Q, _iso(40)), _a("At Acme, in Lisbon."),
                _u(COLOUR_Q, _iso(40)), _a("Teal."),
                _u(OFFICE_Q, _iso(39)), _a("Lisbon."),
            ],
        ))
    finally:
        store.close()


def _live_turn(turn=1, question=WORK_Q, answer="At Acme, in Lisbon.", key="th1"):
    """What the live path writes when the turn closes: the thread key."""
    from kazma_core.memory.dual_write import mirror_episode

    return mirror_episode(session_id=key, turn_number=turn, user_text=question,
                          assistant_text=answer, created_at=time.time() - 40 * 86400)


def _texts(mem, question):
    return [r[0] for r in mem.db.execute(
        "SELECT tier FROM episodes WHERE user_text = ?", (question,))]


def _reconcile(mem):
    return turn_reconcile._reconcile(mem.db, ("", ""), time.monotonic() + 60, time.time())


# ── The memory itself ──────────────────────────────────────────────────────


def test_a_forgotten_memory_is_not_recalled_and_keeps_nothing(mem):
    from kazma_core.memory.recall import recall

    _chat(mem)
    eid = _live_turn()
    found = recall(WORK_Q, conn=mem.db, tenant_id="default", local_only=True)
    assert eid in {h.id for h in found.episodes}  # negative control: recalled before

    out = forget.forget_episode(eid, tenant_id="default")
    assert out["ok"] and out["copies"] == 1

    after = recall(WORK_Q, conn=mem.db, tenant_id="default", local_only=True)
    assert eid not in {h.id for h in after.episodes}
    row = mem.db.execute(
        "SELECT user_text, assistant_text, summary_text, embedding, tier, metadata_json "
        "FROM episodes WHERE id = ?", (eid,)).fetchone()
    assert (row[0], row[1], row[2], row[3], row[4]) == ("", "", "", None, forget.FORGOTTEN_TIER)
    assert "Acme" not in row[5] and WORK_Q not in row[5]


def test_every_copy_of_the_turn_goes(mem):
    """The live write uses the thread key, turn reconcile the session's."""
    _chat(mem)
    first = _live_turn(key="th1")
    second = _live_turn(key="s1")
    assert first != second
    out = forget.forget_episode(first, tenant_id="default")
    assert out["copies"] == 2
    assert _texts(mem, WORK_Q) == []


def test_another_tenant_cannot_forget_it(mem):
    _chat(mem)
    eid = _live_turn()
    assert forget.forget_episode(eid, tenant_id="alpha") == {"ok": False, "error": "not_found"}
    assert _texts(mem, WORK_Q) == ["working"]  # the live write's tier, untouched


def test_the_facts_a_forgotten_turn_produced_lose_their_value(mem):
    from kazma_core.memory.belief_mutation import mutate_belief

    _chat(mem)
    eid = _live_turn()
    mutate_belief(mem.db, "user", "works_at", "acme", predicate_type="functional",
                  source_session="th1", source_turn=1, extraction_method="user_explicit")
    mutate_belief(mem.db, "user", "favourite_colour", "teal", predicate_type="functional",
                  source_session="th1", source_turn=2, extraction_method="user_explicit")

    assert forget.forget_episode(eid, tenant_id="default")["facts_forgotten"] == 1
    facts = {r[0]: r for r in mem.db.execute(
        "SELECT predicate, object, invalidated_at, metadata_json FROM beliefs")}
    assert facts["works_at"][1] == "" and facts["works_at"][2] is not None
    assert "forgotten" in json.loads(facts["works_at"][3])
    assert facts["favourite_colour"][1] == "teal" and facts["favourite_colour"][2] is None


def test_forgetting_again_finishes_a_forget_cut_short(mem):
    """``invalidate_belief`` commits as it goes: a forget can stop with the
    episode done and a fact not. Asking again finishes it."""
    from kazma_core.memory.belief_mutation import mutate_belief

    _chat(mem)
    eid = _live_turn()
    forget.forget_episode(eid, tenant_id="default")
    mutate_belief(mem.db, "user", "employer_city", "lisbon", predicate_type="functional",
                  source_session="th1", source_turn=1, extraction_method="llm_inferred")

    again = forget.forget_episode(eid, tenant_id="default")
    assert again["already"] and again["facts_forgotten"] == 1
    assert mem.db.execute("SELECT object FROM beliefs WHERE predicate = 'employer_city'").fetchone()[0] == ""


# ── The paths that rebuild memory ─────────────────────────────────────────


def test_turn_reconcile_does_not_bring_it_back(mem):
    """The live path wrote the turn under the session id; reconcile writes
    under the thread id -- a different episode id, which the tombstone cannot
    hold. The ledger, written under every key of the chat, does."""
    _chat(mem)
    forget.forget_episode(_live_turn(key="s1"), tenant_id="default")
    report = _reconcile(mem)
    assert report["turns_written"] == 2  # turns 2 and 3, never turn 1
    assert _texts(mem, WORK_Q) == []
    assert _texts(mem, COLOUR_Q) == ["episodic"]


def test_turn_reconcile_would_have_without_the_ledger(mem):
    """Negative control: the tombstone alone does not hold the turn."""
    _chat(mem)
    forget.forget_episode(_live_turn(key="s1"), tenant_id="default")
    mem.db.execute("DELETE FROM memory_forgotten")
    mem.db.commit()
    _reconcile(mem)
    assert _texts(mem, WORK_Q) == ["episodic"]


def test_the_same_question_asked_again_later_is_remembered(mem):
    _chat(mem)
    forget.forget_episode(_live_turn(turn=1), tenant_id="default")
    assert _live_turn(turn=4) is not None
    assert _texts(mem, WORK_Q) == ["working"]


def test_recovery_never_refills_a_forgotten_memory(mem):
    from kazma_core.memory.rehydrate import ERASED_SQL

    _chat(mem)
    eid = _live_turn()
    forget.forget_episode(eid, tenant_id="default")
    # Negative control: an erased row (the old archive rule's NULL pair) is picked.
    mem.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
        "tier, created_at) VALUES ('e_erased', 'default', 'th9', 1, NULL, NULL, 'archived', 1.0)")
    mem.db.commit()
    picked = {r[0] for r in mem.db.execute(f"SELECT id FROM episodes WHERE {ERASED_SQL}")}
    assert "e_erased" in picked and eid not in picked
    # Even if a tombstone ever held NULL text, its tier keeps it out.
    mem.db.execute("UPDATE episodes SET user_text = NULL, assistant_text = NULL WHERE id = ?", (eid,))
    assert eid not in {r[0] for r in mem.db.execute(f"SELECT id FROM episodes WHERE {ERASED_SQL}")}


def _legacy_copy(mem, question):
    """A stranded copy of the turn in the 2026-08-02 legacy table."""
    from kazma_core.memory.legacy_tables import LEGACY_EPISODE_TABLE

    cols = [r[1] for r in mem.db.execute("PRAGMA table_info(episodes)")]
    mem.db.execute(f"CREATE TABLE {LEGACY_EPISODE_TABLE} AS SELECT * FROM episodes WHERE 0")
    mem.db.execute(f"ALTER TABLE {LEGACY_EPISODE_TABLE} ADD COLUMN archived_at REAL")
    values = {c: None for c in cols}
    values.update(id="legacy-1", tenant_id="default", session_id="th1", turn_number=1,
                  user_text=question, assistant_text="At Acme, in Lisbon.", tier="archived",
                  created_at=1.0, metadata_json="{}", structural_importance=1)
    mem.db.execute(
        f"INSERT INTO {LEGACY_EPISODE_TABLE} ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
        list(values.values()))
    mem.db.commit()


def test_a_stranded_copy_of_a_forgotten_turn_stays_stranded(mem):
    from kazma_core.memory.legacy_tables import restore_legacy_episode_archive

    _chat(mem)
    forget.forget_episode(_live_turn(), tenant_id="default")
    _legacy_copy(mem, WORK_Q)
    report = restore_legacy_episode_archive(mem.db)
    assert report["forgotten"] == 1 and report["restored"] == 0
    assert _texts(mem, WORK_Q) == []


def test_the_legacy_restore_would_have_without_the_ledger(mem):
    """Negative control: the forgotten row's text is gone, so nothing but the
    ledger tells the restore the turn is held."""
    from kazma_core.memory.legacy_tables import restore_legacy_episode_archive

    _chat(mem)
    forget.forget_episode(_live_turn(), tenant_id="default")
    mem.db.execute("DELETE FROM memory_forgotten")
    mem.db.commit()
    _legacy_copy(mem, WORK_Q)
    assert restore_legacy_episode_archive(mem.db)["restored"] == 1


def test_past_chats_search_leaves_a_forgotten_turn_out(mem):
    from kazma_core.memory.transcript_recall import search_transcripts

    _chat(mem)
    eid = _live_turn()
    assert search_transcripts(WORK_Q, tenant_id="default")  # negative control: found before
    forget.forget_episode(eid, tenant_id="default")
    assert search_transcripts(WORK_Q, tenant_id="default") == []
    hits = search_transcripts(COLOUR_Q, tenant_id="default")
    assert hits and "Acme" not in hits[0]["snippet"]


# ── A chat kept out of memory ─────────────────────────────────────────────


def test_a_chat_kept_out_is_neither_written_nor_searched(mem):
    from kazma_core.memory.transcript_recall import search_transcripts

    _chat(mem)
    out = forget.set_chat_remembered("s1", False, tenant_id="default")
    assert out["ok"] and set(out["chat_keys"]) == {"s1", "th1"}
    assert not forget.chat_remembered("th1", tenant_id="default")

    assert _live_turn() is None  # the writer refuses, under either key
    assert _live_turn(key="s1") is None
    assert _reconcile(mem)["turns_written"] == 0
    assert search_transcripts(COLOUR_Q, tenant_id="default") == []

    # Let back in (the negative control): the next turn is remembered again.
    forget.set_chat_remembered("th1", True, tenant_id="default")
    assert forget.chat_remembered("s1", tenant_id="default")
    assert _live_turn() is not None
    assert search_transcripts(COLOUR_Q, tenant_id="default")


def test_a_chat_kept_out_gives_the_post_turn_worker_nothing(mem):
    from kazma_core.memory import consolidator

    _chat(mem)
    forget.set_chat_remembered("s1", False, tenant_id="default")
    messages = [_u("my name is Dana"), _a("Nice to meet you, Dana.")]
    assert consolidator._run_turn_memory(messages, session_id="th1", turn=1) is True
    assert mem.db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0
    assert mem.db.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0] == 0


def test_chat_apps_turn_a_chats_memory_off_and_on(mem):
    """``/memory off`` / ``/memory on`` through the gateway's slash resolver."""
    from kazma_gateway.slash_commands import resolve_slash_command

    _chat(mem)
    _live_turn()
    ctx = {"thread_id": "th1", "memory_tenant": "default", "memory_count": 1}
    off = resolve_slash_command("/memory off", context=ctx)
    assert "won't remember" in off and "Forgot 1 memory" in off
    assert not forget.chat_remembered("s1", tenant_id="default")
    assert _live_turn(turn=2, question=COLOUR_Q, answer="Teal.") is None
    assert "not remembered" in resolve_slash_command("/memory", context=ctx)

    assert "remembers this chat again" in resolve_slash_command("/memory on", context=ctx)
    assert _live_turn(turn=2, question=COLOUR_Q, answer="Teal.") is not None
    assert "is remembered" in resolve_slash_command("/memory", context=ctx)


def test_forget_past_forgets_what_the_chat_left(mem):
    _chat(mem)
    _live_turn(turn=1)
    _live_turn(turn=2, question=COLOUR_Q, answer="Teal.")
    out = forget.forget_chat("s1", tenant_id="default")
    assert out["forgotten"] == 2
    assert mem.db.execute(
        "SELECT COUNT(*) FROM episodes WHERE tier != ?", (forget.FORGOTTEN_TIER,)).fetchone()[0] == 0


# ── The source: every episode insert is declared ──────────────────────────

#: (file, innermost function) -> why a forgotten memory cannot come back through it.
EPISODE_INSERTS = {
    ("kazma-core/kazma_core/memory/dual_write.py", "mirror_episode"):
        "the one writer: asks the ledger before it writes anything",
    ("kazma-core/kazma_core/memory/swarm_bridge.py", "_insert_episode"):
        "asks the ledger first; its embedding update skips a tombstone",
    ("kazma-core/kazma_core/memory/legacy_tables.py", "restore_legacy_episode_archive"):
        "asks the ledger for every stranded row (_forgotten)",
    ("kazma-core/kazma_core/memory/backfill_v2.py", "_backfill_memories_to_episodes"):
        "the one-off V1 migration: stable ids from the legacy row, and a forgotten "
        "memory keeps its id, so INSERT OR IGNORE leaves the tombstone as it is",
    ("kazma-core/kazma_core/memory/eval_golden.py", "_seed_episode"):
        "the golden eval's private database",
    ("kazma-core/kazma_core/memory/benchmark.py", "_seed"):
        "the benchmark's private database",
}
#: The writers of live memory: each asks the ledger.
_LEDGER_CHECKS = {"refuses_write", "_forgotten"}
_LIVE_WRITERS = {
    ("kazma-core/kazma_core/memory/dual_write.py", "mirror_episode"),
    ("kazma-core/kazma_core/memory/swarm_bridge.py", "_insert_episode"),
    ("kazma-core/kazma_core/memory/legacy_tables.py", "restore_legacy_episode_archive"),
}


def _episode_inserts(sources: dict[str, str]) -> dict[tuple[str, str], ast.AST]:
    """(file, innermost function) -> that function, for every SQL string that
    inserts into ``episodes``."""
    import re

    pattern = re.compile(r"INSERT\s+(OR\s+\w+\s+)?INTO\s+episodes\b", re.IGNORECASE)
    found: dict[tuple[str, str], ast.AST] = {}
    for rel, text in sources.items():
        tree = ast.parse(text)

        def visit(node: ast.AST, fn: ast.AST | None, rel: str = rel) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(child, child)
                    continue
                if (fn is not None and isinstance(child, ast.Constant)
                        and isinstance(child.value, str) and pattern.search(child.value)):
                    found[(rel, fn.name)] = fn
                visit(child, fn)

        visit(tree, None)
    return found


def _product_sources() -> dict[str, str]:
    out = {}
    for pkg in REPO.glob("kazma-*/kazma_*"):
        for path in pkg.rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if "/tests/" not in rel:
                out[rel] = path.read_text(encoding="utf-8", errors="replace")
    return out


def _calls(fn: ast.AST) -> set[str]:
    return {
        n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
        for n in ast.walk(fn) if isinstance(n, ast.Call)
    }


def test_every_episode_insert_is_declared_and_live_writers_ask_the_ledger():
    found = _episode_inserts(_product_sources())
    undeclared = sorted(set(found) - set(EPISODE_INSERTS))
    assert not undeclared, (
        "An episode is inserted where nothing says a forgotten memory cannot come "
        f"back through it (plan U1). Ask forget.refuses_write, or declare why not: {undeclared}"
    )
    assert set(EPISODE_INSERTS) <= set(found), "a declared insert site is gone"
    for key in _LIVE_WRITERS:
        assert _calls(found[key]) & _LEDGER_CHECKS, f"{key} writes live memory without the ledger"


def test_the_source_gate_sees_an_unchecked_writer():
    """Negative control: a new writer that inserts without asking the ledger."""
    rogue = {"kazma-core/kazma_core/memory/rogue.py": (
        "def write(conn, text):\n"
        "    conn.execute('INSERT OR IGNORE INTO episodes (id, user_text) VALUES (?, ?)', ('x', text))\n"
    )}
    found = _episode_inserts(rogue)
    assert set(found) == {("kazma-core/kazma_core/memory/rogue.py", "write")}
    assert not (_calls(found[("kazma-core/kazma_core/memory/rogue.py", "write")]) & _LEDGER_CHECKS)
