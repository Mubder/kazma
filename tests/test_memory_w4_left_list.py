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

Step 4's review (H1, H2, M1, M4, L3, L4): "alone" anywhere after a denial
turned real denials into additions ("Not Ali I met Omar alone", "مو علي قابلت
عمر بروحه", a day put right "alone"); now only an "only" or "alone" word right
after the denied value adds. A bare "لا" made "لا غلط، عمر" say nothing was
wrong. "No, Omar too" and "لا، عمر بعد" were read as "No, Omar". Each case is a
test here in EN, Gulf and MSA.

The others are left, with why (``docs/agents/15h-facts.md``). The classes:
every denial word before every "only" word adds; every text noun before
every medium word is the user's own. Both are read from the source's own
tables, with the rule taken away as the negative control.
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

#: L1: a denial that adds, or "wrong" said of nothing: both meetings stay.
ADDS = [
    (EN, "Not only Ali, I met Omar"), (EN, "Not just Ali, I met Omar too"),
    (EN, "No, I didn't see Ali alone, I met Omar too"), (EN, "Nothing is wrong, I met Omar"),
    (GULF, "مو بس علي، قابلت عمر"), (GULF, "مب بس علي، قابلت عمر بعد"), (GULF, "لا ما رحت بروحي، قابلت عمر بعد"),
    (GULF, "ما في شي غلط، قابلت عمر"),
    (MSA, "ليس فقط علي، التقيت بعمر"), (MSA, "لم ألتق بعلي وحده، التقيت بعمر أيضا"), (MSA, "لا يوجد خطأ، التقيت بعمر"),
    # "Only" or "alone" right after the value, Arabic's usual order too (step 4's review, M4).
    (EN, "not only Ali but Omar too"), (EN, "Not just Ali - Omar too"), (EN, "I didn't meet Ali alone, Omar was there"),
    (EN, "It wasn't Ali only, Omar too"), (EN, "Not Ali alone, Omar too"),
    (GULF, "مو بس علي قابلت عمر بعد"), (GULF, "مو علي بس، قابلت عمر بعد"), (GULF, "ما قابلت علي بروحه، عمر كان معاه"),
    (GULF, "مو علي لحاله، عمر بعد"),
    (MSA, "ليس فقط علي بل عمر أيضا"), (MSA, "ليس علي فقط، عمر أيضا"), (MSA, "لم ألتق بعلي فقط، بل بعمر أيضا"),
    # A "No." before the sentence that adds goes with it (step 4's fifth review).
    (EN, "No. Not only Ali, Omar too."), (GULF, "لا. مو بس علي، عمر بعد"),
    # "خطأ" said of something else puts nothing right (the fifth review, Low).
    (GULF, "قابلت عمر بعد، وكان خطأ اني ما عزمت علي"),
    # "بعد" ending the clause after the new value, as "Omar too" (the third review, D).
    (EN, "Not Ali only Omar too"), (GULF, "مو علي بس عمر بعد"), (GULF, "مو علي بس، عمر بعد"),
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

def _denials() -> list[str]:
    """Every denial word the product reads, folded, but the ones it says
    never add (``_NEVER_ADDING``: "Actually just Omar" puts right)."""
    from kazma_core.documents.arabic import fold_for_search

    words = event_text._DENIALS_EN | {fold_for_search(w) for w in event_text._DENIALS_AR_RAW}
    return sorted(words - event_text._NEVER_ADDING)


def test_every_denial_before_every_only_word_adds():
    for denial in (d for d in _denials() if d not in event_text._BARE_NO):
        for only in event_text._ONLY:
            assert event_text._adds_rather_than_denies(f"{denial} {only} X, Y"), (denial, only)
    # A bare "no" before "only" puts right: "No, just Omar", "لا بس عمر".
    for no in event_text._BARE_NO:
        for only in event_text._ONLY:
            assert not event_text._adds_rather_than_denies(f"{no} {only} X"), (no, only)
    # Control: a plain denial.
    assert not event_text._adds_rather_than_denies("Not Ali, Omar")


def test_a_denial_adds_only_in_its_narrow_shapes():
    """Step 4's fourth review: base's denial is the rule, and a denial adds
    only in the narrow shapes ``event_text`` names, for every denial, every
    "only", "alone" and "too" word of the product's own sets; each shape's
    near miss denies."""
    adds, denies = event_text._adds_rather_than_denies, []
    for denial in _denials():
        if denial in event_text._ANSWER_NO:
            continue
        for only in event_text._ONLY:
            shapes = [f"{denial} Ali {only} but Omar", f"{denial} Ali {only}, but Omar", f"{denial} Ali {only}, Omar too",
                      f"{denial} Ali {only}, also Omar", f"{denial} Ali {only} Omar as well"]
            shapes += [f"{denial} Ali {only} Omar {too}" for too in event_text._TOO_END]
            if denial not in event_text._BARE_NO:
                shapes.append(f"{denial} {only} Ali, Omar")
            for text in shapes:
                assert adds(text, "Ali"), text
            denies += [f"{denial} Ali {only} Omar", f"{denial} Ali {only}, Omar", f"{denial} Ali {only}",
                       f"{denial} Ali {only} Omar, I saw him again", f"{denial} Ali {only} Omar again",
                       f"{denial} Ali {only} Omar and Sara too", f"{denial} Ali {only} Omar who was too tired",
                       f"{denial} Ali {only} Omar plus his wife", f"{denial} Ali {only} Omar as well as Sara",
                       f"{denial} Ali {only} Omar but Sara too", f"{denial} Ali {only} Omar, Sara too",
                       f"{denial} Ali met Omar {only}", f"{denial} Ali {only} but Omar|Sara"]
        for alone in event_text._ALONE:
            assert adds(f"{denial} Ali {alone}, Omar", "Ali"), (denial, alone)
            assert adds(f"{denial} Ali {alone}", "Ali"), (denial, alone)
            denies += [f"{denial} Ali {alone} Omar", f"{denial} Ali met Omar {alone}"]
    for text in denies:
        value = "Sara" if text.endswith("|Sara") else "Ali"
        assert not adds(text.split("|")[0], value), text
    for no in event_text._ANSWER_NO:
        for word in event_text._ALONE:
            assert not adds(f"{no} went gym {word}", "gym"), (no, word)
    for word in event_text._NEVER_ADDING:
        assert not adds(f"{word} just Omar", "Ali"), word
    # "وحده ثانيه": another one, not alone.
    assert not adds("مو علي وحده ثانيه اسمها عمر", "علي")


def test_a_sentence_that_adds_never_silences_another_sentences_denial():
    """Step 4's fifth review, the class: an adding shape ("Not only X, Y
    too") cancels only the denial of its own sentence. Every shape, said of
    something else after each kind of denial ("No, Omar.", "Not Ali.", "لا،
    عمر.", "مو علي."), leaves that denial standing. Controls: the shape alone
    adds; a bare "No." right before it goes with it."""
    shapes = ["Not only the coffee, lunch too", "It wasn't just a coffee, lunch too",
              "مو بس قهوة، غدا بعد", "ليس فقط القهوة، بل الغداء أيضا", "Not the coffee alone, lunch too"]
    for opener in ("No, Omar.", "Not Ali, Omar.", "لا، عمر.", "مو علي، عمر.", "لا، التقيت بعمر."):
        for shape in shapes:
            assert event_text.denies(f"{opener} {shape}", "Ali" if opener.isascii() else "علي"), (opener, shape)
    for shape in ("Not only Ali, Omar too", "مو بس علي، عمر بعد", "ليس فقط علي، بل عمر أيضا"):
        value = "Ali" if shape.isascii() else "علي"
        assert not event_text.denies(shape, value), shape
        assert not event_text.denies(f"{'No' if shape.isascii() else 'لا'}. {shape}", value), shape


def test_control_the_rule_before_the_review_turns_denials_into_additions(monkeypatch):
    """Step 4's rule ("alone" anywhere after a denial): the message whose
    "alone" is the user's denies nothing."""
    def anywhere(words, at, wanted=None, later=""):
        return any(w in event_text._ALONE for w in words[at + 1:])

    assert not event_text._adds_rather_than_denies("Not Ali I met Omar alone", "Ali")
    monkeypatch.setattr(event_text, "_adds_rather", anywhere)
    assert event_text._adds_rather_than_denies("Not Ali I met Omar alone", "Ali")
    assert event_text._adds_rather_than_denies("مو علي قابلت عمر بروحه", "علي")


def test_control_without_the_rule_a_denial_that_adds_denies(monkeypatch):
    monkeypatch.setattr(event_text, "_adds_rather", lambda *_a, **_k: False)
    assert event_text.denies("Not only Ali, I met Omar", "Ali") and event_text.denies("مو بس علي، قابلت عمر", "علي")
    assert event_text.denies("مو علي بس، قابلت عمر بعد", "علي")


def test_every_text_noun_before_every_medium_is_the_users_own():
    for noun in own_words._TEXT_NOUNS_AR:
        for medium in own_words._MEDIUM_AR:
            assert not own_words._names_someones_text_ar([noun, medium]), (noun, medium)
            assert own_words._names_someones_text_ar(own_words._ar_words(f"{noun} {medium} من عمر")), (noun, medium)


def test_control_without_the_medium_a_voice_note_is_someone_elses(monkeypatch):
    monkeypatch.setattr(own_words, "_MEDIUM_AR", frozenset())
    assert own_words._names_someones_text_ar(["رساله", "صوتيه"])
