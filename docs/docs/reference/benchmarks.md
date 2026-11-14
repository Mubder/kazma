---
title: Benchmarks
---

# Benchmarks

> This page is published at [kazma.ai/docs/benchmarks/](https://kazma.ai/docs/benchmarks/) ([Arabic](https://kazma.ai/ar/docs/benchmarks/)).

How well Kazma remembers, finds and reads, measured on versioned datasets kept in its repository and held by its tests: a change that makes a number worse fails the build. This page is written from those results (`scripts/benchmarks_page.py`), so it says what the code does today. Every dataset, script and result file is in the repository; the commands under each table reproduce it.

The datasets are Kazma's own, built for these questions in English, Arabic and both, with questions held out from tuning. They are not a public leaderboard's: compare runs of Kazma with each other, not with numbers measured elsewhere.

## Memory: does it find what you told it? {#memory}

Dataset version 5: a private memory seeded the product's way (turns, facts, weekly summaries), then asked questions through the real `recall()`, with the meaning-search model's recorded vectors. **Hit rate**: the answer is among what Kazma shows the model. **MRR**: how high it ranks (1 = first). **Precision**: how much of what is shown is about the question.

| Question | Hit rate | MRR | Precision |
|---|---|---|---|
| A fact you told it | 100.0 % | 1.00 | 64.6 % |
| Something said in one turn | 100.0 % | 0.82 | 65.6 % |
| Something spread over several turns | 100.0 % | 1.00 | 79.4 % |
| A fact that changed (the newest wins) | 100.0 % | 1.00 | 57.4 % |
| Asked in other words | 100.0 % | 0.94 | 62.9 % |
| Asked with the exact words | 100.0 % | 1.00 | 93.8 % |
| Something Kazma itself said | 100.0 % | 0.62 | 63.2 % |
| Catch-me-up questions | 90.0 % | 0.67 | 55.5 % |
| Asked in Arabic | 100.0 % | 0.79 | 62.5 % |
| A question with noise around it | 100.0 % | 1.00 | 61.1 % |
| **All questions** | **92.5 %** | **0.84** | **64.4 %** |
| **Held out** (never used to choose a setting) | **89.1 %** | **0.84** | **64.6 %** |

Questions it has no memory for are answered with nothing in 27.8 % of cases (0.0 % held out); the goal is 60.0 %: the weakest number on this page.

### Learning facts from a conversation {#memory-facts}

Turns in English, Arabic and both, with the facts a person would expect remembered. **Quick pass**: Kazma's own rules, on every turn. **Model pass**: the model reading the turn afterwards (recorded replies). **Recall**: facts found. **Precision**: facts found that are right. **Quiet**: turns with nothing to learn that taught nothing.

| Pass | Language | Recall | Precision | Quiet |
|---|---|---|---|---|
| Quick pass | English | 46.2 % | 100.0 % | 100.0 % |
| Quick pass | Arabic | 63.6 % | 100.0 % | 100.0 % |
| Quick pass | Mixed | 60.0 % | 100.0 % | 100.0 % |
| Model pass | English | 76.9 % | 78.6 % | 100.0 % |
| Model pass | Arabic | 90.9 % | 90.9 % | 100.0 % |
| Model pass | Mixed | 70.0 % | 58.3 % | 100.0 % |

With Postgres as the memory store, the same questions score 89.5 % hit rate, 0.83 MRR and 62.1 % precision.

Reproduce: `python scripts/memory_bench.py run` (add `--real` to use the real model), and `python -m pytest tests/test_memory_benchmark.py`.

## Knowledge Library: does search find the right page? {#knowledge}

Dataset version 1: English, Arabic and mixed pages published into a private library the product's way, then searched the way the agent searches (**tool**) and the way a chat turn is given library text (**turn**), with meaning search and with keywords alone.

| Question | Tool: hit rate | Tool: MRR | Turn: hit rate | Turn: precision |
|---|---|---|---|---|
| The exact words of the page | 100.0 % | 1.00 | 100.0 % | 68.1 % |
| Asked in other words | 91.7 % | 0.88 | 91.7 % | 76.4 % |
| Arabic | 100.0 % | 1.00 | 100.0 % | 87.5 % |
| Arabic, in other words | 85.7 % | 0.86 | 85.7 % | 59.5 % |
| Arabic and English mixed | 100.0 % | 1.00 | 100.0 % | 79.6 % |
| Asked in one language, written in the other | 83.3 % | 0.83 | 83.3 % | 52.8 % |
| An answer spread over several pages | 100.0 % | 1.00 | 100.0 % | 75.0 % |

| All questions | Hit rate | MRR | Precision | Nothing shown when nothing answers |
|---|---|---|---|---|
| Tool, meaning search | 85.0 % | 0.94 | 71.1 % | 50.0 % |
| Turn, meaning search | 85.0 % | 0.94 | 72.1 % | 50.0 % |
| Tool, keywords only | 54.4 % | 0.53 | 46.6 % | 78.6 % |
| Turn, keywords only | 54.4 % | 0.53 | 46.6 % | 78.6 % |
| Tool, held out | 68.8 % | 0.92 | 79.2 % | 25.0 % |
| Turn, held out | 68.8 % | 0.92 | 79.2 % | 25.0 % |

Reproduce: `python scripts/knowledge_bench.py run` (`--real` for the real model, `--chroma` for the real vector store), and `python -m pytest tests/test_knowledge_benchmark.py`.

## Reading documents, in Arabic and English {#documents}

Corpus version 2026-10-15.5: documents Kazma builds itself (Word, PDF, HTML; scanned pages for OCR) from a known text, read back by the real parser, measured word by word. The numbers are the **floors** every change must reach (the measurement, less a small margin for OCR); a case is **qualified** when it meets the release targets. Measured on: 2026-10-15, Windows 11, Python 3.12.9, PyMuPDF 1.28.2, python-bidi 0.6.10, LibreOffice 26.2.6.3 (Arabic PDF route).

### Text documents {#documents-text}

| Document | Qualified | Words found | Numbers exact | Punctuation | Table cells | Character errors | Word errors |
|---|---|---|---|---|---|---|---|
| `ar-docx` | yes | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | – | – |
| `ar-html` | yes | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | – | – |
| `ar-pdf` | yes | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | – | – |
| `ar-pdf-long` | yes | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | – | – | – |
| `bilingual-docx` | yes | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | – | – |
| `bilingual-html` | yes | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | – | – |
| `bilingual-pdf` | yes | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | – | – |
| `en-docx` | yes | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | – | – |
| `en-docx-widetable` | yes | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | – | – |
| `en-html` | yes | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | – | – |
| `en-pdf` | yes | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | – | – |
| `en-pdf-columns` | yes | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | – | – | – |
| `en-pdf-long` | yes | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | – | – | – |
| `en-pdf-widetable` | yes | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | ≥ 100.0 % | – | – |

### Scanned pages, Kazma's neural OCR (the default) {#documents-ocr-neural}

| Document | Qualified | Words found | Numbers exact | Punctuation | Table cells | Character errors | Word errors |
|---|---|---|---|---|---|---|---|
| `ar-ocr` | yes | ≥ 98.0 % | ≥ 98.0 % | ≥ 98.0 % | – | ≤ 2.0 % | ≤ 2.0 % |
| `ar-ocr-table` | yes | ≥ 98.0 % | ≥ 98.0 % | ≥ 98.0 % | ≥ 100.0 % | ≤ 2.0 % | ≤ 2.0 % |
| `en-ocr` | yes | ≥ 98.0 % | ≥ 98.0 % | ≥ 98.0 % | – | ≤ 2.0 % | ≤ 2.0 % |
| `en-ocr-degraded` | yes | ≥ 98.0 % | ≥ 98.0 % | ≥ 98.0 % | – | ≤ 2.0 % | ≤ 2.0 % |
| `en-ocr-table` | yes | ≥ 98.0 % | ≥ 98.0 % | ≥ 98.0 % | ≥ 100.0 % | ≤ 2.0 % | ≤ 2.0 % |

### Scanned pages, Tesseract alone {#documents-ocr-tesseract}

| Document | Qualified | Words found | Numbers exact | Punctuation | Table cells | Character errors | Word errors |
|---|---|---|---|---|---|---|---|
| `ar-ocr` | no | ≥ 63.8 % | ≥ 31.3 % | ≥ 73.0 % | – | ≤ 26.2 % | ≤ 31.2 % |
| `ar-ocr-table` | no | ≥ 63.8 % | ≥ 12.3 % | ≥ 73.0 % | ≥ 55.6 % | ≤ 26.7 % | ≤ 37.1 % |
| `en-ocr` | yes | ≥ 98.0 % | ≥ 98.0 % | ≥ 98.0 % | – | ≤ 2.0 % | ≤ 2.0 % |
| `en-ocr-degraded` | no | ≥ 98.0 % | ≥ 73.0 % | ≥ 78.0 % | – | ≤ 2.0 % | ≤ 2.0 % |
| `en-ocr-table` | yes | ≥ 98.0 % | ≥ 98.0 % | ≥ 98.0 % | ≥ 100.0 % | ≤ 2.0 % | ≤ 2.0 % |

- `ar-ocr` is not qualified: Below release: Tesseract 'ara' does not read Arabic-Indic digits in any font or model measured (fast, script/Arabic, tessdata_best ara and script/Arabic) -- the installed fast model's character set holds one of the ten (U+0660) and neither Arabic separator -- and its layout analysis skips a heading and a paragraph (both are read when cropped). Token recall 0.66, CER 0.24, WER 0.29 against the whole page (corpus version 4). Punctuation 0.75 (version 5): a '/' read after 'الفصل' where it misreads the number.

- `ar-ocr-table` is not qualified: Below release: the table is found and read cell by cell (its labels come back), but the Arabic-Indic digits in its cells are not read (the same limit as ar-ocr), and the page around it loses the same heading and paragraph. Punctuation 0.75: the same '/' as ar-ocr.

- `en-ocr-degraded` is not qualified: Below release: on the degraded scan Tesseract reads 3.75 as 3:75 -- a speck of the scan's salt noise sits above the decimal point. The neural engine removes specks from each line before reading it; Tesseract reads the page whole, and a page-wide speck filter would take one pen for every line (on the Arabic page the footer's dots, area 9 with its own pen 3, sit just above the body pen's speck size, 8: a smaller type would lose its dots). Every word is right (CER/WER count words, not the punctuation inside them), so numbers OCR'd from poor scans are not qualified with Tesseract. Punctuation 0.8 (version 5): a full stop it adds mid-line, 'reached.1,234,567.89'.

Reproduce: `python scripts/document_fidelity.py`, and `python -m pytest tests/test_document_fidelity.py`.
