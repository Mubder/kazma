"""A person held in one alphabet and spoken of in the other (the memory plan's W6, step 2).

A fact held of ``omar`` was not replaced by "عمر نقل الدوحة" when the reply
wrote the name as the user did: ``belief_extractor._take_replaces`` compared
the two names letter for letter, and the old home and the new one both stayed
(two of thirty replies of live's model, 25 Nov). ``memory/name_alphabets.py``
knows a person's name in both; **it never joins two people**: a Latin spelling
two names make is nobody's, a name it does not know matches nothing, and a
store where two could be meant, or where the name as said is somebody already,
keeps both facts as before.

Negative controls: the match taken away (the pairs stay open), the rule that
drops a shared spelling taken away (two names join), each guard taken away.
"""

from __future__ import annotations

import sqlite3

import pytest
from kazma_core.memory import belief_extractor, held_facts, name_alphabets
from kazma_core.memory.belief_mutation import mutate_belief
from kazma_core.memory.name_alphabets import may_be, same_name
from kazma_core.memory.schema_v2 import ensure_primary_schema

SAME = [
    ("omar", "عمر"), ("Umar", "عُمَر"), ("noura", "نورة"), ("nora", "نوره"), ("noor", "نور"), ("khaled", "خالد"),
    ("khalid", "خالد"), ("abdulrahman", "عبد الرحمن"), ("abdul_rahman", "عبدالرحمن"), ("abdullah", "عبد الله"),
    ("bu_fahad", "بو فهد"), ("abu_fahad", "أبو فهد"), ("bufahad", "بوفهد"), ("um_ahmad", "أم أحمد"),
    ("omar_alharbi", "عمر الحربي"), ("omar_al_harbi", "عمر_الحربي"), ("ali", "علي"), ("mohammed", "محمد"),
    ("fatima", "فاطمة"), ("aisha", "عائشة"), ("layla", "ليلى"), ("hana", "هناء"), ("sara", "سارة"), ("sarah", "سارة"),
    ("salma", "سلمى"), ("mariam", "مريم"), ("hessa", "حصة"), ("zain", "زين"), ("lulwa", "لولوة"), ("huda", "هدى"),
    ("yousef", "يوسف"), ("saud", "سعود"), ("mona", "منى"), ("reem", "ريم"), ("maha", "مها"), ("faisal", "فيصل"),
    ("nasser", "ناصر"), ("jassim", "جاسم"), ("david", "ديفيد"), ("maria", "ماريا"),
]

#: Two people, or one name that could be another's: no match, either way round.
NOT = [
    ("amr", "عمر"), ("ammar", "عمر"), ("amer", "عمر"), ("omar", "عمرو"), ("omar", "عمار"), ("omar", "عامر"),
    ("omar", "عمير"), ("karima", "كريم"), ("karim", "كريمة"), ("khalida", "خالد"), ("khaled", "خالدة"),
    ("noura", "نور"), ("noor", "نورة"), ("amira", "أمير"), ("samira", "سمير"), ("sameer", "سمر"), ("salem", "سلمى"),
    ("salem", "سليم"), ("saleem", "سالم"), ("salman", "سليمان"), ("hassan", "حسن"), ("hassan", "حسان"),
    ("amal", "أمل"), ("amal", "آمال"), ("amir", "أمير"), ("amir", "عامر"), ("rashid", "راشد"), ("rashid", "رشيد"),
    ("lina", "لينا"), ("dana", "دانة"), ("hamed", "حمد"), ("zahra", "زهرة"), ("amina", "آمنة"), ("hussein", "حسن"),
    ("omar", "عمر الحربي"), ("omar_alharbi", "عمر"), ("omar_alharbi", "عمر العتيبي"), ("omar_otaibi", "عمر الحربي"),
    ("abdullah", "عبد الرحمن"), ("fahad", "بو فهد"), ("abu_fahad", "فهد"), ("berlin_office", "مكتب برلين"),
    ("falcon_project", "مشروع فالكون"), ("user", "المستخدم"), ("", "عمر"), ("omar", ""), ("omar", "omar"), ("عمر", "عمر"),
]


