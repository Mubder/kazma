"""A fact is found by an Arabic question as by an English one, whatever kind it
is and whatever the model named it (the memory plan's W6, step 2).

A fact's words are English, as the model names it ("user intends_to أزور
اليابان"), so an English question shares a word with it and an Arabic one none.
``fact_names.ASKED_WITH`` knew seven kinds, by Kazma's own names alone. Twenty-three
kinds told in English and in Arabic, in stores of one, five and thirty-five
facts, asked in English, Gulf and MSA (``benchmark_kinds``, bge-m3's recorded
vectors): English lost 11 of 138, Gulf 59, MSA 55; now none. A whole list
("List all my plans", "عطني كل خططي") showed the entries of one name: 66 of 76
in English, 52 in Gulf, 59 in MSA; now all.

A question that uses a kind's word otherwise shows no more than it did. The
table before, the question's wording read as any words, and a list read by one
name are the negative controls.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest
from kazma_core.memory import benchmark, benchmark_kinds, fact_names, question_shape, recall

FIXTURE = Path(__file__).parent / "fixtures" / "memory_bench" / benchmark_kinds.FACT_KINDS_VECTORS
WAYS = ("en", "gulf", "msa")

#: What a kind's word in another sense showed before any of this was built, by
#: meaning alone: the number each way may not pass.
OTHER_SENSE_BEFORE = {"en": 5, "gulf": 2, "msa": 3}


def _replayed(monkeypatch: pytest.MonkeyPatch) -> None:
    replay = benchmark.ReplayEmbedder(FIXTURE)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: replay)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model)
    monkeypatch.setattr(recall, "_recall_deadline_s", lambda: 600.0)


def _lost(rows: list[dict]) -> list[str]:
    return [f"told {r['told']} ({r['size']}) asked {r['asked']}: {r['question']} -> {r['shown']}"
            for r in rows if "want" in r and not r["found"]]


def _lost_by_way(rows: list[dict]) -> dict[str, int]:
    return {way: sum(1 for r in rows if "want" in r and r["asked"] == way and not r["found"]) for way in WAYS}


def test_every_kind_is_found_in_every_language_and_dialect(monkeypatch):
    _replayed(monkeypatch)
    rows = benchmark_kinds.run_fact_kinds()
    assert not [r for r in rows if r["degraded"]]
    assert Counter((r["told"], r["size"], r["asked"]) for r in rows if "want" in r) == {
        (told, size, way): len(benchmark_kinds.FACT_KINDS) for told in ("en", "ar") for size in (1, 5, 35) for way in WAYS}
    assert _lost(rows) == []
    # A word in another sense: no more than meaning alone showed.
    shown = {way: sum(1 for r in rows if r.get("never") in r["shown"] and r["asked"] == way) for way in WAYS}
    assert all(shown[way] <= OTHER_SENSE_BEFORE[way] for way in WAYS), shown


def test_control_the_table_before_loses_arabic_first(monkeypatch):
    """The seven kinds under Kazma's own names, and city and home."""
    _replayed(monkeypatch)
    old = {"lives_in", "works_at", "current_role", "name_is", "preferred_name", "allergic_to", "favorite_"}
    monkeypatch.setattr(fact_names, "ASKED_WITH", {k: v for k, v in fact_names.ASKED_WITH.items() if k in old})
    monkeypatch.setattr(fact_names, "ASKED_AS_MINE", {"lives_in": fact_names.ASKED_AS_MINE["lives_in"]})
    monkeypatch.setattr(fact_names, "fact_kind", lambda name: name if name in old else
                        "favorite_" if name.startswith("favorite_") else None)
    monkeypatch.setattr(fact_names, "_kinds_it_is_all_of", lambda question, time_words: ())
    for cached in (fact_names.asked_words, fact_names._asked_sets, fact_names._mine_sets, fact_names._arabic_neighbours):
        cached.cache_clear()
    try:
        lost = _lost_by_way(benchmark_kinds.run_fact_kinds())
    finally:
        monkeypatch.undo()
        for cached in (fact_names.asked_words, fact_names._asked_sets, fact_names._mine_sets, fact_names._arabic_neighbours):
            cached.cache_clear()
    assert lost["gulf"] > 5 * lost["en"] > 0 and lost["msa"] > 4 * lost["en"], lost


