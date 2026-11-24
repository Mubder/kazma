<!-- AGENTS.md, piece 19h-documents-rendering. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
**H. Arabic / RTL — one module, one shaping pass, one fold.**
`documents/arabic.py` is the ONLY home for Arabic text policy. Do not
reintroduce a codepoint-block regex, a second shaping routine, or a
call-site-local normalization.

- **Direction** is decided by Unicode **bidi class** (`R`/`AL`), never by a
  block regex — the regex missed Hebrew/Syriac/Thaana/N'Ko and Arabic
  Extended-B, and counted harakat and Arabic-Indic digits as letters.
  `is_rtl_dominant` has **no** "any RTL char near the start" escape hatch; that
  clause made the threshold dead code and flipped English reports to RTL.
- **`shape_arabic` vs `rtl` are different questions.** `rtl` is "lay the page
  out right-to-left"; `shape_arabic` is "this contains complex script, a visual
  engine must shape it". `PdfEngine.render` gates the DOCX→LibreOffice route on
  **`shape_arabic`** — gating it on `rtl` stranded every mixed-language
  document on the degraded reportlab path.
- **Shape once per paragraph.** `arabic.shape_spans()` resolves bidi embedding
  levels over the whole paragraph, splits styled spans at level boundaries,
  applies UBA rule L2, and only then reshapes. Never call the shaper per
  markdown span: that reorders each fragment internally, emits them in logical
  order, and renders the sentence inside-out with the spaces eaten.
- **Search folding is applied on BOTH sides.** `fold_for_search` feeds the
  `folded` column of `knowledge_chunks_fts` at index time AND the query at
  search time (`knowledge.py:fts_search`). A fold on one side only is worse
  than none. SQLite's `unicode61` treats harakat (`Mn`) as separators, so
  vocalized Arabic indexes as single letters without this.
- **PDF lines with right-to-left script are re-read glyph by glyph**
  (`parsers/pdf_rtl.py`, 2026-10-13). PyMuPDF reverses such a line
  character by character, which turned numbers drawn left to right around
  ("١٢٣٤٥٦٧" -> "٧٦٥٤٣٢١") and swapped the letters of ligature glyphs ("في" ->
  "يف"); Arabic table cells came out fully reversed. Glyphs come from
  `page.get_texttrace` (a ligature's extra characters carry glyph -1, in
  logical order); only right-to-left runs are reversed, as whole glyphs. A
  block's base direction comes from its layout when a line shows it (Kazma
  sets RTL paragraphs against the right margin), else `is_rtl_dominant`. An
  RTL table's columns are read right to left. Digits after Arabic letters are
  ARABIC numbers to the bidi algorithm (W2): "-" does not join them (W4) and
  a space between two of them is right-to-left (N1), so "في 2026-09-30" is
  drawn "30-09-2026 يف" and a table row's numbers right to left. Each number
  cluster's reading is the one `arabic.visual_order` (python-bidi) draws as
  the page shows; with none, the left-to-right reading stands. A gap wider
  than half a font size is a word break; a zero-advance glyph that is not a
  mark is a misread mark (LibreOffice maps a tanween to "/") and is dropped.
  OCR words get the same rule: `arabic.number_in_logical_order` puts a
  separated number read off an Arabic line back in logical order (the TSV
  parser applies it with the paragraph text before the word), and
  `arabic.arabic_number_separators` puts back the ٫/٬ an OCR reader wrote as
  "," or "." between Arabic-Indic digits (a group of other than three digits
  follows a decimal separator; before exactly three it stays as read:
  thousands or fils). OCR readings only: a text layer's marks are the
  page's. Gates:
  `tests/test_document_pdf_rtl.py` (round trips laid out by python-bidi
  itself; without the check the date reads back reversed),
  `tests/test_document_number_order.py`,
  `tests/test_document_ocr_reading_rules.py` (the separators).
- **`to_logical()` runs in `parsers/common.IRBuilder.add_page`**, before limits,
  quality assessment and chunking. It NFKC-folds Arabic presentation forms back
  to base letters so a legacy visual-dump PDF is recovered without OCR. Pages
  whose character *order* is also reversed still fail the quality gate and
  still escalate — that is the correct remaining OCR case.
