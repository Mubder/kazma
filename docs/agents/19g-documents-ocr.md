<!-- AGENTS.md, piece 19g-documents-ocr. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
**G. Certification.**
`scripts/certify_documents.py` (`documents/certification.py`) +
`tests/test_document_certification_phase10.py` + `hostile_corpus.py` /
committed `tests/fixtures/documents/hostile_manifest.json`. Keep CLI gates and
pytest groups honest (architecture/a11y/crash matrix are pytest). The report
runs what it names: the hostile corpus, a bounded performance smoke, engine
readiness, rollout, the isolation profile configured NOW (`process` is
CONDITIONAL and never reported as confined; `container` passes only attested
and with a current image), the fidelity corpus (G2) and a real SQLite backup,
verify and restore. Postgres multi-replica, metadata multi-replica, a 48-72 h
soak and an external security review are NOT RUN, `default_ready` stays
false, and `--soak` is an extended smoke, never a soak
(`tests/test_document_certification_honesty.py`). The soak harness is
`scripts/document_soak.py` (Q03: private SQLite stores unless `--use-env`,
restarts and killed intakes, fails on a job left unfinished, a reservation
left pending or scratch left behind); a two-minute pilot is not the soak.

**G2. Fidelity is measured on a versioned corpus** (2026-10-13, F09/Q02).
`documents/fidelity.py` builds EN/AR/bilingual cases with Kazma's own engines
(DOCX, PDF, HTML, OCR from a rasterized page) from a ground-truth spec and
measures the real parser: word recall, segment recall, order, exact numbers,
table cells, page honesty, alignment, CER/WER, punctuation. Floors and release targets are
declared per corpus version in `tests/fixtures/documents/fidelity_thresholds.json`;
the `qualified` list must match the measurement. Change the corpus = bump
`CORPUS_VERSION` and re-lock (`scripts/document_fidelity.py --lock`, which
never lowers a floor within a version). Version 2 adds long documents (EN/AR,
many pages), two columns from another producer (MuPDF Story), wide wrapping
tables and a degraded scan. A segment counts as recovered only when its
words come back together (`_SPAN_SLACK`): words in order but interleaved with
another column are not. Alignment judges every line that shows its side
against its segment's direction by `arabic.direction_of`, the engines' own
per-block rule. Version 3 adds `*-ocr-table`, an OCR page with its ruled
table; the OCR reference text is the whole page, table included. Version 4
(2026-10-15) puts the page furniture the renderer drew (the Arabic route's
brand footer, read from the rendered PDF: `_drawn_furniture`) into the OCR
reference: without it a perfect reading scored CER 0.114 on `ar-ocr`. The OCR
cases are measured per engine (`fidelity.OCR_ENGINES`: `tesseract`,
`neural`), each with its own floors and `qualified` list under
`ocr_engines.<engine>`; a case another engine read (a fallback) fails the
test and is refused by `--lock`, and a lock keeps the floors of an engine not
installed where it runs. Version 5 (same day) measures `punctuation`: each
ground-truth word's following marks against the marks after the word it
aligns to, where either has one (wrong, dropped and added marks are errors;
a mark inside a number is `number_recall`'s); release target 1.0. Every
other metric reads words, so the live install's "placed under review:"
passed them all. Not qualified, each with its reason in the
thresholds file: with Tesseract `ar-ocr`, `ar-ocr-table` (no Arabic-Indic
digits, lines dropped) and `en-ocr-degraded` (3.75 read as 3:75: a speck
above the decimal point; a full stop it adds mid-line). With the neural
engine every OCR case qualifies, punctuation included (G4: the speck rule,
EasyOCR's second readings, the marks and punctuation rules, the final-mark
vote). A re-lock keeps the file's `notes` and
`measured_on`: a note for a case that now qualifies is removed by hand. Certification runs the
corpus and reports the actual isolation profile and backends (F10). Gate:
`tests/test_document_fidelity.py`.

**G3. Scanned pages: tables cell by cell; real scans measured opt-in**
(2026-10-13, from a real 300 dpi copier scan of an Arabic booklet).
`ocr/tesseract._parse_tsv` reads Tesseract's TSV with `quoting=QUOTE_NONE`: a
word OCR reads as starting with `"` opened a CSV quoted field that swallowed
every later row, so a page's "text" held other rows' coordinates (one line of
14,386 characters) and lost words. `ocr/tables.py` (Pillow only) finds ruled
grids and reads each cell (`TesseractProvider.recognize_region`, psm 6/7); when
`eng` is installed, a cell with no letters, or one read below 0.5 confidence,
is read again as digits only (`eng`), and that reading wins only at
confidence >= 0.75. The page's lines inside a table become one `TABLE` block (`extractor: ocr-grid`, cells right to left
for an RTL table). Rules the scan taught, each gated with the old rule as its
control: a ruling line is ONE continuous stroke (a joined Arabic baseline is
as dark as a rule on average, and cut text lines in half); a cell is >= 3 %
of the page wide (the strips of a page border made every boxed section a
"table"); the line cut follows the page's paper (pale tinted grids). At most
600 cells and 120 s a page: past either, the tables already read are kept and
the rest keep the page's own reading; a cell that fails to read drops the
table pass for that page (`documents.ocr.tables`, default on, Settings →
Documents). Measured on the scan: a contents table's numbers 22 % -> 91 %,
its rows kept together 3 % -> 56 % complete, 6 of 6 searches on the right
page. Tesseract did not read Arabic-Indic digits in any font measured (fast or
best `ara` model, `script/Arabic` fast or best, any segmentation mode): the
installed `ara` model's character set holds one of the ten (٠) and neither
Arabic separator. The neural engine (G4) does. Gates: `tests/test_document_ocr_tables.py`,
`tests/test_document_ocr_phase5.py` (the quote with the old quoting as
control), `test_ocr_without_the_table_pass_fails_the_floor`, and the opt-in
`tests/test_document_real_scans.py` (`KAZMA_TEST_REAL_SCANS_MANIFEST`: a
manifest outside the repository names each scan by path and SHA-256, the
pages, the numbers, symbols and single terms each holds -- never page text --
the searches and the floors; without it the test skips). Since 2026-10-15
each engine is held to its own floors there (`floors_by_engine`), with a long
worker time limit so every page stays with the engine asked for.
**Every Tesseract call names its thread budget** (2026-11-13,
`ocr.tesseract._thread_limit`): `OMP_THREAD_LIMIT` is the caller's
`OMP_NUM_THREADS` (1 in the document worker), else 1. The sandbox passes a
child only the variables it names, so Tesseract started a thread per CPU it
could see (32 here) whatever the worker's CPU limit. Measured in the document
worker image (Tesseract 5.5, libgomp), eight reads four at once: 4.8-6.0 s
unlimited against 2.5-3.2 s at `--cpus=1`, 1.0 s against 0.6 s at `--cpus=4`;
never slower. Gate: `tests/test_tesseract_thread_limit.py` (through the real
sandbox; the old request, which passed no limit, as the control).

