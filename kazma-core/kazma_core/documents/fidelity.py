"""Document extraction fidelity: a versioned EN/AR/bilingual corpus, measured.

2026-10-13 (F09/Q02). Fidelity was asserted case by case (a DOCX order fix,
a synthetic salvage label) with no corpus and no declared thresholds. This
module is the corpus and the measurement; the thresholds are declared in
``tests/fixtures/documents/fidelity_thresholds.json`` with the corpus
version they were measured on, and ``tests/test_document_fidelity.py`` holds
every run to them.

The corpus is generated, not stored: each case is a spec (title, headings,
paragraphs, a table, exact numbers) rendered by Kazma's OWN engines (DOCX,
PDF, HTML) and, for OCR, a page of the rendered PDF rasterized to PNG. The
spec is the ground truth, so a document cannot drift from what it is
measured against. Every case is then parsed by the real isolated parser
(``DocumentService.parse_ingested_blob``) and measured:

``token_recall``      share of the ground-truth words found, in order, per
                      segment (1 - omission), averaged over segments.
``segment_recall``    share of segments recovered at >= 95 % of their words,
                      together: the matched words span no more than the
                      segment plus ``_SPAN_SLACK`` tokens (a page header and
                      footer between the halves of a paragraph broken across
                      pages). Words found in order but interleaved with
                      another column's are not a recovered segment.
``order``             longest in-order run of recovered segments / recovered.
``number_recall``     share of the case's exact numbers present verbatim
                      (Arabic-Indic digits read as their value).
``table_cell_recall`` share of table cells found inside TABLE blocks.
``page_honesty``      1.0 when the page claims are true: a PDF's IR pages are
                      exactly its physical pages; a format without physical
                      pages (DOCX, HTML) claims a logical page, never a
                      physical one.
``alignment``         (PDF) share of paragraphs laid out on their language's
                      side: Arabic right, English left.
``cer`` / ``wer``     (OCR) character / word error rate against the page text.

Version 2 adds the shapes a clean generated page does not show, still
generated from a spec so the truth cannot drift:

``*-pdf-long``      24 sections over many pages (EN, AR): page order,
                    numbers and the RTL repair across page breaks.
``en-pdf-columns``  two columns laid out by a DIFFERENT producer (MuPDF's
                    Story engine, text flowing from one column into the next):
                    reading order must take column 1 before column 2.
``en-*-widetable``  six columns of wrapping cell text and figures (DOCX, PDF).
``en-ocr-degraded`` the English page as a poor scan: rotated 1.2 degrees,
                    two-thirds resolution, blurred, speckled, JPEG-compressed.

Version 3 (the same day, after a real 300 dpi scan of an Arabic booklet
lost its tables): ``*-ocr-table``, the OCR page with its ruled table, read
cell by cell (``ocr/tables.py``). The OCR reference text is now the whole
page -- the sections, then the table's rows -- since a page's table is text
the page shows.

Version 4 (2026-10-15, measuring OCR engines for Arabic): the reference is
ALL the text the page shows, so it also holds the page furniture Kazma's
renderer drew on it -- the brand footer the Arabic route (DOCX through
LibreOffice) prints on every page. Version 3 left it out, so an OCR that
read the page perfectly scored CER 0.114 / WER 0.091 on ``ar-ocr`` (0.093 /
0.075 with the table): the Arabic cases could not reach the release target
whatever the engine. Which furniture a page carries is read from the
rendered PDF's own text layer (``_drawn_furniture``), never assumed per
language. The ``*-ocr`` cases are measured with each OCR engine Kazma can
run (``documents.ocr.engine``), each against its own floors.

What this is NOT: a corpus of real-world documents. Paper scans,
handwriting, forms, nested or merged-cell tables and documents from
producers other than Kazma's engines and MuPDF are not represented, and a
pass here is not a claim about them.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

__all__ = [
    "CORPUS_VERSION",
    "ERROR_RATES",
    "OCR_ENGINES",
    "RELEASE_TARGETS",
    "FidelityCase",
    "corpus",
    "engine_available",
    "is_ocr_case",
    "lock_thresholds",
    "measure_case",
    "meets",
    "run_corpus",
]

CORPUS_VERSION = "2026-10-15.4"

#: Metrics where lower is better (a ceiling, not a floor).
ERROR_RATES = frozenset({"cer", "wer"})
#: What a case must reach before Kazma claims it is qualified (Q02). A case
#: below these is still held to its regression floor; it is just not claimed.
RELEASE_TARGETS: dict[str, float] = {
    "token_recall": 0.99,
    "segment_recall": 0.95,
    "order": 1.0,
    "number_recall": 1.0,
    "table_cell_recall": 1.0,
    "page_honesty": 1.0,
    "alignment": 1.0,
    "cer": 0.02,
    "wer": 0.05,
}
#: Recognition varies a little between Tesseract builds; text formats, page
#: claims and order do not.
_OCR_MARGIN = 0.02
#: Tokens of page furniture allowed inside one recovered segment.
_SPAN_SLACK = 25
_RECOGNITION = frozenset({"token_recall", "segment_recall", "number_recall", "cer", "wer"})

_EN_TITLE = "Quarterly Operations Review"
_EN_SECTIONS = (
    ("Summary", (
        "Revenue for the quarter reached 1,234,567.89 dollars across all regions.",
        "The northern warehouse shipped 4,250 units before the deadline on 2026-09-30.",
    )),
    ("Findings", (
        "Return rates fell to 3.75 percent after the packaging change.",
        "Two suppliers missed delivery windows and were placed under review.",
    )),
)
_AR_TITLE = "مراجعة العمليات الفصلية"
_AR_SECTIONS = (
    ("الملخص", (
        "بلغت الإيرادات لهذا الفصل ١٢٣٤٥٦٧ ريالاً في جميع المناطق.",
        "شحن المستودع الشمالي ٤٢٥٠ وحدة قبل الموعد النهائي في 2026-09-30.",
    )),
    ("النتائج", (
        "انخفضت نسبة المرتجعات إلى ٣٫٧٥ بالمئة بعد تغيير التغليف.",
        "تأخر موردان عن مواعيد التسليم ووضعا تحت المراجعة.",
    )),
)
_BI_TITLE = "Bilingual Status Report تقرير ثنائي اللغة"
_BI_SECTIONS = (
    ("Overview", (
        "The migration completed on schedule with 99.95 percent availability.",
        "اكتملت عملية الترحيل في موعدها بنسبة توفر بلغت 99.95 بالمئة.",
    )),
    ("المخاطر", (
        "يجب مراجعة صلاحيات الوصول قبل نهاية الشهر.",
        "Access permissions must be reviewed before the end of the month.",
    )),
)
_EN_TABLE = (("Region", "Units", "Revenue"), (("North", "1,250", "18,400.75"), ("South", "980", "12,035.10")))
_AR_TABLE = (("المنطقة", "الوحدات", "الإيراد"), (("الشمال", "١٢٥٠", "١٨٤٠٠"), ("الجنوب", "٩٨٠", "١٢٠٣٥")))
_BI_TABLE = (("Item", "البند", "Value"), (("Uptime", "التوفر", "99.95"), ("Incidents", "الحوادث", "3")))


@dataclass(frozen=True)
class FidelityCase:
    """One generated document and the ground truth it is measured against."""

    case_id: str
    language: str  # en | ar | bilingual
    fmt: str  # docx | pdf | html | ocr
    title: str
    sections: tuple[tuple[str, tuple[str, ...]], ...]
    table: tuple[tuple[str, ...], tuple[tuple[str, ...], ...]] | None
    numbers: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)
    variant: str = ""

    def segments(self) -> list[str]:
        out = [self.title]
        for heading, paragraphs in self.sections:
            out.append(heading)
            out.extend(paragraphs)
        return out


def corpus() -> list[FidelityCase]:
    """Every case of the current corpus version (deterministic)."""
    specs = {
        "en": (_EN_TITLE, _EN_SECTIONS, _EN_TABLE, ("1,234,567.89", "4,250", "2026-09-30", "3.75", "18,400.75")),
        "ar": (_AR_TITLE, _AR_SECTIONS, _AR_TABLE, ("١٢٣٤٥٦٧", "٤٢٥٠", "2026-09-30", "١٨٤٠٠")),
        "bilingual": (_BI_TITLE, _BI_SECTIONS, _BI_TABLE, ("99.95",)),
    }
    cases = []
    for fmt in ("docx", "pdf", "html", "ocr"):
        for language, (title, sections, table, numbers) in specs.items():
            if fmt == "ocr" and language == "bilingual":
                continue  # OCR runs one language model per page here
            case = FidelityCase(
                case_id=f"{language}-{fmt}", language=language, fmt=fmt, title=title,
                sections=sections, table=None if fmt == "ocr" else table, numbers=numbers,
            )
            if case.table is None:
                # Only the numbers this document actually shows are expected.
                shown = "\n".join(case.segments())
                case = replace(case, numbers=tuple(n for n in numbers if n in shown))
            cases.append(case)
    return cases + _hard_cases()


def _ar_digits(value: int) -> str:
    return str(value).translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))


def _long_sections(language: str) -> tuple[tuple[tuple[str, tuple[str, ...]], ...], tuple[str, ...]]:
    """24 sections of three numbered paragraphs, deterministic."""
    sections, numbers = [], []
    for s in range(1, 25):
        paragraphs = []
        for p in range(1, 4):
            n = s * 10 + p
            units = (n * 37) % 900 + 100
            date = f"2026-{1 + n % 12:02d}-{1 + n % 28:02d}"
            if language == "ar":
                paragraphs.append(
                    f"سجل البند رقم {_ar_digits(n)} عدد {_ar_digits(units)} وحدة في الموقع الشمالي "
                    f"بتاريخ {date} وتمت مراجعته من فريق العمليات قبل إغلاق الفترة."
                )
                if p == 1:
                    numbers.extend((_ar_digits(units), date))
            else:
                paragraphs.append(
                    f"Line item {n} recorded {units} units at the northern site on {date} and was "
                    "reviewed by the operations team before the period closed."
                )
                if p == 1:
                    numbers.extend((str(units), date))
        heading = f"القسم {_ar_digits(s)}" if language == "ar" else f"Section {s}"
        sections.append((heading, tuple(paragraphs)))
    return tuple(sections), tuple(numbers)


_COLUMN_SECTIONS = (
    ("Background", (
        "The regional depots were consolidated into three hubs during the spring, which shortened the "
        "average delivery route by forty kilometres and reduced the number of handovers per parcel.",
        "Each hub now keeps a rolling inventory of the two hundred most requested items, replenished "
        "every night from the central warehouse according to the previous day's orders.",
        "Drivers reported fewer missed collection windows once the new routing schedule replaced the "
        "fixed daily loops that had been in place since the network was first built.",
    )),
    ("Results", (
        "Average delivery time fell from 3.4 days to 2.1 days, and the share of parcels delivered on the "
        "first attempt rose to 92.5 percent across all three hubs by the end of the quarter.",
        "Fuel consumption dropped by 11 percent even though the number of parcels carried grew, because "
        "vehicles travelled fuller and returned to the hub less often during each shift.",
        "Customer complaints about late deliveries halved, while complaints about damaged parcels stayed "
        "level, which suggests that the extra handling at the hubs did not harm the goods.",
    )),
    ("Next steps", (
        "The operations team will extend the same model to the coastal region in the autumn, starting "
        "with the two depots that already share a loading yard and a dispatch office.",
        "A review of the hub staffing levels is planned for the end of the year, once a full twelve "
        "months of delivery data is available for comparison with the previous network.",
    )),
)
_WIDE_TABLE = (
    ("Site", "Region", "Operator", "Units", "Revenue", "Status"),
    tuple(
        (f"Site {i}", region, operator, f"{1000 + i * 137:,}", f"{18000 + i * 1234.5:,.2f}", status)
        for i, (region, operator, status) in enumerate((
            ("Northern distribution hub annex", "Harbour Logistics", "Active"),
            ("Southern coastal depot", "Delta Freight Partners", "Under review"),
            ("Eastern industrial park", "Harbour Logistics", "Active"),
            ("Western mountain corridor", "Summit Carriers Limited", "Suspended pending audit"),
            ("Central city micro hub", "Urban Courier Cooperative", "Active"),
            ("Airport cargo terminal", "Delta Freight Partners", "Expanding"),
            ("Rail interchange yard", "Summit Carriers Limited", "Active"),
            ("Free zone bonded warehouse", "Harbour Logistics", "Under review"),
        ), start=1)
    ),
)


def _hard_cases() -> list[FidelityCase]:
    en_long, en_numbers = _long_sections("en")
    ar_long, ar_numbers = _long_sections("ar")
    rows = _WIDE_TABLE[1]
    table_numbers = tuple(r[3] for r in rows) + tuple(r[4] for r in rows)
    return [
        FidelityCase("en-pdf-long", "en", "pdf", "Annual Operations Ledger", en_long, None,
                     en_numbers, variant="long"),
        FidelityCase("ar-pdf-long", "ar", "pdf", "سجل العمليات السنوي", ar_long, None,
                     ar_numbers, variant="long"),
        FidelityCase("en-pdf-columns", "en", "pdf", "Network Consolidation Report", _COLUMN_SECTIONS,
                     None, ("3.4", "2.1", "92.5"), variant="columns"),
        FidelityCase("en-docx-widetable", "en", "docx", "Site Register", _EN_SECTIONS, _WIDE_TABLE,
                     table_numbers, variant="widetable"),
        FidelityCase("en-pdf-widetable", "en", "pdf", "Site Register", _EN_SECTIONS, _WIDE_TABLE,
                     table_numbers, variant="widetable"),
        FidelityCase("en-ocr-degraded", "en", "ocr", _EN_TITLE, _EN_SECTIONS, None,
                     ("1,234,567.89", "4,250", "2026-09-30", "3.75"), variant="degraded"),
        FidelityCase("en-ocr-table", "en", "ocr", _EN_TITLE, _EN_SECTIONS, _EN_TABLE,
                     ("1,234,567.89", "4,250", "2026-09-30", "3.75", "1,250", "18,400.75", "980", "12,035.10"),
                     variant="table"),
        FidelityCase("ar-ocr-table", "ar", "ocr", _AR_TITLE, _AR_SECTIONS, _AR_TABLE,
                     ("١٢٣٤٥٦٧", "٤٢٥٠", "2026-09-30", "١٢٥٠", "١٨٤٠٠", "٩٨٠", "١٢٠٣٥"),
                     variant="table"),
    ]


# ── building ───────────────────────────────────────────────────────────────

def _model(case: FidelityCase):
    from .content_model import BodyBlock, ContentModel, HeadingBlock, TableBlock, TitleBlock
    from .profile import DocProfile

    model = ContentModel()
    model.add(TitleBlock(text=case.title, level=0))
    for heading, paragraphs in case.sections:
        model.add(HeadingBlock(text=heading, level=1))
        for paragraph in paragraphs:
            model.add(BodyBlock(text=paragraph))
    if case.table is not None:
        headers, rows = case.table
        model.add(TableBlock(headers=list(headers), rows=[list(r) for r in rows]))
    sample = "\n".join(case.segments())
    language = {"en": "en", "ar": "ar"}.get(case.language)
    return model, DocProfile.for_content(sample, language=language)


def _build_columns(case: FidelityCase, path: Path) -> Path:
    """Two columns laid out by MuPDF's Story engine (not Kazma's)."""
    import html

    import pymupdf

    body = [f"<h1>{html.escape(case.title)}</h1>"]
    for heading, paragraphs in case.sections:
        body.append(f"<h2>{html.escape(heading)}</h2>")
        body.extend(f"<p>{html.escape(p)}</p>" for p in paragraphs)
    story = pymupdf.Story(html="".join(body), user_css="body {font-family: sans-serif; font-size: 10pt;}")
    page_rect = pymupdf.paper_rect("a4")
    # Short columns, so the text flows from the first into the second.
    columns = (pymupdf.Rect(50, 60, 287, 380), pymupdf.Rect(308, 60, 545, 380))
    writer = pymupdf.DocumentWriter(str(path))
    more = True
    while more:
        device = writer.begin_page(page_rect)
        for column in columns:
            more, _filled = story.place(column)
            story.draw(device)
            if not more:
                break
        writer.end_page()
    writer.close()
    return path


def _degrade(png: Path, out: Path) -> Path:
    """The page as a poor scan, deterministically."""
    import io
    import random

    from PIL import Image, ImageFilter

    rng = random.Random(20261013)
    with Image.open(png) as source:
        image = source.convert("L")
    image = image.rotate(1.2, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255)
    width, height = image.size
    image = image.resize((width * 2 // 3, height * 2 // 3), Image.Resampling.BILINEAR)
    image = image.filter(ImageFilter.GaussianBlur(0.7))
    pixels = image.load()
    w, h = image.size
    for _ in range(w * h // 300):
        pixels[rng.randrange(w), rng.randrange(h)] = rng.choice((0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=55)
    buffer.seek(0)
    with Image.open(buffer) as jpeg:
        jpeg.save(out, dpi=(200, 200))
    return out


def build_case(case: FidelityCase, out_dir: Path) -> Path:
    """Render the case with Kazma's engines (or, for ``columns``, MuPDF's
    Story engine); returns the document to parse."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if case.variant == "columns":
        return _build_columns(case, out_dir / f"{case.case_id}.pdf")
    model, profile = _model(case)
    if case.fmt == "docx":
        from .engines.docx import DocxEngine

        path = out_dir / f"{case.case_id}.docx"
        DocxEngine(profile).render(model, path)
        return path
    if case.fmt == "html":
        from .engines.html import HtmlEngine

        path = out_dir / f"{case.case_id}.html"
        path.write_text(HtmlEngine(profile).render(model), encoding="utf-8")
        return path
    from .engines.pdf import PdfEngine

    pdf = out_dir / f"{case.case_id}.pdf"
    PdfEngine(profile, []).render(model, pdf)
    if case.fmt == "pdf":
        return pdf
    import pymupdf

    png = out_dir / f"{case.case_id}.png"
    with pymupdf.open(pdf) as doc:
        doc[0].get_pixmap(dpi=300).save(png)
    if case.variant == "degraded":
        return _degrade(png, out_dir / f"{case.case_id}-scan.png")
    return png


