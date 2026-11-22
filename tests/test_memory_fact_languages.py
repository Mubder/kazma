"""A fact told in either language is found by the question asked in either
language (the memory plan's finding 10, 2026-11-22).

A fact's words are Kazma's English name for it ("user lives in بورتو"). An
English question matched the name -- "live" in lives_in, worth recall's
whole fact floor -- and an Arabic question matched no word of it, so it had
to clear the floor on meaning alone: in the real-use suite "وين أسكن الحين؟"
("where do I live now?") found the user's home once in four runs, and
"الحين" counted as half of what it asked, where English drops "now".

The fix is words only: the words each fact kind is asked with, in both
languages (``fact_names.ASKED_WITH``), read by the fact's word coverage and
its keyword candidates; and the Arabic words for "now" are not content words
(``time_words``). The fact's vector is left as it is.

``benchmark.run_fact_languages`` stores five facts told in English and in
Arabic through the product's quick pass, in a store of one fact and of all
five, and asks the real ``recall`` in both languages, with bge-m3's recorded
vectors (``scripts/memory_bench.py fact-languages-vectors``). Today's rules
-- no table, "الحين" a content word -- are the negative control: they lose
Arabic questions and no English one.

City and home (the same day, the main session's decision): "my city",
"home for me", "مدينتي", "وين بيتي" ask where the user lives, said of the
user only (``fact_names.ASKED_AS_MINE``); "which city is the capital", "the
home page", "مدينة الملاهي", "بيت الشعر" and "my home Wi-Fi" ask nothing.
Meaning puts these questions below the background, so the asker's own fact
of that kind is kept by the question's wording (``recall._named_facts``).
Today's code and the words alone are the negative controls.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kazma_core.memory import benchmark, fact_names, time_words

FIXTURE = Path(__file__).parent / "fixtures" / "memory_bench" / benchmark.FACT_LANGUAGES_VECTORS


def _run(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    replay = benchmark.ReplayEmbedder(FIXTURE)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: replay)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model)
    return benchmark.run_fact_languages()


def _missed(rows: list[dict]) -> list[str]:
    return [f"told {r['told']} ({r['size']}) asked {r['asked']}: {r['question']} -> {r['shown']}"
            for r in rows if r["want"] and r["want"] not in r["shown"]]


def test_a_fact_told_in_either_language_is_found_in_either(monkeypatch):
    rows = _run(monkeypatch)
    assert not [r for r in rows if r["degraded"]]
    pairs = {(r["told"], r["asked"]) for r in rows if r["want"]}
    assert pairs == {("en", "en"), ("en", "ar"), ("ar", "en"), ("ar", "ar")}
    assert _missed(rows) == []


def test_a_word_in_another_sense_shows_no_fact(monkeypatch):
    """"What is the name of this song?", "أي حيوان يعيش في الغابة؟": the
    table's words are of one sense, and a question that uses one otherwise
    finds none of the user's facts."""
    rows = [r for r in _run(monkeypatch) if r["want"] is None]
    assert {r["asked"] for r in rows} == {"en", "ar"}
    assert [(r["question"], r["shown"]) for r in rows if r["shown"]] == []


def test_control_today_s_rules_lose_arabic_questions_only(monkeypatch):
    """No table and "الحين" a content word: Arabic questions are lost, in
    stores told in either language, and no English one."""
    monkeypatch.setattr(fact_names, "asked_words", lambda _predicate: "")
    monkeypatch.setattr(fact_names, "kinds_asked", lambda _terms: ())
    monkeypatch.setattr(time_words, "_NOW_AR_RAW", ())
    monkeypatch.setattr(time_words, "_PRESENT_AR_CACHE", [])
    rows = _run(monkeypatch)
    lost = [r for r in rows if r["want"] and r["want"] not in r["shown"]]
    assert lost and {r["asked"] for r in lost} == {"ar"}
    assert {r["told"] for r in lost} == {"en", "ar"}
    assert {r["question"] for r in lost} >= {"وين أسكن الحين؟", "شنو اسمي؟"}


#: The city and home questions (``fact_names.ASKED_AS_MINE``): said of the
#: user, in both languages; the last of each is the real-use suite's "other
#: words" step.
CITY_HOME = {q for _lang, _want, q in benchmark.FACT_LANGUAGES["asked"]
             if any(w in q.lower() for w in ("city", "home", "مدينتي", "بيتي", "ديرتي"))}
OTHER_WORDS = ("Which city is home for me at the moment?", "أي مدينة صارت بيتي هالفترة؟")