**G4. The neural OCR engine** (2026-10-15, Q02: Arabic and Arabic-Indic
digits). `documents.ocr.engine`: `auto` (default; the neural engine when its
packages and models are installed where documents are parsed, else
Tesseract), `neural` (the same, with a document warning when Tesseract read
it), `tesseract`. Validated on every write (`settings_validation`, and the
Documents settings route asks it): an engine the policy cannot read would
stop every document job. `ocr/pipeline.select_ocr_provider` is the one
choice. `ocr/neural.py` runs four models on ONNX Runtime in the document
worker, never in the Kazma process: PP-OCRv5 detection; boxes of one line
joined (gap <= 1 line height: per-word crops gave stray characters), cut to
their ink; EasyOCR `arabic_g1` reads each line (`english_g2` a line without
Arabic, and English-only pages); each word without a digit is voted on with
PaddleOCR's PP-OCRv5 Arabic recognizer and Tesseract's words on the page --
the two agreeing, or PaddleOCR at >= 0.9 alone, replace EasyOCR's word; a
number is always EasyOCR's. EasyOCR's own second reading of a letter it
doubted (>= 0.1, a letter of the same bidi class) is a reading too: another
reader that reads it makes two (the table page's "ربالأ", ب 0.74 / ي 0.26,
is Tesseract's "ريالاً"). The vote is over letters: a word's punctuation is
EasyOCR's (PaddleOCR drops a full stop), and a space read before a full
stop or comma goes (EasyOCR reads the gap a font leaves after a final ف as
one). EasyOCR writes no harakat (its Arabic list leaves them out) and draws
the tanween over an alef as a hamza ("ريالأ"): where another reader read the
same letters with harakat on that side of that letter, those stand, unless
a reader read EasyOCR's hamza too; a mark only another reader saw is never
added (Tesseract writes "الشماليٍ"). Before a line is read, ink smaller than
the line's own pen (area under half the stroke squared, no side longer than
a stroke) is removed from its box, unless it continues past the box: on the
degraded scan a speck beside the decimal point read "3.75" as "3,75"
(specks 1-3 px of area; the smallest real mark, a blurred full stop, 6). A
"," or "." between Arabic-Indic digits is put back as ٫/٬ by a number's
grammar (`arabic.arabic_number_separators`, both engines: neither EasyOCR
`arabic_g1` nor Tesseract `ara` can write those marks). A line's last mark
is two readers' (`_vote_final_mark`, after the letter vote): EasyOCR says
THAT a mark is there (it wrote one after the last word, or gave one >= 0.1
past its last character: `Reading.beyond`), Tesseract's reading of the same
word -- same folded letters -- says WHICH, when it is ONE mark; on an Arabic
line Tesseract's "," is a comma of either form (`ara` has no "،": EasyOCR's
form if it read a comma, else "،"). A mark Tesseract alone reads is never
added, none is removed, a number's own marks are never touched. English
pages read Tesseract only when a line ends in a mark to vote on. Measured
on 176 generated lines (EN/AR, clean and degraded; the live install read
"placed under review:"): English 61 -> 83 of 88 right, 22 -> 0 wrong;
Arabic 70 -> 74, 6 -> 3 wrong; 0 marks added; no probability threshold on
EasyOCR alone separates "." from ":" (the right mark second at 0.07-0.40).
Left: a mark EasyOCR reads as a letter ("!" as "l", Arabic "." as "ء") --
its own second reading there is mostly under 0.1, so no two readers agree.
Not adopted: a crop margin (EasyOCR's own 0.1 of the height) read English
line-final full stops right but added full stops to Arabic headings that
have none. A drawn line goes to logical order by
`parsers.pdf_rtl.logical_line`, never a recognizer's `get_display` (which
leaves "30-09-2026" reversed). The page pass skips the lines inside ruled
tables (`skips_table_regions`; `tables.table_regions` is `read_tables`' own
grid finder) and the page is read again in full when a table was not read
cell by cell. Near the worker's time limit (`apply_ocr(deadline=)`, 0.8 of
`documents.workers.timeout_seconds` from the parser worker's start) the
remaining pages go to Tesseract, said on the document (`tesseract_from_page`):
one core makes it ~15 s an Arabic text page, 30-45 s with ruled tables. The
digits-only cell pass keeps an English reading only at EasyOCR's own score >=
0.75 (an English reading of an Arabic-Indic number is confident by character).
Models (`ocr/neural_models.py`): every source pinned by URL, size and SHA-256
(Apache-2.0), installed by `scripts/ocr_models.py install` (EasyOCR's PyTorch
weights converted to ONNX and checked against PyTorch; without torch it
converts in a `uv run --isolated` environment) into `KAZMA_OCR_MODEL_DIR` or
`<data dir>/models/ocr` (`store_registry`: `models`, rebuilt, not backed up)
with a `manifest.json`; the worker uses them only when every file matches it.
The service tells the worker where they are (its environment is scrubbed).
Runtime packages are the `ocr` extra (onnxruntime, opencv-python-headless,
pyclipper, numpy). Measured against Tesseract, PaddleOCR (native and ONNX),
EasyOCR alone, tessdata_best and the 2-reader combinations:
`docs/plans/DOCUMENTS_INDUSTRIAL_HARDENING_GOAL.md` (Q02). Gates:
`tests/test_document_ocr_neural.py` (each rule with its control; the
end-to-end readings need the models and skip without them),
`tests/test_document_ocr_reading_rules.py` (separators, punctuation, second
readings, marks, specks, a line's last mark; the old rules as controls, and
end to end on the corpus's table page, clean page and degraded scan, the old
rules re-run on the same model outputs), `tests/test_document_fidelity.py`, `tests/test_document_real_scans.py`, and in
the image `test_the_neural_ocr_engine_reads_inside_the_container`.

