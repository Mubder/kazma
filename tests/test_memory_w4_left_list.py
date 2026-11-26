"""W4's left list, by harm (the memory plan's W6, step 4).

The final check of W4 (9ea531ce1) left seven small ones. Three are built here:

* **L1:** "Not only Ali, I met Omar", "ليس فقط علي", "مو بس علي", "No, I didn't
  see Ali alone, I met Omar too" and "Nothing is wrong, I met Omar" closed
  the meeting with Ali when the model wrongly named it replaced and no day
  was given. A denial that adds ("not only", "alone") denies nothing, and
  "wrong" said of nothing puts nothing right.
* **L3:** a whole message "No, Omar" / "لا، عمر" / "كلا، عمر" whose reply
  names the fact replaced puts it right. Bare, with no reply naming it,
  nothing changes.
* **L4:** MSA "رسالة صوتية: ..." (a voice note) lost the user's own event,
  read as someone else's message; "Voice note:" and Gulf "فويس:" did not.
  "رسالة صوتية من عمر:" stays Omar's. "Omar's voice note:" is the user's
  in doubt, as any label line is (W4's L2, declared).

The others are left, with why (``docs/agents/15h-facts.md``). The classes:
every denial word before every "only" word adds; every text noun before
every medium word is the user's own. Both are read from the source's own
tables, with the rule taken away as the negative control.
"""

from __future__ import annotations

import sqlite3

import pytest
from kazma_core.memory import belief_extractor, event_text, own_words
from kazma_core.memory.held_facts import held_facts
from kazma_core.memory.schema_v2 import ensure_primary_schema

DAY = 86400.0
SAID = 1_780_200_000.0  # 2026-05-31


@pytest.fixture()
def conn(monkeypatch):
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    ensure_primary_schema(c)
    yield c
    c.close()


def _event(name, value, on=None, **more):
    return {"subject": "user", "predicate": name, "predicate_type": "set", "object": value, "confidence": 0.9,
            "importance": 2, **({"on": on} if on else {}), **more}


def _turn(conn, text, beliefs, at, turn):
    held = held_facts(conn, tenant_id="default", text=text)
    belief_extractor._apply_beliefs_to_v2([dict(b) for b in beliefs], conn, None, session_id="c", turn=turn,
                                          tenant_id="default", now=at, private=True, user_text=text, held=held)


def _met(conn, first, said, new, text, *, replaces=True, on=None):
    """I met *first* yesterday; then *text*, whose reply says it met *new*
    (and, with *replaces*, that this replaces the meeting held)."""
    _turn(conn, said, [_event("met", first, "2026-05-30")], SAID, 1)
    _turn(conn, text, [_event("met", new, on, **({"replaces": "f1"} if replaces else {}))], SAID + DAY, 2)
    return sorted(r[0] for r in conn.execute(
        "SELECT object FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL AND predicate = 'met'"))


EN = ("Ali", "I met Ali yesterday", "Omar")
GULF = ("علي", "قابلت علي أمس", "عمر")
MSA = ("علي", "التقيت بعلي أمس", "عمر")

#: L1: a denial that adds, or "wrong" said of nothing: both meetings stay.
ADDS = [
    (EN, "Not only Ali, I met Omar"), (EN, "Not just Ali, I met Omar too"),
    (EN, "No, I didn't see Ali alone, I met Omar too"), (EN, "Nothing is wrong, I met Omar"),
    (GULF, "مو بس علي، قابلت عمر"), (GULF, "مب بس علي، قابلت عمر بعد"), (GULF, "لا ما رحت بروحي، قابلت عمر بعد"),
    (GULF, "ما في شي غلط، قابلت عمر"),
    (MSA, "ليس فقط علي، التقيت بعمر"), (MSA, "لم ألتق بعلي وحده، التقيت بعمر أيضا"), (MSA, "لا يوجد خطأ، التقيت بعمر"),
]


@pytest.mark.parametrize(("who", "text"), ADDS)
def test_a_denial_that_adds_closes_nothing(conn, who, text):
    first, said, new = who
    assert _met(conn, first, said, new, text) == sorted([first, new])


#: Real corrections, in the three ways: one meeting, the new one.
PUT_RIGHT = [
    (EN, "No, Omar"), (EN, "Nope, Omar"), (EN, "No! Omar, I told you"), (EN, "No, not Ali. Omar"), (EN, "No, just Omar"),
    (GULF, "لا، عمر"), (GULF, "لأ، عمر"), (GULF, "لا يا كازما، عمر"), (GULF, "لا مو علي، عمر"),
    (MSA, "كلا، عمر"), (MSA, "لا، بل عمر"), (MSA, "لا، لم يكن علي. عمر"),
]


@pytest.mark.parametrize(("who", "text"), PUT_RIGHT)
def test_no_and_the_value_puts_it_right(conn, who, text):
    first, said, new = who
    assert _met(conn, first, said, new, text) == [new]