def test_control_words_alone_do_not_reach_them(monkeypatch):
    """A question of nothing but the kind ("شنو شريت؟") is kept by its wording:
    bge-m3 puts the fact below an unrelated question's background."""
    _replayed(monkeypatch)
    monkeypatch.setattr(fact_names, "_kinds_it_is_all_of", lambda question, time_words: ())
    lost = _lost_by_way(benchmark_kinds.run_fact_kinds())
    assert lost["gulf"] > 5 and lost["msa"] > 5 and lost["gulf"] > lost["en"], lost


# ── whole lists ──────────────────────────────────────────────────────────

def test_a_whole_list_is_shown_whatever_names_its_entries_have(monkeypatch):
    _replayed(monkeypatch)
    rows = benchmark_kinds.run_lists()
    assert len(rows) == len(benchmark_kinds.LISTS) * 2 * 3 * 2
    assert [r for r in rows if r["shown"] < r["of"] or r["degraded"]] == []


@pytest.mark.parametrize(("gone", "way"), [("kind", "en"), ("kind", "gulf"), ("kind", "msa"), ("all", "gulf")])
def test_control_a_list_read_by_one_name_is_short(monkeypatch, gone, way):
    _replayed(monkeypatch)
    if gone == "kind":
        monkeypatch.setattr(recall, "_of_the_same_list", lambda conn, tenant, subject, predicate: [])
    else:  # "كل خططي" read as no list
        monkeypatch.setattr(question_shape, "asks_for_all", lambda question: "كل" not in question)
    short = [r for r in benchmark_kinds.run_lists() if r["asked"] == way and r["shown"] < r["of"]]
    assert short


#: A question of SOME of a kind, measured on 5f9bd053c (own entries of 68 each
#: way; others of the kind shown: English 9, Gulf 5, MSA 5) and on b80d43c15
#: (60, 59, 58; others 55, 44, 44).
NARROW_BEFORE = {"en": (68, 9), "gulf": (67, 5), "msa": (67, 5)}


def test_a_list_of_some_of_a_kind_shows_its_own_first(monkeypatch):
    """The review of step 2's M4: "List all the books I plan to read" showed 4 of 8."""
    _replayed(monkeypatch)
    rows = benchmark_kinds.run_narrow_lists()
    for way, (own, _others) in NARROW_BEFORE.items():
        mine = [r for r in rows if r["asked"] == way]
        assert sum(r["own"] for r in mine) >= own, way
    how_many = [r for r in rows if r["question"].startswith(("How many", "كم"))]
    assert all(r["other"] == 0 for r in how_many if r["name"] == "attended_concert"), how_many


def test_control_the_kinds_other_names_first_lose_its_own(monkeypatch):
    _replayed(monkeypatch)
    monkeypatch.setattr(recall, "_asks_the_kind", lambda query: (lambda name: True, lambda row: True))
    rows = benchmark_kinds.run_narrow_lists()
    assert sum(r["other"] for r in rows) > 2 * sum(o for _own, o in NARROW_BEFORE.values())


# ── what a question's words ask ──────────────────────────────────────────

ASKED = [
    ("plans", "What are my plans?", "شنو خططي؟", "ما هي خططي؟"),
    ("goals", "What are my goals?", "شنو أهدافي؟", "ما هي أهدافي؟"),
    ("interests", "What are my hobbies?", "شنو هواياتي؟", "ما هي هواياتي؟"),
    ("bought", "What did I buy?", "شنو شريت؟", "ماذا اشتريت؟"),
    ("met", "Who did I meet?", "منو قابلت؟", "من قابلت؟"),
    ("received", "What did I receive?", "شنو استلمت؟", "ماذا استلمت؟"),
    ("attended", "What have I attended?", "شنو حضرت؟", "ماذا حضرت؟"),
    ("finished", "What did I finish?", "شنو خلصت؟", "ماذا أنهيت؟"),
    ("habits", "What are my habits?", "شنو عوايدي؟", "ما هي عاداتي؟"),
    ("doctor", "Who is my doctor?", "منو دكتوري؟", "من هو طبيبي؟"),
    ("study", "Where do I study?", "وين أدرس؟", "أين أدرس؟"),
    ("daughters", "What is my daughter called?", "شنو اسم بنتي؟", "ما اسم ابنتي؟"),
    ("sons", "What is my son called?", "شنو اسم ولدي؟", "ما اسم ابني؟"),
    ("car", "What is my car?", "شنو سيارتي؟", "ما هي سيارتي؟"),
    ("current_role", "What do I do for a living?", "شنو شغلتي؟", "ما هي مهنتي؟"),
    ("diet", "What can I not eat?", "شنو ما أقدر آكل؟", "ما الذي لا أستطيع أن آكله؟"),
    ("lives_in", "What is my city?", "شنو مدينتي؟", "ما هي مدينتي؟"),
]