# ── measuring ──────────────────────────────────────────────────────────────

_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")
_WORD = re.compile(r"[\w]+", re.UNICODE)


def _norm(text: str) -> str:
    from .arabic import fold_for_search, strip_bidi_controls

    text = unicodedata.normalize("NFKC", strip_bidi_controls(text or "")).translate(_INDIC)
    return fold_for_search(text).casefold()


def _tokens(text: str) -> list[str]:
    return _WORD.findall(_norm(text))


def _lis(values: list[int]) -> int:
    import bisect

    tails: list[int] = []
    for value in values:
        index = bisect.bisect_left(tails, value)
        if index == len(tails):
            tails.append(value)
        else:
            tails[index] = value
    return len(tails)


def _edit_distance(a: list[str] | str, b: list[str] | str) -> int:
    previous = list(range(len(b) + 1))
    for i, item in enumerate(a, 1):
        current = [i] + [0] * len(b)
        for j, other in enumerate(b, 1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (item != other))
        previous = current
    return previous[-1]


def _ir_text(ir: Any) -> tuple[str, str]:
    """(all text in reading order, text of TABLE blocks)."""
    body, tables = [], []
    for page in ir.pages:
        for block in page.blocks:
            body.append(block.text or "")
            if str(getattr(block.block_type, "value", block.block_type)) == "table":
                tables.append(block.text or "")
    return "\n".join(body), "\n".join(tables)


