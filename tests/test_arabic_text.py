"""Arabic correctness gate for the Documents system.

Every case here corresponds to a defect found in the 2026-08-30 deep audit and
reproduced before it was fixed. They are cheap, dependency-light and run on
every CI chunk — unlike the pixel-measuring visual tests, which need
LibreOffice — so they are the layer that keeps the regressions from returning
silently.

Grouped by the audit finding they close:

* ``TestDirectionClassification``  — S2-6, S3 (block regex → bidi class)
* ``TestSearchFolding``            — S1-4 (FTS tokenizer shatter + no folding)
* ``TestLogicalRepair``            — S2-10 (presentation forms never NFKC'd)
* ``TestParagraphShaping``         — S1-1 (per-span shaping scrambled Arabic)
* ``TestRenderRouting``            — S1-2 (good PDF route gated on ``rtl``)
* ``TestLocaleChrome``             — S3 (Hijri/numerals engine never wired)
* ``TestRemoteParsePolicy``        — S1-5 (egress was opt-out)
"""

from __future__ import annotations

import pathlib
import unicodedata
import uuid

import pytest
from kazma_core.documents import arabic
from kazma_core.documents.profile import DocProfile

# ── the corpus ────────────────────────────────────────────────────────────

VOCALIZED = "كِتَابٌ فِي الذَّكَاءِ الاصْطِنَاعِيِّ"
PLAIN = "كتاب في الذكاء الاصطناعي"
HAMZA_ALEF = "أحمد محمد"
BARE_ALEF = "احمد محمد"
TATWEEL = "الشـــركة الوطنية"
NO_TATWEEL = "الشركة الوطنية"
TAA_MARBUTA = "المكتبة العامة"
HEH = "المكتبه العامة"
ARABIC_DIGITS = "العدد ١٢٣"
ASCII_DIGITS = "العدد 123"

MIXED_STANDARD = "المعيار ISO 27001 معتمد لدى الشركة"
MIXED_URL = "راجع https://kazma.ai للمزيد"
MIXED_MONEY = "التكلفة 1,250 ريال في 2026"
STYLED = "هذا نص عربي **مهم جدا** ثم بقية الجملة"
STYLED_PLAIN = "هذا نص عربي مهم جدا ثم بقية الجملة"

ENGLISH_WITH_ARABIC_WORD = (
    "This is a long English technical report about distributed systems. "
    "Note: كاظمة is our platform name. " + ("More English text here. " * 40)
)
ARABIC_WITH_LATIN_HEADING = (
    "Kazma Platform\nهذا تقرير عربي طويل عن الأنظمة الموزعة ويستخدم كاظمة كاسم للمنصة"
)
HEBREW = "שלום עולם זהו מסמך בעברית"

# Presentation-form dump, the shape a legacy PDF text layer arrives in.
PRESENTATION_DUMP = "ﻟﺎﻟﻣ ﺩﻮﻟﺠ ﺔﻛﺮﺸﻟﺍ"


class TestDirectionClassification:
    """S2-6: one Arabic word must not flip an English document to RTL."""

    def test_english_with_one_arabic_word_stays_ltr(self):
        assert arabic.rtl_ratio(ENGLISH_WITH_ARABIC_WORD) < 0.05
        assert arabic.direction_of(ENGLISH_WITH_ARABIC_WORD) == "ltr"
        assert DocProfile.for_content(ENGLISH_WITH_ARABIC_WORD).direction == "ltr"

    def test_arabic_with_latin_heading_stays_rtl(self):
        assert arabic.direction_of(ARABIC_WITH_LATIN_HEADING) == "rtl"
        assert DocProfile.for_content(ARABIC_WITH_LATIN_HEADING).direction == "rtl"

    def test_hebrew_is_rtl(self):
        """The old block regex was Arabic-only and called Hebrew LTR."""
        assert arabic.has_rtl(HEBREW)
        assert arabic.direction_of(HEBREW) == "rtl"

    def test_ratio_excludes_marks_and_digits(self):
        """Harakat (Mn) and Arabic-Indic digits are not letters."""
        assert arabic.rtl_ratio(VOCALIZED) == pytest.approx(
            arabic.rtl_ratio(PLAIN), abs=0.01
        )
        assert arabic.rtl_ratio("123") == 0.0
        assert arabic.rtl_ratio("١٢٣") == 0.0

    def test_explicit_language_pins_direction(self):
        profile = DocProfile.for_content(PLAIN, language="en")
        assert profile.direction == "ltr"
        assert DocProfile.for_content("Hello", language="ar-SA").direction == "rtl"

    def test_empty_text_is_ltr(self):
        assert arabic.direction_of("") == "ltr"
        assert arabic.rtl_ratio("") == 0.0
        assert not arabic.has_rtl("")


