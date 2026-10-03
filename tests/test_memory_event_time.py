"""Facts follow the order they were SAID in, whatever order they are written in (W1).

A single-valued fact (where the user lives, where they work) used to be
superseded by whichever statement was WRITTEN last: ``_mutate_functional``
closed the current fact at "now" and made the incoming one current. Every
write that comes late -- a turn from the durable queue after a full pool or
a restart, the LLM deep pass minutes after its turn, an old turn reconciled
from the chat store -- could put an old "I live in Paris" over a newer "I
moved to London". That is why turn reconcile wrote episodes only.

Now the statement time is the valid time. A statement older than the current
fact is recorded as history in its place on the timeline and never replaces
it; the live pipeline stamps each turn's episode and facts with the moment
the turn happened, the deep pass uses the episode's time, and reconciled
turns get their facts at their own time.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import sqlite3
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from kazma_core.memory.belief_extractor import extract_beliefs_heuristic
from kazma_core.memory.belief_mutation import mutate_belief
from kazma_core.memory.schema_v2 import ensure_primary_schema

DAY = 86400.0
T1 = 1_760_000_000.0
T3 = T1 + 2 * DAY
T5 = T1 + 4 * DAY


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    c = sqlite3.connect(str(tmp_path / "memory_state.db"), isolation_level=None)
    c.row_factory = sqlite3.Row
    ensure_primary_schema(c)
    yield c
    c.close()


def _say(conn, obj, when, *, predicate="lives_in", method="user_explicit", ptype=None):
    return mutate_belief(conn, "user", predicate, obj, now=when, extraction_method=method,
                         predicate_type=ptype, private=True)


def _timeline(conn, predicate="lives_in"):
    return [(r["object"], r["valid_from"], r["valid_until"]) for r in conn.execute(
        "SELECT object, valid_from, valid_until FROM beliefs WHERE predicate=? "
        "ORDER BY valid_from", (predicate,))]


def _current(conn, predicate="lives_in"):
    return [r[0] for r in conn.execute(
        "SELECT object FROM beliefs WHERE predicate=? AND valid_until IS NULL "
        "AND invalidated_at IS NULL", (predicate,))]


# ── the rule ──────────────────────────────────────────────────────────────

STATEMENTS = [("Paris", T1), ("Berlin", T3), ("London", T5)]


@pytest.mark.parametrize("order", list(itertools.permutations(STATEMENTS)),
                         ids=lambda o: "-".join(obj for obj, _ in o))
def test_every_order_of_writing_gives_the_same_facts(conn, order):
    for obj, when in order:
        _say(conn, obj, when)
    assert _current(conn) == ["London"]
    assert _timeline(conn) == [("Paris", T1, T3), ("Berlin", T3, T5), ("London", T5, None)]
    # The chain of supersession follows the timeline too.
    chain = {r["object"]: r["supersedes_id"] for r in conn.execute(
        "SELECT object, supersedes_id FROM beliefs")}
    ids = {r["object"]: r["id"] for r in conn.execute("SELECT object, id FROM beliefs")}
    assert chain == {"Paris": None, "Berlin": ids["Paris"], "London": ids["Berlin"]}


def test_without_the_statement_time_the_later_write_wins(conn):
    """Negative control: a caller that passes no time states the fact NOW --
    which is why every late writer passes the turn's own time."""
    mutate_belief(conn, "user", "lives_in", "London", extraction_method="user_explicit", private=True)
    mutate_belief(conn, "user", "lives_in", "Paris", extraction_method="user_explicit", private=True)
    assert _current(conn) == ["Paris"]


def test_an_earlier_statement_of_a_known_value_adds_nothing(conn):
    _say(conn, "Paris", T1)
    _say(conn, "London", T5)
    assert _say(conn, "Paris", T3)["action"] == "noop"  # Paris held until T5 anyway
    assert _say(conn, "London", T3)["action"] == "noop"  # stated later already
    assert _timeline(conn) == [("Paris", T1, T5), ("London", T5, None)]


def test_a_lower_trust_statement_cannot_cut_short_a_user_fact(conn):
    _say(conn, "Paris", T1)
    _say(conn, "London", T5)
    result = _say(conn, "Berlin", T3, method="llm_inferred")
    assert result.get("blocked") == "lower_trust_source"
    assert _timeline(conn) == [("Paris", T1, T5), ("London", T5, None)]


