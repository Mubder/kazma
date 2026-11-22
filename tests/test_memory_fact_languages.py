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