class TestSearchFolding:
    """S1-4: the search normal form, applied identically to index and query."""

    @pytest.mark.parametrize(
        "left,right",
        [
            (VOCALIZED, PLAIN),
            (HAMZA_ALEF, BARE_ALEF),
            (TATWEEL, NO_TATWEEL),
            (TAA_MARBUTA, HEH),
            (ARABIC_DIGITS, ASCII_DIGITS),
            ("آمنة", "امنة"),
            ("إسلام", "اسلام"),
            ("مصطفى", "مصطفي"),
            ("مسؤول", "مسوول"),
        ],
    )
    def test_variants_fold_together(self, left, right):
        assert arabic.fold_for_search(left) == arabic.fold_for_search(right)

    def test_fold_is_idempotent(self):
        once = arabic.fold_for_search(VOCALIZED)
        assert arabic.fold_for_search(once) == once

    def test_fold_preserves_distinct_words(self):
        assert arabic.fold_for_search("كتاب") != arabic.fold_for_search("كتب")

    def test_fold_handles_latin(self):
        assert arabic.fold_for_search("Systems Design") == "systems design"

    def test_fold_of_empty_is_empty(self):
        assert arabic.fold_for_search("") == ""


class TestLogicalRepair:
    """S2-10: presentation forms were detected but never normalized back."""

    def test_presentation_forms_become_base_letters(self):
        from kazma_core.documents.quality import presentation_form_ratio

        assert presentation_form_ratio(PRESENTATION_DUMP) == 1.0
        repaired = arabic.to_logical(PRESENTATION_DUMP)
        assert presentation_form_ratio(repaired) == 0.0
        assert "ش" in repaired

    def test_lam_alef_ligature_decomposes(self):
        assert arabic.to_logical("ﻻ") == "لا"

    def test_clean_text_is_returned_unchanged(self):
        assert arabic.to_logical(PLAIN) is PLAIN

    def test_bidi_controls_are_stripped(self):
        assert arabic.strip_bidi_controls("a‎b‏c") == "abc"

    def test_parser_repairs_before_quality_assessment(self):
        """The IRBuilder must repair the layer, or the page escalates to OCR."""
        from kazma_core.documents.parsers.common import to_logical as builder_fold

        assert builder_fold is arabic.to_logical


