---
id: document-rendering
title: Unified Document Rendering
sidebar_label: Document Rendering
description: The unified rendering layer — one DocProfile drives DOCX, PDF, HTML, Markdown, XLSX, and PPTX so every file Kazma emits shares one design and correct RTL.
---

> This page is published at [kazma.ai/docs/document-rendering/](https://kazma.ai/docs/document-rendering/) ([Arabic](https://kazma.ai/ar/docs/document-rendering/)).

> Source-referenced guide to Kazma's unified document-rendering layer. Every output format flows through one `DocProfile`, so a generated DOCX, PDF, HTML, Markdown, XLSX, and PPTX share one design and correct right-to-left handling — regardless of extension or language.

---

## 1. Why a unified layer

Before this layer, each format had its own renderer with its own theme, its own
direction detection, and its own RTL handling. That produced two classes of bug:

- **Drift** — colours, fonts, and labels diverged across formats; an Arabic
  DOCX and an Arabic PDF looked like different products.
- **Repeated RTL mistakes** — the same bidi/alignment error was fixed five times
  (once per format) and kept coming back.

The unified layer puts **direction + design + alignment semantics in one place**
(`DocProfile`) and makes every format engine a thin projection of it. A bug in
Arabic alignment is now fixed in one method, not five.

## 2. The three layers

```
payload ──► _build_model_and_profile ──► (ContentModel, DocProfile) ──► Engine ──► file
```

### Layer 1 — `DocProfile` (`kazma_core/documents/profile.py`)

The single source of truth for a document's **direction + design + chrome**.

- `direction` (`ltr`/`rtl`) — the *document* base direction (margins, section,
  chrome). Resolution order: an explicit `rtl` wins, then `lang` (an Arabic,
  Persian, Urdu, Hebrew… code pins RTL; any other code pins LTR), then the
  content: `arabic.direction_of` decides by Unicode **bidi class** (`R`/`AL`),
  first strong character checked against the RTL share of the text — never by a
  codepoint-block regex. All text-direction policy lives in
  `kazma_core/documents/arabic.py`.
- `shape_arabic` — the content holds *any* right-to-left character, so a visual
  engine must shape it. A different question from `direction`: a mostly-English
  document quoting Arabic is LTR **and** needs shaping.
- `block_direction(text)` — the direction of one block. A document need not be
  one language; every engine resolves each block on its own, and a block with no
  strong character (a rule, a bare number, a date) inherits the document
  direction.
- `theme` — colours, fonts, sizes, page size (A4), shared from `style_theme.THEME`.
  Brand colours (royal `#3b82f6`, navy ink `#16223a`). Latin and Arabic both
  use IBM Plex Sans Arabic (`font_latin` / `font_arabic`); body is 11pt in
  both directions (`theme_cs_size` is 1:1 by default), leading 1.65 both,
  1.8 cm margins in DOCX, 56 pt (about 2 cm) in the reportlab PDF path. IBM Plex
  Sans Arabic and Amiri ship in `kazma_core/documents/assets/fonts/`
  (`KAZMA_DOCUMENT_FONT_DIR` overrides); Amiri is a naskh fallback when Plex
  is absent. PPTX/XLSX do not hard-code Calibri.
- `chrome` — localized labels (`المحتويات` / `Contents`, brand string) via `localized_chrome`.
- **alignment policy** — the critical piece. `docx_jc(intent)` / `pdf_align(intent)` /
  `html_text_align(intent)` map an *intent* (`start` / `justify` / `end`) to each
  engine's native value. **The Word bidi `w:jc` inversion lives here, once.**

> **The bidi rule (do not re-derive):** under `w:bidi`, Word maps `w:jc="right"`
> to the *logical end* = the **physical LEFT**. To pin Arabic to the reading-start
> edge (physical right), use `w:jc="start"` (or `"left"`) — never `"right"`. The
> profile's `docx_jc("start")` returns `"start"`; no call site picks a raw
> `RIGHT`/`LEFT` again.

### Layer 2 — `ContentModel` (`kazma_core/documents/content_model.py`)

Format-agnostic blocks: `Title`, `Heading`, `Body` (Markdown), `Table`, `TOC`,
`Citation`, plus header/footer/metadata. Every input path produces this tree;
every *document-shaped* engine consumes it. (XLSX/PPTX keep their own
sheets/slides payloads — see below — but still consume a `DocProfile`.)

### Layer 3 — engines (`kazma_core/documents/engines/`)

One per format. Each implements `render(...)` and owns its format-specific
mechanics, reading direction/theme/alignment from the profile:

| Engine | RTL mechanism |
|---|---|
| `docx.py` | `w:bidi` per paragraph from `profile.block_direction` (an LTR paragraph is stamped `w:bidi val="0"`) + `w:jc` from `profile.docx_jc`; complex-script `w:szCs`, and a `w:bCs`/`w:iCs` twin for every `w:b`/`w:i` (Arabic bold and italic are drawn by the twin); `w:rtl` on Arabic runs only; in a right-to-left block, each left-to-right run holding a Latin letter is marked with LRM on both sides (a run of digits alone keeps the Arabic convention); direction marks drawn in a face that has their glyph; `w:bidiVisual` on tables whose own language is right-to-left; heading bars on the heading's own reading side; every bullet level "•" in the house face; `w:keepNext` on headings; `w:outlineLvl` for the TOC field. |
| `pdf.py` | Any document that needs shaping (`profile.shape_arabic`) routes **DOCX→LibreOffice→PDF**; everything else, and the fallback when LibreOffice is absent or fails, is reportlab with per-block alignment and RTL table columns reversed. |
| `html.py` | `dir` attribute + CSS `unicode-bidi`; `dir` on each table from `profile.table_direction`; `text-align-last: start`, so each block ends its paragraphs on its own side; a `dir` wrapper on the blocks whose direction differs from the document's; `<bdi dir="ltr">` isolators for embedded Latin; the pinned font inlined for Arabic exports (`documents.render.embed_html_fonts`). |
| `xlsx.py` | `sheet_view.rightToLeft` + complex-script `readingOrder`. |
| `pptx.py` | `<a:pPr rtl="1">` **and** `algn="r"` (rtl alone does not right-align; the DrawingML attr is `algn`, not `al`). |

## 3. Direction correctness — the hard-won details

These are the non-obvious facts each engine encodes once:

- **DOCX** — bold/italic/size for Arabic must set the *complex-script*
  variants (`w:bCs`/`w:iCs`/`w:szCs`); the engine adds the twin to every bold
  and italic mark at the end of rendering, so the Arabic half of a bilingual
  title and bold lead-ins in an Arabic list are bold. python-docx's `run.bold`/`run.font.size` only write the
  Latin `w:b`/`w:sz`, so Arabic silently falls back to the default size with
  faux bold — the heading-bar "junk letters" symptom. `DocxEngine._mark_run`
  writes them. **Do not copy `w:sz` onto `w:szCs`:** an older Arabic face
  (Sakkal Majalla) read smaller than Calibri at the same nominal pt, and body
  runs often have no per-run `w:sz` (they inherit Normal). IBM Plex Sans
  Arabic matches the Latin optical size (`body_size_ar` equals `body_size`,
  11pt). Latin stays at `body_size`; Arabic uses `theme_cs_size()` →
  `body_size_ar` on `w:szCs` (style + every RTL run). Raise `body_size_ar`
  to opt into a complex-script bump.
- **PDF (Arabic)** — reportlab is a visual LTR engine and cannot correctly shape
  mixed Arabic+Latin+inline-Markdown (tokens jam, Latin splits across lines).
  `PdfEngine` routes every document containing right-to-left script
  (`profile.shape_arabic`, not only RTL-dominant ones) through the DOCX engine
  (correct bidi) then LibreOffice headless → PDF, in a bounded call that kills
  the process tree on timeout. Each conversion first copies the bundled faces
  into LibreOffice's own profile (`fonts.give_soffice_the_bundled_fonts`):
  LibreOffice uses only system and profile fonts, and a host or worker image
  without IBM Plex rendered DejaVu Sans and Noto Serif. Without LibreOffice, or when it fails, the
  reportlab path is used with a warning; it shapes each paragraph once
  (`arabic.shape_spans`) and refuses to render (`arabic_font_unavailable`)
  rather than emit a blank Arabic PDF when no font covers the Arabic
  presentation forms. Documents without RTL script stay on reportlab directly.
- **A DOCX carries its fonts** — a Word file names its fonts and does not
  carry them, so every DOCX the engine writes embeds the bundled IBM Plex Sans
  Arabic, regular and bold, as Word embeds fonts (obfuscated font parts and
  `w:embedTrueTypeFonts`, `engines/docx_fonts.py`). Without it, a device with
  no IBM Plex drew the file in another face. It adds about 210 KB a file, compressed.
- **Per block, not per document** — a bilingual document keeps each language's
  blocks aligned to their own reading start in every engine: `w:bidi` per
  paragraph (DOCX), per-block styles (PDF), a `dir` wrapper (HTML).
- **Tables** read in their own language, whatever the document's
  (`profile.table_direction`): an English table in an Arabic document reads
  left to right, an Arabic table in an English one right to left. The
  mechanism is `w:bidiVisual` (DOCX), `dir` on the table (HTML) and reversed
  columns (reportlab PDF). A cell with no letter (a range like "29 – 32")
  takes its table's direction, so it reads as written.
  `tests/test_document_table_direction.py` checks this through real
  LibreOffice.
- **One type scale** — `theme_cs_size()` sizes complex script in DOCX
  (`w:szCs`), HTML and the PDF engine alike, so the same document paginates the
  same way in each. `tests/test_document_layout.py` measures this on rendered
  PDFs.
- **PPTX** — paragraph RTL needs `rtl="1"` **and** an explicit `algn`
  alignment; `rtl` alone leaves text left-aligned (inherits the layout).
- **HTML** — bidi is native (`dir` + the browser algorithm); explicit `<bdi>` is
  only extra polish for URLs/standards.

## 4. The pipeline

All generation and Markdown→X conversion flows through one translator:
`renderer_worker._build_model_and_profile(payload)` → `(ContentModel, DocProfile)`
→ the chosen engine. The skill layer, the service layer, and the HTTP `/generate`
endpoint all reach the engines through this single door, so a design change
applies to every output at once.

**A Markdown source keeps its shape:** a hard line break (two trailing spaces
or a backslash) stays a line break in paragraphs and quotes; a section's
heading level is its `#` count relative to the shallowest heading below the
title; a quote is upright, in muted ink, with a bar on its own reading side,
and stays on one page with the line that introduces it; `---` is drawn as a
rule; a heading that repeats the title is dropped, a bilingual title whose
halves come in the other order included (`tests/test_document_archive_layout.py`,
`tests/test_document_bilingual_title_and_arabic_bold.py`).

**Faithful conversions are intentional bypasses** (do not "unify" them):
LibreOffice Office→PDF and `mutation_worker` redaction preserve the source's
formatting by design.

**HTML→PDF is the exception: it comes out in the house style**
(`renderer_worker._house_style_html`). The file's content (headings,
paragraphs, lists, tables, `dir` marks) goes into the `HtmlEngine` shell, with
the brand face embedded, heading bars and the table style; its own fonts,
colours, presentation attributes and CSS are dropped, and the page breaks it
asked for are kept. The printed page carries the DOCX engine's chrome: the
short brand at the top, and the brand with the page number at the bottom, on
the reading-start side (`HtmlEngine.page_chrome_css`). Paragraphs written as
list items ("▪ …") become a list, bullet and check symbols the face lacks are
drawn as • and ✓, and a Latin run in right-to-left text is marked, so
"50 °C" does not draw "C° 50". The path-based tool
`convert_document(keep_source_style=true)` keeps a file's own design; the
platform's `document_convert`, the Web route and `/docs convert` have no such
option (`tests/test_document_html_house_style.py`).