@pytest.mark.parametrize(("who", "text"), [(EN, "No, Omar"), (GULF, "لا، عمر"), (MSA, "كلا، عمر")])
def test_control_a_bare_no_whose_reply_names_nothing_changes_nothing(conn, who, text):
    """L3 is read only beside a reply that names the fact replaced."""
    first, said, new = who
    assert _met(conn, first, said, new, text, replaces=False) == sorted([first, new])


@pytest.mark.parametrize(("text", "value", "whole"), [
    ("No, Omar", "Omar", True), ("No, Omar and Sara", "Omar", False), ("No, I met Omar", "Omar", False),
    ("No.", "Omar", False), ("Omar", "Omar", False), ("No, Omar Al-Harbi", "Omar Al-Harbi", True),
    ("لا، عمر", "عمر", True), ("كلا، عمر", "عمر", True), ("لا، عمر وسارة", "عمر", False)])
def test_no_then_is_the_whole_message(text, value, whole):
    assert event_text.no_then(text, value) is whole


# ── L4: a voice note is the user's own ───────────────────────────────────

def _kept(conn, text, value):
    _turn(conn, text, [_event("attended", value, "2026-05-28")], SAID, 1)
    return [r[0] for r in conn.execute("SELECT object FROM beliefs WHERE valid_until IS NULL AND predicate = 'attended'")]


@pytest.mark.parametrize(("text", "value"), [
    ("Voice note: I attended my cousin's wedding on Thursday.", "my cousin's wedding"),
    ("Voice message: I attended my cousin's wedding on Thursday.", "my cousin's wedding"),
    ("فويس: حضرت عرس ولد عمي يوم الخميس.", "عرس ولد عمي"), ("رسالة صوتية: حضرت عرس ولد عمي يوم الخميس.", "عرس ولد عمي"),
    ("رسالة صوتية: حضرتُ زفاف ابن عمي يوم الخميس.", "زفاف ابن عمي"),
    ("رسالة نصية: حضرتُ زفاف ابن عمي يوم الخميس.", "زفاف ابن عمي")])
def test_a_voice_note_label_keeps_the_users_event(conn, text, value):
    assert _kept(conn, text, value) == [value]


@pytest.mark.parametrize(("text", "value"), [
    ("رسالة صوتية من عمر: حضرت عرس ولد عمي يوم الخميس.", "عرس ولد عمي"),
    ("رسالة صوتية من عمر: حضرتُ زفاف ابن عمي يوم الخميس.", "زفاف ابن عمي")])
def test_control_someone_elses_voice_note_is_theirs(conn, text, value):
    assert _kept(conn, text, value) == []


# ── the classes, from the source's own tables ────────────────────────────

def _denials() -> list[str]:
    from kazma_core.documents.arabic import fold_for_search

    plain = sorted(event_text._DENIALS_EN - {"actually", "correct", "rather", "instead"})
    return plain + sorted({fold_for_search(w) for w in event_text._DENIALS_AR_RAW} - {"بل", "الصحيح"})


def test_every_denial_before_every_only_word_adds():
    for denial in (d for d in _denials() if d not in event_text._BARE_NO):
        for only in event_text._ONLY:
            assert event_text._adds_rather_than_denies(f"{denial} {only} X, Y"), (denial, only)
        for alone in event_text._ALONE:
            assert event_text._adds_rather_than_denies(f"{denial} X {alone}, Y"), (denial, alone)
    # A bare "no" before "only" puts right: "No, just Omar", "لا بس عمر".
    for no in event_text._BARE_NO:
        for only in event_text._ONLY:
            assert not event_text._adds_rather_than_denies(f"{no} {only} X"), (no, only)
    # Control: "not only" read whole.
    assert not event_text._adds_rather_than_denies("Not Ali, Omar")


def test_control_without_the_rule_a_denial_that_adds_denies(monkeypatch):
    monkeypatch.setattr(event_text, "_adds_rather", lambda words, at: False)
    assert event_text.denies("Not only Ali, I met Omar", "Ali") and event_text.denies("مو بس علي، قابلت عمر", "علي")


def test_every_text_noun_before_every_medium_is_the_users_own():
    for noun in own_words._TEXT_NOUNS_AR:
        for medium in own_words._MEDIUM_AR:
            assert not own_words._names_someones_text_ar([noun, medium]), (noun, medium)
            assert own_words._names_someones_text_ar(own_words._ar_words(f"{noun} {medium} من عمر")), (noun, medium)


def test_control_without_the_medium_a_voice_note_is_someone_elses(monkeypatch):
    monkeypatch.setattr(own_words, "_MEDIUM_AR", frozenset())
    assert own_words._names_someones_text_ar(["رساله", "صوتيه"])
