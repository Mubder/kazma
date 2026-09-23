"""Unit tests for Kazma Arabic i18n & NLP Architecture.

Verifies:
1. 6-Form Arabic CLDR Plural Engine (Zero, One, Two, Few, Many, Other)
3. Arabic Character Normalization for Search Indexing
"""

from __future__ import annotations

import pytest

from kazma_core.msa_tokenizer import MSATokenizer
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


# ── 4. Arabic NLP Search Normalization Tests ─────────────────────────


def test_arabic_tokenizer_normalization():
    # kazma_memory's ArabicTokenizer was retired (V1 dead code); the
    # MSA tokenizer in kazma_core provides the same normalization for
    # alef variants + diacritics (ta marbuta / alef maqsura are preserved
    # as distinct letters in MSA normalization).
    tokenizer = MSATokenizer()

    raw = "أَحْمَدُ فِي المَكْتَبَةِ وَإِبْرَاهِيمُ يَقْرَأُ ةً ى ٱ"
    normalized = tokenizer.normalize(raw)

    assert "أ" not in normalized
    assert "إ" not in normalized
    assert "احمد" in normalized
    assert "ابراهيم" in normalized
    assert "المكتبة" in normalized


# ── 5. PDF Exporter Two-Stage Pipeline Tests ─────────────────────────


def test_html_export_of_an_arabic_report():
    """The live HTML engine handles what reports actually contain.

    This test used to exercise skills/exporter.py, which no product code
    imported (removed 2026-09-23). Porting it to the live engine
    (documents.engines.html) found three defects there: an escaped dollar
    rendered with its backslash, URL isolation swallowing the full stop after
    the URL, and display math emitted as a <p> nested inside a <p>.
    """
    from kazma_core.documents.engines.html import HtmlEngine
    from kazma_core.documents.profile import DocProfile

    raw_markdown = (
        "# تقرير تقني\n"
        "التكلفة: \\$0.0035 لملف 15 MB.\n"
        "المعيار: ISO/IEC 27001-2026 والسرعة https://kazma.ai.\n"
        "المعادلة: $$R = P \\cdot I$$ والمعادلة الضمنية $P = 0.95$.\n"
        "\nالأمر: `echo \\$HOME`\n"
    )
    html = HtmlEngine(DocProfile.for_content(raw_markdown)).render_markdown(
        raw_markdown, title="تقرير اختبار"
    )

    assert 'lang="ar"' in html and 'dir="rtl"' in html
    assert "$0.0035" in html and "\\$0.0035" not in html
    assert '<bdi dir="ltr">https://kazma.ai</bdi>.' in html
    assert '<bdi dir="ltr">ISO/IEC 27001-2026</bdi>' in html
    assert '<span class="math-display" dir="ltr">' in html
    assert '<span class="math-inline" dir="ltr">' in html
    assert "<p><p" not in html and '<p class="math-display"' not in html
    assert "echo \\$HOME" in html  # code keeps its backslash


# ── 6. Chat Research Recording Tests ─────────────────────────


def test_record_chat_research(tmp_path, monkeypatch):
    """Chat-initiated research must persist a row (audit T-5).

    Isolate the DB so a live research_sessions.db cannot hide the write,
    and assert suppress_chat_recording is off (pipeline leak would return None).
    """
    import kazma_core.tools.research_session as rs

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(rs, "_db_path", lambda: data / "research_sessions.db")
    assert rs._chat_record_suppressed.get() is False

    sess = rs.record_chat_research(
        "اختبار الذكاء الاصطناعي في الكويت",
        tool_name="web_search",
        result_text="نتائج البحث التقني...",
    )
    assert sess is not None
    assert sess.id.startswith("rs_chat_")
    assert "اختبار الذكاء الاصطناعي" in sess.topic
    assert sess.status == "done"

    all_sessions = rs.list_sessions(limit=50)
    found = [s for s in all_sessions if s.id == sess.id]
    assert len(found) == 1
    assert found[0].topic == sess.topic


# ── 6. File Merger & Tool Runner Tests ─────────────────────────


