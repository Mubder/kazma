# Memory: from good to the best measured (plan, 2026-11-21)

The owner asked (21 Nov) for a plan that makes Kazma's memory top notch, with
very high scores on every memory benchmark. This plan starts from what the
benchmarks measured on 21 Nov (live build `3cb18b586`), says what "the best"
can honestly mean, and lists the work in the order that moves the numbers
most. It continues the November memory plan
(`docs/audits/AUDIT_MEMORY_FRAMEWORK_2026-11.md`, whose parts B to H are done
and live) and its benchmark audit (`docs/audits/AUDIT_MEMORY_BENCHMARKS_2026-11.md`).

## 1. Where Kazma stands

**Finding** is strong; **showing** is weak.

| Measure | Today | Source |
|---|---|---|
| Kazma's benchmark (v8): answer among what is shown | 90.0 % (second set 86.2 %) | `docs/docs/reference/benchmarks.md` |
| Kazma's benchmark: share of what is shown that is about the question | 48.7 % | same |
| Kazma's benchmark: questions with no memory answered with nothing | 0.0 % (goal 60 %) | same |
| LongMemEval-S, 100 questions: answer among what is shown | 91.5 % | same |
| LongMemEval-S: answers right from Kazma's recall block | 47.0 % | same, reader deepseek-flash |
| LongMemEval-S: answers right from the same memories read whole | 77.0 % | same |
| LongMemEval-S: whole history / oracle (same reader) | 72.0 % / 94.0 % | same |
| LongMemEval-S, across several conversations, from recall | 29.6 % | same |
| LongMemEval-S, a fact that changed, from recall | 37.5 % | same |
| Fact extraction, model pass, found | 97 to 100 % | same |

Three findings explain most of the gap:

1. **The block cuts the answer off.** Each recalled conversation is shown as
   its first 200 characters of the question and 300 of the reply
   (`memory/limits.py`: `RECALL_SHOW_QUESTION`, `RECALL_SHOW_ANSWER`). On
   LongMemEval 44 of 51 wrong answers had the right conversation in the
   block, cut before the answer. Read whole, the same memories score 77 %,
   above the whole history (72 %).
2. **Nothing judges "is this about the question" well enough.** Recall ranks
   by meaning above the question's background plus word coverage. With the
   unrelated chat without repeats (v8), unrelated conversations on the
   question's topic clear the floor: precision 48.7 %, and no unanswerable
   question comes back empty.
3. **Questions spanning many conversations need gathering, not ranking.**
   "How many times", "what changed", "across my trips" need every relevant
   round with its date, and the block of five shows a few.

## 2. What "the best" means here

No single leaderboard exists, and outside scores depend on the reader model
(Mastra reports 84.2 % on LongMemEval-S with gpt-4o and 94.9 % with
gpt-5-mini). So every outside number Kazma publishes carries, on the same
reader: the whole-history baseline and the oracle (only the conversations
holding the answer). The oracle is the ceiling for that reader.

**Targets** (reader deepseek-flash unless said):

| Benchmark | Measure | Today | Target |
|---|---|---|---|
| LongMemEval-S (500) | answer shown (no model) | 91.5 % (100 q) | ≥ 97 % |
| LongMemEval-S (500) | answers right | 47.0 % (100 q) | ≥ 88 % (oracle 94 %), above whole history by ≥ 10 points |
| LongMemEval-S | its 30 "no answer" questions answered as such | not measured | ≥ 80 % |
| LongMemEval-M (500 sessions a question) | answers right | not measured | ≥ 80 %, where the whole history does not fit |
| Kazma's benchmark (v8) | hit rate / precision / says nothing | 90.0 / 48.7 / 0.0 % | ≥ 95 / ≥ 70 / ≥ 60 % |
| English and Arabic parallel set | answer shown, both languages | 85.0 / 87.5 % (40 q) | ≥ 95 % in each, within 2 points of each other |
| MemoryAgentBench, selective forgetting | accuracy | not measured (best published 32.5) | lead |
| GroupMemBench (shared memory, Spaces) | accuracy | not measured (best published 46.0) | lead |
| LoCoMo (published, original and audited answers) | answers right | not measured | above the whole-history baseline on the same reader |
| Poisoning (MINJA-style injection, AgentPoison-style) | attacks that succeed | not measured (98 % against others) | ≤ 5 % |
| Recall speed (this PC) | p95 per recall | 26 ms median (LongMemEval) | ≤ 1 s with a reranker |