def _page_honesty(case: FidelityCase, ir: Any, source: Path) -> float:
    if case.fmt == "pdf":
        import pymupdf

        with pymupdf.open(source) as doc:
            physical = doc.page_count
        numbers = [page.page_number for page in ir.pages]
        return 1.0 if numbers == list(range(1, physical + 1)) else 0.0
    if case.fmt == "ocr":
        return 1.0 if [page.page_number for page in ir.pages] == [1] else 0.0
    kinds = {str((page.metadata or {}).get("kind", "")) for page in ir.pages}
    claims_physical = any(kind in {"", "physical"} for kind in kinds)
    return 0.0 if claims_physical else 1.0


def _alignment(case: FidelityCase, source: Path) -> float | None:
    if case.fmt != "pdf" or case.variant == "columns":
        return None  # a column's side is not its language's
    import pymupdf

    from .arabic import direction_of
    from .parsers.pdf_rtl import logical_line, page_glyph_lines

    # Every visual line that shows its side -- shorter than the measure and
    # not centred -- must be on the side of the segment it belongs to, by
    # Kazma's per-block rule (``arabic.direction_of``, what the engines lay
    # blocks out by): "Bilingual Status Report تقرير" is a left-to-right
    # title. Lines that fill the measure show nothing; tables, the
    # header/footer bands (page furniture placed by design) and lines of no
    # segment are not judged.
    segments = [(set(_tokens(s)), direction_of(s) == "rtl") for s in case.segments()]
    judged = correct = 0
    with pymupdf.open(source) as doc:
        for page in doc:
            try:
                tables = [pymupdf.Rect(t.bbox) for t in page.find_tables().tables]
            except (RuntimeError, ValueError):
                tables = []
            band = 0.07 * page.rect.height
            lines = []
            for line in page_glyph_lines(page):
                kept = [g for g in line
                        if not any(t.contains(pymupdf.Point(g.cx, g.y)) for t in tables)
                        and band < g.y < page.rect.height - band]
                words = set(_tokens(logical_line(kept))) if kept else set()
                if words:
                    lines.append((kept[0].x0, kept[-1].x1, words))
            if not lines:
                continue
            left, right = min(x0 for x0, _x1, _w in lines), max(x1 for _x0, x1, _w in lines)
            tolerance = max(6.0, 0.03 * (right - left))
            for x0, x1, words in lines:
                left_gap, right_gap = x0 - left, right - x1
                if abs(left_gap - right_gap) <= tolerance:
                    continue
                share, rtl = max(((len(words & seg) / len(words), is_rtl) for seg, is_rtl in segments),
                                 key=lambda item: item[0], default=(0.0, False))
                if share < 0.6:
                    continue  # no segment of the case: furniture or a table row
                judged += 1
                if (rtl and right_gap < left_gap) or (not rtl and left_gap < right_gap):
                    correct += 1
    return round(correct / judged, 4) if judged else 0.0


