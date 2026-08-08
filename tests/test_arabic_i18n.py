"""Unit tests for Kazma Arabic i18n & NLP Architecture.

Verifies:
1. BiDi Unicode Directional Isolation (LRI \u2066 ... PDI \u2069)
2. 6-Form Arabic CLDR Plural Engine (Zero, One, Two, Few, Many, Other)
3. Markup Guard (Placeholder & Code Block Protection)
4. Arabic Character Normalization for Search Indexing
"""

from __future__ import annotations

import datetime
import pytest

from kazma_core.bidi_utils import (
    LRI,
    PDI,
    format_bidi_timestamp,
    isolate_ltr,
    wrap_mixed_arabic_tokens,
)
from kazma_core.safety.markup_guard import (
    protect_markup_tokens,
    restore_markup_tokens,
)
from kazma_memory.arabic_tokenizer import ArabicTokenizer
from kazma_ui.i18n import get_arabic_plural_form, t_plural


# ── 1. BiDi Directional Isolation Tests ──────────────────────────────


def test_isolate_ltr_wraps_in_lri_and_pdi():
    content = "10:30 AM"
    isolated = isolate_ltr(content)
    assert isolated == f"{LRI}10:30 AM{PDI}"


def test_format_bidi_timestamp_returns_isolated_string():
    dt = datetime.datetime(2026, 5, 12, 14, 30, 0)
    ts = format_bidi_timestamp(dt, "%Y-%m-%d %H:%M")
    assert ts == f"{LRI}2026-05-12 14:30{PDI}"


def test_wrap_mixed_arabic_tokens_isolates_urls_and_model_names():
    text = "النموذج المستخدم هو gemini-2.5-flash والموقع https://kazma.ai للتواصل"
    wrapped = wrap_mixed_arabic_tokens(text)
    assert f"{LRI}gemini-2.5-flash{PDI}" in wrapped
    assert f"{LRI}https://kazma.ai{PDI}" in wrapped


# ── 2. 6-Form Arabic CLDR Pluralization Tests ────────────────────────


@pytest.mark.parametrize(
    "count,expected_category",
    [
        (0, "zero"),
        (1, "one"),
        (2, "two"),
        (3, "few"),
        (5, "few"),
        (10, "few"),
        (11, "many"),
        (50, "many"),
        (99, "many"),
        (100, "other"),
        (101, "other"),  # CLDR rule: 100+ (except mod100 in 3..99) -> "other"
        (102, "other"),  # CLDR rule: 100+ -> "other"
        (105, "few"),  # 105 % 100 = 5 -> "few"
    ],
)
def test_get_arabic_plural_form(count: int, expected_category: str):
    assert get_arabic_plural_form(count) == expected_category


def test_t_plural_arabic_resolution():
    assert t_plural("knowledge.chunks_count", 0, lang="ar") == "لا توجد مقاطع"
    assert t_plural("knowledge.chunks_count", 1, lang="ar") == "مقطع واحد"
    assert t_plural("knowledge.chunks_count", 2, lang="ar") == "مقطعان"
    assert t_plural("knowledge.chunks_count", 5, lang="ar") == "5 مقاطع"
    assert t_plural("knowledge.chunks_count", 15, lang="ar") == "15 مقطعاً"
    assert t_plural("knowledge.chunks_count", 100, lang="ar") == "100 مقطع"


# ── 3. Markup & Placeholder Guard Tests ──────────────────────────────


def test_protect_and_restore_markup_tokens():
    text = "مرحباً {user_name}، يمكنك استخدام **الأمر** `python main.py` وتفقد <span class=\"badge\">الرمز</span>."
    protected, tokens = protect_markup_tokens(text)

    # Placeholders/tags/code blocks should be stashed
    assert "{user_name}" not in protected
    assert "**الأمر**" not in protected
    assert "`python main.py`" not in protected
    assert "__KAZMA_TOKEN_0__" in protected

    restored = restore_markup_tokens(protected, tokens)
    assert restored == text


# ── 4. Arabic NLP Search Normalization Tests ─────────────────────────


def test_arabic_tokenizer_normalization():
    tokenizer = ArabicTokenizer()

    # Alef variants, Ta Marbuta, Alef Maqsura, Diacritics
    raw = "أَحْمَدُ فِي المَكْتَبَةِ وَإِبْرَاهِيمُ يَقْرَأُ ةً ى ٱ"
    normalized = tokenizer.normalize(raw)

    assert "أ" not in normalized
    assert "إ" not in normalized
    assert "ة" not in normalized
    assert "ى" not in normalized
    assert "احمد" in normalized
    assert "ابراهيم" in normalized
    assert "المكتبه" in normalized