def test_valid_time_is_when_it_was_said_and_the_write_time_is_now(conn):
    before = time.time()
    _say(conn, "London", T5)
    _say(conn, "Paris", T1)
    for row in conn.execute("SELECT object, valid_from, ingested_at, invalidated_at FROM beliefs"):
        assert row["ingested_at"] >= before  # written now
        assert row["valid_from"] in (T1, T5)  # stated then
    paris = conn.execute("SELECT invalidated_at FROM beliefs WHERE object='Paris'").fetchone()
    assert paris["invalidated_at"] >= before  # history the moment it was learned


def test_a_state_transition_said_earlier_is_history_too(conn):
    for status, when in (("closed", T5), ("open", T1), ("in_progress", T3)):
        _say(conn, status, when, predicate="issue_status", ptype="state")
    assert _current(conn, "issue_status") == ["closed"]
    assert [o for o, _f, _u in _timeline(conn, "issue_status")] == ["open", "in_progress", "closed"]


# ── what the heuristic pass reads ─────────────────────────────────────────


def test_where_the_user_works_is_not_where_they_live():
    """``I work at X`` shared one pattern with ``I live in`` and stored the
    employer as lives_in."""
    assert [(b["predicate"], b["object"]) for b in extract_beliefs_heuristic(
        "I work at Google.")] == [("works_at", "Google")]
    assert [(b["predicate"], b["object"]) for b in extract_beliefs_heuristic(
        "I live in Paris.")] == [("lives_in", "Paris")]
    assert extract_beliefs_heuristic("I work in finance.") == []  # a field, or a place


def test_the_local_embedder_remembers_short_texts(monkeypatch):
    """Every extraction with a fact embedded each entity name of the tenant
    again: 93 on the live install, seconds of CPU per turn."""
    from kazma_core.memory import embedder as emb

    calls: list[str] = []

    class Model:
        def encode(self, text, convert_to_numpy=False):
            calls.append(text)
            return [float(len(text)), 1.0]

    monkeypatch.setattr(emb, "_SHORT_TEXT_CACHE", 2)
    local = emb.LocalSentenceTransformerEmbedder(model_name="x", dim=2)
    local._model = Model()
    assert local.encode("Paris") == local.encode("Paris") == [5.0, 1.0]
    long_text = "x" * (emb._SHORT_TEXT_CHARS + 1)
    local.encode(long_text)
    local.encode(long_text)
    assert calls == ["Paris", long_text, long_text]  # a long text is not kept
    local.encode("Rome")
    local.encode("Oslo")  # the cache holds two: Paris goes
    local.encode("Paris")
    assert calls[-3:] == ["Rome", "Oslo", "Paris"]


# ── the live pipeline stamps a turn with its own time ─────────────────────


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    dual_write._reset_mirror()
    yield SimpleNamespace(dir=tmp_path, db=db)
    dual_write._reset_mirror()
    db.close()


def _turn(text, answer="Noted."):
    return [{"role": "user", "content": text}, {"role": "assistant", "content": answer}]


def _facts(db, predicate="lives_in"):
    return [(r["object"], r["valid_from"], r["valid_until"]) for r in db.execute(
        "SELECT object, valid_from, valid_until FROM beliefs WHERE predicate=? ORDER BY valid_from",
        (predicate,))]


def test_a_turn_run_late_never_overwrites_a_later_one(mem):
    from kazma_core.memory.consolidator import _run_turn_memory

    now = time.time()
    _run_turn_memory(_turn("I live in London now."), session_id="s1", turn=2, at=now - 60)
    # Turn 1 comes from the queue after a restart, a minute late.
    _run_turn_memory(_turn("I live in Paris."), session_id="s1", turn=1, at=now - 120)
    assert _facts(mem.db) == [("Paris", now - 120, now - 60), ("London", now - 60, None)]
    episodes = {r["turn_number"]: r["created_at"] for r in mem.db.execute(
        "SELECT turn_number, created_at FROM episodes")}
    assert episodes == {1: now - 120, 2: now - 60}