@pytest.mark.parametrize(("kind", "english", "gulf", "msa"), ASKED)
def test_a_question_of_nothing_else_asks_its_kind(kind, english, gulf, msa):
    for question in (english, gulf, msa):
        assert fact_names.kinds_said_of_the_user(question) == (kind,), question


NOT_ASKED = [
    ("What can I buy here?", "شنو أشتري هدية؟", "ماذا أشتري هدية؟"),
    ("What can I eat for dinner?", "شنو آكل على العشا؟", "ماذا آكل على العشاء؟"),
    ("How much is the living room lamp?", "شكثر ليت الصالة؟", "كم سعر مصباح غرفة المعيشة؟"),
    ("What is the cost of living in Porto?", "شكثر تكلفة المعيشة في بورتو؟", "ما تكلفة المعيشة في بورتو؟"),
    ("Where is the plan of the building?", "وين خطة المبنى؟", "أين خطة المبنى؟"),
    ("How many goals did he score?", "كم هدف سجل؟", "كم هدفاً سجل؟"),
    # The kind's word beside another: the question is of that one.
    ("What is my car's plate number?", "وش رقم سيارتي؟", "ما رقم لوحة سيارتي؟"),
    ("When is my doctor free?", "متى يفضى دكتوري؟", "متى يتفرغ طبيبي؟"),
    ("How is my daughter's school?", "شلون مدرسة بنتي؟", "كيف مدرسة ابنتي؟"),
    ("Can you recommend a doctor?", "تنصحني بدكتور؟", "هل تنصحني بطبيب؟"),
    ("Thanks!", "مشكور", "شكراً"),
    # Told, not asked ("خلصت" is "I'm done").
    ("I finished.", "خلصت", "أنهيت"), ("I bought it", "شريتها", "اشتريتها"), ("I planned it.", "ناوي", "أنوي"),
]


@pytest.mark.parametrize(("english", "gulf", "msa"), NOT_ASKED)
def test_a_question_of_something_else_asks_none(english, gulf, msa):
    for question in (english, gulf, msa):
        assert fact_names.kinds_said_of_the_user(question) == (), question


def test_every_first_person_word_is_a_word_of_a_kind():
    """Each word the all-of rule knows is one of its kind's words, so it can
    never ask a kind by itself that the table does not know."""
    from kazma_core.memory.query_terms import content_terms

    mine = set(content_terms(fact_names._ALL_MINE_AR))
    of_a_kind = set().union(*fact_names._alone_sets().values())
    assert mine - of_a_kind == set()
    assert "خطه" not in mine and "هدف" not in mine  # nobody's plan, a goal scored (control)


@pytest.mark.parametrize(("asked", "english", "arabic"), [
    (True, "List all my plans.", "عطني كل خططي"), (True, "all our goals", "كل أهدافنا"),
    (False, "I go every day", "أروح كل يوم"), (False, "every time is fine", "كل وقت زين")])
def test_all_of_mine_is_a_list_in_both_languages(asked, english, arabic):
    if asked:
        assert question_shape.asks_for_all(english)
    assert question_shape.asks_for_all(arabic) is asked


@pytest.mark.parametrize(("question", "name", "value"), [
    ("List all the trips I am planning.", "planning_trip", "to Oman in spring"),
    ("عطني كل السفرات اللي أخطط لها", "planning_سفرة", "إلى عمان"),
    ("List every concert I attended.", "attended_concert", "Fairouz in Beirut")])
def test_a_list_entrys_name_holds_a_word_of_the_question(question, name, value):
    """N5 of the delta check: "trip" is in ``planning_trip``; read from the value alone, other plans came first."""
    from kazma_core.memory import recall

    row = {"predicate": name, "object": value}
    if name.isascii():
        assert recall._named_by(question)(row)
    assert not recall._named_by(question)({"predicate": "plans_to", "object": "paint the kitchen"})


# ── told as a turn, asked three ways (W6 step 3) ─────────────────────────

