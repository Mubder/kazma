"""Recovering what the pre-2026-09-26 archive rule erased -- verified text only.

The rule nulled an archived memory's question and answer and kept a stub (76
memories on the live install). ``memory/rehydrate.py`` restores the text from
the sources that still hold it -- an earlier backup of the memory database,
the chat store, LangGraph checkpoint history, the note a ``memory_store``
call left, the knowledge library -- and only text the memory provably held.

Erased rows are built here the way they came to be: the real writer's id,
then the old archive statement, verbatim. Each source is written by its real
writer (SessionManager, a LangGraph SqliteSaver, the knowledge promoter, the
swarm bridge).
"""

from __future__ import annotations

import json
import random
import sqlite3
import time
import uuid
from types import SimpleNamespace

import pytest

from kazma_core.memory import chat_history, rehydrate
from kazma_core.memory.dual_write import _episode_id
from kazma_core.memory.rehydrate import erased_counts
from kazma_core.memory.schema_v2 import ensure_primary_schema

#: The archive statement until 2026-09-26, verbatim.
OLD_ARCHIVE_SQL = (
    "UPDATE episodes SET tier='archived', "
    "summary_text=COALESCE(NULLIF(TRIM(summary_text), ''), TRIM("
    "SUBSTR(COALESCE(user_text, ''), 1, 200) || "
    "CASE WHEN TRIM(COALESCE(user_text, '')) <> '' "
    "AND TRIM(COALESCE(assistant_text, '')) <> '' THEN ' — ' ELSE '' END || "
    "SUBSTR(COALESCE(assistant_text, ''), 1, 300))), "
    "user_text=NULL, assistant_text=NULL WHERE id=?"
)


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_BACKUPS_DIR", str(tmp_path / "backups"))
    (tmp_path / "backups").mkdir()
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    yield SimpleNamespace(dir=tmp_path, db=db)
    db.close()


def _turn(db, session, turn, question, answer, *, tenant="default", source="dual_write_mirror"):
    """A chat turn exactly as ``dual_write.mirror_episode`` stores one (the
    turn's texts come stripped from ``consolidator.extract_turn_texts``)."""
    question, answer = question.strip(), answer.strip()
    eid = _episode_id(session, turn, (question or answer).strip())
    db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, "
        "assistant_text, summary_text, tier, structural_importance, created_at, metadata_json) "
        "VALUES (?,?,?,?,?,?,'','episodic',1,?,?)",
        (eid, tenant, session, turn, question[:4000], answer[:4000],
         time.time() - 86400 * 60, json.dumps({"source": source})),
    )
    db.commit()
    return eid


def _erase(db, *ids):
    for eid in ids:
        db.execute(OLD_ARCHIVE_SQL, (eid,))
    db.commit()


def _row(db, eid):
    return db.execute("SELECT * FROM episodes WHERE id = ?", (eid,)).fetchone()


def _meta(row):
    return json.loads(row["metadata_json"] or "{}")


def _chat(mem, session_id, messages, *, thread_id="", tenant="default"):
    """A conversation as the web UI's SessionManager saves it."""
    from kazma_ui.session_manager import ChatSession, SessionManager

    store = SessionManager(db_path=str(mem.dir / "chat_sessions.db"))
    try:
        store.put(ChatSession(session_id=session_id, tenant_id=tenant, thread_id=thread_id,
                              messages=list(messages)))
    finally:
        store.close()


def _checkpoint(thread_id, messages):
    """One real LangGraph checkpoint of *messages* in the SQLite saver."""
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.sqlite import SqliteSaver

    from kazma_core.checkpoint_serde import kazma_checkpoint_serde
    from kazma_core.paths import checkpoints_db

    conn = sqlite3.connect(checkpoints_db(), check_same_thread=False)
    try:
        saver = SqliteSaver(conn, serde=kazma_checkpoint_serde())
        ckpt = empty_checkpoint()
        ckpt["channel_values"] = {"messages": list(messages)}
        ckpt["channel_versions"] = {"messages": 1}
        saver.put({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}, ckpt,
                  {"source": "loop", "step": 1}, {"messages": 1})
    finally:
        conn.close()


def _pair(question, answer):
    return [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]


# ── The stub rule, twin of the old SQL ────────────────────────────────────