class TestParagraphShaping:
    """S1-1: shaping each markdown span separately scrambled the paragraph."""

    def _reference(self, text: str) -> str:
        """What the whole paragraph should look like, shaped as one unit."""
        pytest.importorskip("arabic_reshaper")
        pytest.importorskip("bidi")
        import arabic_reshaper
        from bidi.algorithm import get_display

        reshaper = arabic_reshaper.ArabicReshaper(
            configuration={"delete_harakat": False, "support_ligatures": True}
        )
        return get_display(reshaper.reshape(text), base_dir="R")

    @pytest.mark.parametrize(
        "text",
        [STYLED_PLAIN, MIXED_STANDARD, MIXED_URL, MIXED_MONEY, PLAIN],
    )
    def test_shape_text_matches_reference(self, text):
        assert arabic.shape_text(text, base_dir="rtl") == self._reference(text)

    def test_styled_paragraph_has_the_same_visual_order_as_unstyled(self):
        """The bold markers must not change where any word lands."""
        from kazma_core.documents.rich_render import inline_markdown_to_reportlab

        styled = inline_markdown_to_reportlab(STYLED)
        stripped = styled.replace("<b>", "").replace("</b>", "")
        assert stripped == arabic.shape_text(STYLED_PLAIN, base_dir="rtl")

    def test_style_markup_survives_the_reorder(self):
        from kazma_core.documents.rich_render import inline_markdown_to_reportlab

        out = inline_markdown_to_reportlab(STYLED)
        assert "<b>" in out and "</b>" in out

    def test_spaces_around_a_style_boundary_survive(self):
        """Per-span shaping used to swallow them and jam the words together."""
        from kazma_core.documents.rich_render import inline_markdown_to_reportlab

        out = inline_markdown_to_reportlab(STYLED)
        assert " <b>" in out and "</b> " in out

    def test_latin_run_inside_arabic_keeps_its_own_order(self):
        from kazma_core.documents.rich_render import inline_markdown_to_reportlab

        out = inline_markdown_to_reportlab("المعيار **ISO 27001** معتمد")
        assert "ISO 27001" in out  # not reversed to "10072 OSI"

    def test_link_href_is_not_shaped(self):
        from kazma_core.documents.rich_render import inline_markdown_to_reportlab

        out = inline_markdown_to_reportlab("راجع [الموقع](https://kazma.ai) اليوم")
        assert 'href="https://kazma.ai"' in out

    def test_pure_latin_is_not_shaped_at_all(self):
        from kazma_core.documents.rich_render import inline_markdown_to_reportlab

        out = inline_markdown_to_reportlab("Plain **bold** text")
        assert out == "Plain <b>bold</b> text"

    def test_segments_carry_their_style(self):
        segments = arabic.shape_spans(
            [("هذا ", None), ("مهم", "b"), (" جدا", None)], base_dir="rtl"
        )
        styles = [s.style for s in segments]
        assert "b" in styles
        # Visual order is reversed relative to logical order for RTL.
        assert styles.index("b") == 1

    def test_wrapped_paragraph_keeps_lines_in_reading_order(self):
        from kazma_core.documents.rich_render import inline_markdown_to_reportlab

        long_text = "هذه فقرة عربية طويلة تحتوي على كلمات كثيرة جدا " * 6
        out = inline_markdown_to_reportlab(
            long_text, col_width=380, font_name="Helvetica", font_size=11
        )
        assert "<br/>" in out
        # No line may be empty and none may start with a stray space.
        for line in out.split("<br/>"):
            assert line == line.lstrip(" ")
            assert line.strip()

    def test_shaping_is_skipped_when_disabled(self):
        from kazma_core.documents.rich_render import inline_markdown_to_reportlab

        out = inline_markdown_to_reportlab(PLAIN, shape_arabic=False)
        assert not any(
            unicodedata.name(c, "").startswith("ARABIC")
            and "FORM" in unicodedata.name(c, "")
            for c in out
        )


class TestRenderRouting:
    """S1-2: the good PDF route was gated on ``rtl``, not on ``shape_arabic``."""

    def test_mixed_document_requests_shaping(self):
        profile = DocProfile.for_content(ENGLISH_WITH_ARABIC_WORD)
        assert profile.direction == "ltr"
        assert profile.shape_arabic is True

    def test_pure_english_does_not_request_shaping(self):
        assert DocProfile.for_content("Purely English content.").shape_arabic is False

    def test_arabic_document_requests_shaping(self):
        assert DocProfile.for_content(PLAIN).shape_arabic is True

    def test_engine_gates_on_shape_arabic(self):
        source = pathlib.Path(
            "kazma-core/kazma_core/documents/engines/pdf.py"
        ).read_text(encoding="utf-8")
        assert "if self.profile.shape_arabic and self._render_via_docx" in source
        assert "if self.profile.rtl and self._render_via_docx" not in source


class TestLocaleChrome:
    """S3: Kazma had a Hijri engine wired to chat and never to documents."""

    def test_arabic_numerals_render(self):
        from kazma_core.documents.style_theme import format_page_number

        assert format_page_number(123, numerals="arab") == "١٢٣"
        assert format_page_number(123) == "123"

    def test_hijri_date_is_available_to_documents(self):
        from datetime import date

        from kazma_core.documents.style_theme import format_document_date

        out = format_document_date(
            date(2026, 8, 30), rtl=True, calendar="islamic-umalqura"
        )
        assert "هـ" in out

    def test_gregorian_is_the_default(self):
        from datetime import date

        from kazma_core.documents.style_theme import format_document_date

        assert format_document_date(date(2026, 8, 30), rtl=False) == "2026-08-30"

    def test_profile_carries_numerals_and_calendar(self):
        profile = DocProfile.for_content(PLAIN, numerals="arab", calendar="islamic-umalqura")
        assert profile.numerals == "arab"
        assert profile.calendar == "islamic-umalqura"
        assert profile.chrome["numerals"] == "arab"

    def test_defaults_stay_latin_gregorian(self):
        profile = DocProfile.for_content(PLAIN)
        assert profile.numerals == "latn"
        assert profile.calendar == "gregory"


