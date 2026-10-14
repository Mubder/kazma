"""The quick fact pass reads Arabic (2026-10-14, Memory audit M24 finding).

The extraction benchmark measured the heuristic pass at 0.00 recall in Arabic:
it read English patterns only, so "اسمي ليلى وساكنة في دبي" kept no name and
no city until a model's deep pass ran -- and only if one answered.
``memory/arabic_facts.py`` reads the same kinds of fact in Arabic and in
Arabic mixed with English, from grammar: negation, imperatives, questions,
third persons and "work in a field" keep nothing.

The sentences here are NOT the benchmark's: the benchmark measured the
change; these hold each rule on its own, both ways.
"""

from __future__ import annotations

import sqlite3

import pytest
from kazma_core.memory.arabic_facts import extract_arabic_facts
from kazma_core.memory.belief_extractor import extract_beliefs_heuristic, is_filler_turn


def _facts(text: str) -> set[tuple[str, str]]:
    return {(f["predicate"], f["object"]) for f in extract_beliefs_heuristic(text)}


@pytest.mark.parametrize(("text", "expected"), [
    ("مرحبا، اسمي سارة", {("name_is", "سارة")}),
    ("انا اسمي أبو خالد", {("name_is", "أبو خالد")}),
    ("أسكن في الدوحة من ثلاث سنين", {("lives_in", "الدوحة")}),
    ("وأنا ساكنة في مدينة الكويت.", {("lives_in", "مدينة الكويت")}),
    ("انتقلت إلى جدة قبل شهرين", {("lives_in", "جدة")}),
    ("أعمل في مستشفى الملك فهد", {("works_at", "مستشفى الملك فهد")}),
    ("موظفة في وزارة الصحة", {("works_at", "وزارة الصحة")}),
    ("أشتغل عند Careem", {("works_at", "Careem")}),
    ("لوني المفضل الأخضر", {("favorite_color", "الأخضر")}),
    ("فيلمي المفضل هو الرسالة", {("favorite_movie", "الرسالة")}),
    ("أعشق القهوة التركية", {("prefers", "القهوة التركية")}),
    ("أنا أستخدم Notion للملاحظات", {("prefers", "Notion")}),
    ("عندي حساسية ضد اللاكتوز", {("allergic_to", "اللاكتوز")}),
    ("I'm allergic to shellfish and dust.", {("allergic_to", "shellfish and dust")}),
    ("I love الكبسة", {("prefers", "الكبسة")}),
    ("I live in Muscat الحين", {("lives_in", "Muscat")}),
])
def test_a_statement_about_the_user_is_kept(text, expected):
    assert _facts(text) == expected
    assert not is_filler_turn(text)


@pytest.mark.parametrize("text", [
    "ما أحب الحليب",            # negated
    "مو ساكن في دبي",           # negated
    "استخدم البحث للإجابة",      # the imperative to Kazma
    "اشتغل على التقرير الحين",   # the imperative
    "شو كان اسمي؟",             # a question
    "غيرت اسمي في التطبيق",      # the name word inside another clause
    "أبوي ساكن في العين",        # a third person
    "أخوي شغال في شركة نفط",      # a third person
    "أشتغل في المبيعات",         # a field, like "I work in sales"
    "انتقلت إلى المرحلة الثانية",  # not a place: the value does not end there
    "ساكن في بيت كبير",          # not a place either
    "أحب أتعلم البرمجة",          # "I'd like to learn"
    "أفضل الكتب لهذا الموضوع؟",   # "the best books"
    "المدينة المفضلة للسياح",     # a favourite, but not the user's
])
def test_a_trap_keeps_nothing(text):
    assert extract_arabic_facts(text) == []


def test_english_only_text_is_left_to_the_english_pass():
    assert extract_arabic_facts("My name is Layla and I live in Lisbon.") == []


def test_one_fact_per_kind_and_english_first():
    """Mixed text: the English pass's reading wins a kind both find."""
    facts = extract_beliefs_heuristic("My name is Omar واسمي عمر وساكن في Doha")
    assert [(f["predicate"], f["object"]) for f in facts] == [("name_is", "Omar"), ("lives_in", "Doha")]


def test_an_arabic_turn_reaches_memory_as_the_users_own_words(tmp_path, monkeypatch):
    """Through the post-turn write path: a short Arabic statement is not
    filler, and its facts are stored as the user stated them."""
    from kazma_core.memory.belief_extractor import extract_and_apply_beliefs_sync
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    primary = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"), check_same_thread=False)
    ensure_primary_schema(primary)
    ensure_ops_schema(ops)
    try:
        stats = extract_and_apply_beliefs_sync(primary, ops, "ساكن في دبي", session_id="s1", turn=1)
        assert not stats["skipped_filler"] and stats["applied"] == 1, stats
        row = primary.execute(
            "SELECT subject, predicate, object, extraction_method FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL").fetchone()
        assert tuple(row) == ("user", "lives_in", "دبي", "user_stated")
    finally:
        primary.close()
        ops.close()


def test_without_the_arabic_pass_the_benchmark_reads_no_arabic(monkeypatch):
    """Negative control: the measurement sees the pass. With it switched off
    the Arabic recall is back to the 0.00 the audit measured."""
    from kazma_core.memory import benchmark

    monkeypatch.setattr("kazma_core.memory.arabic_facts.extract_arabic_facts", lambda _t: [])
    report = benchmark.run_extraction_benchmark()
    assert report["heuristic"]["ar"]["recall"] == 0.0
    on = benchmark.run_extraction_benchmark  # the same run with the pass
    monkeypatch.undo()
    assert on()["heuristic"]["ar"]["recall"] > 0.5