Rules that keep the numbers honest:

* **Tune on dev, report on test.** LongMemEval's 100-question sample used so
  far is the dev split; the other 400 questions are the report split and are
  never used to choose anything. Kazma's benchmark keeps its tuning half and
  second set; the sealed set is looked at once, at the end
  (`scripts/memory_bench.py sealed --look`).
* **One change per commit**, each with its before and after on Kazma's
  benchmark (v8) and LongMemEval dev, and its cost (tokens, model calls,
  latency). A change that lowers a locked score fails the build; a trade is
  recorded with `lock --trade` and its reason.
* **No threshold moves to save one question** (the edge rule).
* Model spend is reported with each run (DeepSeek: the owner's standing
  approval).

## 3. The work, in order

Sizes: S about a day, M two to four days, L a week or more, of one builder.

### W1. Show the answer, not the first 300 characters (M)

The largest gain on LongMemEval for the least risk.

* **A budget by rank**: the top memory gets the most room, later ones less,
  inside a total block budget (start around 8,000 characters, measured from
  4,000 to 16,000 against answers right and tokens sent).
* **The stretch that matched**: in a memory longer than its room, show the
  passage around the sentences that match the question (by words and by
  meaning), with the memory's first line kept as its header, never only
  its start.
* **Neighbours**: a hit's adjacent rounds of the same conversation when they
  continue it (part F joined follow-ups; extend it to the round before and
  after).
* **The model can open a memory**: `memory_read` exists; measure an answering
  reader that may call it once, as its own column (it is how the product
  works).
* Measured on LongMemEval dev and Kazma's benchmark. Expected: answers right
  from 47 % toward 70 to 75 %.

### W2. Say nothing when nothing fits (S, then with W3)

* Choose v8's thresholds again, sweeping half the questions and checking the
  other half (queued with the memory builder).
* After W3, the floor moves to the reranker's calibrated score.
* Checked on LongMemEval's 30 "no answer" questions and a "no memory at all"
  control.

### W3. A reranker (M; decision 14)

Zep and Hindsight rank their candidates with a cross-encoder; Kazma does not.