TOLD_FIXTURE = Path(__file__).parent / "fixtures" / "memory_bench" / benchmark_kinds.TOLD_VECTORS
#: Lost of 138 a way and form (and of the ``also`` rows, "my pet": 12 in
#: English, 6 in Gulf and in MSA), with this run's vectors. Shown in another
#: sense, by way and store size (of 78, 70 and 68 a size), at most. What step 3 as first built
#: (0f0c3186c) lost and showed, and meaning alone, are in 15g-recall.
TOLD_NOW = {("en", "mine"): 0, ("en", "thing"): 1, ("en", "also"): 0, ("gulf", "mine"): 0, ("gulf", "thing"): 2,
            ("gulf", "also"): 0, ("msa", "mine"): 0, ("msa", "thing"): 1, ("msa", "also"): 0}
OTHER_SENSE_TOLD = {("en", 5): 37, ("en", 30): 51, ("gulf", 5): 18, ("gulf", 30): 41, ("msa", 5): 22, ("msa", 30): 40}


def _told(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    replay = benchmark.ReplayEmbedder(TOLD_FIXTURE)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: replay)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model)
    monkeypatch.setattr(recall, "_recall_deadline_s", lambda: 600.0)
    return benchmark_kinds.run_fact_languages_kinds()


def _told_lost(rows: list[dict]) -> dict[tuple[str, str], int]:
    return {(way, form): sum(1 for r in rows if r["asked"] == way and r["form"] == form and not r["found"])
            for way in WAYS for form in ("mine", "thing", "also")}


def _other_shown(rows: list[dict]) -> dict[tuple[str, int], int]:
    return {(way, size): sum(1 for r in rows if r["form"] == "other" and r["asked"] == way and r["size"] == size and r["shown"])
            for way in WAYS for size in (5, 30)}


def test_a_fact_told_as_a_turn_is_found_three_ways(monkeypatch):
    """Told in either language through the quick pass and the deep pass, in
    stores of 1, 5 and 30; asked in English, Gulf and MSA, in the first person
    and with the thing asked. What is still lost: the store of one fact (no
    background) and "at the gym", which the fact does not hold. The questions
    in another sense are asked in the stores of five and thirty, and the words
    the tables add stand in some of them bare (the review of step 3, M3)."""
    rows = _told(monkeypatch)
    assert not [r for r in rows if r["degraded"]]
    assert Counter((r["told"], r["size"], r["asked"]) for r in rows if r["form"] == "mine") == {
        (told, size, way): len(benchmark_kinds.TOLD)
        for told in ("en", "ar") for size in benchmark_kinds.TOLD_SIZES for way in WAYS}
    assert _told_lost(rows) == TOLD_NOW
    shown = _other_shown(rows)
    assert all(shown[key] <= OTHER_SENSE_TOLD[key] for key in shown), shown


#: The table as step 3 first built it (0f0c3186c): its words with another sense
#: and its "my X" from them (the review of step 3, M1, M2, L1).
_STEP3_WITH = {"car": ("drive", "drives", "driving", "أسوق", "نسوق", "أسوقها"),
               "visited": ("visit", "visited", "زرت", "زرتها", "زرته", "زرتهم", "زرنا"),
               "pets": ("pet", "pets", "قطوتي", "قطتي", "كلبي", "حيواني", "حيواناتي", "أليفة", "اليف")}


def _clear() -> None:
    for cached in (fact_names.asked_words, fact_names._asked_sets, fact_names._alone_sets, fact_names._all_mine,
                   fact_names._mine_sets, fact_names._arabic_neighbours):
        cached.cache_clear()


def test_control_the_table_as_step_3_built_it_shows_more_in_another_sense(monkeypatch):
    for kind, words in _STEP3_WITH.items():
        monkeypatch.setitem(fact_names.ASKED_WITH, kind, words)
    monkeypatch.delitem(fact_names.ASKED_ALONE, "pets")
    monkeypatch.setitem(fact_names.ASKED_AS_MINE["diet"], "en", re.compile(
        fact_names.ASKED_AS_MINE["diet"]["en"].pattern.split("|^")[0] + r"|^\W*(?:can|may) (?:i|we) eat\b"))
    _clear()
    try:
        shown = _other_shown(_told(monkeypatch))
    finally:
        monkeypatch.undo()
        _clear()
    assert sum(shown.values()) > sum(OTHER_SENSE_TOLD.values()), shown


