# Arabic + Documents Hardening — Execution Record

Derived from the deep audit of `kazma-core/kazma_core/documents/` (19 findings).
**Status: landed.** Every S1 and S2 finding is closed; the S3 items that were
cheap to close alongside them were closed too. What remains open is listed at
the bottom.

## Design decisions (industrial default chosen at each fork)

| Fork | Chosen | Rejected, and why |
|------|--------|-------------------|
| Fix reportlab shaping vs. refuse on RTL | **Fix it** — paragraph-level shape with UBA rule L2 over styled segments | Refusing would break every pip/Linux deploy that has no LibreOffice |
| Codepoint block regex vs. Unicode bidi class | **`unicodedata.bidirectional ∈ {R, AL}`** | The block regex missed Hebrew/Syriac/Thaana/N'Ko and Arabic Ext-B, and counted harakat and Arabic-Indic digits as letters |
| Where search folding lives | **One module, applied at index AND query** | Per-call-site folding drifts, and a fold on one side only is worse than none |
| FTS strategy | **A second `folded` column in the same FTS5 table**, rebuilt once per DB | A custom tokenizer needs a compiled SQLite extension; a separate table doubles the write path |
| Remote parse egress | **Opt-in via ConfigStore, audited before the bytes move** | An env-var kill switch that defaults to on is not a policy |
| Font selection | **Coverage-verified ranking, pinned bundle dir first, hard refusal on RTL without coverage** | First-file-that-exists is not deterministic and produced blank Arabic PDFs |
| Numerals / calendar | **`DocProfile` fields defaulted from language, consumed by chrome** | Hard-coding Gregorian + ASCII digits in an Arabic-first product |
| Ligature support during shaping | **Keep ligatures on**, segment at bidi level boundaries | Disabling ligatures for a 1:1 index map renders لا wrong |

## What landed

| # | Change | Closes |
|---|--------|--------|
| 1 | `documents/arabic.py` — `direction_of`, `has_rtl`, `rtl_ratio`, `is_rtl_dominant`, `to_logical`, `fold_for_search`, `to_arabic_numerals`, `shape_spans`, `shape_text` | S1-1, S1-4, S2-6, S2-10, S3 |
| 2 | `documents/fonts.py` — cmap-verified resolution, pinned-bundle-first, `embedded_font_face_css` | S2-8, S3 (font embedding) |
| 3 | `profile.py` — bidi-class direction, `text[:200]` hatch removed, BCP-47 language, `numerals`/`calendar`, `shape_arabic` on **any** RTL char | S2-6, S1-2 |
| 4 | `rich_render.py` — `_inline_spans` / `_wrap_span` / `_render_shaped_lines`; one shaping pass per paragraph, line breaking on logical text | S1-1, S2-9 |
| 5 | `engines/pdf.py` — DOCX route gated on `shape_arabic`; `DocumentRenderError` instead of a blank Arabic PDF; page numbers honour the digit set | S1-2, S1-3 |
| 6 | `parsers/common.py` — `to_logical()` before limits, quality and chunking | S2-10 |
| 7 | `stores/knowledge.py` — `folded` FTS column, one-time rebuild migration, folded queries | S1-4 |
| 8 | `config.py` + `extract_salvage.py` + `ingestion.py` — `documents.security.remote_parse` (off) / `local_salvage` (on), audit hook | S1-5 |
| 9 | `agent/pipelines/document.py` — language threading, real XLSX table extraction, anchored failure test | S2-11, S2-12 |
| 10 | `style_theme.py` — `format_page_number`, `format_document_date` (Hijri via `cultural_context`) | S3 |
| 11 | `templates/documents.html` — `dir="auto"` on user-supplied strings | S3 |
| 12 | `Dockerfile` + `.github/workflows/ci.yml` — fonts, LibreOffice, Tesseract(+ara), `document-platform` | S1-3, S2-7 |
| 13 | `tests/test_arabic_text.py` — 66 assertions, one group per finding | S2-7 |
| 14 | `AGENTS.md` §19H/§19I + `CHANGELOG.md` | — |
| 15 | `renderer_worker.py` — direction sample now covers tables, subtitle, header and footer | found by the smoke test |

## Verification

- `tests/test_arabic_text.py` — 66 passed, including a round-trip that renders
  an Arabic PDF, re-extracts it and asserts it scores above `SALVAGE_SCORE`
  after repair — verified on BOTH the DOCX/LibreOffice route and the
  reportlab fallback (93.6% presentation forms repaired to 0.0, score 0.784).
- `tests/test_docx_rtl_visual.py` — 11 passed **executed**, not skipped (the
  pixel-measuring tests that "cannot lie"; they now also run in CI).
