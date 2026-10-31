---
id: arabic-engineering
title: How Kazma handles Arabic
sidebar_label: How Kazma handles Arabic
description: The code Kazma writes itself so Arabic works as well as English, in text direction, letter shaping, search, memory, documents, OCR and the interface, with the file and the test for each
---

# How Kazma handles Arabic

> This page is published at [kazma.ai/docs/arabic-engineering/](https://kazma.ai/docs/arabic-engineering/) ([Arabic](https://kazma.ai/ar/docs/arabic-engineering/)).

A language model can read and write Arabic. That does not make an *app*
work in Arabic. Text has to run the right way, letters have to join, search
has to match words written with or without vowel marks, and a scanned page
has to come back with its digits in order. Kazma does that work in its own
code, mostly in one module, `kazma_core/documents/arabic.py`, so every part of
the product follows the same rules. This page goes through it piece by piece,
with the file and the test for each. For the user's view (the Arabic
interface, the Majlis greeting, dialect notes), see
[Arabic and cultural features](arabic-cultural-features.md).

## 1. Which way a paragraph runs

**The problem.** A page in one direction is easy. Real text mixes languages:
an Arabic reply quoting an English error message, an English report with an
Arabic name, a document that is a third Arabic. The first attempt decided the
direction once per document by counting letters. A bilingual archive that was
35% Arabic came out entirely left to right, with every Arabic post aligned the
wrong way.

**What Kazma does.** The direction is decided *per paragraph*, from the
Unicode bidi class of its letters (`R` and `AL` are right to left). It never
uses a hand-written range of "Arabic characters", because those miss Hebrew,
Syriac and Arabic Extended-B, and count vowel marks and Arabic-Indic digits as
letters. A paragraph with no letters at all, such as a date or a divider,
takes the direction of the document around it. The web interface uses the same
rule: in either interface, English paragraphs run left to right and Arabic ones
right to left, and a table's direction comes from the table's own language.

**Where.** `kazma_core/documents/arabic.py` (`direction_of`,
`is_rtl_dominant`), `kazma_ui/static/js/bidi.js` (`blockDir`, for the chat).

**Proof.** `tests/test_arabic_single_home.py` (no other module may shape or
judge direction), `tests/js/test_block_direction.js`,
`tests/test_text_follows_its_language.py` (real turns in both interfaces,
compared line by line).

## 2. Drawing Arabic correctly in documents

**The problem.** Arabic letters change shape by position and join their
neighbours, and a right-to-left line has to be put in visual order before a
PDF engine draws it. Kazma first did this per piece of formatting (a bold word,
a link). Each piece came out reversed on its own and in the wrong order, so the
sentence rendered inside-out with its spaces lost.

**What Kazma does.**

- **Shape once per paragraph.** `shape_spans` resolves the bidi levels over
  the whole paragraph, splits the styled pieces at level boundaries, reorders
  them, and only then joins the letters.
- **Choose fonts by what they cover.** A font is used for Arabic only if it
  really contains the letter forms. Otherwise the PDF shows empty boxes with no
  error. Kazma ships IBM Plex Sans Arabic (the interface's own font, under its
  open license) for both languages, with Amiri as a fallback. It embeds Plex in
  Word files, so a computer without it still shows the document as intended.
- **Arabic bold is its own setting in Word.** Word draws Arabic bold and
  italic from separate flags (`w:bCs`, `w:iCs`). Kazma sets both whenever it
  sets the Latin ones, or an Arabic title stays regular beside a bold English
  one.
- **Mark Latin runs inside Arabic lines.** In a right-to-left paragraph, a
  piece of Latin text such as "50 °C" or "15:15 UTC" is wrapped in direction
  marks, or it draws as "C° 50". A run of digits alone is left alone, because a
  date written after Arabic reads right to left by Arabic convention, and the
  PDF and OCR readers expect it that way.

**Where.** `kazma_core/documents/arabic.py` (`shape_spans`,
`isolate_ltr_runs`), `kazma_core/documents/fonts.py`.
[Document rendering](document-rendering.md) has the full design.

**Proof.** `tests/test_document_layout.py`, which measures a rendered PDF
rather than reading source code. `tests/test_document_archive_layout.py` checks
the text read back from LibreOffice's PDF, with the marks taken out as the
negative control.

## 3. Search that ignores vowel marks

**The problem.** The same Arabic word can be written with or without its
vowel marks (harakat), with different forms of alef, or with a final ta
marbuta written as ha. SQLite's full-text search treats the marks as word
separators, so a vocalized word was indexed as a row of single letters, and
never matched.

**What Kazma does.** `fold_for_search` removes the marks and unifies the
letter forms. It runs on *both* sides, when a text is indexed and when a
question is searched. Folding only one side is worse than none. For memory,
`query_terms` picks the content words of a question. It knows English, Gulf
and Modern Standard Arabic stop words, handles the article with the prefixes
that attach to it (و, ف, ب, ك, ل), handles light plurals, and matches whole
words only.

