"""Unit tests for Kazma Arabic i18n & NLP Architecture.

Verifies:
1. 6-Form Arabic CLDR Plural Engine (Zero, One, Two, Few, Many, Other)
2. Markup Guard (Placeholder & Code Block Protection)
3. Arabic Character Normalization for Search Indexing
"""

from __future__ import annotations

import pytest

from kazma_core.safety.markup_guard import (
    protect_markup_tokens,
    restore_markup_tokens,
)
from kazma_memory.arabic_tokenizer import ArabicTokenizer
from kazma_ui.i18n import get_arabic_plural_form, t_plural


# ── 1. 6-Form Arabic CLDR Pluralization Tests ────────────────────────


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


# ── 5. PDF Exporter Two-Stage Pipeline Tests ─────────────────────────


def test_exporter_pdf_compilation():
    from kazma_core.skills.exporter import generate_pdf_html_document, prepare_markdown_for_pdf

    raw_markdown = """# تقرير تقني
التكلفة: \\$0.0035 لملف 15 MB.
المعيار: ISO/IEC 27001-2026 والسرعة https://kazma.ai.
المعادلة: $$R = P \\cdot I$$ والمعادلة الضمنية $P = 0.95$.
    """

    compiled_html = generate_pdf_html_document(
        raw_markdown,
        title="تقرير اختبار",
        model="deepseek-v4-flash",
        session_id="sec-1234",
        timestamp="2026-08-08 17:00",
    )

    assert "<html lang=\"ar\" dir=\"rtl\">" in compiled_html
    assert "$0.0035" in compiled_html
    assert "\\$0.0035" not in compiled_html
    assert '<bdi dir="ltr">https://kazma.ai</bdi>' in compiled_html
    assert '<bdi dir="ltr">ISO/IEC 27001-2026</bdi>' in compiled_html
    assert '<div class="math-block" dir="ltr">' in compiled_html
    assert '<span class="math-inline" dir="ltr">' in compiled_html


# ── 6. Chat Research Recording Tests ─────────────────────────


def test_record_chat_research():
    from kazma_core.tools.research_session import list_sessions, record_chat_research

    sess = record_chat_research(
        "اختبار الذكاء الاصطناعي في الكويت",
        tool_name="web_search",
        result_text="نتائج البحث التقني...",
    )

    assert sess.id.startswith("rs_chat_")
    assert "اختبار الذكاء الاصطناعي" in sess.topic
    assert sess.status == "done"

    all_sessions = list_sessions(limit=50)
    found = [s for s in all_sessions if s.id == sess.id]
    assert len(found) == 1
    assert found[0].topic == sess.topic


# ── 6. File Merger & Tool Runner Tests ─────────────────────────


def test_file_merger_atomic_execution(tmp_path):
    from kazma_core.skills.file_merger import merge_html_parts_and_export_pdf

    template_file = tmp_path / "template.html"
    template_file.write_text("<html><body>{{BODY_PLACEHOLDER}}</body></html>", encoding="utf-8")

    part1 = tmp_path / "part1.html"
    part1.write_text("<h2>الجزء الأول</h2>", encoding="utf-8")
    part2 = tmp_path / "part2.html"
    part2.write_text("<p>محتوى الجزء الثاني</p>", encoding="utf-8")

    result = merge_html_parts_and_export_pdf(
        workspace_dir=str(tmp_path),
        template_relative_path="template.html",
        part_relative_paths=["part1.html", "part2.html"],
        output_html_name="merged_test.html",
        output_pdf_name="merged_test.pdf",
    )

    assert result["status"] == "completed"
    out_html = tmp_path / "merged_test.html"
    assert out_html.exists()
    content = out_html.read_text(encoding="utf-8")
    assert "الجزء الأول" in content
    assert "محتوى الجزء الثاني" in content