def test_control_the_table_before_step_3_loses_arabic_questions_of_the_thing(monkeypatch):
    for kind in ("car", "visited", "pets", "married"):
        monkeypatch.delitem(fact_names.ASKED_WITH, kind)
    monkeypatch.delitem(fact_names.ASKED_ALONE, "pets")
    monkeypatch.setitem(fact_names.ASKED_WITH, "doctor", ("doctor", "physician", "دكتوري", "دكتورتي", "طبيبي", "طبيبتي"))
    for kind in ("married", "doctor", "interests", "pets"):
        monkeypatch.delitem(fact_names.ASKED_AS_MINE, kind)
    _clear()
    try:
        lost = _told_lost(_told(monkeypatch))
    finally:
        monkeypatch.undo()
        _clear()
    assert lost["gulf", "thing"] > 3 * TOLD_NOW["gulf", "thing"] and lost["msa", "thing"] > TOLD_NOW["msa", "thing"], lost


_QUICK = {"lives_in", "works_at", "name_is", "preferred_name", "allergic_to", "favorite_"}


def _kinds_not_told() -> set[str]:
    """The kinds the tables ask that have no told row (``run_fact_languages``
    has the five the quick pass writes)."""
    asked = set(fact_names.ASKED_WITH) | set(fact_names.ASKED_ALONE) | set(fact_names._FEW) | set(fact_names.LISTS)
    return asked - _QUICK - {k["kind"] for k in benchmark_kinds.TOLD}


def test_every_kind_asked_is_told_and_asked_three_ways():
    """The class: each kind the tables ask has a told row, questions in all three
    ways in both forms, and a question in another sense in all three."""
    assert _kinds_not_told() == set()
    for kind in benchmark_kinds.TOLD:
        assert set(kind["asked"]) == set(kind["other"]) == set(WAYS) and set(kind["told"]) == {"en", "ar"}, kind["kind"]
        assert all(len(q) == 2 for q in kind["asked"].values()), kind["kind"]
    assert set(benchmark_kinds.TOLD_OTHER_BARE) <= {k["name"] for k in benchmark_kinds.TOLD}


def test_control_a_kind_with_no_told_row_is_found(monkeypatch):
    """The gate's own check, run with a planted kind (the review of step 3, L5)."""
    monkeypatch.setitem(fact_names.ASKED_WITH, "planted", ("planted", "مزروع"))
    assert _kinds_not_told() == {"planted"}


@pytest.mark.parametrize(("kind", "english", "gulf", "msa"), [
    ("diet", "Can I eat gluten?", "أقدر آكل جلوتين؟", "هل أستطيع أكل الجلوتين؟"),
    ("married", "What is my marital status?", "شنو حالتي الاجتماعية؟", "ما حالتي الاجتماعية؟"),
    ("visited", "Which places have I visited?", "عدد كل الأماكن اللي زرتها", "ما الأماكن التي زرتها؟"),
    ("pets", "What are my pets?", "شنو اسم قطوتي؟", "ما اسم قطتي؟"),
    ("pets", "What is my cat called?", "شنو اسم كلبي؟", "ما اسم قطتي؟"),
    ("diet", "Can I eat peanuts?", "أقدر آكل فول سوداني؟", "هل أستطيع أكل الفول السوداني؟"),
    ("doctor", "Which doctor do I see?", "عند أي دكتور أراجع؟", "أي طبيب أراجع؟"),
    ("interests", "Which art am I interested in?", "بأي فن أهتم؟", "بأي فن أهتم؟")])
def test_a_question_of_the_kind_asks_it_in_three_ways(kind, english, gulf, msa):
    for question in (english, gulf, msa):
        assert kind in fact_names.kinds_said_of_the_user(question), question