- Shaping verified byte-identical to `get_display(reshape(text))` across mixed
  Arabic/Latin/numbers/URLs, with markup landing in the correct visual place.
- Arabic search verified end-to-end through `KnowledgeStore`: vocalized text,
  alef-hamza, tatweel, taa-marbuta and both digit directions all match.
- End-to-end render smoke across four profiles (Arabic; Arabic with Hijri +
  Arabic-Indic numerals; English-with-Arabic; pure English) — PDF/DOCX/HTML
  produced with no warnings, and 287 Arabic characters extractable from the
  Arabic PDF (i.e. real text, not tofu).

## Round two — the open items

| # | Item | Outcome |
|---|------|---------|
| 1 | Vendor an OFL Arabic face | **Done.** Amiri 1.003 (`Amiri-Regular.ttf`, `Amiri-Bold.ttf`, `OFL.txt`) vendored from the upstream release. Precedence made direction-aware so it does not restyle Latin. HTML exports inline it behind `documents.render.embed_html_fonts`. |
| 4 | Consolidate the document tool surface | **Done, as a routing fix.** The three overlapping tools (`read_document`, `convert_document`, `pdf_redact`) now open with `TRANSIENT, PATH-BASED` and name their durable counterpart; `document-platform` declares itself preferred. No tool removed — deleting them would break existing flows for a problem that is really one of model routing. |
| 5 | Port GC mark/sweep to Postgres | **Already done before this work.** `retention._mark` dispatches to `repository.gc_mark`, implemented by both backends, and `tests/test_leftovers_except_g.py` asserts the skip sentinel stays gone. The audit reported it as open because AGENTS.md §19D/F and two doc pages still described the pre-port world. Those are corrected, and `TestDocsMatchCode` now fails if they drift again. |
| 2 | DOCX font embedding | **Deliberately not landed.** See below. |
| 3 | Kashida justification | **Deliberately not landed.** See below. |

### Why 2 and 3 are not in this change

**DOCX font embedding** is not really a packaging task. The DOCX Arabic
typeface is `THEME["font_arabic"]` = Sakkal Majalla, and `body_size_ar: 16` /
`line_height_ar: 2.0` exist specifically because Sakkal Majalla reads optically
small. Embedding Amiri means *switching* the typeface, which invalidates that
tuning and changes the look of every Arabic DOCX. That is an aesthetic decision
for whoever owns the brand, not a defect fix — and it cannot be validated here,
because a malformed font part makes Word refuse to open the file and only
LibreOffice is available to test against.

**Kashida justification** needs `uharfbuzz` and a rewrite of the PDF text path
to draw positioned glyph runs instead of strings. That is the Tier 2 item in
the roadmap, measured in weeks, and it is not something to land unreviewed in a
push to `main`.

## Round three — unified styling, formatting and pagination

Prompted by the question "is the styling still unified after all this, and
does a heading ever end a page with its paragraph on the next one?" The answer
to the second was already yes; the answer to the first was no, and had not been
for some time.

Measured on the same Arabic document through both PDF routes:

| | before | after |
|---|---|---|
| reportlab body size | 11.0pt | **16.0pt** |
| DOCX/LibreOffice body size | 16.0pt | 16.0pt |
| pages (reportlab) | 6 | **14** |
| pages (DOCX route) | 13 | 13 |
| left / right margin | 83 / 62 pt | **66 / 57 pt** |
| RTL table column 1 | on the LEFT | **on the right** |

Fixed:

1. **PDF ignored `theme_cs_size()`** — the one engine that did. Arabic was set
   at the Latin 11pt, which is why the same content paginated to 6 pages one
   way and 13 the other. Also picks `line_height_ar` now.
2. **PDF tables had no `FONTSIZE`**, defaulting to ReportLab's 10pt. Now
   `theme_cs_size(10)`, the same call HTML makes.
3. **RTL tables were not column-reversed** in PDF. ReportLab has no bidi table
   model, so the reversal is done on the data to match `w:bidiVisual`.
4. **The 20pt wrap fudge** became 4pt — it was compensating for measuring
   logical text, and `_visual_width` measures the shaped line now.
5. **Widow/orphan control** added to PDF body styles and HTML print CSS.

Verified unchanged: heading keep-with-next was already correct in both engines
(0 stranded headings across EN and AR, both routes, on a 9–14 page document
with staggered section lengths), and English type scale is untouched.

## Still open

1. **DOCX font embedding** — blocked on the typeface decision above.
2. **Kashida justification** — needs the `uharfbuzz` layout rewrite (Tier 2).
3. **Charts / bar graphs are not a supported block type.** `ContentModel` has
   Title, Heading, Body, Table, Image, TOC and Citation. A chart would need a
   new block plus a renderer per engine — say so rather than implying tables
   cover it.