def _drawn_furniture(case: FidelityCase, source: Path) -> list[str]:
    """The page chrome (brand, short brand, page number) Kazma's renderer drew
    on the OCR'd page, as its rendered PDF's own text layer shows it.

    A line counts only when it IS one of the chrome strings: text the page
    shows, so part of the OCR reference (version 4)."""
    pdf = source.parent / f"{case.case_id}.pdf"
    if case.fmt != "ocr" or not pdf.is_file():
        return []
    import pymupdf

    from .arabic import fold_for_search
    from .parsers.pdf_rtl import logical_line, page_glyph_lines
    from .style_theme import format_page_number, localized_chrome

    with pymupdf.open(pdf) as doc:
        shown = {" ".join(fold_for_search(logical_line(line)).split()) for line in page_glyph_lines(doc[0])}
    chrome = localized_chrome(rtl=case.language == "ar")
    candidates = [chrome["brand"], chrome["brand_short"]] + [
        chrome["page_fmt"].format(n=format_page_number(1, numerals=numerals)) for numerals in ("latn", "arab")
    ]
    return [text for text in dict.fromkeys(candidates) if " ".join(fold_for_search(text).split()) in shown]


def measure_case(case: FidelityCase, ir: Any, source: Path) -> dict[str, Any]:
    text, table_text = _ir_text(ir)
    doc_tokens = _tokens(text)
    recalls, positions = [], []
    for segment in case.segments():
        seg_tokens = _tokens(segment)
        if not seg_tokens:
            continue
        matcher = difflib.SequenceMatcher(None, doc_tokens, seg_tokens, autojunk=False)
        blocks = [b for b in matcher.get_matching_blocks() if b.size]
        matched = sum(b.size for b in blocks)
        recall = matched / len(seg_tokens)
        recalls.append(recall)
        span = blocks[-1].a + blocks[-1].size - blocks[0].a if blocks else 0
        if recall >= 0.95 and blocks and span <= len(seg_tokens) + _SPAN_SLACK:
            positions.append(blocks[0].a)
    flat = re.sub(r"\s+", "", _norm(text))
    numbers = [n for n in case.numbers if re.sub(r"\s+", "", _norm(n)) in flat]
    result: dict[str, Any] = {
        "token_recall": round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
        "segment_recall": round(len(positions) / len(recalls), 4) if recalls else 0.0,
        "order": round(_lis(positions) / len(positions), 4) if positions else 0.0,
        "number_recall": round(len(numbers) / len(case.numbers), 4) if case.numbers else None,
        "page_honesty": _page_honesty(case, ir, source),
    }
    if case.table is not None:
        cells = [c for row in (case.table[0], *case.table[1]) for c in row]
        table_tokens = " ".join(_tokens(table_text))
        found = sum(1 for c in cells if " ".join(_tokens(c)) and " ".join(_tokens(c)) in table_tokens)
        result["table_cell_recall"] = round(found / len(cells), 4)
    alignment = _alignment(case, source)
    if alignment is not None:
        result["alignment"] = alignment
    if case.fmt == "ocr":
        page = list(case.segments())
        if case.table is not None:
            page += [" ".join(row) for row in (case.table[0], *case.table[1])]
        page += _drawn_furniture(case, source)
        truth = " ".join(_tokens("\n".join(page)))
        got = " ".join(doc_tokens)
        result["cer"] = round(_edit_distance(got, truth) / max(1, len(truth)), 4)
        result["wer"] = round(_edit_distance(got.split(), truth.split()) / max(1, len(truth.split())), 4)
    return result