class TestRemoteParsePolicy:
    """S1-5: third-party parse egress must be opt-in, and audited."""

    def test_remote_parse_defaults_off(self):
        from kazma_core.documents.config import DocumentConfig

        config = DocumentConfig(storage_root=pathlib.Path("."))
        assert config.security_remote_parse is False
        assert config.security_local_salvage is True

    def test_egress_is_skipped_without_policy(self, monkeypatch, tmp_path):
        from kazma_core.documents import extract_salvage
        from kazma_core.documents.config import DocumentConfig
        from kazma_core.documents.models import DocumentIR, Provenance

        called: list[str] = []
        monkeypatch.setattr(
            extract_salvage,
            "try_remote_parse",
            lambda *a, **k: called.append("remote") or None,
        )
        monkeypatch.setattr(extract_salvage, "try_docling", lambda *a, **k: None)

        weak = DocumentIR(
            document_id=str(uuid.uuid4()),
            version_id=str(uuid.uuid4()),
            pages=(),
            provenance=Provenance(source="test", parser="test"),
            metadata={"extraction_score": 0.0},
        )
        config = DocumentConfig(storage_root=tmp_path)
        assert config.security_remote_parse is False
        extract_salvage.maybe_salvage_extract(tmp_path / "x.pdf", weak, config=config)
        assert called == []

        # ...and fires when the operator turns it on.
        config_on = DocumentConfig(storage_root=tmp_path, security_remote_parse=True)
        extract_salvage.maybe_salvage_extract(tmp_path / "x.pdf", weak, config=config_on)
        assert called == ["remote"]

    def test_audit_hook_fires_on_egress(self, tmp_path):
        from kazma_core.documents import extract_salvage

        seen: list[tuple[str, int]] = []
        extract_salvage.set_salvage_audit_hook(
            lambda provider, path, size: seen.append((provider, size))
        )
        try:
            extract_salvage._record_egress("llamaparse", tmp_path / "x.pdf", 4096)
        finally:
            extract_salvage.set_salvage_audit_hook(None)
        assert seen == [("llamaparse", 4096)]


class TestFontPolicy:
    """S2-8: font selection ranked by coverage, not by whichever file exists."""

    def test_latin_only_fonts_are_rejected_for_arabic(self):
        from kazma_core.documents.fonts import has_arabic_coverage

        # A path that cannot be parsed must be rejected, never assumed good.
        assert has_arabic_coverage("/nonexistent/font.ttf") is False

    def test_resolution_reports_readiness_rather_than_guessing(self):
        from kazma_core.documents.fonts import resolve_fonts

        choice = resolve_fonts(arabic=True)
        assert isinstance(choice.arabic_ready, bool)
        if choice.regular is None:
            assert choice.arabic_ready is False


class TestSearchIndexMigration:
    """S1-4: existing databases must gain the folded column, once, in place."""

    def _legacy_db(self, path: str) -> None:
        """Build a database with the pre-fold FTS schema and pre-fold rows."""
        import sqlite3

        from kazma_core.stores.knowledge import _SCHEMA

        old = _SCHEMA.replace("    content,\n    folded,\n", "    content,\n")
        conn = sqlite3.connect(path)
        conn.executescript(old)
        conn.execute(
            "INSERT INTO knowledge_libraries (id,name,description,seed_url,"
            "chunk_count,created_at,updated_at) VALUES "
            "('lib','Lib','','',0,'now','now')"
        )
        for chunk_id, content in (("c0", VOCALIZED), ("c1", HAMZA_ALEF)):
            conn.execute(
                "INSERT INTO knowledge_chunks (id,library_id,source_url,"
                "document_title,section_header,chunk_index,content_hash,has_code,"
                "char_count,content,metadata_json,active,tombstoned,created_at) "
                "VALUES (?,?,?,'','',0,?,0,?,?,'{}',1,0,'now')",
                (chunk_id, "lib", f"doc://{chunk_id}", chunk_id, len(content), content),
            )
            conn.execute(
                "INSERT INTO knowledge_chunks_fts "
                "(content,library_id,source_url,chunk_id) VALUES (?,?,?,?)",
                (content, "lib", f"doc://{chunk_id}", chunk_id),
            )
        conn.commit()
        conn.close()

    def test_legacy_database_is_migrated_and_searchable(self, tmp_path):
        import sqlite3

        from kazma_core.stores.knowledge import KnowledgeStore

        db = str(tmp_path / "settings.db")
        self._legacy_db(db)

        conn = sqlite3.connect(db)
        before = {r[1] for r in conn.execute("PRAGMA table_info(knowledge_chunks_fts)")}
        conn.close()
        assert "folded" not in before

        store = KnowledgeStore(db_path=db)

        conn = sqlite3.connect(db)
        after = {r[1] for r in conn.execute("PRAGMA table_info(knowledge_chunks_fts)")}
        rows = conn.execute("SELECT COUNT(*) FROM knowledge_chunks_fts").fetchone()[0]
        conn.close()
        assert "folded" in after
        assert rows == 2, "rows must be repopulated from knowledge_chunks"

        # Content indexed before the migration is now reachable by folded queries.
        assert "c0" in [h[0] for h in store.fts_search("كتاب", "lib")]
        assert "c1" in [h[0] for h in store.fts_search("احمد", "lib")]

    def test_migration_is_idempotent(self, tmp_path):
        from kazma_core.stores.knowledge import KnowledgeStore

        db = str(tmp_path / "settings.db")
        self._legacy_db(db)
        KnowledgeStore(db_path=db)
        reopened = KnowledgeStore(db_path=db)
        assert "c0" in [h[0] for h in reopened.fts_search("كتاب", "lib")]


