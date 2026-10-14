"""Forget reaches every copy; every memory table is declared (Memory audit M01).

Until 2026-10-13 Forget emptied the episode and the fact's value column and
nothing else. The value stayed in the fact's vector and its operator-edit
history, in ``beliefs_archive``, in ``memory_audit_log``, in a queued turn's
payload, in the legacy tables, in the full-text index segments, in a tool
record's arguments, and in ``unified_items`` -- which federated search served.

The behavioural half plants one word through the product writers (with
``ops_conn``: the old forget test never passed it, so the audit log stayed
empty), forgets the turn, and reads EVERY column of EVERY table of both memory
databases -- enumerated from ``sqlite_master``, FTS shadow tables included --
for the word. Negative controls: the forget of the day before (fact value only)
leaves it in nine places; a forget without the index purge leaves it in the
index segments.

The static half holds ``memory_stores``: every table the memory schema creates
is declared, every declaration names a table something creates, and a planted
table fails.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
import time
from pathlib import Path

import pytest

from kazma_core.memory import forget, memory_stores
from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

REPO = Path(__file__).resolve().parents[1]

from tests._memory_plant import (
    WORD,
    _everywhere,
    _plant,
    _record_tool_call,
    _tables,
    open_mem,
)


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    yield from open_mem(tmp_path, monkeypatch)


# ── The behavioural gate ──────────────────────────────────────────────────


def test_forget_leaves_the_word_nowhere(mem):
    planted = _plant(mem)
    before = _everywhere(mem)
    # The seed reached every kind of copy (else the test proves nothing).
    for where in ("episodes.user_text", "beliefs.metadata_json", "beliefs_archive.original_belief_json",
                  "ops:memory_audit_log.state_after_json", "ops:memory_task_queue.payload_json",
                  "episodes_archive.user_text", "entities_archive.name", "episodes_fts_data.block"):
        assert where in before, (where, sorted(before))
    assert set(planted["facts"]) & mem.vectors.held

    out = forget.forget_episode(planted["eid"], tenant_id="default")
    assert out["ok"] and out["remote_vector_deleted"]

    assert _everywhere(mem) == set()
    # No vector of the turn, of its note or of its facts, here or remotely.
    held = {planted["eid"], planted["note"], *planted["facts"]}
    assert not held & mem.vectors.held
    rows = mem.db.execute(
        "SELECT id FROM beliefs WHERE source_turn = 1 AND embedding IS NOT NULL").fetchall()
    assert rows == []


def test_forgetting_the_chat_leaves_the_word_nowhere(mem):
    _plant(mem)
    assert forget.forget_chat("th1", tenant_id="default")["ok"]
    assert _everywhere(mem) == set()


def test_the_forget_of_the_day_before_left_it_in_nine_places(mem, monkeypatch):
    """Negative control: the value column alone, as forget did before 2026-10-13."""
    planted = _plant(mem)
    for name in ("_forget_archived_facts", "_scrub_legacy_copies", "purge_search_index"):
        monkeypatch.setattr(forget, name, lambda *a, **k: [] if name != "purge_search_index" else None)
    monkeypatch.setattr(forget, "_after_facts", lambda *a, **k: [])
    monkeypatch.setattr(forget, "_provenance_only", lambda meta, *, now: {**meta, "forgotten": {"at": now}})
    forget.forget_episode(planted["eid"], tenant_id="default")
    left = _everywhere(mem)
    for where in ("beliefs.metadata_json", "beliefs_archive.original_belief_json",
                  "ops:memory_audit_log.state_after_json", "ops:memory_task_queue.payload_json",
                  "episodes_archive.user_text", "entities_archive.name", "episodes_fts_data.block"):
        assert where in left, (where, sorted(left))


def test_without_the_index_purge_the_words_stay_in_the_segments(mem, monkeypatch):
    """Negative control: FTS5 keeps a deleted row's words in its old segment."""
    planted = _plant(mem)
    monkeypatch.setattr(forget, "purge_search_index", lambda conn: None)
    forget.forget_episode(planted["eid"], tenant_id="default")
    left = _everywhere(mem)
    assert left and all(re.match(r"(episodes|beliefs|entities)_fts_", w) for w in left), sorted(left)


def test_a_forgotten_fact_is_not_found_by_federated_search(mem):
    """The audit's scenario: forget, then the Memory page's Federated search."""
    from kazma_core.memory.federated_search import federated_search

    planted = _plant(mem)
    forget.forget_episode(planted["eid"], tenant_id="default")
    out = federated_search(WORD, tenant_id="default", include_knowledge=False)
    assert WORD.lower() not in json.dumps(out["hits"], default=str).lower()


def _export(tenant: str = "default") -> dict:
    from datetime import UTC, datetime

    from kazma_ui.memory_api import _export_chunks

    return json.loads(b"".join(_export_chunks(tenant, datetime.now(UTC))))