@pytest.mark.parametrize(("latin", "arabic"), SAME)
def test_one_name_in_two_alphabets(latin, arabic):
    assert same_name(latin, arabic) and same_name(arabic, latin)


@pytest.mark.parametrize(("latin", "arabic"), NOT)
def test_two_names_never_match(latin, arabic):
    assert not same_name(latin, arabic) and not same_name(arabic, latin)


def _shared() -> list[tuple[str, list[str]]]:
    """Each Latin spelling that is read as a name, and every name of the list
    that makes it: one, or the match joins two people."""
    made, one = name_alphabets._names()
    whose: dict[str, list[str]] = {}
    for entry in name_alphabets._NAMES:
        name, spellings = name_alphabets._read(entry)
        for spelling in spellings:
            whose.setdefault(spelling, []).append(name)
    return sorted((s, sorted(set(names))) for s, names in whose.items() if s in one and len(set(names)) > 1)


def test_no_spelling_of_the_whole_list_is_two_names():
    """The class: over every name there is, and every spelling made of it."""
    made, one = name_alphabets._names()
    assert len(made) > 400 and len(one) > 3000
    assert _shared() == []
    for spelling, name in one.items():
        assert [n for n, its in made.items() if spelling in its] == [name], spelling


def test_control_a_planted_rival_takes_the_spelling_from_both(monkeypatch):
    """A second name that writes "omar" leaves it nobody's; a match made of
    spellings alone would have joined the two."""
    monkeypatch.setattr(name_alphabets, "_NAMES", [*name_alphabets._NAMES, "أَوْمَر:omar"])
    name_alphabets._names.cache_clear()
    try:
        assert not same_name("omar", "عمر") and not same_name("omar", "أومر")
        assert same_name("umar", "عمر")
    finally:
        name_alphabets._names.cache_clear()


def test_every_arabic_name_of_the_list_is_one_name():
    """Two entries that memory folds to one spelling are one name written
    twice, never two people: their spellings are joined, and declared."""
    from kazma_core.documents.arabic import fold_for_search

    folded: dict[str, set[str]] = {}
    for entry in name_alphabets._NAMES:
        name = entry.partition(":")[0]
        folded.setdefault(fold_for_search(name), set()).add(name)
    twice = {k: v for k, v in folded.items() if len(v) > 1}
    assert set(twice) == {"فهد", "مشاري", "مشاعل", "وسام", "جهاد", "عهود", "لولوه"}, twice


# ── the write ────────────────────────────────────────────────────────────


@pytest.fixture()
def conn(monkeypatch):
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    ensure_primary_schema(c)
    yield c
    c.close()


def _hold(conn, subject, name, value, at=1.0, method="llm_inferred"):
    return mutate_belief(conn, subject, name, value, predicate_type="functional", extraction_method=method, now=at,
                         private=True, tenant_id="default")["belief_id"]


def _reply(conn, said, text, *facts):
    """A reply's *facts* (subject, name, value, the held subject it replaces)."""
    held = held_facts.held_facts(conn, tenant_id="default", text=text)
    ref = {(h["subject"], h["predicate"]): h["ref"] for h in held}
    beliefs = [{"subject": s, "predicate": p, "predicate_type": "functional", "object": v, "confidence": 0.9,
                **({"replaces": ref[replaced]} if replaced else {})} for s, p, v, replaced in facts]
    belief_extractor._apply_beliefs_to_v2(beliefs, conn, None, session_id="now", turn=1, tenant_id="default", now=9.0,
                                          private=True, user_text=text, held=held)


def _current(conn, *, user=False):
    return sorted((r[0], r[1], r[2]) for r in conn.execute(
        "SELECT subject, predicate, object FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL")
        if user or r[0] != "user")


#: (told, the name held, the name as said, the turn, the new value): each
#: language's turn about a person held in the other alphabet.
CROSSED = [
    ("gulf", "omar", "عمر", "عمر نقل الدوحة الأسبوع اللي طاف", "الدوحة"),
    ("msa", "omar", "عمر", "انتقل عمر إلى الدوحة الأسبوع الماضي.", "الدوحة"),
    ("english", "عمر", "omar", "Omar moved to Doha last week.", "Doha"),
    ("english, as a name is written", "عمر", "Omar", "Omar moved to Doha last week.", "Doha"),
]


