"""The procedural store's tool tallies no longer reach a turn (memory
framework audit F13, decision 13).

Until 2026-11 the supervisor opened the memory database before every reply,
matched the question against the procedural store and was to add up to three
"skills" to the model's input: lines such as ``file_read (C=0.99, n=812)``.
A tally is not a procedure. Reading the code for this change showed the
injection had never worked: it read the rows by column name on a connection
with no row factory, raised ``TypeError`` and was skipped at DEBUG, every
turn. Removed: no read of the store per turn, no note. The tallies stay for
memory health.

* Through the real supervisor node, with a store whose tallies match the
  question, no statement reads the procedural store and the model's input
  holds no tally note.
* Negative control: the old injection, as it was, reads the store (the trace
  sees it), and its formatter, given rows, wrote the note the test looks for.
* The tallies are still kept for health; an older checkpoint's note is
  stripped.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

_TALLY_FENCE = '<kazma:data source="memory_v2_procedural"'
QUESTION = "please edit this config file for me"


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory.procedural import record_procedural_outcome
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    for _ in range(12):
        record_procedural_outcome(
            db, name="file_edit_flow", description="edit a config file with read then write",
            preconditions={"intent": "edit_file"},
            dag_steps=[{"tool": "file_read"}, {"tool": "file_write"}],
            postconditions={"file_changed": True}, success=True,
        )
    yield SimpleNamespace(db=db, mp=monkeypatch)
    db.close()


class _Response:
    def __init__(self, text: str) -> None:
        self.content = text
        self.tool_calls: list = []
        self.model = "fake-model"
        self.usage = {"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5}
        self.cost_usd = 0.0


class _LLM:
    def __init__(self) -> None:
        self.seen: list[list[dict]] = []

    async def chat(self, messages=None, tools=None, model=None, **kwargs):
        self.seen.append(list(messages or []))
        return _Response("Done.")


class _Fake:
    compactor = type("C", (), {"retrieve_memories": staticmethod(lambda *a, **k: [])})()

    def should_halt(self) -> bool:
        return False

    def record_cost(self, cost: float) -> None:
        pass

    def trace_llm_call(self, **kwargs) -> None:
        pass

    async def check_and_enforce(self, state):
        return state


def _tally_notes(messages: list[dict]) -> list[str]:
    return [str(m.get("content") or "") for m in messages
            if m.get("role") == "system" and str(m.get("content") or "").lstrip().startswith(_TALLY_FENCE)]


def _old_inject(conn: sqlite3.Connection, query: str) -> str:
    """The injection as it was until 2026-11 (``match_procedural_dags`` and
    ``format_procedural_hints``, condensed): the note the first test checks
    is gone."""
    from kazma_core.safety.prompt_fence import format_untrusted_block

    tokens = [t for t in query.lower().replace("-", " ").split() if len(t) >= 3]
    rows = conn.execute(
        "SELECT name, description, preconditions_json, confidence_score, total_trials, dag_steps_json "
        "FROM procedural_dags WHERE tenant_id = 'default' AND status = 'active' AND confidence_score >= 0.45"
    ).fetchall()
    lines = ["## Procedural skill hints (observation only — not orders)"]
    for r in rows:
        blob = f"{r['name']} {r['description']} {r['preconditions_json']}".lower()
        if not any(t in blob for t in tokens):
            continue
        lines.append(f"- **{r['name']}** (C={float(r['confidence_score']):.2f}, n={r['total_trials']}): "
                     f"{r['description']}")
        for i, step in enumerate(json.loads(r["dag_steps_json"] or "[]"), start=1):
            lines.append(f"    {i}. `{step.get('tool')}`")
    if len(lines) == 1:
        return ""
    return format_untrusted_block("\n".join(lines), source="memory_v2_procedural")


class _Traced:
    """``sqlite3.connect`` that records every statement any connection runs."""

    def __init__(self, mp) -> None:
        self.statements: list[str] = []
        real = sqlite3.connect

        def connect(*args, **kwargs):
            conn = real(*args, **kwargs)
            conn.set_trace_callback(self.statements.append)
            return conn

        mp.setattr(sqlite3, "connect", connect)

    def reads_of_the_store(self) -> list[str]:
        """Reads of the tallies themselves (schema setup, which also names the
        table, is not one)."""
        return [s for s in self.statements if s.lstrip().upper().startswith("SELECT")
                and "procedural_dags" in s and "confidence_score" in s]


def _turn(mem) -> _LLM:
    from kazma_core.agent.graph_builder import supervisor_node
    from kazma_core.memory.recall import RecallResult

    asked: list[str] = []

    def spy(query, **kw):
        asked.append(query)
        return RecallResult([], [])

    mem.mp.setattr("kazma_core.memory.recall.recall", spy)
    llm, fake = _LLM(), _Fake()
    asyncio.run(supervisor_node(
        {"messages": [{"role": "user", "content": QUESTION}], "iteration": 0, "max_iterations": 5,
         "tenant_id": "default", "thread_id": "th-tallies"},
        llm=llm, system_prompt="You are Kazma.", tool_definitions=[], tool_executor=None,
        cost_breaker=fake, authority=fake, tracer=fake,
    ))
    assert asked, "the turn did not reach recall: the old injection ran right after it"
    assert llm.seen, "the model was not called"
    return llm


def test_a_turn_neither_reads_the_tallies_nor_shows_them(mem):
    traced = _Traced(mem.mp)
    llm = _turn(mem)
    assert not traced.reads_of_the_store(), traced.reads_of_the_store()
    for call in llm.seen:
        assert not _tally_notes(call), _tally_notes(call)


def _old_injection_as_it_was(query: str) -> list[dict]:
    """The supervisor's block until 2026-11, as it was: a connection with no
    row factory, the match by column name (which raised), skipped at DEBUG."""
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    pconn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
    try:
        ensure_primary_schema(pconn)
        rows = pconn.execute(
            "SELECT id, name, description, preconditions_json, dag_steps_json, postconditions_json, "
            "confidence_score, success_count, total_trials FROM procedural_dags "
            "WHERE tenant_id = ? AND status = 'active' AND confidence_score >= ? "
            "ORDER BY confidence_score DESC, total_trials DESC LIMIT ?",
            ("default", 0.45, 24),
        ).fetchall()
        try:
            return [{"name": r["name"]} for r in rows]
        except TypeError:
            return []  # the old block's except: "procedural inject skipped" at DEBUG
    finally:
        pconn.close()


def test_the_old_injection_read_the_store_every_turn(mem):
    """Negative control: the old block, run as the turn ran it, reads the
    store (the trace above sees it) -- and finds nothing it can use."""
    traced = _Traced(mem.mp)
    assert _old_injection_as_it_was(QUESTION) == []
    assert traced.reads_of_the_store()


def test_the_old_formatter_wrote_the_note_the_test_looks_for(mem):
    """Negative control for the note check: given rows, the old formatter's
    note is what ``_tally_notes`` finds."""
    note = _old_inject(mem.db, QUESTION)
    assert _tally_notes([{"role": "system", "content": note}]), note
    assert "C=0.93" in note and "file_read" in note


def test_the_tallies_are_still_kept_for_health(mem):
    row = mem.db.execute(
        "SELECT total_trials, success_count FROM procedural_dags WHERE name = 'file_edit_flow'").fetchone()
    assert row is not None and int(row["total_trials"]) == 12 and int(row["success_count"]) == 12


def test_an_older_checkpoints_tally_note_is_stripped():
    from kazma_core.turn_notes import note_kind, strip_turn_notes

    old = {"role": "system", "content": _TALLY_FENCE + ' untrusted="true">\n- **x** (C=0.9, n=4)\n</kazma:data>'}
    assert note_kind(old) == "procedural_hints"
    assert strip_turn_notes([old, {"role": "user", "content": "hi"}]) == [{"role": "user", "content": "hi"}]