**Where.** `kazma_core/documents/arabic.py` (`fold_for_search`),
`kazma_core/memory/query_terms.py`, the Knowledge Library's search index.

**Proof.** `tests/test_arabic_text.py`, `tests/test_memory_relevance.py`, and
the memory benchmark's Arabic and mixed-language questions.

## 4. Memory that understands Arabic requests

**The problem.** Kazma's quick fact-reading pass looked for English patterns
only. On the extraction benchmark it found none of the facts in Arabic messages
(recall 0.00). "Remember this" worked only in English. Because a whole message
under a length limit was treated as small talk, "تذكر هذا" was thrown away as
too short.

**What Kazma does.**

- **Remember requests in both languages.** It recognizes Arabic forms (تذكر،
  لا تنسى، احفظ، سجل عندك، خلك فاكر) as whole words, with an optional و or ف
  in front. "تذكرة" (a ticket) is not "تذكر". A request marks the turn as worth
  keeping.
- **Arabic facts from grammar.** The quick pass reads Arabic sentences by rule.
  A negation, an imperative ("استخدم البحث"), a question, or a statement about
  someone else ("أخوي ساكن في دبي") stores nothing about the user. A place has
  to end where it is read.
- **Small talk by words, not length.** A greeting, thanks or "تمام" is small
  talk only when the whole message is, and only with a short reply. A short
  question about something is still a memory.

**Where.** `kazma_core/memory/remember_request.py`,
`kazma_core/memory/arabic_facts.py`, `kazma_core/memory/episode_text.py`
(`is_small_talk`).

**Proof.** `tests/test_memory_arabic_facts.py` (sentences not in the
benchmark, both ways, with the pass switched off as the negative control),
`tests/test_memory_remember_and_small_talk.py`.

## 5. Reading Arabic documents and scans

**The problem.** PDF tools hand back Arabic lines reversed character by
character. That turns a number drawn left to right inside an Arabic line
backwards ("١٢٣٤٥٦٧" read as "٧٦٥٤٣٢١"), and swaps the letters of joined
glyphs ("في" read as "يف"). On scanned pages, Tesseract cannot read
Arabic-Indic digits at all: its Arabic model contains one of the ten.

**What Kazma does.**

- **PDF text is re-read glyph by glyph.** Only right-to-left runs are
  reversed, as whole glyphs. A number keeps the reading the bidi algorithm
  itself would draw. Presentation forms are folded back to base letters, so an
  old PDF that stored drawn shapes reads as normal text.
- **A neural OCR engine for Arabic.** Detection and recognition models run on
  ONNX Runtime inside the document worker. Where they disagree, the readings of
  several recognizers are voted word by word. Decimal and thousands separators
  (٫ and ٬) are restored by the grammar of numbers, which no reader writes
  itself. Ruled tables on a scan are read cell by cell.
- **Measured, not assumed.** A versioned corpus of English, Arabic and
  bilingual documents, including scans, is parsed by the real engines. Word,
  number, order, table and punctuation scores have floors per engine that only
  go up. Every OCR case of the corpus qualifies with the neural engine. It is
  marked Partial because only one real scan has been measured so far.

**Where.** `kazma_core/documents/parsers/pdf_rtl.py`,
`kazma_core/documents/arabic.py` (`to_logical`, `number_in_logical_order`,
`arabic_number_separators`), `kazma_core/documents/ocr/neural.py`,
`kazma_core/documents/fidelity.py`.

**Proof.** `tests/test_document_pdf_rtl.py` (round trips laid out by
python-bidi itself), `tests/test_document_number_order.py`,
`tests/test_document_ocr_reading_rules.py`, `tests/test_document_fidelity.py`.

## 6. An interface that is Arabic, not translated English

**What Kazma does.** Every word of the interface comes from one catalog with
an English and an Arabic entry, and a test fails the build on an English word
left in a template or a script. Counts use real plural forms: Arabic has six
(zero, one, two, few, many, other), so the interface never shows "1 أدوات".
Numbers and dates are written with Latin digits in the Arabic interface, as
elsewhere in the product. What is not interface text (your messages, the
model's replies, names, file contents) is marked so the browser never
translates it, and keeps its own direction.

**Where.** `kazma_ui/i18n/` (`t_plural`), `kazma_ui/static/js/locale_format.js`,
`kazma_ui/static/js/bidi.js`.

**Proof.** `tests/test_kazma_count.py` (every count key has every form),
`tests/test_count_labels.py`, `tests/test_templates_have_no_english.py`,
`tests/test_scripts_have_no_english.py`,
`tests/e2e/test_pages_read_in_arabic.py` (every page rendered in Arabic).

## What this does not do

Kazma's answers come from the model you choose. It does not add a dialect
model, and the quality of an Arabic reply is that model's. Kazma keeps the
reply in your language (a language lock, which never blocks quoting a draft
in another language) and handles everything around the reply. The Majlis
greeting is a fast reply to a greeting or farewell, not a dialect engine
([Arabic and cultural features](arabic-cultural-features.md)).