**G5. A long scan is OCR'd a few pages per run; a stopped call stops its
worker** (2026-10-22: a 54-page Arabic scan sent to the chat, ~35 s a page
on one core with the neural engine). The worker takes `ocr.max_pages` and
names the rest in `ocr.deferred_pages`; `DocumentService.ocr_in_batches`
reads them in further runs, each sized from the seconds a page has taken. A
durable job reads every page (`parse_ingested_blob`, progress through
`documents/ocr_progress.py` into `job_status`'s `progress`); a tool call
(`read_with_ocr_budget`, `ocr_transient_within`) reads what fits in
`tool_ocr_budget()` and names the rest (`ocr_continuation_note`). A chat
attachment's excerpt is the text layer only (`text_layer_sync`, `ocr.disable`):
it OCR'd the whole scan for five minutes before the model saw the message.
Every async `DocumentService` worker call goes through
`documents/cancellation.run_cancellable`: a cancelled await sets the run's
cancel signal, the broker exchange closes its connection (the broker removes
the container) and the local sandbox kills the tree -- timed-out tool calls
left their OCR containers running and each retry shared the CPU with them.
Gate: `tests/test_document_ocr_batches.py` (the one-run parse, a read without
a cancel check and the old excerpt as negative controls). `document_convert` to
Word or Markdown of a document read by OCR exports that text
(`DocumentIngestionService._ocr_text_payload`), never the scan's images.
`document_convert` and `document_redact` save their artifact under
`<data dir>/documents` and name the path for `send_file`
(`document_platform/tools._saved_copy`, through `resolve_artifact_blob`'s
ACL): an `artifact_id` alone is something no agent tool takes, and the model
hunted the disk for the file and remade it (2026-10-22). Gate:
`tests/test_document_convert_saves_a_file.py`.

