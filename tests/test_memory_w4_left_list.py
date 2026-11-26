"""W4's left list, by harm (the memory plan's W6, step 4).

The final check of W4 (9ea531ce1) left seven small ones. Built here:

* **L1, in part:** "Nothing is wrong, I met Omar", "ما في شي غلط" put nothing
  right; "لا غلط، عمر" still does (never a bare "لا"), and "خطأ" corrects
  only as an answer ("لا خطأ، كان عمر"). L1's "adds" rules ("not only Ali",
  "alone", "only ... too") were taken out after six review rounds: a denial
  behaves exactly as before step 4. Their cases wait in the notes folder
  (``memory-w6/adds-rules-later.md``) for a step of their own.
* **L3:** a whole message "No, Omar" / "لا، عمر" / "كلا، عمر" whose reply
  names the fact replaced puts it right. Bare, with no reply naming it,
  nothing changes; "No, Omar too", "No, not Omar", "لا، وعمر" are not it.
* **L4:** MSA "رسالة صوتية: ..." (a voice note) lost the user's own event,
  read as someone else's message; "Voice note:" and Gulf "فويس:" did not.
  "رسالة صوتية من عمر:" stays Omar's; "مني" and "لأمي" stay the user's.

The reviews' corrections are put-right rows in EN, Gulf and MSA, each
correct as before step 4. The others are left, with why
(``docs/agents/15h-facts.md``). The class: every text noun before every
medium word is the user's own, read from the source's own tables, with the
rule taken away as the negative control.
"""

from __future__ import annotations

import json
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

#: "Wrong" said of nothing puts nothing right, and "خطأ" said of something
#: else neither: both meetings stay. (The adds rules of W4's L1 -- "not only",
#: "alone", "only ... too" -- were taken out of step 4 after six review rounds:
#: a denial behaves as before step 4. Their cases are in the notes folder,
#: ``memory-w6/adds-rules-later.md``, for a step of their own.)
ADDS = [
    (EN, "Nothing is wrong, I met Omar"), (GULF, "ما في شي غلط، قابلت عمر"), (MSA, "لا يوجد خطأ، التقيت بعمر"),
    (GULF, "لا ما رحت بروحي، قابلت عمر بعد"), (GULF, "ما قابلت علي بروحه، عمر كان معاه"),
    (GULF, "قابلت عمر بعد، وكان خطأ اني ما عزمت علي"),
]


@pytest.mark.parametrize(("who", "text"), ADDS)
def test_wrong_said_of_nothing_closes_nothing(conn, who, text):
    first, said, new = who
    assert _met(conn, first, said, new, text) == sorted([first, new])


