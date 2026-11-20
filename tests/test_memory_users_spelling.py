"""A value is kept in the user's own spelling (framework audit F1's third
fault; part C2, "the user's spelling asked for").

The extraction prompt asked for slugs ("Slug subjects/objects"), so the
user's "Layla" was stored as "layla" and "Kuwait City" as "kuwait_city", and
the Memory page and recall said "user name is layla". The prompt now asks
for an object as the user wrote it, and ``belief_extractor._users_spelling``
keeps the user's spelling whatever the model wrote: the run of the message's
words that is the same value (case, underscores, spacing, Arabic forms and
marks folded). Trust is not touched: the fact stays the model's reading.

Negative control: the apply step without the respelling stores the slug.
"""

from __future__ import annotations

import sqlite3

import pytest
from kazma_core.memory import belief_extractor
from kazma_core.memory.belief_extractor import _EXTRACT_SYSTEM, _users_spelling
from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

AT = 1780266600.0


@pytest.mark.parametrize(("value", "text", "kept"), [
    ("kuwait_city", "We moved to Kuwait City last month.", "Kuwait City"),
    ("gulf bank", "I work at Gulf Bank now", "Gulf Bank"),
    ("layla", "My name is Layla.", "Layla"),
    ("Layla", "my name is layla", "layla"),                     # the user's own lower case
    ("سلمى", "اسمي سَلمى", "سَلمى"),                             # the user's marks kept
    ("الدوحة", "أعيش في الدوحه", "الدوحه"),                       # the user's letter form
    ("lisbon", "No, Lisbon.", "Lisbon"),
])
def test_the_users_spelling_is_kept(value, text, kept):
    assert _users_spelling(value, text) == kept


@pytest.mark.parametrize(("value", "text"), [
    ("kuwait_city", "I live in Kuwait-City."),                  # joined otherwise: not one value
    ("kuwait_city", "I live in Kuwait."),                       # not in the message
    ("user", "I am the user."),
    ("paris", ""),
    ("a b c d e f g h i", "a b c d e f g h i"),                 # longer than a value is looked for
])
def test_otherwise_the_value_is_left_as_it_is(value, text):
    assert _users_spelling(value, text) == value


def test_the_prompt_asks_for_the_users_spelling():
    assert "Slug subjects/objects" not in _EXTRACT_SYSTEM
    assert "as the user wrote it, in their spelling and capitals" in _EXTRACT_SYSTEM


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


def _stored(memory, beliefs, user_text):
    conn, ops = memory
    belief_extractor._apply_beliefs_to_v2(
        [dict(b, confidence=0.9) for b in beliefs], conn, ops, session_id="chat-1", turn=1, tenant_id="default",
        now=AT, private=True, user_text=user_text)
    return sorted((r[0], r[1], r[2], r[3]) for r in conn.execute(
        "SELECT subject, predicate, object, extraction_method FROM beliefs WHERE valid_until IS NULL"))


_READING = [
    {"subject": "user", "predicate": "lives_in", "predicate_type": "functional", "object": "kuwait_city"},
    {"subject": "hana", "predicate": "works_at", "predicate_type": "functional", "object": "gulf_bank"},
]
_TURN = "We moved to Kuwait City last month, and my friend Hana works at Gulf Bank."


def test_the_deep_pass_stores_the_users_spelling_and_keeps_it_the_models_reading(memory):
    assert _stored(memory, _READING, _TURN) == [
        ("hana", "works_at", "Gulf Bank", "llm_inferred"),
        ("user", "lives_in", "Kuwait City", "llm_inferred"),
    ]


def test_control_without_the_respelling_the_slug_is_stored(memory, monkeypatch):
    monkeypatch.setattr(belief_extractor, "_users_spelling", lambda value, _text: value)
    assert [row[2] for row in _stored(memory, _READING, _TURN)] == ["gulf_bank", "kuwait_city"]