def test_archive_stub_is_the_old_sql_expression():
    """The recovery trusts this twin; it is checked against the SQL itself."""
    rnd = random.Random(20260926)
    alphabet = "ab cd\n\tzé  كتاب —😀x"
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE episodes (id TEXT, tier TEXT, user_text TEXT, assistant_text TEXT, summary_text TEXT)"
    )

    def text():
        choice = rnd.random()
        if choice < 0.08:
            return None
        return "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 700)))

    naive_differs = False
    for i in range(400):
        user, answer = text(), text()
        summary = rnd.choice([None, "", "  ", text()])
        db.execute("INSERT INTO episodes VALUES (?,?,?,?,?)", (str(i), "episodic", user, answer, summary))
        db.execute(OLD_ARCHIVE_SQL, (str(i),))
        stored = db.execute("SELECT summary_text FROM episodes WHERE id = ?", (str(i),)).fetchone()[0]
        assert rehydrate._archive_stub(user, answer, summary) == stored, (user, answer, summary)
        naive = ((user or "")[:200] + " — " + (answer or "")[:300]).strip()
        naive_differs = naive_differs or (naive != stored)
    assert naive_differs  # negative control: an approximate twin does not survive this


# ── Sources ───────────────────────────────────────────────────────────────


def test_a_memory_is_restored_from_an_earlier_backup_of_the_database(mem):
    kept = _turn(mem.db, "s1", 1, "Where did I park at the airport?", "Level P3, bay 41.")
    edited = _turn(mem.db, "s1", 2, "What is the gate code?", "4471#")
    backup = sqlite3.connect(str(mem.dir / "backups" / "memory_state_1786000000.db"))
    mem.db.backup(backup)
    backup.close()
    # After the backup the second answer was corrected, then both were erased:
    # the backup's copy of it is not the text that was erased.
    mem.db.execute("UPDATE episodes SET assistant_text = '4417#' WHERE id = ?", (edited,))
    mem.db.commit()
    _erase(mem.db, kept, edited)

    report = rehydrate._rehydrate_erased(mem.db)
    row = _row(mem.db, kept)
    assert (row["user_text"], row["assistant_text"]) == (
        "Where did I park at the airport?", "Level P3, bay 41.")
    assert _meta(row)["rehydrated"]["sources"] == ["backup"]
    assert _meta(row)["rehydrated"]["verified"] == "row+stub"
    assert _row(mem.db, edited)["user_text"] is None
    assert (report["restored"], report["unrecovered"]) == (1, 1)
    assert not list((mem.dir / "backups").glob("*-wal"))  # read without a trace


@pytest.mark.postgres
def test_a_turn_is_restored_from_the_chat_store(mem):
    session = f"chat-{uuid.uuid4().hex[:8]}"
    question = "Remind me what we named the second cat? " + "Details. " * 40
    answer = "You named her Saffron, after the colour of her eyes. " + "More. " * 80
    _chat(mem, session, [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
                         *_pair(question, answer), {"role": "user", "content": "thanks"}])
    eid = _turn(mem.db, session, 3, question, answer)
    _erase(mem.db, eid)

    rehydrate._rehydrate_erased(mem.db)
    row = _row(mem.db, eid)
    assert (row["user_text"], row["assistant_text"]) == (question.strip(), answer.strip())
    assert _meta(row)["rehydrated"] | {"at": 0} == {
        "version": rehydrate.REHYDRATE_VERSION, "at": 0, "sources": ["chat_store"],
        "verified": "id+stub", "answer": "full",
    }


def test_a_turn_is_restored_from_checkpoint_history(mem):
    from langchain_core.messages import AIMessage, HumanMessage

    thread = "thread-ck"
    question, answer = "Which dentist did I pick?", "Dr. Hamad at Salmiya, Tuesdays."
    _checkpoint(thread, [HumanMessage(question), AIMessage(answer)])
    eid = _turn(mem.db, thread, 1, question, answer)
    _erase(mem.db, eid)

    rehydrate._rehydrate_erased(mem.db)
    row = _row(mem.db, eid)
    assert (row["user_text"], row["assistant_text"]) == (question, answer)
    assert _meta(row)["rehydrated"]["sources"] == ["checkpoint"]