- **Fonts are chosen by verified coverage** (`documents/fonts.py`), not by first
  path that exists. An RTL job requires presentation-form cmap coverage
  (`U+FB50-FDFF` / `U+FE70-FEFF`); base-block coverage renders tofu.
  `_setup_fonts` raises `DocumentRenderError` rather than emitting a blank
  Arabic PDF behind a warning string.
- **IBM Plex Sans Arabic (OFL) is vendored** in `documents/assets/fonts/`
  (Regular + Bold + `OFL-IBM-Plex.txt`) so generated documents match the web
  UI and the Docusaurus docs on Windows, macOS and the container. Amiri
  remains a naskh fallback if Plex is removed. `KAZMA_DOCUMENT_FONT_DIR`
  overrides the directory. Precedence: **Plex wins both directions** when
  present; Amiri only for Arabic jobs if Plex is absent; system fonts last.
  Do not revert to "bundle-for-Arabic, system-for-Latin" — that split
  restyled English documents away from the product face. The OFL text must
  keep travelling with the fonts.
- **HTML exports inline the pinned font** as a data URI
  (`documents.render.embed_html_fonts`, default on) so an Arabic export renders
  the same wherever it is opened. That costs ~850 KB per Arabic file; the flag
  exists for deployments that serve these over the wire. English exports are
  never affected. DOCX names `THEME["font_latin"]` / `THEME["font_arabic"]`
  (both IBM Plex Sans Arabic); LibreOffice uses only the system's fonts and
  its profile's, so every rendering conversion (the PDF route,
  `renderer_worker._libreoffice`) copies the bundle into the profile's
  `user/fonts` first (`fonts.give_soffice_the_bundled_fonts`). Until
  2026-10-18 nothing did, and the document worker image installs no Plex:
  every Arabic PDF the container profile rendered was DejaVu Sans with Noto
  Serif for Latin (live: the owner's tweet archive). Gate:
  `tests/test_document_archive_layout.py` (the opt-in
  `KAZMA_TEST_DOCUMENT_WORKER_IMAGE` case converts in the real image with and
  without the bundle).
- **The images and CI carry the system deps.** `fonts-noto-core` (the Naskh
  faces ship inside it; `fonts-noto-naskh-arabic` is not a Debian/Ubuntu
  package, and naming it failed the image build and CI's Tests job),
  `libreoffice-writer` (`-nogui` in the worker image), `tesseract-ocr` with
  `-ara` and `-eng` -- in the
  application image (`Dockerfile`), the document worker image
  (`deploy/document-worker/Dockerfile`) and CI, which checks `fc-list` for
  Naskh. Removing them does not fail a test — it silently returns the platform
  to blank Arabic PDFs, and makes `tests/test_docx_rtl_visual.py` skip instead
  of run.

**I. Direction is per BLOCK, not per document.**
`DocProfile.direction` is the *document* base direction — margins, section,
chrome, gutter. `DocProfile.block_direction(text)` resolves each block on its
own, and every engine must use it for content: `w:bidi` per paragraph (DOCX),
per-block style alignment (PDF `_styles_for`), a `dir` wrapper on the blocks
that differ (HTML).

A document is not required to be all one language. A bilingual archive, a
report quoting Arabic sources, minutes with two languages — resolving direction
once for the whole document means whichever language loses the ratio has every
one of its blocks aligned backwards. This was reported from a real generated
tweet archive: 35% Arabic, so the document resolved LTR and every Arabic tweet
was left-aligned with no `w:bidi` at all. Nudging the ratio would simply have
inverted the bug onto the English blocks.

Blocks with no strong directional character — a divider rule, a bare number, a
date — inherit the document direction rather than defaulting to LTR. The DOCX
LTR branch must stamp `w:bidi val="0"` explicitly: in an RTL document the
Normal style and the section both carry bidi, so silence is not neutral.

**J. One type scale, enforced by measurement.**
`theme_cs_size()` is the complex-script optical size and **every** engine must
apply it: DOCX via `w:szCs`, HTML via `_css`, PDF via `_build_styles`. The PDF
engine was the odd one out for a long time — it used the Latin `body_size` for
Arabic, so the same document was set at 11pt one way and 16pt the other and
paginated to 6 pages versus 13. Same rule for `line_height` vs `line_height_ar`
and for table cells (`theme_cs_size(10)`).

RTL tables must read right-to-left in every format: `w:bidiVisual` (DOCX),
`dir="rtl"` (HTML), and an explicit column reversal in the PDF engine, which
has no bidi table model of its own. **Which tables are RTL is the table's own
language** (`DocProfile.table_direction`, 2026-10-20), and a cell with no
letter ("29 – 32") takes its table's direction (`block_direction(text,
neutral)`): an English table in an Arabic document was mirrored and its
ranges read "32 – 29", and the agent dropped the house style for hand-written
HTML to get a readable table. `convert_document` says it converts an existing
file in its own styling and that reports are made with `generate_pdf` /
`generate_docx`. Gate: `tests/test_document_table_direction.py` (through real
LibreOffice; the document's direction as the negative control).
**An HTML file converted to PDF is in the house style** (2026-10-20,
`renderer_worker._house_style_html`): its content -- headings, paragraphs,
lists, tables, `dir` marks -- in `HtmlEngine.render_html_body`'s themed shell
(brand face embedded, heading bars, table style), its own fonts, colours,
presentational attributes and CSS dropped, the page breaks it asked for kept;
`convert_document(keep_source_style=true)` keeps a file's own design, only
when the user asks. The agent made a bilingual report as its own HTML four
times (DejaVu/Noto), and memory then held the file, so every later request
converted it again. The house CSS ends paragraphs and lays out tables by each
block's own direction (`text-align-last: start`, no `direction` on `table`):
an English section of an Arabic report was set right to left. Gate:
`tests/test_document_html_house_style.py`; checked in the worker image on the
live file. The printed page carries the DOCX engine's chrome
(`HtmlEngine.page_chrome_css`: the short brand on top, the brand and the page
number at the bottom, on the reading-start side, as `@page` margin boxes),
and a Latin run in right-to-left text is marked (`arabic.isolate_ltr_runs` on
the text nodes, `_isolate_latin_in_rtl`): "50 °C" in an Arabic note drew
"C° 50". Everything in the house face (the owner's word, "house every
thing"): paragraphs written as list items ("▪ <b>Summer:</b> ...") become a
list (`_house_lists`), and a bullet or check symbol the face lacks is drawn
as the house draws it (`_house_symbols`: ■ ▪ ● ◆ ► ▸ ○ □ as •, ✔ as ✓) --
they fell back to DejaVu. A generated document's list bullets too
(`DocxEngine._house_bullets`): every bullet level is "•" in the house face;
the template's levels named Symbol and LibreOffice drew them in OpenSymbol,
the one face of the first house-style report that was not the house's. Gate:
`tests/test_document_house_bullets.py` (real LibreOffice, the PDF's embedded
faces; the template's levels as the negative control). **Arabic bold and
italic** (`DocxEngine._complex_script_emphasis`): every `w:b`/`w:i` in the
body and styles gets its `w:bCs`/`w:iCs` twin, which is what draws Arabic
bold; only Arabic runs of right-to-left paragraphs had it, so a bilingual
title's Arabic half and an Arabic list's bold lead-ins came out regular. And
a bilingual title's halves in either order are one heading
(`heading_text.headings_equivalent`): the title was drawn twice (live
2026-10-20). Gate: `tests/test_document_bilingual_title_and_arabic_bold.py`
(real LibreOffice; the engine without the twins as the negative control).

**A DOCX carries the house fonts** (2026-10-22, `engines/docx_fonts.py`):
`DocxEngine` embeds the bundled IBM Plex Sans Arabic (regular, bold) after
every save -- obfuscated font parts, `w:embedTrueTypeFonts` -- because a DOCX
only names its fonts, and a device without Plex drew the owner's OCR'd booklet
in another face. A file that cannot take them is still written (WARNING).
Gate: `tests/test_docx_embeds_house_fonts.py` (Word 16's own encoding as a
fixture; the engine without the step as the negative control).

**Every font slot of every Office file names the house face** (2026-11-11,
`engines/office_fonts.py`). Live 2026-11-10 the owner: a deck "has the font
set to the Kazma font but displays it as Arial". A DrawingML run has three
slots and Arabic is drawn from `a:cs`; python-pptx's `font.name` writes
`a:latin` alone, so Arabic fell to the template theme (Calibri, an empty `cs`,
`script="Arab"` = Times New Roman / Arial), and so did everything that
inherits in every Office format: the caret at a line's end, a new text box or
cell, Word's heading styles (Times New Roman for Arabic, measured in Word),
the notes page, the master's bullets (Arial). Now: each PPTX run, paragraph
default (`a:defRPr`) and end mark (`a:endParaRPr`) names IBM Plex Sans Arabic
in `a:latin`, `a:ea` and `a:cs` (`house_text_properties`, the one helper); the
themes (`house_theme`: latin/ea/cs and `script="Arab"`, other scripts
dropped), the master's `p:txStyles`, `p:defaultTextStyle`, the layouts and the
notes master (made even with no notes) too (`PptxEngine._house_package`);
bullets are `a:buChar` "•" in the house face, hanging on the paragraph's
reading side, never typed into the text. A PPTX paragraph takes its own
direction (`block_is_rtl`, the footer the deck's) and its text one run per
script with its `lang` (`arabic.script_runs`: in PowerPoint a mixed run drew
its spaces on the wrong side of the Arabic words as `en-US`, or moved the
full stop as `ar-SA`). The deck embeds the face (`engines/pptx_fonts.py`:
`p:embeddedFontLst`, `ppt/fonts/fontN.fntdata` as uncompressed Embedded
OpenType 0x00020001, which PowerPoint loads -- checked with a renamed copy of
the face no machine has). DOCX: the theme, `w:docDefaults` and every style's
`w:rFonts` (`styles.xml`, `stylesWithEffects.xml`) name it with no theme
reference, and the font table lists only the faces the document names
(`DocxEngine._house_package`). XLSX: the stylesheet's first font -- the
Normal style a new cell takes -- names it with no theme scheme, and the theme
is the house theme (`XlsxEngine._house_workbook`); Excel cannot embed fonts.
Code keeps its monospace face and formulas Cambria Math (DOCX/HTML), and DOCX
direction marks their face (above). Word's PDF names ArialMT for one
invisible glyph: the tab after a list bullet (no font maps U+0009; a plain
Word document does the same). Gate:
`tests/test_office_files_name_the_house_face.py` (English, Arabic and
bilingual files in every format through `renderer_worker._generate_*`; every
slot of every XML part resolved through theme references; each run, end mark
and bullet of the slides and notes; the embedded faces equal to the bundle;
PDF and HTML faces; LibreOffice draws the deck and workbook in the house face
only; the engines with these steps taken out, and a planted Segoe UI and
mixed run, as negative controls). **Office's Protected View loads no embedded
font, and garbles a face installed for one Windows user** (measured 2026-11-12,
the owner's machine: IBM Plex Sans Arabic in the user's own font folder):
PowerPoint draws Arabic in it as wrong glyphs and Word shows Arial until Enable
Editing. A python-pptx deck with no embedded font garbles the same way; one
naming Arial or a face installed nowhere draws correctly. It is the install,
not the file: do not change the engines for it (the guide's "Office's
Protected View"). Setup installs the bundled faces for all users and retires
a per-user copy (`kazma_core.house_font_install`; the fonts part of
`kazma setup system`/`install`; `tests/test_house_font_install.py`, the old
"bundled with Kazma" check as the negative control). The one document font
stays IBM Plex (the owner's decision, 2026-11-12): Calibri, Segoe UI and Dubai
display in Protected View, but Microsoft's terms let Kazma ship none of them
for its PDFs and HTML. **A document built by hand in code goes to
its generator** (`agent/raw_document_guard.py`, asked by the tool worker
before a `python_exec`/`code_exec`/`shell_exec` call runs): reportlab or
fpdf -> `generate_pdf`, python-docx -> `generate_docx`, a new openpyxl or
xlsxwriter workbook -> `generate_xlsx`, python-pptx -> `generate_pptx`. The
owner's Arial deck of 2026-11-10 was python-pptx run that way: the guard
knew no PowerPoint library and named `generate_pdf` for everything. Gate:
`tests/test_raw_document_guard.py` (the old marker list as control).

**A ReportLab PDF draws every letter, and bold is bold** (2026-10-31,
`documents/glyph_fallback.py`). The house face has no Latin Extended-A or
Additional (ā ḥ ṣ ẓ ḍ ṭ ʿ ʾ: every scholarly transliteration of Arabic), and
ReportLab draws a letter its face lacks blank: live, a Kuwait history paper
read "Kāẓima" as "K ima" and the agent stripped the transliteration from its
own paper. `GlyphFallback` wraps each such run in a face whose cmap has it (a
wide-Latin sans the machine has, else the bundled Amiri), in every paragraph
(`paragraph_class`), table cell (`cell`) and contents entry; ReportLab's
`<font>` resets bold, so a run inside `<b>` names the bold face. The house
face is registered as a family (`addMapping`, italic mappings first): inline
`**bold**` came out regular in every ReportLab PDF before. Gate:
`tests/test_pdf_glyph_fallback.py` (the markup without the fallback and the
font without the family as negative controls).

**A ReportLab table's cells wrap inside the page** (2026-10-31,
`rich_render.ltr_table_cells`, used by the body's Markdown tables and
`PdfEngine._add_table` for left-to-right tables). A plain-string cell is drawn
on one line at its natural width: live, the Kuwait paper's chronology ran
past both page edges, a date ran into its event ("c. 3000 BCE onwardDilmunite")
and `**1752**` kept its asterisks, and the agent rewrote the table as a list.
Cells are Paragraphs with inline Markdown; columns keep their natural width
when the table fits, else narrow columns keep theirs and wide ones share the
rest (at least 48 pt). Right-to-left and shaped Arabic tables keep their own
path. Gate: `tests/test_pdf_table_cells_fit.py` (plain cells as the negative
control).

Headings carry keep-with-next (`w:keepNext`/`w:keepLines`/`w:widowControl` in
DOCX, `_keep_headings_with_body` → `KeepTogether` in PDF, `break-after: avoid`
in HTML) so a heading is never the last thing on a page. Paragraph widows and
orphans are off (`allowWidows=0`/`allowOrphans=0`, `orphans: 3`/`widows: 3`).

**A Markdown source keeps its shape** (2026-10-18, the same archive):
`rich_render.parse_rich_blocks` keeps a hard line break (two trailing spaces
or a backslash) as `\n` in a paragraph's or quote's text, and the engines
draw it (DOCX a `w:br`, ReportLab line by line, so an Arabic paragraph's lines
are not reversed; HTML through `markdown`) -- a tweet's lines ran into one
justified paragraph. A quote is upright (Arabic has no italic face; LibreOffice
slanted it), in muted ink, with a bar on its own reading side, kept on one page
with the paragraph that introduces it. A section's heading level is its `#`
count relative to the shallowest heading below the title
(`renderer_worker._build_model_and_profile`; every section was level 1). A
heading's bar sits on the heading's own reading side (`_edge_bar`, which also
keeps `w:pBdr` in schema order). A divider is a rule, never a row of "─".
Table cells are compact and vertically centred. **In a right-to-left block a
left-to-right run that holds a Latin letter is marked with LRM on both sides**
(`arabic.isolate_ltr_runs`, applied by `DocxEngine._isolated` to body runs,
headings and cells): "10 · 2026-10-05 — 15:15 UTC · <id> — محذوفة" came out
in reverse group order around "UTC"; the strong mark makes the run's digits
and separators left to right (W7). The sentence punctuation right after
the run's last letter (`. ! ? , ; :`, `_LTR_TRAILING_PUNCT`) is inside it: an
English sentence ending an Arabic paragraph drew its full stop on the far
side of the next line (2026-10-20, `tests/test_document_english_sentence_in_arabic.py`).
A run of digits alone is NOT marked:
"في 2026-09-30" drawing "30-09-2026" (W2) is the Arabic convention, read right
to left as the date, and the PDF and OCR readers (§19H) read it back by that
rule -- marking it broke the fidelity corpus. An attached `@ # $` (or `+ -`
before a digit) joins its run. **Every direction mark is drawn in a face that
has its glyph** (`DocxEngine._give_direction_marks_a_face`, the last step
before saving: each mark its own run in `fonts.direction_mark_family`, Amiri;
none bundled, the marks are dropped). IBM Plex Sans Arabic has no glyph for
any mark, and LibreOffice draws a missing mark with the face's space glyph:
the PDF's text map then named every space of the bold face U+2066 (the first
version used LRI…PDI in Plex runs, deployed for an afternoon: copied text lost
its spaces, and Kazma's reader failed on the page). `to_logical` strips the
marks when Kazma reads a PDF back. And a block
`to_logical` shortened is no longer taken for a limit cut
(`IRBuilder.add_page`): every block after the first one holding an RLM or an
isolate was dropped from a parse, silently. Gates in
`tests/test_document_archive_layout.py`: the LibreOffice word-order test (no
marks as the control), the text read back from the real LibreOffice PDF (its
spaces, the heading in order, a table once; the morning's isolates as the
control), the bidi-mark parse (the old check fails it).

**Kazma reads past the faults LibreOffice writes into a text map, and reads a
table once** (2026-10-18, the same archive read back by the agent, which took
the duplicate for a rendering defect and tried to "fix" it). `pdf_rtl`: a
direction mark with a visible advance is the space it was drawn with; a digit
or Latin letter of no width is a misnamed mark (Plex's shadda as "8"/"3"); a
Latin letter under `_KASHIDA_EM` (0.15 em) is a misnamed kashida ("G"); Arabic
letters of no width stay (a ligature's parts). A letter glyph's text keeps the
marks it names only where a mark glyph is drawn on it (`_drop_undrawn_marks`):
LibreOffice 24.2 (Ubuntu 24.04, Linux CI 2026-11-10) names "ريالاً"'s tanween
glyph "#" and puts the tanween into the lam-alef ligature's text, so every
lam-alef read "لاً" (the footer's "الاصطناعي" with a tanween).
`arabic.visual_order` drops
isolates (python-bidi asserts on them; the whole page's reading failed). The
PDF parser finds the tables first and reads a single-column page's text
around them (`pdf_layout._segments_around_tables`): each table once, where it
stands; a table's cells were also page text. Gates:
`tests/test_document_pdf_text_map_faults.py`, `tests/test_document_pdf_rtl.py`.
**Reading Kazma's own bilingual PDF back** (2026-10-20: the agent read a
house-style PDF as broken and dropped the house style): a kashida glyph with
no name (U+FFFD, narrower than 0.15 em) is no text; table cells are left out
of column detection (a table is read from its grid), and two sides are
columns only when they stand side by side (`_side_by_side`: short Arabic and
English lines stacked one above the other made a "second column", which put
a title's last word after the paragraph and read the table twice); on a
single-column right-to-left page each block is read once
(`_without_contained`), top to bottom (`_reading_order`); a number-only cell
of a right-to-left table runs right to left ("٤٣ – ٤٦", `text_in_rect(...,
base="R")`); a cell's wrapped lines are one value; and a left-to-right line
whose brackets read inside out ("Avg high )°C(": one glyph draws "(" in an
English cell and ")" in an Arabic one, and the text map names it once) is set
right. Gate: `tests/test_document_table_direction.py` (a bilingual PDF made
through real LibreOffice and read back). **Table cells are read from the
glyphs on every page** (`pdf._logical_table(rtl_page=False)`: left to right,
PyMuPDF's text only for a cell the glyphs leave empty): on the all-English
page PyMuPDF's table reader put the "fi" ligature's "i" after the next glyph,
and Kazma quoted its own cell as "frist rains" (test 18, live 2026-10-20).
Gate: `tests/test_document_pdf_ligature_cells.py` (PyMuPDF's reader as the
negative control).

`tests/test_document_layout.py` measures all of this against a rendered PDF
with PyMuPDF rather than grepping the source — every defect it covers was
invisible at the code level and obvious on the page. Do not weaken it to source
assertions.