@pytest.mark.parametrize(("english", "arabic"), [
    ("What is my city?", "شنو مدينتي؟"),
    ("Where is my home these days?", "وين بيتي الحين؟"),
    ("Where is home for me?", "وين ديرتي؟"),
    ("Which city is home for me at the moment?", "أي مدينة صارت بيتي هالفترة؟"),
])
def test_city_and_home_said_of_the_user_ask_where_they_live(english, arabic):
    assert fact_names.kinds_said_of_the_user(english) == ("lives_in",)
    assert fact_names.kinds_said_of_the_user(arabic) == ("lives_in",)


@pytest.mark.parametrize(("english", "arabic"), [
    ("Which city is the capital?", "وين مدينة الملاهي؟"),
    ("How do I open the home page?", "شنو بيت الشعر؟"),
    ("Is my home page loading?", "بيت جدي كبير."),
    # The word describes another thing, or is where something is: the small
    # store's abstain question q805 and its Arabic twin.
    ("What is my home Wi-Fi network called?", "شنو اسم شبكة الواي فاي في بيتي؟"),
    ("What is my home address?", "شنو عنوان بيتي؟"),
    ("What is the Wi-Fi password in my home?", "شنو رقم الواي فاي في بيتي؟"),
    # Another kind of city.
    ("What is my favorite city?", "شنو مدينتي المفضلة؟"),
    ("Where is my home town?", "وين مدينتي الأصلية؟"),
])
def test_city_and_home_not_said_of_the_user_ask_nothing(english, arabic):
    assert fact_names.kinds_said_of_the_user(english) == ()
    assert fact_names.kinds_said_of_the_user(arabic) == ()


def test_every_city_and_home_question_finds_the_home(monkeypatch):
    rows = [r for r in _run(monkeypatch) if r["question"] in CITY_HOME]
    assert {r["asked"] for r in rows} == {"en", "ar"} and {r["told"] for r in rows} == {"en", "ar"}
    assert _missed(rows) == []


def test_control_without_the_rule_city_and_home_questions_are_lost(monkeypatch):
    """Today's rule: the words read as any other and meaning alone decides.
    bge-m3 puts these questions below an unrelated one's background for
    "user lives in Porto": the real-use suite's "other words" question is
    lost in every store, in both languages."""
    monkeypatch.setattr(fact_names, "kinds_said_of_the_user", lambda _question: ())
    lost = {(r["told"], r["size"], r["question"]) for r in _run(monkeypatch)
            if r["question"] in CITY_HOME and r["want"] not in r["shown"]}
    assert {(told, size, OTHER_WORDS[1]) for told in ("en", "ar") for size in (1, 5)} <= lost
    assert {(told, 1, OTHER_WORDS[0]) for told in ("en", "ar")} <= lost
    assert {q for _t, _s, q in lost} >= {"What is my city?", "شنو مدينتي؟"}


def test_control_the_words_alone_do_not_reach_the_home(monkeypatch):
    """The words read as "live" without the fact kept by the question's
    wording (``recall._named_facts``): a fact's word coverage is worth too
    little to lift what meaning put below the background."""
    monkeypatch.setattr("kazma_core.memory.recall._named_facts", lambda _query, _rows: frozenset())
    lost = {r["question"] for r in _run(monkeypatch) if r["question"] in CITY_HOME and r["want"] not in r["shown"]}
    assert OTHER_WORDS[1] in lost and "What is my city?" in lost


def test_every_fact_a_quick_pass_writes_has_its_words_or_says_why():
    """The class: each predicate the quick passes (English and Arabic) write
    is asked by the table in both languages, or declared with why meaning
    alone decides (``fact_names.NOT_ASKED_BY_WORDS``)."""
    from kazma_core.memory.belief_extractor import extract_beliefs_heuristic

    written = set()
    for told in benchmark.FACT_LANGUAGES["told"].values():
        for text in told:
            written |= {b["predicate"] for b in extract_beliefs_heuristic(text)}
    written |= {b["predicate"] for b in extract_beliefs_heuristic("I prefer tea.")}
    written |= {b["predicate"] for b in extract_beliefs_heuristic("أحب الشاي.")}
    for predicate in sorted(written):
        if predicate in fact_names.NOT_ASKED_BY_WORDS:
            continue
        words = fact_names.asked_words(predicate).split()
        assert any(w.isascii() for w in words), predicate
        assert any(not w.isascii() for w in words), predicate
    # Control: a predicate the table does not know is caught.
    assert fact_names.asked_words("owns_boat") == ""