def test_a_deferred_turn_keeps_the_time_it_happened(mem, monkeypatch):
    from kazma_core.memory import consolidator

    queued: list[dict] = []
    monkeypatch.setattr("kazma_core.memory.task_queue.enqueue_task",
                        lambda kind, payload, **kw: queued.append(payload) or "task-1")
    then = time.time() - 300
    consolidator._defer_turn_memory(_turn("I live in Porto."), session_id="s2", turn=1,
                                    tenant_id="default", why="pool full", at=then)
    [payload] = queued
    assert payload["at"] == then
    assert consolidator.run_deferred_turn_memory(payload) is True
    assert _facts(mem.db) == [("Porto", then, None)]
    # A task queued before 2026-09-27 has no time: it is stated when run.
    legacy = {k: v for k, v in payload.items() if k != "at"} | {"user_text": "I live in Faro."}
    assert consolidator.run_deferred_turn_memory(legacy) is True
    assert _facts(mem.db)[-1][0] == "Faro"


def test_the_deep_pass_states_facts_at_the_turn_time(mem, monkeypatch):
    """The LLM pass runs minutes after its turn -- or after a restart -- and
    a later turn's facts may be written by then."""
    from kazma_core.memory import worker_bootstrap
    from kazma_core.memory.belief_mutation import mutate_belief as mutate
    from kazma_core.memory.dual_write import _episode_id, mirror_episode

    turn_time = time.time() - 600
    mirror_episode(session_id="s3", turn_number=1, user_text="Remember where I lived: Paris",
                   assistant_text="Paris, got it.", created_at=turn_time)
    later = sqlite3.connect(str(mem.dir / "memory_state.db"), isolation_level=None)
    later.row_factory = sqlite3.Row
    mutate(later, "user", "lives_in", "London", now=turn_time + 60, extraction_method="llm_inferred")
    later.close()

    async def llm(user_text, assistant_text="", *, use_llm=True, ignore_filler=False, vocabulary=None,
                  entities=None):
        return ([{"subject": "user", "predicate": "lives_in", "predicate_type": "functional",
                  "object": "Paris", "confidence": 0.9, "importance": 4}],
                {"skipped_filler": False, "source": "llm", "applied": 0, "rejected": 0,
                 "actions": []})

    monkeypatch.setattr("kazma_core.memory.belief_extractor.extract_beliefs_for_turn", llm)
    eid = _episode_id("s3", 1, "Remember where I lived: Paris")
    assert asyncio.run(worker_bootstrap._handle_micro_consolidation({"episode_id": eid})) is True
    assert _facts(mem.db) == [("Paris", turn_time, turn_time + 60), ("London", turn_time + 60, None)]


# ── reconciled turns get their facts ──────────────────────────────────────


def _iso(days_ago: float) -> str:
    return datetime.fromtimestamp(time.time() - days_ago * 86400, UTC).isoformat()


def _save(mem, session_id, messages, *, thread_id="", tenant="default"):
    from kazma_ui.session_manager import ChatSession, SessionManager

    store = SessionManager(db_path=str(mem.dir / "chat_sessions.db"))
    try:
        store.put(ChatSession(session_id=session_id, tenant_id=tenant, thread_id=thread_id,
                              messages=list(messages)))
    finally:
        store.close()


def _user(text, ts):
    return {"role": "user", "content": text, "ts": ts}


def _queued(mem, task_type):
    from kazma_core.paths import memory_ops_db

    ops = sqlite3.connect(memory_ops_db())
    try:
        return [json.loads(r[0]) for r in ops.execute(
            "SELECT payload_json FROM memory_task_queue WHERE task_type = ?", (task_type,))]
    except sqlite3.OperationalError:
        return []
    finally:
        ops.close()


def test_reconciled_turns_get_their_facts_at_their_own_time(mem):
    from kazma_core.memory import turn_reconcile

    # The live pipeline knew London (said ten days ago); the chat store also
    # holds an older turn the web never handed to memory.
    london = time.time() - 10 * DAY
    live = sqlite3.connect(str(mem.dir / "memory_state.db"), isolation_level=None)
    try:
        mutate_belief(live, "user", "lives_in", "London", now=london, extraction_method="llm_inferred")
    finally:
        live.close()
    _save(mem, "s-old", [_user("I live in Paris.", _iso(40)), {"role": "assistant",
                                                                "content": "Paris, lovely."}])

    report = turn_reconcile.run_turn_reconcile_pass()
    assert (report["turns_written"], report["facts_turns"], report["deep_queued"]) == (1, 1, 1)
    facts = _facts(mem.db)
    assert [f[0] for f in facts] == ["Paris", "London"]
    assert abs(facts[0][1] - (time.time() - 40 * DAY)) < 60 and facts[0][2] == london
    assert facts[1][2] is None  # London is still the fact
    [episode_id] = [r[0] for r in mem.db.execute("SELECT id FROM episodes")]
    assert _queued(mem, "micro_consolidation") == [{"episode_id": episode_id}]

    again = turn_reconcile.run_turn_reconcile_pass()
    assert (again["turns_written"], again["facts_turns"]) == (0, 0)  # the cursor moved on


