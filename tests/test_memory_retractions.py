"""A fact the user says stopped being true is ended (the memory framework
audit's part C2, retractions: "I stopped", "no longer").

Memory could only replace a fact with another value: "I don't live in Paris
anymore" left Paris current, and so did "I stopped using vim". Now the deep
pass may return a fact with ``"ended": true`` (naming the held fact it ends,
F4's numbers, when it is held), and Kazma ends it
(``belief_mutation.end_belief``) instead of writing it:

* Kazma's or the model's own reading ends at once (the trust gate lets the
  model change the model's fact).
* A fact the user stated ends only by the user's own say: the model's
  reading of its end is put to the user (``changes.record_pending_end``),
  only when the fact's value stands in the user's own words with no one
  else named (``clause_subject.ends_in_own_words``: an ending denies, so the
  denial is allowed; a question or someone else's ending asks nothing).
  "Yes, it ended" ends it and records an update (no new value) the user can
  undo.

Negative controls: with the ending ignored the old fact stays current; the
rule that reads statements (``names_no_one_else``) answers nothing for a
denial, which is why endings have their own.
"""

from __future__ import annotations

import sqlite3

import pytest
from kazma_core.memory import belief_extractor, changes
from kazma_core.memory.belief_extractor import _sanitize_belief
from kazma_core.memory.held_facts import held_facts
from kazma_core.memory.belief_mutation import end_belief, mutate_belief
from kazma_core.memory.clause_subject import ends_in_own_words, names_no_one_else
from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

AT = 1780266600.0


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"), isolation_level=None)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"), isolation_level=None)
    ensure_ops_schema(ops)
    yield conn, ops
    conn.close()
    ops.close()


def _fact(conn, predicate, value, *, method="llm_inferred", kind="functional", subject="user"):
    return mutate_belief(conn, subject, predicate, value, predicate_type=kind, extraction_method=method,
                         source_session="chat-1", source_turn=1, now=AT, private=True)["belief_id"]


def _current(conn):
    return sorted((r[0], r[1], r[2], r[3]) for r in conn.execute(
        "SELECT subject, predicate, object, extraction_method FROM beliefs WHERE valid_until IS NULL "
        "AND invalidated_at IS NULL"))


@pytest.mark.parametrize(("text", "value", "ends"), [
    ("I don't live in Paris anymore.", "Paris", "Paris"),
    ("I stopped using vim.", "vim", "vim"),
    ("ما عدت ساكن في الكويت.", "الكويت", "الكويت"),
    ("بطلت أستخدم فيم.", "فيم", "فيم"),
    ("My sister doesn't live in Paris anymore.", "Paris", None),
    ("أختي ما عادت ساكنة في الكويت.", "الكويت", None),
    ("Do I still live in Paris?", "Paris", None),
    ('Translate: "I don\'t live in Paris anymore."', "Paris", None),
])
def test_an_ending_in_the_users_own_words(text, value, ends):
    assert ends_in_own_words(text, value) == ends


def test_control_the_statement_rule_reads_no_denial():
    """Why endings have their own rule: a denied value states nothing."""
    assert names_no_one_else("I don't live in Paris anymore.", "Paris") is None
    assert names_no_one_else("ما عدت ساكن في الكويت.", "الكويت") is None


@pytest.mark.parametrize(("given", "kept"), [(True, True), ("true", None), (1, None), (False, None), (None, None)])
def test_ended_is_read_strictly(given, kept):
    clean = _sanitize_belief({"subject": "user", "predicate": "lives_in", "object": "Paris", "ended": given})
    assert clean.get("ended") == kept


def test_the_models_own_fact_ends_at_once_and_holds_until_it_was_said(memory):
    conn, ops = memory
    fact = _fact(conn, "uses_tool", "vim", kind="set")
    out = end_belief(conn, fact, tenant_id="default", extraction_method="llm_inferred", now=AT + 3600, ops_conn=ops)
    assert out["action"] == "ended" and out["change_id"] is None
    assert _current(conn) == []
    assert conn.execute("SELECT valid_until FROM beliefs WHERE id = ?", (fact,)).fetchone()[0] == AT + 3600
    assert changes.list_changes(conn, tenant_id="default") == []
    assert end_belief(conn, fact, tenant_id="default", extraction_method="llm_inferred")["reason"] == "not_current"


