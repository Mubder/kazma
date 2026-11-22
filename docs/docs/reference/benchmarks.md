---
title: Benchmarks
---

# Benchmarks

> This page is published at [kazma.ai/docs/benchmarks/](https://kazma.ai/docs/benchmarks/) ([Arabic](https://kazma.ai/ar/docs/benchmarks/)).

How well Kazma remembers, finds and reads, measured on versioned datasets kept in its repository and held by its tests: a change that makes a number worse fails the build. This page is written from those results (`scripts/benchmarks_page.py`), so it says what the code does today. Every dataset, script and result file is in the repository; the commands under each table reproduce it.

The datasets are Kazma's own, built for these questions in English, Arabic and both; the memory and Knowledge datasets each keep a second set of questions apart from tuning. They are not a public leaderboard's: compare runs of Kazma with each other, not with numbers measured elsewhere. The one public benchmark here, LongMemEval, carries its own baselines on the same reader.

## Memory: does it find what you told it? {#memory}

Dataset version 8: a private memory seeded the product's way (turns, facts, weekly summaries), then asked questions through the real `recall()`, with the meaning-search model's recorded vectors. **Hit rate**: the answer is among what Kazma shows the model. **MRR**: how high it ranks (1 = first). **Precision**: how much of what is shown is about the question.

| Question | Hit rate | MRR | Precision |
|---|---|---|---|
| A fact you told it | 100.0 % | 1.00 | 28.1 % |
| Something said in one turn | 100.0 % | 0.82 | 52.4 % |
| Something spread over several turns | 100.0 % | 1.00 | 73.8 % |
| A fact that changed (the newest wins) | 100.0 % | 1.00 | 49.2 % |
| Asked in other words | 100.0 % | 0.94 | 42.4 % |
| Asked with the exact words | 100.0 % | 1.00 | 78.8 % |
| Something Kazma itself said | 100.0 % | 0.57 | 56.0 % |
| Catch-me-up questions | 90.0 % | 0.67 | 51.3 % |
| Asked in Arabic | 100.0 % | 0.79 | 50.6 % |
| A question with noise around it | 100.0 % | 1.00 | 47.2 % |
| One topic over many turns | 78.6 % | 0.38 | 22.6 % |
| A follow-up to the message before | 100.0 % | 0.69 | 63.7 % |
| How many, or list them all | 83.3 % | 0.81 | 50.7 % |
| A time, or what was true before | 88.9 % | 0.76 | 51.4 % |
| A month, or a project over many weeks | 100.0 % | 0.44 | 44.2 % |
| **All questions** | **90.0 %** | **0.75** | **48.7 %** |
| **Second set** (see below) | **86.2 %** | **0.76** | **40.4 %** |

Questions it has no memory for are answered with nothing in 0.0 % of cases (0.0 % in the second set); the goal is 60.0 %: the weakest number on this page.

The second set was written after the settings were chosen, and no setting is chosen by sweeping it. It is not an unseen test: two decisions were taken by looking at it, and a change that lowers it fails the build like any other score, so it is a second check on the same memories. A set nobody has looked at would be a sealed one, scored only on request.

One answer stands at the edge of what the recorded vectors can tell: "what is it that I do for a living?" (A question with noise around it) is shown by 0.0004 of evidence, and with the vectors moved inside their rounding it changes in 1 of 10 replays. Its hit counts in the table above; the lock names it (`lock --edge`) instead of moving a threshold for it.

**In a small store** (a new install's size): each of 164 questions asked of a store holding only its own conversations and facts, five unrelated conversations and ten other facts. Every answer shown in 96.4 % of answerable questions, precision 67.5 %, and questions it has no memory for answered with nothing in 3.9 % of cases (it still shows unrelated facts there).

**As memory grows**: the same questions on a store whose unrelated chat is 4 times as large (4,774 memories, every conversation different): hit rate 89.7 % (against 90.0 %), MRR 0.74 (0.75), precision 43.8 % (48.7 %); questions it has no memory for answered with nothing in 5.6 % of cases (0.0 %).

### Learning facts from a conversation {#memory-facts}

Turns in English, Arabic and both, with the facts a person would expect remembered. **Quick pass**: Kazma's own rules, on every turn. **Model pass**: the model reading the turn afterwards (recorded replies). **Found**: the facts expected, kept with their whole value about the right person, whatever the model called them (a name that turns the fact around, such as "dislikes" or "former", does not count). **Same name**: of those, the share kept under a name on the list (a fact kept under two names is two facts to Kazma). **Right**: the facts kept that hold a value the turn gives, about the right person, and are not on the turn's list of what must not be kept; the name is scored apart. **Quiet**: turns with nothing to learn that taught nothing. **Rests on**: the turns behind the row; for the model pass, the askings (one asking is one model call for one turn).

| Pass | Language | Found | Same name | Right | Quiet | Rests on |
|---|---|---|---|---|---|---|
| Quick pass | English | 31.6 % | 100.0 % | 100.0 % | 100.0 % | 18 turns |
| Quick pass | Arabic | 50.0 % | 100.0 % | 100.0 % | 100.0 % | 14 turns |
| Quick pass | Mixed | 60.0 % | 100.0 % | 100.0 % | 100.0 % | 8 turns |
| Model pass (5 askings a turn) | English | 100.0 % | 86.3 % | 100.0 % | 100.0 % | 90 askings (18 turns, 5 each) |
| Model pass (5 askings a turn) | Arabic | 97.1 % | 94.1 % | 100.0 % | 100.0 % | 70 askings (14 turns, 5 each) |
| Model pass (5 askings a turn) | Mixed | 100.0 % | 72.0 % | 92.6 % | 100.0 % | 40 askings (8 turns, 5 each) |

The model pass is deepseek-flash's replies, recorded on 2026-11-20, 5 askings a turn. Asked again, a model answers a little differently, so the lock keeps each score's spread: two standard errors over the askings, at most 7.5 points for the scores in this table.
On the ten turns that give it the turn's day, the turn before and the facts held, the model pass gives a fact's start right 90.0 % in English, 100.0 % in Arabic; names the held fact it replaces 100.0 % in English, 100.0 % in Arabic; reads that a fact ended 100.0 % in English, 100.0 % in Arabic.

With Postgres and pgvector as the memory store: not yet measured on dataset version 8. CI's pgvector job measures it on every run, with pgvector's own search and with every row compared, and its numbers are printed here once they are locked from a named run.

Reproduce: `python scripts/memory_bench.py run` (add `--real` to use the real model), and `python -m pytest tests/test_memory_benchmark.py`.

## Memory in an ordinary turn {#memory-ordinary-turn}

What memory adds to every turn, and what it gets right, in English and in Arabic: a change to memory that raises an outside score while making one of these worse is not adopted. Measured on the retrieval benchmark's store (dataset version 8, 183 questions) with its recorded vectors, by `scripts/memory_product_measures.py`.

| | English (137 questions) | Arabic (46) |
|---|---|---|
| Answer shown (questions with one) | 95.7 % | 90.5 % |
| Precision of what is shown | 47.7 % | 42.8 % |
| Questions with no answer that show nothing | 0.0 % of 22 | 0.0 % of 4 |
| Tokens the recall block adds (median / 95th / largest) | 711 / 1,035 / 1,341 | 590 / 823 / 1,091 |
| The same, the store four times as large | 736 / 1,211 / 1,343 | 598 / 848 / 1,085 |

Recall took 95.9 ms (median; 102.9 ms at the 95th) on the store, 243.6 ms at four times its size; the embedding model 75.6 ms a question; Kazma's own share of the time to the first token 0.115 s (0.12 s at the 95th, `tests/e2e/test_first_token_latency.py`). Times are the development PC's. A new install's small store: 96.4 % answered, precision 67.5 %, 3.9 % of the questions with no answer show nothing.

What differs from a live turn: a private store, a fixed "now", recorded vectors.

## Memory against a public benchmark: LongMemEval {#longmemeval}

[LongMemEval](https://github.com/xiaowu0162/LongMemEval) (MIT) asks questions of a user's own chat history: about 48 conversations, the one or few that hold the answer among unrelated ones. Kazma is measured on a fixed sample of it (the question ids are in `tests/fixtures/longmemeval/sample.json`), each history written into a private memory the product's way, one memory per round, and the question asked through the real `recall()`. No facts are extracted (that would cost a model call per round).

**Finding it** (100 questions, BAAI/bge-m3, no model call): the answer is in what Kazma shows the model.

| Question | Questions | Answer shown | From the right conversation | Precision |
|---|---|---|---|---|
| Something the user said | 14 | 100.0 % | 100.0 % | 87.7 % |
| Something Kazma said | 11 | 81.8 % | 100.0 % | 95.5 % |
| A preference to apply | 6 | 100.0 % | 100.0 % | 54.4 % |
| A fact that changed | 16 | 93.3 % | 100.0 % | 92.4 % |
| When something happened | 26 | 92.0 % | 96.0 % | 77.3 % |
| Across several conversations | 27 | 87.5 % | 95.8 % | 83.9 % |
| **All questions** | **100** | **91.5 %** | **97.9 %** | **83.6 %** |

Recall took 26.0 ms (median) on these stores.

**Answering from it** (100 questions; reader deepseek-flash, judge deepseek-flash, LongMemEval's own judge prompts): the share of answers the judge called correct, from Kazma's recall as the model is shown it, from the same recalled conversations read whole (what the model gets when it opens them with `memory_read`, which this one-shot reader cannot call), from the whole history (the full-context baseline) and from the conversations holding the answer alone (the oracle).

| Question | Kazma's recall | Read whole | History cut to fit | Oracle |
|---|---|---|---|---|
| Something the user said | 64.3 % | 100.0 % | 92.9 % | 100.0 % |
| Something Kazma said | 45.5 % | 90.9 % | 90.9 % | 100.0 % |
| A preference to apply | 83.3 % | 100.0 % | 66.7 % | 100.0 % |
| A fact that changed | 37.5 % | 81.2 % | 87.5 % | 93.8 % |
| When something happened | 53.8 % | 84.6 % | 57.7 % | 96.2 % |
| Across several conversations | 29.6 % | 44.4 % | 59.3 % | 85.2 % |
| **All questions** | **47.0 %** | **77.0 %** | **72.0 %** | **94.0 %** |

The reader was sent about 1,017 tokens a question from Kazma's recall, 2,214 with its conversations read whole, 96,356 for the history cut to 440,000 characters (its oldest conversations left out) and 6,306 for the oracle. Scores from different readers are not comparable: compare the columns with each other.

**The same memories in English and Arabic**: 40 of the sample's questions and the 84 conversations holding their answers, in one memory, in English and translated into Arabic (the user's messages in Gulf Arabic, the replies in Modern Standard Arabic; translated by a model, kept outside the repository). Each question is asked in its language.

| | English | Arabic |
|---|---|---|
| Answer shown | 85.0 % | 87.5 % |
| From the right conversation | 97.5 % | 97.5 % |

### The dev split {#longmemeval-dev}

200 questions to choose settings on (`tests/fixtures/longmemeval/split.json`: the sample above and 100 more); the other 300 are never used to choose anything, and every look at them is logged.

**Finding it, against plain retrieval, at the same size** (no model): Kazma's recall beside the rounds nearest the question by bge-m3 alone (the same vectors, with none of recall's background, word coverage or gap) and by BM25, each read whole: as many as fit Kazma's own block (its median and 95th-percentile tokens), as many rounds as Kazma showed for the question, and ten. Tokens are counted as the product measures count the block.

At Kazma's own block size, bge-m3's nearest rounds show the answer's round for 70.1 % and every answering conversation for 42.3 %, against Kazma's 90.2 % and 82.0 %; with as many rounds as Kazma showed, 89.7 % and 77.3 %. Ten rounds find more (94.8 % and 91.8 %) with 4.8 times Kazma's tokens.

| Method | Memories shown | Tokens (median / 95th) | The answer's round shown | Every answering conversation shown |
|---|---|---|---|---|
| Kazma's recall | 4.47 | 1,080 / 1,317 | 90.2 % | 82.0 % |
| bge-m3, within Kazma's median block | 1.77 | 797 / 1,056 | 70.1 % | 42.3 % |
| bge-m3, within Kazma's 95th-percentile block | 2.25 | 1,053 / 1,296 | 77.3 % | 51.5 % |
| bge-m3, as many rounds as Kazma showed | 4.47 | 2,310 / 5,009 | 89.7 % | 77.3 % |
| bge-m3, ten rounds | 10.0 | 5,148 / 7,118 | 94.8 % | 91.8 % |
| BM25, within Kazma's median block | 1.59 | 745 / 1,041 | 62.4 % | 36.1 % |
| BM25, within Kazma's 95th-percentile block | 2.08 | 1,054 / 1,292 | 65.5 % | 47.9 % |
| BM25, as many rounds as Kazma showed | 4.47 | 2,473 / 5,475 | 80.9 % | 71.7 % |
| BM25, ten rounds | 10.0 | 5,599 / 7,414 | 89.2 % | 83.5 % |

Of the 129 answerable questions whose answer is a word or a phrase, its text is in the rounds Kazma shows for 45.0 %, and still in the block after the block's bounds for 30.2 %.

**The 6 questions with no answer**: Kazma shows nothing for 0.0 % of them; it shows 4.67 memories (982 tokens, median), ten rounds show ten (4,825). Kazma showed nothing for 1.0 % of all the questions.

**Answering from it**: reader qwen3.7-plus with its reasoning default; judges qwen3.8-flash and qwen3.7-plus (LongMemEval's prompts; the judges swapped on 2026-11-22: each verdict is kept with the model that gave it, and no answer was asked again). The same build answered every question twice: an answer that flips between the two runs is the noise, and no change is accepted on a difference smaller than it.

| History | Answered | Second run | Answers that flipped | Judges agree | Tokens a question |
|---|---|---|---|---|---|
| Kazma's recall | 69.0 % | 69.0 % | 2 | 97.5 % | 1,322 |
| Kazma's recall before the change judged (its blocks from the earlier code) | 47.0 % | 45.5 % | 5 | 98.0 % | 1,126 |
| Kazma's own chat turn (it reads a memory whole with memory_read) | 79.0 % | – | – | 96.5 % | 20,727 |
| The same conversations read whole | 75.0 % | 76.0 % | 4 | 98.5 % | 2,395 |
| The whole history | 91.0 % | 88.5 % | 13 | 97.5 % | 114,326 |
| Only the answering conversations (oracle) | 92.0 % | 92.5 % | 9 | 97.5 % | 6,495 |
| bge-m3's 10 nearest rounds (plain retrieval) | 85.0 % | 84.0 % | 8 | 97.5 % | 4,790 |
| BM25's 10 best rounds (plain retrieval) | 76.5 % | 75.5 % | 2 | 98.0 % | 5,196 |

**Where Kazma stands**: from its recall block alone Kazma answers 69.0 %, against 85.0 % for plain top-10 retrieval (bge-m3) at 3.6 times the tokens, and 91.0 % for the whole history. Kazma's own turn, which reads a memory whole when the block shows it in part, reaches 79.0 %. The largest gaps are multi-session questions (39.6 % against 92.5 % for the whole history; W4) and temporal-reasoning questions (60.4 % against 90.6 % for the whole history; W2).

Kazma's own turn answers through the agent's chat turn (W1c): its base prompt, the recall block a turn gets, `memory_search`, `memory_read` and `memory_list`, at most three tool rounds, asked once (1.76 model calls and 0.71 tool requests a question, 10.43 s median).

Answers cut at the reader's limit: 0; answers or verdicts the API refused: 4 (scored wrong).

**The judges, read beside**: 50 answers read by Claude (labelled model-read until the owner checks them), the 27 the two judges disagree on first. qwen3.8-flash gave the reading's verdict on 86.0 % of them, qwen3.7-plus on 60.0 %; on the disagreements 74.1 % and 25.9 %. That is why qwen3.8-flash judges first: qwen3.7-plus's wrong verdicts were mostly refusals of an answer the reading took for right (17 of its 20), typically a right answer that adds a detail the expected answer leaves out.

Reproduce: download LongMemEval into a folder of its own, then `python scripts/longmemeval_bench.py retrieve --data DIR` (and `answer`, `translate`, `parallel`; those call a model).

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