def test_the_deep_passes_are_queued_a_few_at_a_time(mem, monkeypatch):
    from kazma_core.memory import turn_reconcile

    monkeypatch.setattr(turn_reconcile, "_DEEP_PER_PASS", 1)
    _save(mem, "s-a", [_user("I live in Oslo.", _iso(30)), {"role": "assistant", "content": "ok"}])
    _save(mem, "s-b", [_user("I live in Bergen.", _iso(20)), {"role": "assistant", "content": "ok"}])
    first = turn_reconcile.run_turn_reconcile_pass()
    assert (first["turns_written"], first["facts_turns"], first["facts_done"]) == (2, 1, False)
    second = turn_reconcile.run_turn_reconcile_pass()
    assert second["facts_turns"] == 1
    assert _facts(mem.db)[-1][0] == "Bergen"  # said last, current, whatever the write order
    assert len(_queued(mem, "micro_consolidation")) == 2


# ── a history row, and who must pass the time ─────────────────────────────


def test_a_history_row_reaches_the_mirror_but_never_the_graph(conn, monkeypatch):
    mirrored: list[str] = []
    graphed: list[str] = []
    monkeypatch.setattr("kazma_core.memory.state_backend.remirror_belief_by_id",
                        lambda c, bid: mirrored.append(bid))
    monkeypatch.setattr("kazma_core.memory.graph_backend.upsert_belief_edge",
                        lambda **kw: graphed.append(kw["obj"]))
    monkeypatch.setattr("kazma_core.memory.unified_index.upsert_unified", lambda **kw: None)
    mutate_belief(conn, "user", "lives_in", "London", now=T5, extraction_method="user_explicit")
    assert graphed == ["London"]
    result = mutate_belief(conn, "user", "lives_in", "Paris", now=T1, extraction_method="user_explicit")
    assert result["action"] == "history"
    assert graphed == ["London"]  # the graph holds current facts only
    london = conn.execute("SELECT id FROM beliefs WHERE object='London'").fetchone()[0]
    assert mirrored[-2:] == [result["belief_id"], london]  # the row and the one it now precedes


#: Modules whose writers run AFTER the turn they write for: the durable queue,
#: the LLM deep pass, turn reconcile. Each call must pass the turn's time.
_LATE_WRITERS = ("kazma-core/kazma_core/memory/consolidator.py",
                 "kazma-core/kazma_core/memory/worker_bootstrap.py",
                 "kazma-core/kazma_core/memory/turn_reconcile.py")
_TIME_KEYWORD = {"mutate_belief": "now", "_apply_beliefs_to_v2": "now",
                 "extract_and_apply_beliefs_sync": "now", "_v2_extract_sync": "now",
                 "mirror_episode": "created_at"}


def _untimed_writes(source: str) -> list[tuple[int, str]]:
    import ast

    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            keyword = _TIME_KEYWORD.get(name or "")
            if keyword and keyword not in {k.arg for k in node.keywords}:
                found.append((node.lineno, name))
    return found


def test_every_late_writer_passes_the_turn_time():
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    offenders = [f"{rel}:{line} {name}()" for rel in _LATE_WRITERS
                 for line, name in _untimed_writes((repo / rel).read_text(encoding="utf-8"))]
    assert not offenders, (
        "A memory write that runs after its turn must say when the turn happened, or a "
        "late old statement overwrites a newer fact:\n  " + "\n  ".join(offenders))
    # Negative control: the deep pass as it was.
    before = ("async def handle(row):\n"
              "    _apply_beliefs_to_v2(raw, primary, ops, stats=stats,\n"
              "                         tenant_id=row['tenant_id'])\n")
    assert _untimed_writes(before) == [(2, "_apply_beliefs_to_v2")]