#: Real corrections, in the three ways: one meeting, the new one.
PUT_RIGHT = [
    (EN, "No, Omar"), (EN, "Nope, Omar"), (EN, "No! Omar, I told you"), (EN, "No, not Ali. Omar"), (EN, "No, just Omar"),
    (GULF, "لا، عمر"), (GULF, "لأ، عمر"), (GULF, "لا يا كازما، عمر"), (GULF, "لا مو علي، عمر"),
    (MSA, "كلا، عمر"), (MSA, "لا، بل عمر"), (MSA, "لا، لم يكن علي. عمر"),
    # The commonest Gulf correction, never "nothing is wrong" (step 4's review, H2).
    (GULF, "لا غلط، عمر"), (GULF, "لا غلط كان عمر"), (GULF, "لا غلط عمر"), (MSA, "لا خطأ، كان عمر"),
    (EN, "No - Omar"), (EN, "Nope Omar!"), (EN, "no, it's Omar"), (GULF, "لا، هو عمر"),
    # "Alone" said of someone else or of the user, unpunctuated: the denial
    # stands (step 4's review, H1: each closed nothing at 98a4cfce4).
    (EN, "No I didn't meet Ali I was home alone"), (EN, "Not Ali I met Omar alone"), (EN, "Not Ali - I met Omar alone"),
    (EN, "it wasn't Ali I was alone with Omar"), (EN, "I didn't meet Ali because I was home alone, I met Omar later"),
    (GULF, "لا ما قابلت علي كنت بروحي مع عمر"), (GULF, "مو علي قابلت عمر بروحه"), (GULF, "مو علي - قابلت عمر لحاله"),
    (GULF, "مو علي وحده ثانيه اسمها عمر"),
    (MSA, "لم ألتق بعلي بل التقيت بعمر وحده"), (MSA, "لم ألتق بعلي فقد كنت وحدي مع عمر"),
    # "Only" before the new value is the new value's (step 4's delta review, N1:
    # each kept Ali at e4772d2ac).
    (EN, "Not Ali just Omar"), (EN, "not Ali only Omar"), (EN, "It wasn't Ali just Omar"),
    (EN, "I didn't meet Ali just Omar"), (EN, "No not Ali just Omar"), (EN, "Not Ali simply Omar"),
    (EN, "I didn't see Ali just now, it was Omar"),
    (GULF, "مو علي بس عمر"), (GULF, "لا مو علي بس عمر"), (GULF, "مب علي بس عمر"), (GULF, "لا ما قابلت علي بس عمر"),
    (GULF, "مو علي بس عمر اللي قابلته"), (GULF, "مو علي بس، عمر"),
    (MSA, "ليس علي فقط عمر"), (MSA, "لم ألتق بعلي فقط بعمر"),
    # "Again" or a "too" of a later clause adds nothing to "Not Ali just Omar"
    # (step 4's third review, B: each kept Ali at de794c799).
    (EN, "Not Ali just Omar. I also went to the gym"), (EN, "Not Ali just Omar, and then we went to the gym again"),
    (EN, "Not Ali just Omar, I saw him again today"), (EN, "Not Ali just Omar again"),
    (GULF, "مو علي بس عمر، وكمان رحنا السوق"), (GULF, "مو علي بس عمر، وبعدين رحنا المطعم مره ثانيه"),
    (MSA, "ليس علي فقط عمر، ثم ذهبنا إلى المطعم مرة أخرى"),
    # A "too" in the clause not right after the new value adds nothing (step 4's
    # fourth review: each kept Ali at a9cb57a51; base denied them all).
    (EN, "Not Ali just Omar who was too tired to stay"), (EN, "Not Ali only Omar plus his wife"),
    (EN, "Not Ali just Omar and he was also late"), (EN, "Not Ali just Omar as well as Sara"),
    (EN, "Not Ali just Omar but Sara too"), (EN, "I didn't meet Ali just Omar and his brother too"),
    (GULF, "مو علي بس عمر وجاب ولده بعد"), (GULF, "مو علي بس عمر واخوه بعد"), (GULF, "مو علي بس عمر وكان متأخر كمان"),
    (MSA, "لم ألتق بعلي فقط بعمر وكان متعبا أيضا"), (MSA, "ليس علي فقط عمر وأخوه كذلك"),
    # "Not only" in another sentence leaves this one's "No" a denial (step 4's
    # fifth review: each kept Ali from step 4's first commit; base denied).
    (EN, "No. I met Omar, Ali was at work. It wasn't just a coffee either"),
    (GULF, "لا، قابلت عمر، علي كان بالدوام. مو بس قهوة، تغدينا سوا"),
    (MSA, "لا، التقيت بعمر، وكان علي في العمل. اللقاء ليس فقط للقهوة"),
    (EN, "Ali? No. Just Omar. Ali was not only busy, he was abroad."),
    (GULF, "علي؟ لا. بس عمر. علي مو بس مشغول، كان مسافر"), (MSA, "علي؟ لا. عمر فقط. علي ليس فقط مشغولا، بل مسافر"),
]


#: A counted day ("yesterday") put right with "alone" in the message: the
#: one visit moves to the new day, the old one kept as ``day_before`` (step
#: 4's review, H1: each kept the wrong day as ``day_sure`` at 98a4cfce4).
DAY_PUT_RIGHT = [
    ("I went to the gym yesterday", "gym", "Actually I went to the gym alone on Thursday"),
    ("I went to the gym yesterday", "gym", "No I went to the gym alone on Thursday"),
    ("رحت النادي أمس", "النادي", "لا رحت النادي بروحي يوم الخميس"),
    ("ذهبت إلى النادي أمس", "النادي", "بل ذهبت إلى النادي وحدي يوم الخميس"),
    # "Not only" in another sentence (step 4's fifth review: each kept the old day).
    ("I went to the gym yesterday", "gym", "No, Thursday. I'm not just guessing, I checked"),
    ("I went to the gym yesterday", "gym", "Thursday, not yesterday. Not just the gym, I went to the pool too"),
    ("I went to the gym yesterday", "gym", "No, on Thursday. It isn't only the gym I go to, I swim too"),
    ("رحت النادي أمس", "النادي", "لا، الخميس. مو بس النادي، رحت المسبح بعد"),
    ("رحت النادي أمس", "النادي", "لا، الخميس مو أمس. مو بس تمرين خفيف، تمرنت ساعتين"),
    ("ذهبت إلى النادي أمس", "النادي", "لا، يوم الخميس وليس أمس. ليس فقط النادي، بل المسبح أيضا"),
    ("ذهبت إلى النادي أمس", "النادي", "لا، يوم الخميس. الأمر ليس فقط تخمينا، فقد تأكدت"),
]


