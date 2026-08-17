---
id: document-rendering
title: Unified Document Rendering
sidebar_label: Document Rendering
description: The unified rendering layer — one DocProfile drives DOCX, PDF, HTML, Markdown, XLSX, and PPTX so every file Kazma emits shares one design and correct RTL.
---

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

- `direction` (`ltr`/`rtl`), auto-detected from content via `is_arabic_dominant`
  (full Unicode Arabic blocks), honouring explicit `lang`/`rtl` overrides.
- `theme` — colours, fonts, sizes, page size (A4), shared from `style_theme.THEME`.
  Brand colours (royal `#3b82f6`, navy ink `#16223a`). Arabic uses
  `font_arabic` (Sakkal Majalla) and a larger body size / leading.
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
| `docx.py` | `w:bidi` + `w:jc` from `profile.docx_jc`; complex-script `w:szCs`/`w:bCs`; `w:rtl` on Arabic runs only; `w:outlineLvl` for the TOC field. |
| `pdf.py` | reportlab (LTR) for English; **Arabic routes DOCX→LibreOffice→PDF** (reportlab can't shape mixed Arabic+Latin+Markdown). |
| `html.py` | `dir` attribute + CSS `unicode-bidi`; `<bdi dir="ltr">` isolators for embedded Latin. |
| `xlsx.py` | `sheet_view.rightToLeft` + complex-script `readingOrder`. |
| `pptx.py` | `<a:pPr rtl="1">` **and** `algn="r"` (rtl alone does not right-align; the DrawingML attr is `algn`, not `al`). |

## 3. Direction correctness — the hard-won details

These are the non-obvious facts each engine encodes once:

- **DOCX** — bold/size for Arabic must set the *complex-script* variants
  (`w:bCs`/`w:szCs`); python-docx's `run.bold`/`run.font.size` only write the
  Latin `w:b`/`w:sz`, so Arabic silently falls back to the default size with
  faux bold — the heading-bar "junk letters" symptom. `DocxEngine._mark_run`
  writes them. **Do not copy `w:sz` onto `w:szCs`:** Sakkal Majalla reads
  smaller than Calibri at the same nominal pt, and body runs often have no
  per-run `w:sz` (they inherit Normal). Latin stays at `body_size`; Arabic
  uses `theme_cs_size()` → `body_size_ar` on `w:szCs` (style + every RTL run).
- **PDF (Arabic)** — reportlab is a visual LTR engine and cannot correctly shape
  mixed Arabic+Latin+inline-Markdown (tokens jam, Latin splits across lines).
  `PdfEngine` routes RTL content through the DOCX engine (correct bidi) then
  LibreOffice headless → PDF. LTR stays on reportlab directly.
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

**Faithful conversions are intentional bypasses** (do not "unify" them):
LibreOffice Office→PDF and `mutation_worker` redaction preserve the source's
formatting by design — re-theming an uploaded file would be a regression.

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

> **Approved assets only.** Image embedding resolves assets that have passed
> intake approval; arbitrary inline image bytes are not rendered. This keeps the
> generate/convert path consistent with the Document Intelligence security model
> (see [Document security](../security/document-security)).

Verification extends to these features — re-run `scripts/verify_documents.py`
after touching the engines.