class TestPinnedFonts:
    """The vendored IBM Plex pair — present, verified, brand face for EN+AR."""

    def test_plex_is_vendored_and_arabic_ready(self):
        from kazma_core.documents.fonts import bundled_font_dir, has_arabic_coverage

        directory = bundled_font_dir()
        assert directory is not None, "no pinned font directory found"
        regular = directory / "IBMPlexSansArabic-Regular.ttf"
        bold = directory / "IBMPlexSansArabic-Bold.ttf"
        assert regular.is_file() and bold.is_file()
        assert has_arabic_coverage(str(regular))
        assert has_arabic_coverage(str(bold))

    def test_amiri_fallback_still_ships(self):
        from kazma_core.documents.fonts import bundled_font_dir, has_arabic_coverage

        directory = bundled_font_dir()
        assert directory is not None
        regular = directory / "Amiri-Regular.ttf"
        bold = directory / "Amiri-Bold.ttf"
        assert regular.is_file() and bold.is_file()
        assert has_arabic_coverage(str(regular))

    def test_licence_ships_with_the_fonts(self):
        """OFL 1.1 requires the licence to travel with the font."""
        from kazma_core.documents.fonts import bundled_font_dir

        directory = bundled_font_dir()
        assert directory is not None
        licence = (directory / "OFL.txt").read_text(encoding="utf-8", errors="replace")
        assert "SIL OPEN FONT LICENSE" in licence.upper()
        plex_licence = (directory / "OFL-IBM-Plex.txt").read_text(
            encoding="utf-8", errors="replace"
        )
        assert "SIL OPEN FONT LICENSE" in plex_licence.upper()

    def test_arabic_job_uses_the_pinned_font(self):
        from kazma_core.documents.fonts import resolve_fonts

        choice = resolve_fonts(arabic=True)
        assert choice.source == "bundled"
        assert choice.arabic_ready is True
        assert "Plex" in choice.regular.name

    def test_latin_job_uses_brand_plex_not_naskh(self):
        """Brand Plex is the generated-doc face in both directions.

        Amiri must not restyle English documents; Plex is the shared sans.
        """
        from kazma_core.documents.fonts import resolve_fonts

        choice = resolve_fonts(arabic=False)
        assert choice.regular is not None
        assert "Amiri" not in choice.regular.name
        assert "Plex" in choice.regular.name
        assert choice.source == "bundled"


class TestToolSurfaceBoundary:
    """S3: the path-based and durable document tools were indistinguishable."""

    def _manifest(self, name: str) -> dict:
        import yaml

        path = pathlib.Path(
            f"kazma-skills/kazma_skills/native/{name}/skill_manifest.yaml"
        )
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    @pytest.mark.parametrize(
        "tool,counterpart",
        [
            ("read_document", "document_read"),
            ("convert_document", "document_convert"),
            ("pdf_redact", "document_redact"),
        ],
    )
    def test_overlapping_processor_tools_name_their_counterpart(self, tool, counterpart):
        """The model picks from descriptions, so the boundary must live there."""
        description = self._manifest("document_processor")["tools"][tool]["description"]
        assert "TRANSIENT, PATH-BASED" in description
        assert counterpart in description

    def test_platform_states_it_is_preferred(self):
        assert "PREFERRED" in self._manifest("document_platform")["description"]

    def test_no_tools_were_removed(self):
        """Consolidation is a routing fix, not a deletion — flows must not break."""
        assert len(self._manifest("document_processor")["tools"]) == 9
        assert len(self._manifest("document_platform")["tools"]) == 8