@pytest.mark.parametrize(("_dialect", "held", "said", "turn", "new"), CROSSED)
def test_a_reply_in_the_users_script_replaces_the_fact_held_in_the_other(conn, _dialect, held, said, turn, new):
    _hold(conn, held, "lives_in", "Muscat")
    _reply(conn, said, turn, (said, "moved_home_to", new, (held, "lives_in")))
    assert _current(conn) == [(held, "lives_in", new)]
    # The old home is history, not gone.
    assert conn.execute("SELECT object FROM beliefs WHERE valid_until IS NOT NULL").fetchall()[0][0] == "Muscat"


@pytest.mark.parametrize(("_dialect", "held", "said", "turn", "new"), CROSSED)
def test_the_replys_other_facts_of_that_name_are_the_same_persons(conn, _dialect, held, said, turn, new):
    _hold(conn, held, "lives_in", "Muscat")
    _reply(conn, said, turn, (said, "drives", "Tesla", None), (said, "moved_home_to", new, (held, "lives_in")))
    assert _current(conn) == [(held, "drives", "Tesla"), (held, "lives_in", new)]


@pytest.mark.parametrize(("_dialect", "held", "said", "turn", "new"), CROSSED)
def test_control_without_the_match_both_stay(conn, monkeypatch, _dialect, held, said, turn, new):
    monkeypatch.setattr(name_alphabets, "same_name", lambda one, other: False)
    monkeypatch.setattr(name_alphabets, "in_arabic", lambda subject: None)
    _hold(conn, held, "lives_in", "Muscat")
    shown = held_facts.held_facts
    # What names the person in the list is taken away too: the fact is handed over.
    monkeypatch.setattr(held_facts, "held_facts", lambda c, **kw: shown(c, **{**kw, "text": f"{kw['text']} {held}"}))
    _reply(conn, said, turn, (said, "moved_home_to", new, (held, "lives_in")))
    assert len(_current(conn)) == 2 and (held, "lives_in", "Muscat") in _current(conn)


def test_a_name_in_another_case_is_the_same_person(conn):
    _hold(conn, "noura", "works_as", "dentist")
    _reply(conn, "Noura", "My sister Noura is a surgeon now.", ("Noura", "profession", "surgeon", ("noura", "works_as")))
    assert _current(conn) == [("noura", "works_as", "surgeon")]


# Never two people: in each the fact is written as it came, and both stay.

def test_somebody_held_under_the_name_as_said_is_himself(conn):
    _hold(conn, "omar", "lives_in", "Muscat")
    _hold(conn, "عمر", "works_as", "طبيب", at=2.0)
    _reply(conn, "عمر", "عمر نقل الدوحة", ("عمر", "lives_in", "الدوحة", ("omar", "lives_in")))
    assert _current(conn) == [("omar", "lives_in", "Muscat"), ("عمر", "lives_in", "الدوحة"), ("عمر", "works_as", "طبيب")]


@pytest.mark.parametrize("rival", ["umar", "omer", "omaar"])
def test_two_who_could_be_meant_neither_is(conn, rival):
    _hold(conn, "omar", "lives_in", "Muscat")
    _hold(conn, rival, "lives_in", "Sur", at=2.0)
    _reply(conn, "عمر", "عمر نقل الدوحة", ("عمر", "lives_in", "الدوحة", ("omar", "lives_in")))
    assert ("omar", "lives_in", "Muscat") in _current(conn) and (rival, "lives_in", "Sur") in _current(conn)


def test_a_known_other_name_is_no_rival(conn):
    _hold(conn, "omar", "lives_in", "Muscat")
    _hold(conn, "ammar", "lives_in", "Sur", at=2.0)
    _hold(conn, "amr", "lives_in", "Nizwa", at=3.0)
    _reply(conn, "عمر", "عمر نقل الدوحة", ("عمر", "lives_in", "الدوحة", ("omar", "lives_in")))
    assert _current(conn) == [("ammar", "lives_in", "Sur"), ("amr", "lives_in", "Nizwa"), ("omar", "lives_in", "الدوحة")]