* Candidates from today's channels (meaning, words, graph, facts, summaries),
  the top 30 or so, rescored by a local cross-encoder on this PC, no API call.
  Two candidates were downloaded on 21 Nov at pinned revisions:
  `BAAI/bge-reranker-v2-m3` (568M parameters, Apache-2.0, multilingual,
  revision `953dc6f6f85a`) for quality and
  `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (118M, Apache-2.0, trained on
  mMARCO including Arabic, revision `1427fd652930`) for speed.
* Run through ONNX Runtime (Kazma already ships it for OCR), quantized, in a
  thread, inside recall's existing deadline; past the deadline recall answers
  with today's ranking and says it is degraded, as it does now.
* Its score also judges "about the question" (W2): a calibrated floor and gap.
* Adopted only if it raises precision and keeps the hit rate on Kazma's
  benchmark and LongMemEval dev, in English and Arabic, within the latency
  budget. Expected: precision from 49 % toward 70 %, MRR up, "says nothing"
  above 60 %.

### W4. Questions across many conversations, and facts that change (M)

* **Facts as keys**: index each round with the facts extracted from it
  (LongMemEval's authors report +9.4 % from this). The deep pass already
  extracts them; the round gets a second key, no new model call.
* **Gathering**: "how many", "every time", "all the trips" gather every
  matching round with its date (list mode exists: raise its reach and show
  each item's date), sorted in time.
* **A value's history**: a fact that changed shows its earlier values with
  their dates (done for facts in part C2); extend to the conversations that
  stated them, newest first.
* **A short key per conversation**: one line saying what a conversation was
  about, written when it goes quiet, as another index key (one model call a
  conversation; measured against its cost).
* Expected: LongMemEval multi-session from 30 % and knowledge updates from
  38 % toward the read-whole column (44 % and 81 %), then past it.

### W5. Long memories found by their middle (M)

A memory is embedded by its first 4,000 characters (`EMBED_TEXT_CHARS`);
keyword search reads all of it. Add a vector per stretch of a long memory
(2,000 characters, overlapping), in a table of its own, as
`docs/plans/MEMORY_REMAINING.md` describes ("Passage vectors"). Measured on
LongMemEval, whose answers sit deep in long replies.

### W6. The answering side (S, along the way)

What the model is told about the block: prefer the newest dated value, count
across the items shown, open a memory with `memory_read` when its answer is
cut, say "I don't remember that" when the block says nothing matched.
Measured with the same reader; no score is claimed from a prompt the product
does not use.

### W7. Facts written under the right name (S)

The model pass keeps 86 % of English facts and 72 % of mixed-language facts
under Kazma's names. Extend the fact-name table (F4) and the extractor's
guidance, measured on the extraction benchmark (recording needs DeepSeek).

### W8. Scale and speed (M)

* LongMemEval-M (about 500 conversations a question; downloaded 21 Nov to
  the benchmark data folder outside the repository, revision
  `98d7416c24c7`, `longmemeval_m_cleaned.json` SHA-256 `9d79e55247...`):
  the setting where the whole history does not fit and memory has to work.
* Recall p50 and p95, tokens sent and model calls reported with every run;
  the scale bench at 10 and 100 times the store.

### W9. Measured against the world, published (L, last)

* LongMemEval-S, all 500 questions: dev (100) and report (400) apart; the
  report split run once per release, with whole-history and oracle columns.
* LongMemEval-M, its abstention questions included.
* The English and Arabic parallel set grown from 40 to 200 questions
  (translation by DeepSeek, kept outside the repository).
* MemoryAgentBench's selective-forgetting and conflict tasks, and
  GroupMemBench for shared spaces.
* Poisoning: MINJA-style injection and AgentPoison-style retrieval attacks
  run against Kazma's trust ladder and fences; the success rate published.
* LoCoMo (licence CC BY-NC 4.0, accepted by the owner; see section 5),
  scored against its original answers and the audited ones.
* Every number on the benchmarks page, generated from its result file, with
  the reader named and the baselines beside it.

## 4. Order and who

One builder on recall at a time (the files overlap): the memory session.

1. W1 and W2's re-tune (in progress).
2. W3, then W2's floor on the reranker.
3. W4.
4. W5 and W8.
5. W6 and W7 along the way.
6. W9.

Each stage ends with the full suite, the browser tests near it, a push and a
deploy, and the benchmarks page regenerated.

## 5. Decisions (the owner, 21 Nov)

1. **LoCoMo: published.** The owner accepted its licence (CC BY-NC 4.0,
   non-commercial, attribution). Downloaded 21 Nov to the benchmark data
   folder outside the repository: `locomo10.json` from
   `snap-research/locomo` at commit `3eb6f2c585f5` (SHA-256
   `79fa87e90f04...`), and the published audit of its answers
   (`dial481/locomo-audit` at `9493fb4b4af4`, same licence), which found
   6.4 % of the answers wrong. Kazma's LoCoMo score is reported against both
   the original answers and the audit's corrections, with the attribution
   the licence asks for, and no LoCoMo text in the repository (question ids
   and results only, as for LongMemEval). LoCoMo's own known weakness is said
   beside the score: sending the whole conversation to the model beats most
   memory systems on it, so the whole-history column matters most there.
2. **A second reader: deepseek-v4-pro, where it fits.** It is a second
   column beside deepseek-v4-flash on the report split and the outside
   benchmarks, after one call confirms the account serves it and its window
   holds the whole-history baseline (about 96,000 tokens a LongMemEval-S
   question). deepseek-v4-flash stays the main column and the dev reader
   (nothing is tuned on pro). Spend reported with each run.
3. **Latency (decided by Claude, the owner left it):** the reranker may add
   at most 1 s p95 to a recall on this PC, measured on LongMemEval-S stores;
   between the two rerankers, the faster one is taken when its scores are
   within 1 point of the larger one's. A recall past its deadline answers
   with today's ranking and says it is degraded, as it does now.