class TestDocsMatchCode:
    """The audit reported an already-fixed GC defect because the docs were stale."""

    def test_gc_is_backend_agnostic(self):
        retention = pathlib.Path(
            "kazma-core/kazma_core/documents/retention.py"
        ).read_text(encoding="utf-8")
        assert "gc_postgres_metadata_sql_port_pending" not in retention
        assert "gc_mark" in retention
        for backend in ("repository.py", "repository_pg.py"):
            source = pathlib.Path(
                f"kazma-core/kazma_core/documents/{backend}"
            ).read_text(encoding="utf-8")
            assert "def gc_mark(" in source, f"{backend} must implement gc_mark"

    def test_docs_do_not_claim_the_removed_skip(self):
        """The sentinel may only appear in prose that marks it historical.

        Checked per sentence rather than per line: prose wraps, so a line-based
        rule reports a false positive the moment the qualifier lands on the
        next line.
        """
        import re

        historical = ("removed", "gone", "earlier release", "no longer")
        for doc in (
            "AGENTS.md",
            "docs/docs/guide/document-intelligence.md",
            "docs/docs/ops/document-processing.md",
        ):
            text = re.sub(r"\s+", " ", pathlib.Path(doc).read_text(encoding="utf-8"))
            stale = [
                sentence
                for sentence in re.split(r"(?<=[.|])\s+", text)
                if "gc_postgres_metadata_sql_port_pending" in sentence
                and not any(marker in sentence.lower() for marker in historical)
            ]
            assert not stale, f"{doc} still describes the removed GC skip: {stale}"


class TestGeneratedArabicRoundTrip:
    """The platform used to flag its own Arabic output as low quality.

    ``generate_document`` renders an artifact and re-ingests it through the
    normal pipeline. A reportlab-rendered Arabic PDF contains presentation
    forms by construction, so the parser flagged it ``high_presentation_forms``
    and tried to escalate to OCR — the system reporting that its own Arabic
    path was broken, with nobody reading the message. This closes that loop.
    """

    def test_generated_arabic_pdf_reparses_cleanly(self, tmp_path):
        fitz = pytest.importorskip("pymupdf")

        from kazma_core.documents.content_model import (
            BodyBlock,
            ContentModel,
            HeadingBlock,
            TitleBlock,
        )
        from kazma_core.documents.engines.pdf import PdfEngine
        from kazma_core.documents.quality import (
            presentation_form_ratio,
            score_extracted_text,
        )

        body = (
            "هذا تقرير عن منصة كاظمة للذكاء الاصطناعي. يدعم النظام المعيار "
            "**ISO 27001** ويوفر واجهة برمجية موثقة. تمت مراجعة هذه الوثيقة "
            "من قبل فريق الأمن ووافق عليها المدير التنفيذي في الاجتماع الأخير."
        )
        model = ContentModel()
        model.add(TitleBlock(text="تقرير منصة كاظمة", level=0, fill="3b82f6"))
        model.add(HeadingBlock(text="المقدمة", level=1))
        model.add(BodyBlock(text=body))

        profile = DocProfile.for_content("تقرير منصة كاظمة\n" + body)
        assert profile.direction == "rtl" and profile.shape_arabic

        out = tmp_path / "arabic.pdf"
        warnings: list[str] = []
        PdfEngine(profile, warnings).render(model, out)
        assert out.is_file() and out.stat().st_size > 0

        extracted = "".join(page.get_text() for page in fitz.open(out))
        assert arabic.has_rtl(extracted), "generated Arabic PDF has no Arabic text"

        # After the parser's repair pass the text must be logical-order base
        # letters, not a visual glyph dump that needs OCR to recover.
        repaired = arabic.to_logical(extracted)
        assert presentation_form_ratio(repaired) < 0.05
        assert score_extracted_text(repaired, extractor="pymupdf") >= 0.55, (
            "the platform's own Arabic output scores below SALVAGE_SCORE"
        )