@pytest.mark.parametrize(("held", "said"), [("amr", "عمر"), ("hassan", "حسن"), ("noor", "نورة"), ("omar_otaibi", "عمر"),
                                            ("falcon_project", "مشروع فالكون")])
def test_another_name_or_an_unknown_one_stays_apart(conn, held, said):
    _hold(conn, held, "lives_in", "Muscat")
    shown = held_facts.held_facts(conn, tenant_id="default", text=held)
    belief_extractor._apply_beliefs_to_v2(
        [{"subject": said, "predicate": "lives_in", "predicate_type": "functional", "object": "الدوحة",
          "confidence": 0.9, "replaces": shown[0]["ref"]}], conn, None, session_id="now", turn=1, tenant_id="default",
        now=9.0, private=True, user_text="نقل الدوحة", held=shown)
    assert len(_current(conn)) == 2


def test_a_fact_with_no_replaces_is_never_filed_under_the_other_alphabet(conn):
    _hold(conn, "omar", "lives_in", "Muscat")
    _reply(conn, "عمر", "عمر يحب القهوة", ("عمر", "likes", "القهوة", None))
    assert ("عمر", "likes", "القهوة") in _current(conn)


def test_the_user_is_nobody_elses_spelling(conn):
    _hold(conn, "user", "lives_in", "Muscat")
    _reply(conn, "عمر", "عمر نقل الدوحة", ("عمر", "lives_in", "الدوحة", ("user", "lives_in")))
    assert ("user", "lives_in", "Muscat") in _current(conn, user=True)


def test_control_each_guard_is_needed(conn, monkeypatch):
    monkeypatch.setattr(name_alphabets, "may_be", lambda said, subject: False)
    _hold(conn, "omar", "lives_in", "Muscat")
    _hold(conn, "omaar", "lives_in", "Sur", at=2.0)
    _reply(conn, "عمر", "عمر نقل الدوحة", ("عمر", "lives_in", "الدوحة", ("omar", "lives_in")))
    assert ("omar", "lives_in", "الدوحة") in _current(conn), "the control: without it, one of two is taken"


def test_a_rival_is_a_name_of_the_same_consonants_that_is_nobodys():
    assert may_be("عمر", "umar") and may_be("عمر", "omaar") and may_be("حمود", "hamoud")
    assert not may_be("عمر", "ammar") and not may_be("عمر", "sara") and not may_be("عمر", "عمر")


# ── what the deep pass is shown ──────────────────────────────────────────

@pytest.mark.parametrize(("subject", "text", "named"), [
    ("omar", "عمر نقل الدوحة", True), ("omar", "كلمت عمر أمس", True), ("omar", "وعمر بعد", True),
    ("عمر", "Omar moved to Doha", True), ("عمر_الحربي", "Omar Al-Harbi called", True), ("omar_alharbi", "عمر الحربي اتصل", True),
    ("bu_fahad", "بو فهد رجع", True), ("ali", "كلمت علي أمس", True),
    ("omar", "عمار نقل", False), ("omar", "عمرو نقل", False), ("ali", "الكتاب على الطاولة", False),
    ("عمر", "Amr moved", False), ("عمر_الحربي", "Omar called", False), ("acme", "شنو أطبخ اليوم؟", False)])
def test_a_turn_names_who_is_held_in_the_other_alphabet(subject, text, named):
    assert bool(held_facts._Said(text).names(subject, person=True)) is named


def test_a_friend_held_in_the_other_alphabet_is_shown_with_no_embedder(conn):
    """Found by name, not near in meaning: among 300 newer facts."""
    _hold(conn, "omar", "lives_in", "Muscat")
    _hold(conn, "عمر_العتيبي", "lives_in", "الكويت", at=2.0)
    for n in range(300):
        mutate_belief(conn, "user", "visited", f"city {n}", predicate_type="set", extraction_method="llm_inferred",
                      now=10.0 + n, private=True, tenant_id="default")
    shown = {h["subject"] for h in held_facts.held_facts(conn, tenant_id="default", text="عمر نقل الدوحة")}
    assert "omar" in shown and "عمر_العتيبي" not in shown