def test_a_rerun_turn_gets_its_question_and_the_answer_the_stub_kept(mem):
    """The stored conversation now holds a different answer (the turn was
    re-run). The question is proven by the id; the answer is restored only
    where the stub kept all of it."""
    short_q, short_a = "What time is the recital?", "Seven, doors at 6:30."
    long_q, long_a = "Summarise the lease terms", "Clause " * 80
    _chat(mem, "rerun", [*_pair(short_q, "It moved; I will check."),
                         *_pair(long_q, "A different summary.")])
    short = _turn(mem.db, "rerun", 1, short_q, short_a)
    long = _turn(mem.db, "rerun", 2, long_q, long_a)
    _erase(mem.db, short, long)

    report = rehydrate._rehydrate_erased(mem.db)
    row = _row(mem.db, short)
    assert (row["user_text"], row["assistant_text"]) == (short_q, short_a)
    assert _meta(row)["rehydrated"]["answer"] == "whole_in_stub"
    row = _row(mem.db, long)
    assert (row["user_text"], row["assistant_text"]) == (long_q, None)
    assert _meta(row)["rehydrated"]["answer"] == "stub_only"
    assert row["summary_text"].startswith(long_q + " — Clause")  # the answer's start stays
    assert report["restored_question_only"] == 2


def _note(db, text):
    """A note as the memory_store tool saves it (session 'memory_store', turn 0)."""
    eid = _episode_id("memory_store", 0, text.strip())
    db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, "
        "assistant_text, summary_text, tier, structural_importance, created_at, metadata_json) "
        "VALUES (?, 'default', 'memory_store', 0, ?, '', '', 'episodic', 1, ?, ?)",
        (eid, text[:4000], time.time(), json.dumps({"source": "memory_store_tool"})),
    )
    db.commit()
    return eid


def test_a_note_is_restored_from_the_belief_it_left(mem):
    live = "The car insurance renews on the 3rd of March with Gulf Insurance. " * 4
    moved = "Passport number is kept in the blue folder, top drawer. " * 4
    now = time.time()
    mem.db.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, "
        "confidence, structural_importance, source_trust_weight, valid_from, ingested_at) "
        "VALUES ('b1', 'default', 'user', 'noted', 'set', ?, 1.0, 5, 1.0, ?, ?)",
        (live[:1000], now, now),
    )
    mem.db.execute(
        "INSERT INTO beliefs_archive (id, tenant_id, original_belief_json, archived_at) "
        "VALUES ('b2', 'default', ?, ?)",
        (json.dumps({"subject": "user", "predicate": "noted", "object": moved[:1000]}), now),
    )
    first, second = _note(mem.db, live), _note(mem.db, moved)
    _erase(mem.db, first, second)

    rehydrate._rehydrate_erased(mem.db)
    assert _row(mem.db, first)["user_text"] == live
    assert _row(mem.db, second)["user_text"] == moved
    assert _meta(_row(mem.db, second))["rehydrated"]["sources"] == ["noted_belief"]