@pytest.mark.parametrize(("english", "gulf", "msa"), [
    ("What can I eat for dinner?", "شنو آكل على العشا؟", "ماذا آكل على العشاء؟"),
    # The review of step 3: M1, M2, L1, each in the three ways.
    ("What's in my drive?", "وين السوق؟", "أين السوق؟"), ("How is my driving?", "متى أراجع البنك؟", "متى أراجع البنك؟"),
    ("Can I eat here?", "أقدر آكل الحين؟", "هل أستطيع أن آكل الآن؟"), ("Can we eat outside?", "يصير آكل بالليل؟", "هل أستطيع أن آكل في الخارج؟"),
    ("Can I take Panadol with coffee?", "أقدر آخذ بنادول مع القهوة؟", "هل أستطيع أن أتناول البنادول مع القهوة؟"),
    ("What's my pet peeve?", "البروتين الحيواني زين؟", "هل البروتين الحيواني صحي؟"),
    ("How do I take care of my health?", "شلون أهتم بصحتي؟", "كيف أهتم بصحتي؟"),
    ("What should I visit in Paris?", "شنو لازم أراجع قبل الامتحان؟", "ماذا أزور في باريس؟"),
    ("Which places do tourists visit?", "وين يروحون السياح؟", "ما الأماكن التي يزورها السياح؟"),
    ("How is my health?", "شلون حالتي الصحية؟", "ما حالتي الصحية؟"),
    ("How many cars are there?", "كم عدد السيارات؟", "ما عدد السيارات؟")])
def test_the_same_words_in_another_sense_ask_no_kind(english, gulf, msa):
    for question in (english, gulf, msa):
        assert fact_names.kinds_said_of_the_user(question) == (), question


@pytest.mark.parametrize(("question", "kinds"), [
    ("ما الحيوانات الأليفة التي لدي؟", ("pet",)), ("هل لدي حيوانات أليفة؟", ("pet",)),
    ("Where did I save the file on Google Drive?", ()), ("What should I visit in Paris?", ()), ("متى أراجع البنك؟", ()),
    ("هل البروتين الحيواني صحي؟", ()), ("كيف أهتم بصحتي؟", ())])
def test_what_the_keyword_index_is_asked(question, kinds):
    """"أليفة" folds to "يفه" and "الأليفة" to "اليفه": both are the table's; and the
    words with another sense ask the keyword index nothing (the review of step 3, L1, L4)."""
    from kazma_core.memory.query_terms import content_terms

    asked = fact_names.kinds_asked(content_terms(question))
    assert all(k in asked for k in kinds) and (kinds or asked == ()), asked


def test_a_pet_is_looked_up_by_every_name_it_is_kept_under():
    """``has_dog``, ``has_cat`` are split into words by the index (the review of step 3, L2)."""
    assert {"pet", "cat", "dog"} <= set(fact_names.indexed_words(("pets",)))
    assert all(fact_names.fact_kind(n) == "pets" for n in ("has_dog", "has_cat", "owns_cat", "cat_name", "has_pet"))


# ── the delta review of step 3 (N1-N4): the three ways alike ─────────────

#: (the kind all three ask, or None for nothing; English, Gulf, MSA).
ALIKE = [
    # N1: "أهتم" ends the question and "which" is of a thing.
    ("interests", "Which art am I interested in?", "بأي فن أهتم؟", "في أي مجال نهتم؟"),
    (None, "How do I care for my mother?", "بأي طريقة أهتم بأمي؟", "بأي شكل أهتم بنفسي؟"),
    (None, "When do I care?", "اي وقت اهتم؟", "بأي طريقة أهتم؟"),
    # N2: "my pet", and "حيواني" beside "الأليف".
    ("pets", "What is my pet's name?", "شنو اسم حيواني الأليف؟", "ما اسم حيواني الأليف؟"),
    ("pets", "What's my pet called?", "شنو حيواني الأليف؟", "ما هو حيواني الأليف؟"),
    (None, "Is my pet project done?", "البروتين الحيواني زين؟", "هل البروتين الحيواني صحي؟"),
    (None, "What's my pet peeve?", "حيوان أليف مناسب للشقة؟", "أي حيوان أليف يعيش أكثر؟"),
    # N4: the car by what or which; a doctor asked ends the question.
    ("car", "What car do I drive?", "شنو سيارتي؟", "ما هي سيارتي؟"),
    (None, "Where did I drive?", "وين سقت؟", "أين قدت؟"),
    (None, "Did we drive there?", "متى سقت؟", "هل قدت إلى هناك؟"),
    ("doctor", "Which doctor do I see?", "أي دكتور أروح له؟", "أي طبيب أراجع؟"),
    (None, "What doctor do I see for a cold?", "لأي دكتور أروح للأسنان؟", "أي طبيب أراجع لعيوني؟"),
    (None, "Which doctor do I see for my back?", "أي دكتور أراجع عشان ظهري؟", "أي طبيب أراجع لعيوني؟"),
]