def test_the_export_says_when_a_fact_was_forgotten_never_what(mem):
    planted = _plant(mem)
    forget.forget_episode(planted["eid"], tenant_id="default")
    body = _export()
    forgotten = [f for f in body["facts"] + body["archived_facts"] if f.get("forgotten")]
    assert len(forgotten) == 2  # the current fact, and the superseded one Hygiene archived
    for fact in forgotten:
        assert set(fact) <= {"tenant_id", "forgotten", "forgotten_at", "ingested_at", "archived_at"}
    assert WORD.lower() not in json.dumps(body).lower()


# ── The export: every section the registry names, streamed (M22) ──────────


def test_the_export_has_every_section_the_registry_names(mem):
    _plant(mem)
    body = _export()
    assert set(body) == {"exported_at", "tenant", "not_included"} | memory_stores.exported_sections()
    # What the file leaves out is named, with why.
    assert body["not_included"]["memory_task_queue"] == "work in flight, not memory"
    assert all(reason for reason in body["not_included"].values())
    # Before a forget, the word is in the archived facts and the fact changes:
    # what the store holds is what the file holds (the old export had neither).
    assert WORD.lower() in json.dumps(body["archived_facts"]).lower()
    assert WORD.lower() in json.dumps(body["fact_changes"]).lower()
    assert body["procedures"]


def test_an_export_section_without_a_reader_refuses_to_export(mem, monkeypatch):
    """Negative control: a section the registry names that the export cannot
    read is a file that is not everything -- refused, never sent short."""
    from datetime import UTC, datetime

    from kazma_ui import memory_api

    monkeypatch.setattr(memory_api, "_EXPORT_QUERIES",
                        {k: v for k, v in memory_api._EXPORT_QUERIES.items() if k != "archived_facts"})
    with pytest.raises(RuntimeError, match="archived_facts"):
        b"".join(memory_api._export_chunks("default", datetime.now(UTC)))


def test_every_table_with_a_tenant_names_its_export_section_or_why_not():
    """The tables that hold a tenant's rows, read from a fresh schema, against
    the registry; a planted tenant table fails (negative control)."""
    def tenant_tables(extra: str = "") -> set[str]:
        state = sqlite3.connect(":memory:")
        ops = sqlite3.connect(":memory:")
        try:
            ensure_primary_schema(state)
            ensure_ops_schema(ops)
            if extra:
                state.execute(extra)
            out = set()
            for conn in (state, ops):
                for name in _tables(conn):
                    cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')}
                    if "tenant_id" in cols:
                        out.add(name)
            return out
        finally:
            state.close()
            ops.close()

    def unaccounted(names: set[str]) -> list[str]:
        out = []
        for name in sorted(names):
            entry = memory_stores._table_entry(name)
            if entry is None or not (entry.export or entry.excluded):
                out.append(name)
        return out

    assert unaccounted(tenant_tables()) == []
    planted = "CREATE TABLE memory_moods (id TEXT, tenant_id TEXT, mood TEXT)"
    assert unaccounted(tenant_tables(planted)) == ["memory_moods"]