**Where it runs.** Rendering happens in the isolated renderer worker
(`renderer_worker.py`) — a host subprocess in the `process` profile, a fresh
container of the worker image in the `container` profile — never in the Kazma
process. Rendering never fetches: only `data:` URIs in the source and the
assets copied into the job (checked against their checksums) are read. An HTML
or Markdown source that names an external URL or any other local file is
refused (`external_resource_denied`); an image block naming an asset the job
does not hold is left out. Every PDF the worker produces passes the PDF
active-content policy (`pdf_policy.py`) before it leaves the worker.

## 5. Adding a new format engine

1. Create `kazma_core/documents/engines/<format>.py` with a class that takes a
   `DocProfile` and implements `render(...)`. Read direction/theme from the
   profile (`profile.rtl`, `profile.theme`, the alignment policy) — never
   re-detect direction or hardcode colours.
2. Wire a `_generate_<format>(output, payload)` in `renderer_worker.py` that
   builds a profile (via `_build_model_and_profile` for document shapes, or
   `DocProfile.for_content` for native-shape payloads) and calls the engine.
3. Add the operation to the renderer registry
   (`kazma_core/documents/renderers/__init__.py`) and `_MIME`/`_EXTENSIONS`.
   If it needs a Python package or system binary, add it to the worker image
   too (`deploy/document-worker/requirements.in` /
   `deploy/document-worker/Dockerfile`): in the `container` profile the engines
   that exist are the ones the image reports.