#: The OCR engines the ``*-ocr`` cases are measured with, each against its
#: own floors (``documents.ocr.engine``; ``ocr/pipeline.select_ocr_provider``).
OCR_ENGINES: tuple[str, ...] = ("tesseract", "neural")


def is_ocr_case(case_id: str) -> bool:
    return case_id.split("-")[1] == "ocr"


def engine_available(engine: str, languages: tuple[str, ...] = ("ara", "eng")) -> str | None:
    """None when ``engine`` reads ``languages`` here; else why not."""
    from .ocr.pipeline import get_ocr_health

    health = get_ocr_health(languages, engine=engine)
    if health.selected_engine != engine:
        reports = dict(health.engines)
        report = reports.get(engine)
        return (report.health.reason if report else None) or f"the {engine} OCR engine is not installed"
    if str(getattr(health.readiness, "value", health.readiness)) == "unavailable":
        return health.reason or f"the {engine} OCR engine is not ready"
    return None


def run_corpus(
    work_dir: Path, *, service: Any = None, cases: list[FidelityCase] | None = None,
    ocr_engine: str | None = None,
) -> dict[str, Any]:
    """Build, parse and measure every case; the report names what could not run.

    ``ocr_engine`` reads the OCR cases with that engine (the service's live
    configuration otherwise); each OCR case's report names the engine that
    actually read it (``ocr_engine``), so a fallback is never mistaken for it."""
    from dataclasses import replace as replace_config

    from .service import DocumentService

    if service is None:
        service = DocumentService()
        if ocr_engine is not None:
            service = DocumentService(config=replace_config(service.config, ocr_engine=ocr_engine))
    results: dict[str, Any] = {}
    for case in cases or corpus():
        try:
            source = build_case(case, Path(work_dir) / "corpus")
            ir = service.parse_ingested_blob(source, force_ocr=case.fmt == "ocr",
                                             ocr_language="ara" if case.language == "ar" else "eng")
            results[case.case_id] = {"metrics": measure_case(case, ir, source), "source": source.name}
            if case.fmt == "ocr":
                results[case.case_id]["ocr_engine"] = (ir.metadata.get("ocr") or {}).get("engine")
        except (OSError, RuntimeError, ValueError, ImportError) as exc:  # one case's failure is reported, never hidden
            results[case.case_id] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    return {"corpus_version": CORPUS_VERSION, "cases": results}


