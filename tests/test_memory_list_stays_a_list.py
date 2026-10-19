"""A list of notes stays a list, and "forget" in chat forgets the turn.

Live 2026-10-19, testing memory on the web chat:

- "Remember this: my test codeword is BLUE-HERON-42." The deep pass stored
  ``user noted "test codeword is BLUE-HERON-42"`` labelled single-valued, and
  the write took the owner's 58 current ``noted`` entries for one fact's stray
  values: it superseded the newest and closed the other 57 ("58 current values
  of user noted: keeping the newest, closing [...]").
- "Forget the codeword." The approved ``memory_invalidate`` closed the facts;
  the turn stayed a memory, recall found it at once, and the next turn that
  repeated it learned ``test_codeword`` again.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from kazma_core.memory import belief_mutation
from kazma_core.memory.belief_mutation import mutate_belief
from kazma_core.memory.list_reopen import find_closed_entries, reopen_entries
from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
from tests._tool_answer import answer as tool_answer
from tests.test_memory_forget import _chat, _live_turn
from tests.test_memory_routes_tenant_scope import _as_tenant, _memory_tools

NOTES = 58


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write

    for var in ("KAZMA_PRODUCTION", "KAZMA_MULTI_USER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    dual_write._reset_mirror()
    yield SimpleNamespace(dir=tmp_path, db=db, ops=ops)
    dual_write._reset_mirror()
    ops.close()
    db.close()


def _notes(mem) -> None:
    for n in range(NOTES):
        mutate_belief(mem.db, "user", "noted", f"note number {n} about project {n}",
                      predicate_type="set", extraction_method="agent_asserted",
                      private=True, now=1_000_000.0 + n)


def _current(mem, predicate="noted") -> int:
    return mem.db.execute(
        "SELECT COUNT(*) FROM beliefs WHERE subject='user' AND predicate=? "
        "AND valid_until IS NULL AND invalidated_at IS NULL", (predicate,)).fetchone()[0]


def _the_live_write(mem, conn=None):
    return mutate_belief(conn or mem.db, "user", "noted", "test codeword is BLUE-HERON-42",
                         predicate_type="functional", extraction_method="llm_inferred",
                         source_session="th1", source_turn=4, private=True)


class _OldQuery:
    """The connection as the old code queried it: no list filter."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *args):
        return self._conn.execute(sql.replace("AND COALESCE(predicate_type, '') != 'set'", ""), *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _old_code(monkeypatch, mem):
    monkeypatch.setattr(belief_mutation, "_established_kind", lambda conn, *, ptype, **kw: ptype)
    return _OldQuery(mem.db)


def test_a_single_valued_label_on_a_list_adds_one_entry(mem):
    _notes(mem)
    out = _the_live_write(mem)
    assert out["action"] == "append" and out["superseded_id"] is None
    assert _current(mem) == NOTES + 1
    kind = mem.db.execute("SELECT predicate_type FROM beliefs WHERE id=?", (out["belief_id"],)).fetchone()[0]
    assert kind == "set"


def test_negative_control_the_old_write_closed_the_list(mem, monkeypatch):
    _notes(mem)
    _the_live_write(mem, conn=_old_code(monkeypatch, mem))
    assert _current(mem) == 1  # the new value alone: 57 closed, 1 superseded


def test_the_extra_close_never_takes_a_list_entry(mem, monkeypatch):
    """The second layer alone: a single-valued write that reaches the close
    (the kind check off) still leaves every list entry current."""
    _notes(mem)
    monkeypatch.setattr(belief_mutation, "_established_kind", lambda conn, *, ptype, **kw: ptype)
    _the_live_write(mem)
    assert _current(mem) == NOTES + 1


def test_a_predicate_kazma_classifies_still_supersedes(mem):
    mutate_belief(mem.db, "user", "lives_in", "lisbon", predicate_type="set",
                  extraction_method="llm_inferred", private=True, now=1_000_000.0)
    out = mutate_belief(mem.db, "user", "lives_in", "london", predicate_type="functional",
                        extraction_method="user_explicit", private=True)
    assert belief_mutation.kazma_classifies("lives_in")
    assert out["action"] == "supersede" and _current(mem, "lives_in") == 1


def test_the_repair_reopens_exactly_what_the_old_write_closed(mem, monkeypatch):
    _notes(mem)
    mutate_belief(mem.db, "user", "noted", "an entry the user forgot", predicate_type="set",
                  extraction_method="agent_asserted", private=True, now=900_000.0)
    forgotten = mem.db.execute("SELECT id FROM beliefs WHERE object='an entry the user forgot'").fetchone()[0]
    mem.db.execute("UPDATE beliefs SET valid_until=950000, invalidated_at=950000 WHERE id=?", (forgotten,))
    mem.db.commit()
    _the_live_write(mem, conn=_old_code(monkeypatch, mem))
    monkeypatch.undo()

    groups = find_closed_entries(mem.db)
    assert len(groups) == 1 and len(groups[0]["closed"]) == NOTES
    assert forgotten not in groups[0]["closed"], "closed another way: never reopened"
    mem.db.isolation_level = None
    out = reopen_entries(mem.db, mem.ops, groups)
    assert out["reopened"] == NOTES
    assert _current(mem) == NOTES + 1  # the notes, and the wrong write until it is forgotten
    rows = mem.ops.execute("SELECT COUNT(*) FROM memory_audit_log WHERE event_type='list_entry_reopened'").fetchone()
    assert rows[0] == NOTES
    assert find_closed_entries(mem.db) == []  # idempotent


def _forget_through_the_tool(mem, *, forget_source_turn: bool) -> dict:
    _chat(mem)
    _live_turn(turn=1, question="Remember this: my test codeword is BLUE-HERON-42.",
               answer="Saved.", key="th1")
    fact = mutate_belief(mem.db, "user", "test_codeword", "BLUE-HERON-42", predicate_type="set",
                         extraction_method="user_explicit", source_session="th1", source_turn=1)
    mem.db.commit()
    tool = _memory_tools()["memory_invalidate"]
    return json.loads(_as_tenant("default", lambda: tool_answer(
        tool(fact["belief_id"], forget_source_turn=forget_source_turn))))


def _recalled(mem) -> bool:
    from kazma_core.memory.recall import recall

    found = recall("what is my test codeword", conn=mem.db, tenant_id="default", local_only=True)
    return any("BLUE-HERON" in (getattr(h, "text", "") or str(h)) for h in found.episodes)


def test_forget_in_chat_forgets_the_turn_the_fact_came_from(mem):
    out = _forget_through_the_tool(mem, forget_source_turn=True)
    assert out["ok"] and out["source_turn"]["ok"] and len(out["source_turn"]["episodes_forgotten"]) == 1
    assert not _recalled(mem)
    from kazma_core.memory.forget import FORGOTTEN_TIER

    tiers = {r[0] for r in mem.db.execute("SELECT tier FROM episodes WHERE session_id IN ('th1','s1')")}
    assert tiers == {FORGOTTEN_TIER}


def test_negative_control_invalidating_alone_leaves_it_recalled(mem):
    out = _forget_through_the_tool(mem, forget_source_turn=False)
    assert out["ok"] and "source_turn" not in out
    assert _recalled(mem)


def test_a_fact_with_no_source_turn_says_so(mem):
    from kazma_core.memory.forget import forget_belief_source

    fact = mutate_belief(mem.db, "user", "noted", "typed on the Memory page", predicate_type="set",
                         extraction_method="user_explicit")
    mem.db.commit()
    assert forget_belief_source(fact["belief_id"], tenant_id="default") == {
        "ok": False, "error": "no_source_turn"}


def _tool_call(*args, **kwargs):
    tool = _memory_tools()["memory_invalidate"]
    return _as_tenant("default", lambda: tool_answer(tool(*args, **kwargs)))


@pytest.mark.parametrize("missing", ["b_no_such_fact", "e_no_such_memory"])
def test_an_unknown_id_is_refused_before_anything_is_done(mem, missing):
    """Live 2026-10-19 the model passed memory ids as fact ids; the "not
    found" answer was recorded as an unknown effect and failed the turn."""
    from kazma_core.agent.tool_refusal import RefusedBeforeEffect

    tool = _memory_tools()["memory_invalidate"]
    with pytest.raises(RefusedBeforeEffect):
        _as_tenant("default", lambda: tool(missing, forget_source_turn=True))


def test_a_memory_id_forgets_that_memory(mem):
    from kazma_core.memory.forget import FORGOTTEN_TIER

    _chat(mem)
    eid = _live_turn(turn=1, question="Remember this: my test codeword is BLUE-HERON-42.",
                     answer="Saved.", key="th1")
    mem.db.commit()
    assert _recalled(mem)  # negative control: recalled before
    out = json.loads(_tool_call(eid))
    assert out["ok"]
    assert mem.db.execute("SELECT tier FROM episodes WHERE id=?", (eid,)).fetchone()[0] == FORGOTTEN_TIER
    assert not _recalled(mem)


def _a_later_answer_elsewhere(mem):
    """Kazma's answer in another chat, quoting the value (live 2026-10-19:
    a new chat recalled such an answer after the forget)."""
    return _live_turn(turn=1, question="What's my test codeword?",
                      answer="Your test codeword is **BLUE-HERON-42**.", key="other-chat")


def test_every_memory_repeating_a_distinctive_value_is_forgotten(mem):
    later = _a_later_answer_elsewhere(mem)
    out = _forget_through_the_tool(mem, forget_source_turn=True)
    assert later in out["source_turn"]["repeated_value"]
    from kazma_core.memory.forget import FORGOTTEN_TIER

    assert mem.db.execute("SELECT tier FROM episodes WHERE id=?", (later,)).fetchone()[0] == FORGOTTEN_TIER
    assert not _recalled(mem)


def test_negative_control_the_source_turn_alone_leaves_the_repeat(mem, monkeypatch):
    from kazma_core.memory import forget

    monkeypatch.setattr(forget, "_episodes_holding", lambda conn, value, **kw: [])
    _a_later_answer_elsewhere(mem)
    _forget_through_the_tool(mem, forget_source_turn=True)
    assert _recalled(mem)


def test_a_plain_word_is_not_swept(mem):
    from kazma_core.memory.forget import _distinctive_value, forget_belief_source

    assert _distinctive_value("London") == "" and _distinctive_value("in London 4") == ""
    assert _distinctive_value("BLUE-HERON-42") == "BLUE-HERON-42"
    _chat(mem)
    _live_turn(turn=1, question="I live in London", answer="Noted.", key="th1")
    other = _live_turn(turn=1, question="Best parks in London?", answer="Hyde Park.", key="other-chat")
    fact = mutate_belief(mem.db, "user", "lives_in", "London", extraction_method="user_explicit",
                         source_session="th1", source_turn=1)
    mem.db.commit()
    out = forget_belief_source(fact["belief_id"], tenant_id="default")
    assert out["ok"] and out["repeated_value"] == []
    assert mem.db.execute("SELECT tier FROM episodes WHERE id=?", (other,)).fetchone()[0] != "forgotten"