4. Add a test to `tests/test_unified_document_layer.py` asserting the engine
   consumes the profile (direction + a theme token), and extend
   `scripts/verify_documents.py`.

If the format is a document shape (title + sections), reuse `ContentModel`. If
it has its own shape (like sheets/slides), keep that payload but consume the
`DocProfile` — unification is at the profile level, not the content model.

## 6. Verification

`scripts/verify_documents.py` generates Arabic + English samples for all six
formats and checks each for the expected theme + direction signals, including a
**rendered-PDF pixel check** (Arabic on the right, English on the left) that
XML-flag assertions cannot make. Run it before any rendering change:

```bash
python scripts/verify_documents.py
```

The regression tests (`tests/test_unified_document_layer.py`,
`tests/test_docx_rtl_visual.py`) lock the bidi `jc=start` mapping, the
complex-script `szCs/bCs`, the PPTX `algn`, and the cross-format theme tokens.
`tests/test_document_layout.py` measures rendered PDFs with PyMuPDF: the
complex-script type scale, RTL table column order, per-block direction and
alignment, headings kept with their text, and shaped lines inside their
column. `tests/test_document_table_direction.py` (each table in its own
language), `tests/test_document_house_bullets.py` (bullets in the house face)
and `tests/test_document_html_house_style.py` (HTML→PDF in the house style)
render through real LibreOffice. `tests/test_docx_rtl_visual.py` skips without
LibreOffice or PyMuPDF.
The fidelity corpus (`kazma_core/documents/fidelity.py`) also renders English,
Arabic and bilingual DOCX, HTML and PDF with these engines and parses them
back, so a rendering change that breaks extraction shows there.