#: N3: what follows "eat" that is no food, English beside Arabic.
NOT_FOOD = [("here", "هني"), ("at home", "بالبيت"), ("in the car", "بالسيارة"), ("this", "هذا"),
            ("on the plane", "بالطيارة"), ("inside", "داخل الفصل"), ("later", "بعدين"), ("in the morning", "الصبح"),
            ("on the bed", "على السرير"), ("now", "الحين"), ("a little", "شوي"), ("the cake", "الكيكة"),
            ("with you", "عندكم"), ("at noon", "الظهر")]


def _diet_rows() -> list[tuple[str | None, str, str, str]]:
    """The food list's pairs and the words that are no food, as (kind, EN, Gulf, MSA)."""
    foods = [("diet", f"Can I eat {en}?", f"أقدر آكل {ar}؟", f"هل أستطيع أكل ال{ar}؟")
             for en, ars in fact_names.DIET_FOODS for ar in ars]
    return foods + [(None, f"Can I eat {en}?", f"أقدر آكل {ar}؟", f"هل يمكنني أن آكل {ar}؟") for en, ar in NOT_FOOD]


def _unalike(rows) -> list[tuple[str, frozenset[str]]]:
    """Each question whose named kinds are not its row's: the three ways must
    ask the same kind, or none."""
    out = []
    for kind, *questions in rows:
        for question in questions:
            said = frozenset(fact_names.kinds_said_of_the_user(question))
            if said != (frozenset({kind}) if kind else frozenset()):
                out.append((question, said))
    return out


def test_the_three_ways_ask_alike():
    """The class (the delta review of step 3, N1-N4, §57): a question asks a
    kind in English exactly when its Gulf and MSA twins do."""
    assert _unalike(ALIKE) == []


def test_a_food_asks_the_diet_and_nothing_else_does_in_both_languages():
    """N3: whether the user may eat a food, read from one list in both
    languages; "Can I eat here / this / later?" and their Arabic twins ask
    nothing."""
    assert len(fact_names.DIET_FOODS) >= 20
    assert _unalike(_diet_rows()) == []


#: The Arabic head form as the review of step 3 left it: any word after "eat"
#: but a list of times and meals.
_EXCLUDING_AR = re.compile(r"^\W*(?:هل )?(?:اقدر|اكدر|استطيع|يمكنني|يصير) (?:ان )?(?:اكل|اكله) "
                           r"(?!(?:الحين|الان|هني|هنا|هناك|برا|بره|بالليل|الليله|اليوم|بكره|قبل|بعد|مع|في|شي|شيء|"
                           r"الغدا|الغداء|العشا|العشاء|الفطور|الريوق)(?:\s|$|[؟?.,]))[^\W\d_]")


def test_control_an_exclusion_list_is_unalike(monkeypatch):
    """Negative control: the Arabic form as the review of step 3 left it names
    the diet for "أقدر آكل بالبيت؟", "أقدر آكل هذا؟" while English names none."""
    monkeypatch.setitem(fact_names.ASKED_AS_MINE["diet"], "ar_said", _EXCLUDING_AR)
    unalike = {q for q, _ in _unalike(_diet_rows())}
    assert {"أقدر آكل بالبيت؟", "أقدر آكل هذا؟", "هل يمكنني أن آكل هذا؟"} <= unalike
    assert not any(q.startswith("Can I") for q in unalike)


def test_control_the_named_kinds_before_the_delta_review_are_unalike(monkeypatch):
    """Negative control for the three ways: without "pet" and "حيواني الأليف",
    and with "أهتم" free to go on, the rows differ."""
    monkeypatch.setitem(fact_names.ASKED_ALONE, "pets", tuple(w for w in fact_names.ASKED_ALONE["pets"] if w != "pet"))
    monkeypatch.delitem(fact_names.ASKED_AS_MINE, "pets")
    monkeypatch.setitem(fact_names.ASKED_AS_MINE["interests"], "ar_said",
                        re.compile(r"(?:^|\s)(?:ب|في )?اي [^\W\d_]+ (?:اهتم|نهتم)(?=\s|$|[؟?.,])"))
    _clear()
    try:
        unalike = {q for q, _ in _unalike(ALIKE)}
    finally:
        monkeypatch.undo()
        _clear()
    assert {"What is my pet's name?", "ما اسم حيواني الأليف؟", "بأي طريقة أهتم بأمي؟", "اي وقت اهتم؟"} <= unalike