def test_the_export_is_streamed_a_batch_at_a_time(mem):
    """5,000 memories: many pieces, none holding more than one batch."""
    from datetime import UTC, datetime

    from kazma_ui import memory_api

    rows = [(f"ep{i}", "default", "s", i, f"question {i} " + "x" * 200, "answer", "", "episodic", time.time())
            for i in range(5000)]
    mem.db.executemany(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
        "summary_text, tier, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    mem.db.commit()
    pieces = list(memory_api._export_chunks("default", datetime.now(UTC)))
    assert len(pieces) >= 5000 // memory_api._EXPORT_BATCH
    biggest = max(len(p) for p in pieces)
    assert biggest < memory_api._EXPORT_BATCH * 600  # one batch of these rows, never the whole body
    assert len(json.loads(b"".join(pieces))["memories"]) == 5000


def test_the_export_route_streams(mem):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui.memory_api import router

    _plant(mem)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        resp = client.get("/api/memory/v2/export")
    assert resp.status_code == 200
    assert "content-length" not in resp.headers  # streamed, not built first
    assert resp.headers["content-disposition"].startswith('attachment; filename="kazma-memory-')
    assert set(resp.json()) >= memory_stores.exported_sections()


def test_a_tool_record_keeps_argument_names_never_values(mem):
    _record_tool_call({"text": f"{WORD} alpha", "path": "notes.txt"})
    steps = [json.loads(r[0]) for r in mem.db.execute("SELECT dag_steps_json FROM procedural_dags")]
    assert steps == [[{"tool": "probe_tool", "args_keys": ["path", "text"]}]]


def test_a_tool_record_is_counted_each_time(mem):
    """The recorder's connection returned tuples, and the procedure's counts
    never moved past the first call (a ``row["id"]`` TypeError, swallowed)."""
    for _ in range(3):
        _record_tool_call({"q": "x"})
    row = mem.db.execute("SELECT total_trials FROM procedural_dags WHERE name = 'probe_tool'").fetchone()
    assert row[0] == 3


def test_older_tool_records_lose_their_argument_values(mem):
    from kazma_core.memory.procedural import strip_argument_values

    mem.db.execute(
        "INSERT INTO procedural_dags (id, tenant_id, name, description, precond_signature_hash, "
        "preconditions_json, dag_steps_json, postconditions_json, success_count, total_trials, "
        "confidence_score, status, created_at, last_executed) VALUES "
        "('d1', 'default', 'probe_tool', '', 'sig', '{}', ?, '{}', 1, 1, 0.6, 'active', 0, 0)",
        (json.dumps([{"tool": "probe_tool", "args": {"text": WORD}}]),),
    )
    assert strip_argument_values(mem.db) == 1
    assert json.loads(mem.db.execute("SELECT dag_steps_json FROM procedural_dags").fetchone()[0]) == [
        {"tool": "probe_tool", "args_keys": ["text"]}]
    assert strip_argument_values(mem.db) == 0  # nothing left to rewrite


# ── The static gate: every memory table is declared ───────────────────────


def _created_tables() -> set[str]:
    """Every table a fresh memory schema has, both databases."""
    state = sqlite3.connect(":memory:")
    ops = sqlite3.connect(":memory:")
    try:
        ensure_primary_schema(state)
        ensure_ops_schema(ops)
        # The commitment layer keeps its tables in memory_ops.db (section 20).
        from kazma_core.memory.forget_journal import _SCHEMA as journal_schema
        from kazma_core.safety.commitment.store import ensure_commitment_schema

        ensure_commitment_schema(ops)
        for ddl in journal_schema:  # memory_forget_journal.db's own tables
            ops.execute(ddl)
        return set(_tables(state)) | set(_tables(ops))
    finally:
        state.close()
        ops.close()


def _undeclared(names: set[str]) -> list[str]:
    return sorted(n for n in names if memory_stores._table_entry(n) is None)


def test_every_memory_table_is_declared():
    assert _undeclared(_created_tables()) == []


def test_a_planted_table_is_not_declared():
    """Negative control: a new table nobody declared."""
    assert _undeclared(_created_tables() | {"memory_scratch_notes"}) == ["memory_scratch_notes"]


def test_every_declaration_names_a_table_that_exists():
    created = _created_tables()
    legacy_or_retired = {e.name for e in memory_stores.MEMORY_TABLES if e.where == "legacy" or e.erase == "retired"}
    stale = [e.name for e in memory_stores.MEMORY_TABLES if e.name not in created | legacy_or_retired]
    assert stale == []


def test_every_declaration_is_complete():
    kinds = {"forget", "index", "no_turn_text", "retention", "retired"}
    for e in (*memory_stores.MEMORY_TABLES, *memory_stores.EXTERNAL_STORES):
        assert e.erase in kinds, e
        assert e.forget.strip(), e
        assert e.export or e.excluded, e
        if e.where != "external":
            assert e.bound.strip(), e


def test_no_product_code_creates_a_memory_table_the_registry_does_not_name():
    """CREATE TABLE in the memory package, read from the source: a table
    created outside ``schema_v2`` (a store's own ``CREATE TABLE IF NOT
    EXISTS``) is declared too."""
    # A name built at run time (``{self._table}``) is not read: the f-string's
    # literal part ends before it.
    pattern = re.compile(
        r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"']?(?!IF\b)(\w+)", re.I)
    names: set[str] = set()
    core = REPO / "kazma-core" / "kazma_core"
    for path in [*(core / "memory").glob("*.py"), core / "safety" / "commitment" / "store.py"]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.update(pattern.findall(node.value))
    # Kazma's own stores outside the two memory databases.
    other = {"kazma_episodes", "kazma_beliefs", "kazma_memory_vectors", "memory_vectors", "vec_episodes",
             "vec_beliefs", "kazma_memory_summaries"}
    assert _undeclared(names - other - {n for n in names if n.startswith(("tmp_", "temp_", "_"))}) == []


def test_the_unified_index_is_not_written_or_read():
    """Its writers and its reader are gone with the table (negative control:
    the 2026-10-12 source of either writer matches)."""
    sources = "\n".join(p.read_text(encoding="utf-8") for p in (REPO / "kazma-core" / "kazma_core").rglob("*.py"))
    assert "upsert_unified" not in sources and "search_unified" not in sources
    old = "from kazma_core.memory.unified_index import upsert_unified\n"
    assert "upsert_unified" in old