---

## 7. Rich-content features

On top of the unified profile/direction layer, the engines ship several
rich-content capabilities:

| Feature | Where | Notes |
|---|---|---|
| **Code/syntax highlighting** | `documents/rich_render.py`, `engines/html.py` | Fenced code blocks get syntax-colored output in both **HTML and PDF** render paths. |
| **XLSX charts** | `engines/xlsx.py` | `BarChart` / `LineChart` (+ more types) with **multi-series** support and **axis titles** via `add_chart`. |
| **Image embedding** | `engines/docx.py`, `mutation_worker.py` | **Approved assets** can be embedded inline in DOCX (`add_picture`), HTML, and PDF (`InlineImage`-style) renders. Markdown image references resolve to approved assets. |
| **Real PDF TOC** | PDF engine | Structural table-of-contents generated from heading outline (not just an outline field). |
| **Clickable HTML TOC** | HTML engine | The HTML table-of-contents is hyperlinked — entries jump to their sections. |

> **Approved assets only.** An image reference resolves only to an asset the
> caller approved for this job, copied into the job with its checksum (the
> worker refuses one that changed); a `data:` URI in the source is read as
> embedded data. Nothing is fetched from a URL or another local path: an HTML
> or Markdown source that names one is refused (`external_resource_denied`),
> and an image block whose asset is missing is skipped, never embedded. This
> keeps the generate/convert path consistent
> with the Document Intelligence security model
> (see [Document security](../security/document-security)).

Verification extends to these features — re-run `scripts/verify_documents.py`
after touching the engines.
