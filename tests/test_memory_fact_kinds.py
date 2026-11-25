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
        monkeypatch.setattr(recall, "_names_of_the_list", lambda conn, tenant, subject, predicate: [predicate])
    else:  # "كل خططي" read as no list
        monkeypatch.setattr(question_shape, "asks_for_all", lambda question: "كل" not in question)
    short = [r for r in benchmark_kinds.run_lists() if r["asked"] == way and r["shown"] < r["of"]]
    assert short


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