def test_the_users_fact_ends_only_by_the_users_own_say_and_can_be_undone(memory):
    conn, ops = memory
    fact = _fact(conn, "lives_in", "Paris", method="user_stated")
    refused = end_belief(conn, fact, tenant_id="default", extraction_method="llm_inferred")
    assert refused["blocked"] == "lower_trust_source"
    assert _current(conn) == [("user", "lives_in", "Paris", "user_stated")]
    asked = changes.record_pending_end(conn, tenant_id="default", belief_id=fact,
                                       user_text="I don't live in Paris anymore.", session="chat-1", turn=2)
    assert asked and changes.is_ending(changes.list_changes(conn, tenant_id="default")[0])
    # Asked once for one fact.
    assert changes.record_pending_end(conn, tenant_id="default", belief_id=fact,
                                      user_text="I don't live in Paris anymore.", session="chat-1", turn=3) is None
    out = changes.confirm(conn, asked, tenant_id="default", actor="user")
    assert out["action"] == "ended" and out["update_id"]
    assert _current(conn) == []
    rows = {r["id"]: r for r in changes.list_changes(conn, tenant_id="default", states=("open", "confirmed"))}
    assert rows[asked]["state"] == "confirmed" and changes.is_ending(rows[out["update_id"]])
    changes.undo(conn, out["update_id"], tenant_id="default", actor="user", ops_conn=ops)
    assert _current(conn) == [("user", "lives_in", "Paris", "user_stated")]


def _apply(memory, beliefs, *, user_text, held=None):
    conn, ops = memory
    return belief_extractor._apply_beliefs_to_v2(
        [dict(b, confidence=0.9) for b in beliefs], conn, ops, session_id="chat-1", turn=2, tenant_id="default",
        now=AT + 60, private=True, user_text=user_text, held=held)


def test_the_deep_pass_ends_the_models_reading_and_asks_about_the_users_fact(memory):
    conn, _ops = memory
    _fact(conn, "uses_tool", "vim", kind="set")
    _fact(conn, "lives_in", "Paris", method="user_stated")
    held = held_facts(conn, tenant_id="default", text="I stopped using vim and I don't live in Paris anymore.")
    refs = {h["object"]: h["ref"] for h in held}
    stats = _apply(memory, [
        {"subject": "user", "predicate": "uses_tool", "predicate_type": "set", "object": "vim", "ended": True,
         "replaces": refs["vim"]},
        {"subject": "user", "predicate": "lives_in", "predicate_type": "functional", "object": "Paris",
         "ended": True}], user_text="I stopped using vim and I don't live in Paris anymore.", held=held)
    assert stats["ended"] == 1 and stats["end_asked"] == 1
    assert _current(conn) == [("user", "lives_in", "Paris", "user_stated")]
    [waiting] = [c for c in changes.list_changes(conn, tenant_id="default") if c["kind"] == "pending"]
    assert waiting["old_value"] == "Paris" and changes.is_ending(waiting)


def test_someone_elses_ending_and_an_unknown_one_change_nothing(memory):
    conn, _ops = memory
    _fact(conn, "lives_in", "الكويت", method="user_stated")
    stats = _apply(memory, [
        {"subject": "user", "predicate": "lives_in", "predicate_type": "functional", "object": "الكويت",
         "ended": True},
        {"subject": "user", "predicate": "uses_tool", "predicate_type": "set", "object": "emacs", "ended": True}],
        user_text="أختي ما عادت ساكنة في الكويت.")
    assert stats.get("end_asked", 0) == 0 and stats["ended_unknown"] == 1
    assert _current(conn) == [("user", "lives_in", "الكويت", "user_stated")]
    assert changes.list_changes(conn, tenant_id="default") == []


def test_control_with_the_ending_ignored_the_fact_stays(memory, monkeypatch):
    conn, _ops = memory
    _fact(conn, "uses_tool", "vim", kind="set")
    real = belief_extractor._sanitize_belief

    def no_ending(b):
        clean = real(b)
        if clean:
            clean.pop("ended", None)
        return clean

    monkeypatch.setattr(belief_extractor, "_sanitize_belief", no_ending)
    _apply(memory, [{"subject": "user", "predicate": "uses_tool", "predicate_type": "set", "object": "vim",
                     "ended": True}], user_text="I stopped using vim.")
    assert _current(conn) == [("user", "uses_tool", "vim", "llm_inferred")]


def test_the_note_and_the_chat_command_say_an_ending(memory, monkeypatch):
    conn, _ops = memory
    fact = _fact(conn, "lives_in", "Paris", method="user_stated")
    asked = changes.record_pending_end(conn, tenant_id="default", belief_id=fact,
                                       user_text="I don't live in Paris anymore.", session="chat-1", turn=2)
    note = changes.format_changes_note(changes.list_changes(conn, tenant_id="default"))
    assert 'still "Paris"; their message may have meant it is no longer true' in note
    out = changes.confirm(conn, asked, tenant_id="default")
    rows = changes.list_changes(conn, tenant_id="default")
    assert 'ended from the user\'s own words: lives in: no longer "Paris"' in changes.format_changes_note(rows)
    from kazma_gateway import slash_commands

    monkeypatch.setattr(changes, "open_changes", lambda **_kw: rows)
    listing = slash_commands._memory_changes("changes", ["/memory", "changes"], "default")
    assert "lives in: no longer Paris. Undo it: `/memory undo" in listing
    assert out["update_id"]