def meets(metrics: dict[str, Any], bounds: dict[str, float]) -> list[str]:
    """Every bound the metrics miss (empty when all are met)."""
    misses = []
    for name, bound in bounds.items():
        value = metrics.get(name)
        if value is None:
            continue  # this case does not measure it
        if name in ERROR_RATES and value > bound:
            misses.append(f"{name}: {value} > {bound}")
        elif name not in ERROR_RATES and value < bound:
            misses.append(f"{name}: {value} < {bound}")
    return misses


def _lock_cases(
    cases: dict[str, Any], old_floors: dict[str, Any] | None, *, label: str = "",
) -> tuple[dict[str, dict[str, float]], list[str]]:
    """Floors and the qualified list for one set of measured cases; a floor
    is never lowered against ``old_floors`` (the same corpus version)."""
    floors: dict[str, dict[str, float]] = {}
    for case_id, entry in sorted(cases.items()):
        metrics = {k: v for k, v in entry["metrics"].items() if v is not None}
        ocr = is_ocr_case(case_id)
        floor = {}
        for name, value in metrics.items():
            margin = _OCR_MARGIN if ocr and name in _RECOGNITION else 0.0
            floor[name] = round(min(1.0, value + margin) if name in ERROR_RATES else max(0.0, value - margin), 4)
        if old_floors is not None:
            old = old_floors.get(case_id, {})
            for name, value in floor.items():
                before = old.get(name)
                if before is None:
                    continue
                worse = value > before if name in ERROR_RATES else value < before
                if worse:
                    raise ValueError(f"{label}{case_id}.{name} would loosen {before} -> {value}; fix the regression")
        floors[case_id] = floor
    qualified = sorted(case_id for case_id, entry in cases.items() if not meets(entry["metrics"], RELEASE_TARGETS))
    return floors, qualified