@pytest.mark.parametrize(("said", "value", "text"), DAY_PUT_RIGHT)
def test_a_day_put_right_with_alone_moves_the_visit(conn, said, value, text):
    _turn(conn, said, [_event("visited", value, "2026-05-30")], SAID, 1)
    _turn(conn, text, [_event("visited", value, "2026-05-28", replaces="f1")], SAID + DAY, 2)
    rows = [json.loads(r[0] or "{}") for r in conn.execute(
        "SELECT metadata_json FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL "
        "AND predicate = 'visited'")]
    assert len(rows) == 1 and rows[0].get("started") == "2026-05-28" and rows[0].get("day_before"), rows


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
    ("لا، عمر", "عمر", True), ("كلا، عمر", "عمر", True), ("لا، عمر وسارة", "عمر", False),
    # Denies or adds, never "no, the value" (step 4's review, M1); a vocative is no value.
    ("No, not Omar", "Omar", False), ("No, Omar too", "Omar", False), ("No, Omar also", "Omar", False),
    ("No, and Omar", "Omar", False), ("No, Omar was there too", "Omar", False), ("لا، عمر بعد", "عمر", False),
    ("لا، مو عمر", "عمر", False), ("لا، وعمر بعد", "عمر", False), ("كلا، ليس عمر", "عمر", False),
    ("لا يا عمر", "عمر", False), ("No - Omar", "Omar", True), ("Nope Omar!", "Omar", True),
    ("no, it's Omar", "Omar", True), ("لا، هو عمر", "عمر", True),
    # "And" on the value's front adds in Arabic as in English; articles and
    # prepositions are no new words (step 4's delta review, N3).
    ("لا، وعمر", "عمر", False), ("لا، فعمر", "عمر", False), ("لا، وليد", "وليد", True),
    ("No, the gym", "the gym", True), ("No, my dentist", "my dentist", True), ("No, at the gym", "the gym", True),
    ("No, with Omar", "Omar", True), ("No, in Dubai", "Dubai", True), ("لا، في دبي", "دبي", True),
    ("لا، مع عمر", "عمر", True), ("كلا، في دبي", "دبي", True),
    # The fillers' twins (the third review, E).
    ("No, on Thursday", "Thursday", True), ("لا، يوم الخميس", "الخميس", True), ("No, actually Omar", "Omar", True),
    ("لا، الصحيح عمر", "عمر", True)])
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
    ("رسالة نصية: حضرتُ زفاف ابن عمي يوم الخميس.", "زفاف ابن عمي"),
    # From the user, or to their own (step 4's review, L3).
    ("رسالة صوتية مني: حضرت عرس ولد عمي يوم الخميس.", "عرس ولد عمي"),
    ("رسالة صوتية لأمي: حضرتُ زفاف ابن عمي يوم الخميس.", "زفاف ابن عمي")])
def test_a_voice_note_label_keeps_the_users_event(conn, text, value):
    assert _kept(conn, text, value) == [value]


@pytest.mark.parametrize(("text", "value"), [
    ("رسالة صوتية من عمر: حضرت عرس ولد عمي يوم الخميس.", "عرس ولد عمي"),
    ("رسالة صوتية من عمر: حضرتُ زفاف ابن عمي يوم الخميس.", "زفاف ابن عمي"),
    ("رسالة صوتية من أخوي: حضرت عرس ولد عمي يوم الخميس.", "عرس ولد عمي")])
def test_control_someone_elses_voice_note_is_theirs(conn, text, value):
    assert _kept(conn, text, value) == []


# ── the classes, from the source's own tables ────────────────────────────

def test_every_text_noun_before_every_medium_is_the_users_own():
    for noun in own_words._TEXT_NOUNS_AR:
        for medium in own_words._MEDIUM_AR:
            assert not own_words._names_someones_text_ar([noun, medium]), (noun, medium)
            assert own_words._names_someones_text_ar(own_words._ar_words(f"{noun} {medium} من عمر")), (noun, medium)


def test_control_without_the_medium_a_voice_note_is_someone_elses(monkeypatch):
    monkeypatch.setattr(own_words, "_MEDIUM_AR", frozenset())
    assert own_words._names_someones_text_ar(["رساله", "صوتيه"])
