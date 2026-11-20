"""The deep pass is shown the facts Kazma holds and may say which one a new
fact replaces (framework audit F4, second layer; part C2).

The model chooses each fact's name freely, and Kazma merges two names only
when they are the same words, so "hana occupation pilot" then "hana works_as
captain" left Hana a pilot and a captain. Now the memory worker shows the
model the current facts about the turn's people and things, numbered
(``held_facts``: the speaker's own first, then each subject the turn names;
never another member's entry, another space's fact or a forgotten one), and
a fact that says ``"replaces": "f1"`` goes by that fact's name and kind --
only for the same subject, and only while it is current; the trust gate
still decides (a fact the user stated yields only to the user's own words).
A replaced list entry is closed when the gate lets the source change it.

Negative control: with "replaces" ignored, both facts stay current.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from types import MappingProxyType
from unittest import mock

import pytest
from kazma_core.memory import belief_extractor, worker_bootstrap
from kazma_core.memory.belief_extractor import _sanitize_belief, held_facts, turn_message
from kazma_core.memory.belief_mutation import mutate_belief
from kazma_core.memory.limits import DEEP_PASS_HELD_FACTS, DEEP_PASS_HELD_VALUE
from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
from kazma_core.memory.speakers import Member, Room

AT = 1780266600.0


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
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


def _fact(conn, subject, predicate, value, *, kind="functional", method="llm_inferred", tenant="default", at=AT):
    return mutate_belief(conn, subject, predicate, value, predicate_type=kind, extraction_method=method,
                         tenant_id=tenant, source_session="chat-1", source_turn=1, now=at, private=True)


def _current(conn, tenant="default"):
    return sorted((r[0], r[1], r[2]) for r in conn.execute(
        "SELECT subject, predicate, object FROM beliefs WHERE tenant_id = ? AND valid_until IS NULL "
        "AND invalidated_at IS NULL", (tenant,)))


def test_held_facts_are_the_speakers_then_the_named_subjects(memory):
    conn, _ops = memory
    _fact(conn, "user", "lives_in", "Kuwait", at=AT - 10)
    _fact(conn, "user", "works_at", "Zain", at=AT)
    _fact(conn, "hana", "occupation", "pilot")
    _fact(conn, "omar", "occupation", "chef")                      # not named in the turn
    _fact(conn, "hana", "lives_in", "Doha", tenant="space_b")      # another space
    gone = _fact(conn, "hana", "allergic_to", "peanuts", kind="set")
    conn.execute("UPDATE beliefs SET object = '' WHERE id = ?", (gone["belief_id"],))  # forgotten
    held = held_facts(conn, tenant_id="default", text="Hana is a captain now.")
    assert [(h["ref"], h["shown"], h["predicate"], h["object"]) for h in held] == [
        ("f1", "user", "works_at", "Zain"), ("f2", "user", "lives_in", "Kuwait"),
        ("f3", "hana", "occupation", "pilot")]


def test_in_a_shared_space_the_speakers_entry_is_shown_as_user_and_no_other_members(memory, monkeypatch):
    conn, _ops = memory
    room = Room(space="shared", shared=True, members=MappingProxyType({
        "sara": Member(account="sara", name="Sara"), "omar": Member(account="omar", name="Omar")}))
    monkeypatch.setattr("kazma_core.memory.speakers.room", lambda _tenant: room)
    _fact(conn, "member_sara", "lives_in", "Kuwait", tenant="shared")
    _fact(conn, "member_omar", "lives_in", "Doha", tenant="shared")
    held = held_facts(conn, tenant_id="shared", text="Omar and I moved.", said_by="sara")
    assert [(h["shown"], h["subject"], h["object"]) for h in held] == [("user", "member_sara", "Kuwait")]


def test_held_facts_are_bounded(memory):
    conn, _ops = memory
    for i in range(DEEP_PASS_HELD_FACTS + 5):
        _fact(conn, "user", "uses_tool", f"tool{i}", kind="set", at=AT + i)
    assert len(held_facts(conn, tenant_id="default", text="hi")) == DEEP_PASS_HELD_FACTS
    message = turn_message("I switched.", held=[{"ref": "f1", "shown": "user", "predicate": "notes",
                                                  "object": "line\none " + "w" * 1000}])
    assert "f1. user notes line one " in message and message.count("w") <= DEEP_PASS_HELD_VALUE


def test_the_message_numbers_the_held_facts_and_the_prompt_asks_for_replaces():
    message = turn_message("Hana is a captain now.", held=[
        {"ref": "f1", "shown": "hana", "predicate": "occupation", "object": "pilot"}])
    assert "Facts Kazma holds now about this turn's people and things" in message
    assert "\nf1. hana occupation pilot\n\nThis turn:\nUser: Hana is a captain now." in message
    prompt = belief_extractor._EXTRACT_SYSTEM
    assert '"replaces"' in prompt and "Only a held fact about the same\n  subject" in prompt


@pytest.mark.parametrize(("given", "kept"), [("f2", "f2"), (" F12 ", "f12"), ("x2", None), ("f", None),
                                             ("2", None), ("f1234", None), (None, None)])
def test_a_replaces_number_is_read_strictly(given, kept):
    clean = _sanitize_belief({"subject": "hana", "predicate": "works_as", "object": "captain", "replaces": given})
    assert clean.get("replaces") == kept


def _apply(memory, beliefs, held, *, user_text="Hana is a captain now."):
    conn, ops = memory
    stats = belief_extractor._apply_beliefs_to_v2(
        [dict(b, confidence=0.9) for b in beliefs], conn, ops, session_id="chat-1", turn=2, tenant_id="default",
        now=AT + 60, private=True, user_text=user_text, held=held)
    return stats


def test_a_fact_that_replaces_a_held_one_supersedes_it(memory):
    conn, _ops = memory
    _fact(conn, "hana", "occupation", "pilot")
    held = held_facts(conn, tenant_id="default", text="Hana is a captain now.")
    _apply(memory, [{"subject": "hana", "predicate": "works_as", "predicate_type": "functional",
                     "object": "captain", "replaces": held[0]["ref"]}], held)
    assert _current(conn) == [("hana", "occupation", "captain")]


def test_control_with_replaces_ignored_both_stay(memory, monkeypatch):
    conn, _ops = memory
    _fact(conn, "hana", "occupation", "pilot")
    held = held_facts(conn, tenant_id="default", text="Hana is a captain now.")
    monkeypatch.setattr(belief_extractor, "_take_replaces", lambda clean, *_a: clean.pop("replaces", None) and None)
    _apply(memory, [{"subject": "hana", "predicate": "works_as", "predicate_type": "functional",
                     "object": "captain", "replaces": held[0]["ref"]}], held)
    assert _current(conn) == [("hana", "occupation", "pilot"), ("hana", "works_as", "captain")]


def test_replaces_is_refused_for_another_subject_or_a_fact_no_longer_current(memory):
    conn, _ops = memory
    _fact(conn, "hana", "occupation", "pilot")
    held = held_facts(conn, tenant_id="default", text="Unlike Hana, I teach.")
    _apply(memory, [{"subject": "user", "predicate": "teaches", "predicate_type": "functional",
                     "object": "maths", "replaces": "f1"}], held, user_text="Unlike Hana, I teach.")
    assert ("hana", "occupation", "pilot") in _current(conn)
    # The held fact changed after the model was shown it: the number names
    # nothing current, and the new fact is written as it came.
    conn.execute("UPDATE beliefs SET valid_until = ?, invalidated_at = ? WHERE subject = 'hana'", (AT, AT))
    _apply(memory, [{"subject": "hana", "predicate": "works_as", "predicate_type": "functional",
                     "object": "captain", "replaces": "f1"}], held)
    assert ("hana", "works_as", "captain") in _current(conn)


def test_the_trust_gate_still_decides_for_a_replaced_fact_the_user_stated(memory):
    conn, _ops = memory
    _fact(conn, "user", "uses_tool", "vim", kind="set", method="user_stated")
    held = held_facts(conn, tenant_id="default", text="I switched to emacs.")
    stats = _apply(memory, [{"subject": "user", "predicate": "editor", "predicate_type": "set",
                             "object": "emacs", "replaces": "f1"}], held, user_text="I switched to emacs.")
    # The user's own entry stays; the model's reading is added beside it.
    assert ("user", "uses_tool", "vim") in _current(conn) and ("user", "uses_tool", "emacs") in _current(conn)
    assert stats["replace_refused"] == 1


def test_a_replaced_list_entry_the_model_wrote_is_closed(memory):
    conn, _ops = memory
    _fact(conn, "user", "uses_tool", "vim", kind="set")
    held = held_facts(conn, tenant_id="default", text="I switched to emacs.")
    _apply(memory, [{"subject": "user", "predicate": "editor", "predicate_type": "set",
                     "object": "emacs", "replaces": "f1"}], held, user_text="I switched to emacs.")
    assert _current(conn) == [("user", "uses_tool", "emacs")]


def test_the_worker_shows_the_held_facts_and_honours_replaces(memory):
    conn, _ops = memory
    _fact(conn, "hana", "occupation", "pilot", at=AT - 600)
    conn.execute("INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
                 "created_at, tier) VALUES ('e2', 'default', 'chat-1', 2, 'Hana is a captain now.', '', ?, "
                 "'episodic')", (AT,))
    sent: list[str] = []

    class Client:
        async def chat(self, messages, **_kw):
            sent.append(messages[-1]["content"])
            return json.dumps({"beliefs": [{"subject": "hana", "predicate": "works_as", "predicate_type": "functional",
                                            "object": "captain", "confidence": 0.9, "replaces": "f1"}]})

    registry = type("R", (), {"get_client": lambda self: Client()})()
    with mock.patch("kazma_core.model_registry.get_model_registry", lambda: registry):
        assert asyncio.run(worker_bootstrap._handle_micro_consolidation({"episode_id": "e2"}))
    assert "f1. hana occupation pilot" in sent[0]
    # (The live writer also links Hana to the user's hub: user related_to hana.)
    assert [f for f in _current(conn) if f[0] == "hana"] == [("hana", "occupation", "captain")]