def lock_thresholds(report: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    """Regression floors from a measurement; never lowered within a corpus version.

    ``report["cases"]`` holds the cases that read no page image; the OCR
    cases are under ``report["ocr_engines"][engine]["cases"]``, one set per
    engine measured. An engine not measured in this run keeps its floors
    (same corpus version) -- a machine without the neural engine cannot
    erase its floors. Raises when a case could not be measured, when an OCR
    case was read by another engine than the one it is filed under (a
    fallback), or when a floor would drop while the corpus version is
    unchanged (a regression is fixed, not locked in). ``qualified`` names the
    cases that meet ``RELEASE_TARGETS``.
    """
    sections = report.get("ocr_engines") or {}
    every = dict(report["cases"])
    for engine, section in sections.items():
        every.update({f"{engine}:{case}": entry for case, entry in section["cases"].items()})
    errors = {case: entry["error"] for case, entry in every.items() if "error" in entry}
    if errors:
        raise ValueError(f"cases could not be measured: {errors}")
    stray = sorted(case for case in report["cases"] if is_ocr_case(case))
    if stray:
        raise ValueError(f"OCR cases must be measured per engine (ocr_engines), not at the top: {stray}")
    for engine, section in sections.items():
        read_by = {case: entry.get("ocr_engine") for case, entry in section["cases"].items()}
        wrong = {case: used for case, used in read_by.items() if used != engine}
        if wrong:
            raise ValueError(f"{engine}: cases read by another engine (a fallback): {wrong}")
    same_version = bool(previous) and previous.get("corpus_version") == report["corpus_version"]
    kept = previous or {}
    floors, qualified = _lock_cases(report["cases"], kept.get("thresholds") if same_version else None)
    locked: dict[str, Any] = {
        "corpus_version": report["corpus_version"],
        "release_targets": RELEASE_TARGETS,
        "qualified": qualified,
        "thresholds": floors,
        # What the file says by hand -- why a case is not qualified, the
        # machine it was measured on -- is kept: a re-lock used to drop it.
        "notes": {case: note for case, note in (kept.get("notes") or {}).items() if case in floors},
        "ocr_engines": {},
    }
    if kept.get("measured_on"):
        locked["measured_on"] = kept["measured_on"]
    old_engines = kept.get("ocr_engines") or {}
    for engine in OCR_ENGINES:
        old = old_engines.get(engine) or {}
        section = sections.get(engine)
        if section is None:
            if same_version and old:
                locked["ocr_engines"][engine] = old
            continue
        engine_floors, engine_qualified = _lock_cases(
            section["cases"], old.get("thresholds") if same_version else None, label=f"{engine}: ")
        # Version 3 kept Tesseract's OCR notes at the top: they carry over once.
        notes = old.get("notes") or ({
            case: note for case, note in (kept.get("notes") or {}).items() if is_ocr_case(case)
        } if engine == "tesseract" else {})
        entry: dict[str, Any] = {
            "qualified": engine_qualified,
            "thresholds": engine_floors,
            "notes": {case: note for case, note in notes.items() if case in engine_floors},
        }
        if old.get("measured_on"):
            entry["measured_on"] = old["measured_on"]
        locked["ocr_engines"][engine] = entry
    return locked