def test_a_long_note_is_found_in_the_call_that_saved_it(mem):
    """A note of 1,000+ characters left only its first 1,000 in its belief."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    note = "Project Falcon decisions: " + "; ".join(f"item {i} agreed" for i in range(120))
    assert len(note) > 1000
    now = time.time()
    mem.db.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, "
        "confidence, structural_importance, source_trust_weight, valid_from, ingested_at) "
        "VALUES ('b1', 'default', 'user', 'noted', 'set', ?, 1.0, 5, 1.0, ?, ?)",
        (note[:1000], now, now),
    )
    _checkpoint("some-other-chat", [
        HumanMessage("save the falcon decisions"),
        AIMessage("", tool_calls=[{"name": "memory_store", "args": {"text": note}, "id": "c1"}]),
        ToolMessage("Stored memory", tool_call_id="c1", name="memory_store"),
        AIMessage("Saved."),
    ])
    eid = _note(mem.db, note)
    _erase(mem.db, eid)

    report = rehydrate._rehydrate_erased(mem.db)
    assert _row(mem.db, eid)["user_text"] == note
    assert report["sources"] == {"checkpoint_note": 1}
    assert report["note_scan_done"] is True


def test_the_note_scan_resumes_where_it_stopped(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(rehydrate, "time", SimpleNamespace(monotonic=lambda: clock["now"], time=time.time))
    threads = ["t-a", "t-b"]
    monkeypatch.setattr(chat_history, "threads_containing",
                        lambda mark, *, after="", strict=False: [t for t in threads if t > after])

    def versions(thread, *, containing=None, strict=False):
        clock["now"] += 5  # reading a thread takes the pass past its deadline
        return [[{"role": "assistant", "content": "",
                  "tool_calls": [{"name": "memory_store", "args": {"text": f"note in {thread}"}}]}]]

    monkeypatch.setattr(chat_history, "checkpoint_message_versions", versions)
    row = {"id": "e1", "summary_text": "note in t-b"}
    cands: dict = {"e1": set()}
    assert rehydrate._scan_notes([row], cands, "", deadline=1.0) == (False, "t-a")
    assert cands["e1"] == set()
    assert rehydrate._scan_notes([row], cands, "t-a", deadline=clock["now"] + 1) == (True, "")
    assert cands["e1"] == {rehydrate._Candidate(user="note in t-b", source="checkpoint_note")}


def test_a_knowledge_excerpt_is_restored_from_the_library(mem, monkeypatch):
    import kazma_core.stores.knowledge as knowledge
    from kazma_core.memory import dual_write
    from kazma_core.memory.federated_search import promote_kb_hits_to_episodes

    store = knowledge.KnowledgeStore(db_path=str(mem.dir / "kb.db"))
    monkeypatch.setattr(knowledge, "_knowledge_store", store)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    content = "Webhooks retry for 7 days with exponential backoff. " * 40
    store.create_library("wa-docs", "WhatsApp docs")
    store.upsert_chunk({"library_id": "wa-docs", "source_url": "https://example.test/webhooks",
                        "document_title": "Webhooks", "chunk_index": 0,
                        "content_hash": "h1", "content": content})
    dual_write._reset_mirror()
    try:
        assert promote_kb_hits_to_episodes(
            [{"store": "knowledge", "content": content,
              "provenance": {"document_title": "Webhooks"}}],
            session_id="kb-session",
        ) == 1
    finally:
        dual_write._reset_mirror()
        store.close()
    eid = mem.db.execute("SELECT id FROM episodes").fetchone()[0]
    original = _row(mem.db, eid)["user_text"]
    _erase(mem.db, eid)

    store = knowledge.KnowledgeStore(db_path=str(mem.dir / "kb.db"))
    monkeypatch.setattr(knowledge, "_knowledge_store", store)
    try:
        rehydrate._rehydrate_erased(mem.db)
    finally:
        store.close()
    assert _row(mem.db, eid)["user_text"] == original
    assert _meta(_row(mem.db, eid))["rehydrated"]["sources"] == ["knowledge_library"]


# ── Verdicts ──────────────────────────────────────────────────────────────


def test_a_compaction_summary_lost_nothing(mem):
    from kazma_core.memory.swarm_bridge import _insert_episode

    eid = _insert_episode(mem.db, session_id="compaction", turn_number=5, user_text="",
                          summary_text="We agreed to ship on Friday.", source="compaction_summary",
                          importance=2, metadata={})
    mem.db.execute("UPDATE episodes SET tier = 'archived' WHERE id = ?", (eid,))
    mem.db.commit()
    report = rehydrate._rehydrate_erased(mem.db)
    row = _row(mem.db, eid)
    assert report["nothing_lost"] == 1
    assert row["user_text"] is None and _meta(row)["rehydrate"]["result"] == "nothing_lost"
    assert erased_counts(mem.db) == {"pending": 0, "unrecovered": 0, "restored": 0,
                                     "restored_question_only": 0}


def test_an_unrecoverable_memory_keeps_its_stub_and_is_searched_once_per_version(mem, monkeypatch):
    eid = _turn(mem.db, "gone", 1, "a question nobody stored", "an answer nobody stored")
    _erase(mem.db, eid)
    first = rehydrate._rehydrate_erased(mem.db)
    assert first["unrecovered"] == 1
    row = _row(mem.db, eid)
    assert row["summary_text"] == "a question nobody stored — an answer nobody stored"
    assert erased_counts(mem.db)["unrecovered"] == 1
    assert rehydrate._rehydrate_erased(mem.db)["candidates"] == 0
    monkeypatch.setattr(rehydrate, "REHYDRATE_VERSION", rehydrate.REHYDRATE_VERSION + 1)
    assert rehydrate._rehydrate_erased(mem.db)["candidates"] == 1  # a better rule looks again


def test_a_source_that_cannot_be_read_leaves_the_row_pending(mem, monkeypatch):
    question, answer = "Who is my landlord?", "Mr. Al-Sabah, 9999 1234."
    _chat(mem, "flaky", _pair(question, answer))
    eid = _turn(mem.db, "flaky", 1, question, answer)
    _erase(mem.db, eid)

    def down(key, *, sqlite_path=None, strict=False):
        raise sqlite3.OperationalError("database is locked")

    real = chat_history.conversations_for
    monkeypatch.setattr(chat_history, "conversations_for", down)
    report = rehydrate._rehydrate_erased(mem.db)
    assert (report["pending"], report["unrecovered"]) == (1, 0)
    assert "rehydrate" not in _meta(_row(mem.db, eid))  # no verdict from an outage

    monkeypatch.setattr(chat_history, "conversations_for", real)
    rehydrate._rehydrate_erased(mem.db)
    assert _row(mem.db, eid)["user_text"] == question


def test_one_unreadable_source_does_not_discard_what_another_proved(mem, monkeypatch):
    """The chat store proves one turn; checkpoint history cannot be read. The
    proven turn is restored; the unproven one waits instead of being judged."""
    question, answer = "Where is the spare tyre?", "Under the boot floor."
    _chat(mem, "half", _pair(question, answer))
    proven = _turn(mem.db, "half", 1, question, answer)
    unproven = _turn(mem.db, "half", 2, "only in checkpoints", "which are down")
    _erase(mem.db, proven, unproven)

    def down(thread, *, containing=None, strict=False):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(chat_history, "checkpoint_message_versions", down)
    report = rehydrate._rehydrate_erased(mem.db)
    assert _row(mem.db, proven)["user_text"] == question
    assert (report["restored"], report["pending"], report["unrecovered"]) == (1, 1, 0)
    assert "rehydrate" not in _meta(_row(mem.db, unproven))


def test_sources_that_disagree_restore_nothing(mem):
    """Two stored questions share the 512 characters the id covers and the 200
    the stub kept, then differ: either could be the one erased."""
    from langchain_core.messages import AIMessage, HumanMessage

    head = "Here is the full plan for the move " * 16
    assert len(head) > 520
    first, second = head + "ending A", head + "ending B"
    _chat(mem, "split", _pair(first, "OK"))
    _checkpoint("split", [HumanMessage(second), AIMessage("OK")])
    eid = _turn(mem.db, "split", 1, first, "OK")
    _erase(mem.db, eid)

    report = rehydrate._rehydrate_erased(mem.db)
    assert report["ambiguous"] == 1
    assert _row(mem.db, eid)["user_text"] is None


def test_text_that_exists_is_never_overwritten(mem):
    eid = _turn(mem.db, "s", 1, "original question", "original answer")
    decision = {"result": "restored", "user": "other", "assistant": "other", "sources": ["x"],
                "verified": "id+stub", "answer": "full"}
    assert rehydrate._restore(mem.db, _row(mem.db, eid), decision) is False
    assert _row(mem.db, eid)["user_text"] == "original question"


def test_every_tenant_is_recovered_in_one_pass(mem):
    ids = []
    for tenant in ("default", "acme"):
        question, answer = f"{tenant}: what is the wifi?", f"{tenant}-net / pass123"
        _chat(mem, f"t-{tenant}", _pair(question, answer), tenant=tenant)
        ids.append(_turn(mem.db, f"t-{tenant}", 1, question, answer, tenant=tenant))
    _erase(mem.db, *ids)
    assert rehydrate._rehydrate_erased(mem.db)["restored"] == 2


def test_the_pass_reports_and_saves_its_state(mem):
    from kazma_core.config_store import get_config_store

    question, answer = "Which gym did I join?", "Oxygen, Salmiya branch."
    _chat(mem, "gym", _pair(question, answer))
    eid = _turn(mem.db, "gym", 1, question, answer)
    _erase(mem.db, eid)
    report = rehydrate.run_rehydrate_pass()
    assert report["restored"] == 1
    state = get_config_store().get(rehydrate.STATE_KEY)
    assert state["last"]["restored"] == 1 and state["note_scan_after"] == ""
    assert rehydrate.run_rehydrate_pass()["candidates"] == 0
