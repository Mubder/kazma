# Memory: from good to the best measured (plan, 2026-11-21)

The owner asked (21 Nov) for a plan that makes Kazma's memory top notch, with
very high scores on every memory benchmark, and set its first rule: **the
work must make memory better for the people who use Kazma, not only raise
scores**, so that Kazma can claim the best memory framework of any agent
available today and have the product back the claim.

This plan was written by the main session, reviewed by a Fable session (a
second opinion, read-only; its review is outside the repository), and
revised until both agreed. It starts from what was measured on 21 Nov (live
build `3cb18b586`), continues the November memory plan
(`docs/audits/AUDIT_MEMORY_FRAMEWORK_2026-11.md`, parts B to H done and live)
and its benchmark audit (`docs/audits/AUDIT_MEMORY_BENCHMARKS_2026-11.md`).

## 1. Where Kazma stands

**Finding** is strong; **showing** is weak; and part of what was measured
does not yet measure the product.

| Measure | Today | Source |
|---|---|---|
| Kazma's benchmark (v8): answer among what is shown | 90.0 % (second set 86.2 %) | `docs/docs/reference/benchmarks.md` |
| Kazma's benchmark: share of what is shown that is about the question | 48.7 % | same |
| Kazma's benchmark: questions with no memory answered with nothing | 0.0 % (goal 60 %) | same |
| LongMemEval-S, 100 questions: answer among what is shown | 91.5 % | same |
| LongMemEval-S: answers right from Kazma's recall block | 47.0 % | same, reader deepseek-flash |
| LongMemEval-S: the same memories read whole | 77.0 % | same |
| LongMemEval-S: oracle (only the answer sessions), same reader | 94.0 % | same |

What the review measured from the result files and the data (counts only):

* **The "whole history" column is not full context.** Every history was cut
  to fit (`FULL_MAX_CHARS = 440_000` in `scripts/longmemeval_bench.py`;
  history median about 517,000 characters), and in 33 of the 100 questions
  the cut removed a session holding the answer. Its 72 % is "history cut to
  fit"; the true full-context score is unmeasured.
* **100 questions is a wide instrument**: 47 % is 38 to 57 at 95 %; per
  question type (6 to 27 questions) the numbers say little. Only 6 of the 30
  "no answer" questions are in the sample.
* **The run left half of Kazma out**: no facts extracted at ingestion,
  hidden reasoning off (the product ships `low`), LongMemEval's prompt
  instead of Kazma's, no memory tools, and the reader judging itself.
* **The block is capped twice**: 200 + 300 characters a conversation
  (`memory/limits.py`, `RECALL_SHOW_*`) and 1,500 tokens in all
  (`format_recall_block`, `max_tokens`). 44 of 51 wrong answers had the right
  conversation in the block, cut before the answer.
* **Of the 23 questions still wrong when read whole**, 17 are right with the
  oracle and 15 need several conversations.

## 2. The rules

### 2.1 The product first (the owner's rule)

1. **One path.** Every benchmark runs the code and settings Kazma ships: the
   recall block as configured, Kazma's prompt and recall rules, the shipped
   reasoning level, the memory tools (`memory_read`, `memory_list`,
   `memory_search`), facts from the deep pass. No benchmark-only flag,
   prompt or code path. The harness's unavoidable differences are listed on
   the benchmarks page beside the numbers: a private store, a fixed "now",
   recorded vectors where stated, a cap on tool rounds.
2. **Product measures gate every change**, before any outside score counts:
   Kazma's own benchmark in English and Arabic, the tokens a change adds to an
   ordinary turn, time to the first token, and a new install's small store.
   A change that raises an outside score while worsening any of these is not
   adopted.
3. **A real-use suite in the browser**, English and Arabic, reached by the
   sidebar: facts told across chats, changed, forgotten, asked in other
   words, and asked about what was never said. It passes at every stage.
4. **Live signals are an alarm, not a gate.** After each deploy the recall
   records (part H: was a memory used, was it corrected) are compared with
   the week before. One person's uncontrolled traffic can show a regression;
   it cannot prove a gain.

### 2.2 Honest numbers

* **Dev and report apart.** LongMemEval-S's 500 questions: 200 to tune on
  (dev, including the 100 used so far), 300 to report on, never used to
  choose anything. Every look at the report split is logged (date, reason,
  commit), as the sealed set's looks are. Kazma's benchmark keeps its
  tuning half and second set; the sealed set is looked at once, at the end.
* **Noise first.** The unchanged build runs twice on dev and the answers
  that flip are counted. No change is accepted on a difference smaller than
  that.
* **The judge is checked.** A second judge (deepseek-v4-pro) grades every
  dev answer; agreement is published. 50 answers, drawn first where the two
  judges disagree and then at random, are read by Claude and labelled
  "model-read" until the owner has checked them by hand.
* **Free proxies, no model**: "the gold answer's text is in the block" and
  "every answer session is shown", so block settings are tuned without a
  reader.
* **Baselines beside every number, on the same reader**: true full context
  where it fits the reader's window (else named "history cut to fit"), the
  oracle, a plain retrieval baseline (bge-m3's top 10 rounds read whole, and
  BM25), and reasoning off / low / high as a row.
* **One change per commit**, with its before and after on the product
  measures and LongMemEval dev, and its cost (tokens, model calls, time).
  A change that lowers a locked score fails the build; a trade is recorded
  with `lock --trade` and its reason. No threshold moves to save one
  question (the edge rule).
* **Transfer runs, nothing tuned.** LongMemEval-M, LoCoMo and BEAM run with
  the settings chosen on dev and Kazma's benchmark, declared before the run.
* Model spend is reported with each run (DeepSeek, any model, any budget the
  work needs: the owner's standing approval).

## 3. Targets

Reader deepseek-v4-flash (the shipped reasoning level) unless said; every
number with its n.

| Benchmark | Measure | Today | Target |
|---|---|---|---|
| Kazma's benchmark (v8) | hit rate / precision / says nothing | 90.0 / 48.7 / 0.0 % | ≥ 95 / ≥ 70 / ≥ 60 % |
| An ordinary turn | tokens added by memory; time to first token | measured in W0 | no worse than today unless the evidence is strong |
| LongMemEval-S, report split (300) | answers right | 47 % (100, block) | gate 80 %, goal 85 %: at least 90 % of the oracle; stretch 88 % |
| LongMemEval-S | against true full context | not measured | not below it, at under 5 % of its tokens |
| LongMemEval-S | answer shown / every answer session shown | 91.5 % / 70 % (multi-session) | ≥ 97 % / ≥ 90 % |
| LongMemEval-S | "no answer" questions answered as such | 4 of 6 | ≥ 80 % (reported with its n, 24 on report) |
| LongMemEval-M | answers right | not measured | within 8 points of the S score on the same reader |
| LoCoMo (published; original and audited answers) | answers right | not measured | above the plain retrieval baseline; reported beside full context |
| BEAM (1M tier, if its licence allows) | answers right | not measured | reported with baselines |
| English and Arabic parallel set (200) | answer shown | 85.0 / 87.5 % (40) | ≥ 95 % each, within 3 points; a native reader checks a sample of the translation |
| MemoryAgentBench, GroupMemBench | accuracy | not measured | published with the same-reader baseline; a lead claimed only against a same-reader number |
| Poisoning (after W1) | stored as a user fact / shown / acted on | not measured | each rate published; ≤ 5 % acted on |
| Recall speed (with a reranker) | added time | — | ≤ 150 ms p50, ≤ 400 ms p95 on this PC, anytime |

## 4. The work, in order

Sizes: S about a day, M two to four days, L a week or more, of one builder.

### W0. Fix the instrument (M) — before any change is accepted

The uncut full-context baseline (or the column renamed), the noise run, dev
200 / report 300 with the look-log, the second judge and the 50 model-read
answers, the free proxies, the plain retrieval baselines, and the product
measures' own baseline (tokens per ordinary turn, time to first token, small
store), plus the browser real-use suite. The R9 v8 re-tune and the W1 work
started on 21 Nov wait for this.

*Measured, 22 Nov* (dev 200; reader qwen3.7-plus at the provider's default
reasoning, what Kazma sends Qwen; judge qwen3.8-flash, second judge
qwen3.7-plus; the owner moved the memory work from DeepSeek to Qwen that
day, and the partial DeepSeek answers are kept aside, labelled, as a record
only). The runs were judged first by qwen3.7-plus; the model-read answers
below found qwen3.8-flash the stronger judge, so the main session swapped
them the same day, from the cached verdicts (each verdict now kept with the
model that gave it, `rejudge`; the noise run, which had no second judge,
got qwen3.8-flash's verdicts on its answers, no answer asked again).
Answered, the main run and its noise run (the answers that flip): Kazma's
block 0.69 / 0.69 (2); the block before W1a 0.47 / 0.455 (5); the same
rounds read whole 0.75 / 0.76 (4); the whole history 0.91 / 0.885 (13;
three histories answered HTTP 400 in both); the oracle 0.92 / 0.925 (9);
bge-m3's ten rounds 0.85 / 0.84 (8); BM25's 0.765 / 0.755 (2). With
qwen3.7-plus first they read 0.675, 0.46, 0.755, 0.895, 0.905, 0.835 and
0.745. Judges agree on 97.8 % of 1,400 pairs. Tokens: prompt 27.8M and 27.5M,
completion 1.04M and 1.05M (0.93M and 0.94M reasoning); Qwen served 1 % and
0.1 % of the prompt tokens from its cache (each history is its question's
own). Results: `tests/fixtures/longmemeval/answers_dev.json`,
`answers_dev_noise.json`, `noise_dev.json`; the page prints them. The
largest gaps to the whole history: multi-session 0.40 against 0.92 (W4's),
temporal 0.60 against 0.91 (W2's). Said plainly (the page says it too): from
its block alone Kazma answers 0.69, against 0.85 for plain top-10
retrieval at 3.6 times the tokens and 0.91 for the whole history; its own
turn reaches 0.79 (W1c). The 50 answers read beside the judges
(`model_read.json`, Claude's reading, labelled model-read until the owner
checks them; the 27 the judges disagree on first): qwen3.8-flash gave the
reading's verdict on 86 % of them, qwen3.7-plus on 60 %; on the
disagreements 74 % and 26 %. qwen3.7-plus says no to a right answer that
adds a detail ("a yellow dress and a pair of earrings") or names the missing
information and then estimates (17 of its 20 wrong verdicts are such
refusals); the disagreements are 2 % of the 1,400 pairs, so the swap moved
a score by up to two and a half points. W0 is complete.

### W1a. Show the answer (M)

A budget by rank (the top conversation shown whole when it fits, later ones
less) inside a total block budget, `format_recall_block`'s 1,500-token cap
moved with it, and a conversation's neighbouring rounds when they continue
it. Tuned on the free proxies and dev, gated on the tokens it adds to an
ordinary turn: the block stays small when the evidence is weak.

*State, 22 Nov (built on the side branch, not accepted).* Room by rank as
the bounds proposal shaped it: the best conversation up to 1,500
characters, the others 500, each side cut around the stretch holding the
question's words, the others giving way first (to 250) and then the best
(to 500) when the 1,500-token budget would leave a line out; the cap stays.
A side whose stretch is deep inside it shows its opening sentence first (up
to 120 characters), unless the stretch alone would begin inside that
opening. Neighbouring rounds are not added (recall's joined follow-ups keep
their own line). With and without it on one commit, no model call: on dev
the answer's text is in the block for 0.442 of the worded questions (0.302
without; 0.450 in the rounds shown), 19 gained and none lost; the block's
tokens p50 1,075 -> 1,372 and p95 1,317 -> 1,894 there; an ordinary turn's
block, Kazma's benchmark EN/AR, the small store and the speakers set
unchanged. Where memories are as long as live's (the product measures'
`long` store, live's episode lengths: answers a median 1,351 characters,
4,088 at the 95th) an ordinary turn's block grows from 872 / 1,352 tokens
(p50 / p95; 1,644 the largest) to 1,146 / 1,659 (1,922): about 275 tokens
a turn on the median, 310 at the 95th. The cap bounded the lines'
characters (a quarter of them counted as tokens), not the block: the rules,
the fence and Arabic's shorter tokens were outside it, with or without W1a.
Fixed 22 Nov (a bug from before W1a): the cap is the whole block's, counted
as the context trim counts. Then (the same review) room by rank goes to hits
recall rates strong only; a "possibly related" one keeps the fixed bounds.
With and without W1a on one commit, split by the question's top hit (the
long store; p50 / p95 / mean tokens):

| Top hit | Questions | With W1a | Without | Growth (p50) |
|---|---|---|---|---|
| Strong | 129 | 1,133 / 1,486 / 1,109 | 869 / 1,348 / 904 | +264 |
| Possibly related | 28 | 881 / 1,403 / 885 | 881 / 1,403 / 875 | 0 |
| No answer | 26 | 1,027 / 1,494 / 1,018 | 892 / 1,352 / 915 | +135 |
| All | 183 | 1,073 / 1,484 / 1,062 | 872 / 1,350 / 901 | +201 |

The growth sits in the strong group; the no-answer group grows because recall
still rates a wrong memory strong for a question memory cannot answer (item 5
of section 7, W3 + W2's to fix). On dev: the answer's text in the block for
0.434 of the worded questions (17 gained, none lost against the fixed bounds;
room for every hit had 0.442, one more answer held by a possibly related
hit), tokens p50 1,080 -> 1,337 and p95 1,317 -> 1,489. On the long store the block is then 1,146 /
1,494 tokens (1,499 the largest) with W1a and 872 / 1,350 (1,497) without;
on dev the answer's text in the block is unchanged (0.442 with W1a, 0.302
without), the largest block 1,946 -> 1,498 with W1a and 1,620 -> 1,424
without. The opening was chosen against the stretch alone on dev: one
answer gained (a name in a memory's first sentence), none lost, 8 tokens
a question on average; a plain "opening first" rule tied (one gained, one
lost, the opening spending room the stretch nearly showed), and the rule
that settles it was set after reading that one question. Accepted only on W0's end-to-end dev answering, its noise run,
the second judge and the model-read answers, all waiting for DeepSeek
credit.

*Accepted, 22 Nov* on dev, with and without W1a on one reader and judge
(the before-block built by 064dfec3b, `longmemeval_blocks.py`): answered
0.47 -> 0.69 in the main run (+51 / -7 questions) and 0.455 -> 0.69 in the
noise run (+54 / -7), +50 / -7 by the second judge (qwen3.7-plus); 51 gains
and 5 losses repeat in both runs, against 2 (W1a's block) and 5 (the old
block) answers that flip between two runs of one build. The real-use suite, two runs each
side on qwen3.7-plus: 15/18 and 17/18 before, 16/18 and 18/18 with W1a: no
regression, too noisy to credit (every miss an Arabic step; finding 10).

### W1c. The product path as the only path (M)

The benchmark answers through Kazma's real turn: base prompt, recall rules,
facts, summaries, the memory tools (`memory_read` to open a cut memory), the
shipped reasoning level, at most three tool rounds, with tokens, calls and
time reported. This is how searching again arrives without a model in the
recall path (decision 14 stays).

State, 22 Nov: built and tested with a stand-in model, not yet run (it runs
with W0's credit runs). `longmemeval_bench.py answer --conditions kazma_turn`
asks each question through the supervisor graph (`scripts/longmemeval_turn.py`):
the agent's own base prompt with the question's date
(`cultural_context_enrichment.note_for_day`, the one format), recall on a
private store of the question's history at the question's time, the block as
a turn sends it, `memory_search`, `memory_read` and `memory_list` only, the
run's `--reasoning` as `agent.chat_reasoning` (low, live's; the shipped
default sends nothing, which on DeepSeek is "high"), at most three tool rounds
(the turn's own iteration budget refuses a fourth), `persist=False`. Each
answer is cached by what it was asked from (the base prompt, question, date,
recall block, reader) and judged like the others; the result has its calls,
tool requests, tokens, the cache's share, seconds, and whether the turn's
block was the harness's. The reader is Kazma's own DeepSeek client, so its
temperature is the product's (ignored by DeepSeek while it reasons). Turns
run one at a time. Left for a later piece: facts and summaries in the store
(the harness seeds conversations only, so the turn's block has neither).

Run 22 Nov on dev (qwen3.7-plus, the provider's default reasoning, one run):
0.79 answered (0.765 by qwen3.7-plus), ten points over the block alone
(0.69), from its `memory_read` / `memory_list` calls; 1.76 model calls and
0.71 tool requests a
question, 20,727 prompt tokens mean (59 % from Qwen's cache: the base prompt
repeats), 10.4 s median; one call refused (permanent). Judges agree on 96.5 %.
The harness reported the turn's block equal to its own for 92 %: it read the
first model call, and on a gray-zone request the intent classifier's
refinement (`agent/intent/classify._refine_acts_llm`) asks first, with the
user's message alone. Read from the call that holds the recall note (fixed
the same day), the turn saw the harness's block for 199 of 200; the one left
is a product finding (section 7, item 11).

Run again 23 Nov on the product of that day (d1d87f26b: findings 11 to 17
in since), twice, each run asking every question afresh (judges
qwen3.8-flash, then qwen3.7-plus): 0.815 and 0.79 answered (0.80 and 0.785
by the second judge), against 0.79 on 22 Nov. 15 answers flip between the
two runs of the same code, against 2 for the block read alone: the turn's
own model calls and tool rounds make it the noisier path, and a change
measured through it needs a difference beyond that. 6 questions right in
both of the day's runs were wrong on 22 Nov, 3 the other way: inside the
noise. The turn saw the harness's block for all 200 (finding 12's fix: the
classifier no longer asks first on Qwen); 1.74 model calls and 0.70 tool
requests a question, 20,908 prompt tokens mean (61 % from Qwen's cache),
10.8 s median; no call refused. Judges agree on 97.5 % and 96.5 %. Spend:
4.30M and 4.29M prompt tokens (2.61M and 2.53M cached), 0.13M and 0.14M
completion (95K and 98K reasoning). The harness now tries a dropped
connection again (a judge call's "Server disconnected" ended the second
run's judging; its turns were cached and the rerun asked only the missing
verdicts). Results: `docs/audits/evidence/memory_w1c/` (ids and scores
only).

### W5 + W1b. One passage unit (M)

A passage of about 256 tokens per stretch of a long memory, in a table of
its own (`docs/plans/MEMORY_REMAINING.md`, "Passage vectors"), each naming
its memory so a forget follows it. It serves three things built once: the
stretch that matched shown in the block (W1b), the reranker's input (W3),
and meaning search over a long memory's middle (W5).

**Built 22 Nov (`memory/passages.py`), behind a switch until accepted**
(`passages.in_use()`, off). The main session's choices: coverage (a),
counting max().

- *What gets passages.* Only what the memory's own vector misses. That
  vector is made from the first 4,000 characters of the summary, else the
  question, else the answer (`episode_text.embed_text`), so every other side
  gets passages (the answer, as a rule) and the vector's own side gets them
  past its first 4,000 characters. A passage is about 256 tokens
  (`limits.PASSAGE_TOKENS`, counted as the context trim counts, Arabic at
  about 2.5 characters a token) of whole sentences, about 32 tokens of whole
  sentences overlapping the one before (`PASSAGE_OVERLAP_TOKENS`); a
  sentence longer than a passage is cut at spaces. A passage holds no text:
  its memory's id and space, the side, where it stands in that side, its
  vector and the model that made it (`episode_passages`);
  `episode_passage_state` records which memories the current model has
  filled.
- *What a forget takes.* Triggers on `episodes` delete a memory's passages
  and its fill record when its row is deleted, when it becomes `forgotten`,
  when its question, answer or summary changes (the repair writes them again
  from the new text) and when it moves to another space. Every forget path
  goes through one of those (a memory's, a turn's or a chat's forget, a
  rewind, a forget by mention, a space's erase), so none needs a call of its
  own. Passages are not exported (they are rebuilt from the text), the
  Postgres mirror and a remote vector index hold none (recall reads them
  from this install's own database on both paths), and the memory store
  registry declares both tables.
- *What the triggers cost with the switch off.* The triggers are on every
  install; the tables are empty. An insert (every turn written) fires none;
  a tier move to anything but `forgotten` and an access bump fire nothing
  (the trigger's condition, or a column it does not watch); a change of a
  memory's text, a forget and a delete run two deletes by key on the empty
  tables. Measured on this PC (2,000 statements each, six alternated rounds
  with and without the triggers, medians, 22 Nov): an insert, a tier move,
  an access bump and a delete within noise (under 1 µs each way); a text
  change 6 µs and a forget 3 µs more, on statements of 25-31 µs.
- *How older memories get theirs.* The vector repair sweep
  (`reembed.run_vector_repair_pass`, every 15 minutes) ends with
  `passages.fill_passages`: up to 200 recallable memories a pass, newest
  first, about 20 seconds, those with no fill record of the current model; a
  model switch makes every memory due again. A memory's vectors are made with
  no lock held and no transaction open, then written in one short
  transaction that checks the memory still holds the text they were cut from
  (a forget or an edit in between wins). A memory the writer stores
  (`dual_write.mirror_episode`) gets its passages right after its own vector.
  The benchmark's and the product measures' stores fill theirs at seeding
  (`benchmark.fill_passages_whole`, which refuses a store missing any).
- *Counting.* A memory's meaning similarity is the better of its vector and
  its best passage (`recall._with_passages`), before the background and the
  evidence rules, on the local and the Postgres-primary path alike; a memory
  only a passage found enters the dense pool as any other. When a passage
  won, the block shows that stretch (`episode_text.display_at(anchor=)`, W1b)
  instead of the stretch around the question's words.
- *Measured* with and without on the same commit (`memory_threshold_sweep.py
  sweep|judge --passages`, `memory_product_measures.py measure --passages`;
  the passages' vectors recorded by `memory_bench.py passage-vectors`: 5,457
  texts, bge-m3; evidence in `docs/audits/evidence/memory_passages/`).

  **Not accepted: max() costs answers and precision** (22 Nov; the main
  session's fallback rule). At today's thresholds:

  | store | answers lost / gained | precision off → on |
  |---|---|---|
  | the store, first set | none | 0.4872 → 0.4818 |
  | the store, second set | none | 0.4042 → 0.4417 |
  | four times its chat, first set | q107 lost | 0.4379 → 0.4337 |
  | four times its chat, second set | h009 gained | 0.3641 → 0.3874 |
  | small stores | none | 0.6752 → 0.6747 |
  | the speakers' space | q37 lost | 0.6078 → 0.5922 |

  Both losses have one cause. A memory's meaning becomes the better of its
  vector and its passages, so the dense pool's similarities rise and the
  question's background (dense ranks 4-15) with them; a memory whose answer
  is not what matched keeps its own similarity and loses lift (q107's gold
  turn 0.044 → 0.026, q37's 0.051 → 0.028, under the 0.04 floor). Taking the
  background from the memories' own vectors instead keeps every answer
  (h009 gained) but lowers precision on the store (0.4872 → 0.4678), at four
  times its chat (0.4379 → 0.4233), in small stores (0.6752 → 0.6715) and in
  the speakers' space (0.6078 → 0.5922): every passage that clears the floor
  is one more memory shown. The block's tokens hold (store p50 675 → 674,
  p95 1,030 → 989; four times the chat p50 710 → 701, p95 1,111 → 1,198;
  live's lengths p50 1,073 → 1,059, p95 1,484 → 1,486); recall takes 20 ms
  more on the store (p50 53 → 73 ms) and 75 ms more at four times its chat
  (133 → 208 ms), every passage compared exactly. The sweep under the plan's
  rule (`--by sets`, passages on, against today's product without them,
  `sweep_first_set.json`) chose nothing at its first stage: of the 57 turn
  floors and gaps, the only ones that keep every first-set answer on all four
  stores have the floor at 0.02, the grid's edge (rule 5), and they lower
  precision further (the store 0.4872 → 0.4584, four times its chat 0.4379 →
  0.4117, the speakers' space 0.6255 → 0.6000 on the first set). Nothing was
  judged or logged (`log_choice` records only a judged choice). The table,
  the writer, the forget triggers, the repair and W1b's stretch stay, off:
  the main session's fallback is a lane of their own (a passage hit judged
  on its own evidence beside the memories' vectors, not mixed into them).
  The passages' vectors (4.4 MB) are not committed;
  `memory_bench.py passage-vectors` records them again (about 14 minutes).

### W3 + W2. Rank by relevance, and say nothing when nothing fits (M)

A local cross-encoder rescoring the top 20 passages, anytime inside the
latency budget: rescore in rank order until the budget ends, keep today's
order for the rest, say it was partial. Shipping candidate
`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (118M, Apache-2.0, revision
`1427fd652930`); `BAAI/bge-reranker-v2-m3` (568M, Apache-2.0, revision
`953dc6f6f85a`) is the quality reference; both downloaded 21 Nov. Its
calibrated score becomes the floor for "about the question" (W2), tuned on
Kazma's benchmark halves only; Gulf Arabic measured on Kazma's own Arabic
questions. bge-m3's own sparse weights are measured as an option for the word
signal. **Accepted on Kazma's benchmark and the product measures, not on
LongMemEval**, where precision is already 83.6 % and it is expected to move
little: a flat LongMemEval number is not a failure.

These come before W4 under the owner's rule: precision and saying nothing
touch every turn of every user; questions across many conversations are
rarer.

**Built 22 Nov (`memory/reranker.py`, `recall._by_relevance`), behind a
switch until accepted** (`reranker.in_use()`, off). The main session's
decisions: no cosine lane of its own for passages; they become the
reranker's candidate source, and the cross-encoder's calibrated score
decides what is shown.

- *Candidates.* Today's evidence ranks every candidate as before; the
  reranker reads the best 20 by it (40 for a question asking for every
  memory of a kind) and every memory whose best passage is among the
  question's 20 nearest passages, whatever its evidence. Passages no longer
  lift a memory's meaning (`_with_passages` is gone).
- *What it reads.* A memory's start (the text the block shows, both sides)
  and, for a memory a passage brought, that passage's stretch cut from the
  memory's text by its offsets: two pairs of the question and up to 256
  tokens, the better one counting. A memory kept by its passage is shown at
  that stretch (W1b).
- *Calibrated (W2).* The probability that a memory answers is a logistic fit
  of recall's evidence and the model's score together, per kind, on the
  benchmark's first set (`scripts/memory_rerank_calibrate.py`): the model's
  score alone ranks no better than the evidence (turns AUC 0.878 against
  0.935 on the first set), the two together better (0.944; on the second
  set, never fitted on, 0.949 against 0.918). Weekly summaries 0.956 / 0.971
  against 0.937 / 0.964. Facts 0.936 / 0.946 against 0.938 / 0.945: no
  better, so facts keep today's rule and cost no reading. A memory is shown
  at or above the floor and within the gap of the best read.
- *Anytime.* Read a few pairs at a time in today's order until the
  recall's budget (`reranker.BUDGET_S`, all kinds together) ends; what was
  not read keeps today's rule and is ranked after what was; the recall says
  it was partial (`RecallResult.rerank`). No model on disk: today's rule,
  said once, tried again after ten minutes.
- *Measured* with and without on the same commit: the recorded scores
  (`memory_bench.py rerank-scores`: 40,807 pairs of every store, 688 s on
  this PC; `--reference` for the reference model: 35,415 pairs, 11,362 s)
  replayed by `memory_threshold_sweep.py sweep|judge --rerank
  [--reference]`, `memory_product_measures.py measure --rerank
  [--real-reranker] [--reference]` and `longmemeval_bench.py retrieve
  --rerank`, `longmemeval_blocks.py --rerank`. Evidence in
  `docs/audits/evidence/memory_rerank/`. The recorded scores (2 MB) and the
  passages' vectors (4.4 MB) are not committed: those commands record them
  again.

  **Not accepted (22-23 Nov): neither model passes the plan's rule, and the
  reference does not fit the budget.** The sweep (`--by sets`, the turns'
  floor and gap over the first set, against today's product) found no
  configuration that keeps every answer on all four stores with its grid
  neighbours (rules 1 and 5), for either model; it stops there
  (`sweep_shipping_first_set.json`, `sweep_reference_first_set.json`). The
  lowest floor, 0.03, is the grid's edge; first set, precision (answers
  lost):

  | model, floor / gap | the store | four times its chat | small stores | speakers |
  |---|---|---|---|---|
  | today (no reranker) | 0.4872 | 0.4379 | 0.6722 | 0.6255 |
  | shipping, 0.03 / 0.5 | 0.5159 | 0.4718 (q204) | 0.6889 | 0.6118 |
  | shipping, 0.12 / 0.5 | 0.5929 (4) | 0.5739 (3) | 0.6813 (4) | 0.7000 |
  | reference, 0.03 / 0.5 | 0.5535 (q1307) | 0.5094 | 0.6907 (q1307) | 0.6569 |
  | reference, 0.06 / 0.5 | 0.5859 (q1307) | 0.5449 (q1005) | 0.6967 (q1307) | 0.6765 |
  | reference, 0.12 / 0.5 | 0.5960 (2) | 0.5747 (1) | 0.6855 (3) | 0.7196 |

  Unanswerable questions answered with nothing, 2 today: the shipping
  model 2 at 0.03, 10 at 0.06, 13 at 0.12; the reference 7, 9 and 12. Precision stops near 0.60 on the store whatever is
  lost: facts, not reranked, keep their noise (107 of the 332 non-answers
  shown on the first set). Why answers are lost: the shipping model reads
  "Who looks after my teeth?" beside "My dentist is Dr. Marta Pires" at
  -3.5 (calibrated 0.057), and ranks Gulf Arabic turns about car batteries
  above the English answer about a laptop battery (q204); both models lose
  q1307, "وش قلت لك عن مايا في يوليو؟" ("what did I tell you about Maya in
  July?"), whose answering turn never says July: the reranker reads the
  text, not when it was said. Speed on this PC's CPU (torch, fp32, at most
  256 tokens a pair, 4 threads, nearly quiet under the timing flag;
  `latency_cpu.json`): the shipping model reads 20 short memories in 85 ms
  and 20 long ones in 610 ms (33 ms each); the reference model 1,048 ms and
  6,434 ms (335 ms each), several times the budget (150 ms p50) even for
  short memories (during a full suite: 105 / 641 ms and 1,141 / 7,282 ms).
  Decision 4 ships the reference only if it fits; it does not. Nothing was judged or
  logged; LongMemEval was not answered (the reranker's candidates need
  every dev history's passages embedded: 83,831 texts the harness's cache
  lacked, about 6,000 an hour on this CPU beside the other measurements;
  30,112 are in the cache, the rest wait for a run that needs them).

  **Measured and not adopted** (the main session, 23 Nov). The reranker
  stays off, and no faster runtime is tried now: it loses an answer at every
  floor with either model, and an int8 or ONNX runtime might bring the
  shipping model under the budget but would not keep the answers it loses.
  `memory/reranker.py`, its calibration and its scripts stay as they are, off,
  so a later attempt starts from the recorded scores and the sweep. What
  would reopen it: a model or setting that loses no answer on all four of
  Kazma's stores (the store, four times its chat, the small stores and the
  speakers' space, both sets, the plan's rules 1 and 5) **and** fits the
  latency budget on this CPU (150 ms p50, 400 ms p95 added to a recall).
  Until then W2's "say nothing when nothing fits" stays today's evidence
  floor, and item 5 of section 7 (a wrong memory rated strong for a question
  memory cannot answer) stays open.

### W4. Questions across many conversations (M)

Gathering ("how many", "every time", "all my trips") every matching round
with its date, sorted in time; dated event lines per conversation (a few
lines each, the date said and the date meant, each naming its source turns
so a forget follows); a changed value's history with the conversations that
stated it. Target: every answer session shown ≥ 90 %.

*A changed value names the conversations that said it, built 23 Nov.* A fact
line with the value it replaced ("user lives in London; before that: Paris
(Jun 2026 to Oct 2026)") now follows each value with the conversation that
stated it, "(memory e_...)", as every recalled conversation line does, so the
model can open it with `memory_read` ("why did I move?"): the fact's source
turn, in the asker's space, a turn recall could still show
(`recall._stated_in`); a fact whose value never changed names none. No model;
Kazma's benchmark's scores unchanged (an id in a line is not a memory shown);
an ordinary turn's block p50 675 -> 676 tokens (Arabic 590 -> 592), the long
store's 1,073 -> 1,080. Not measurable on LongMemEval: its histories hold no
facts until W6. Gate: `tests/test_memory_changed_value_sources.py`.

*Gathering, measured and not adopted (23 Nov).* A question asking how many
there are of a kind, or their total ("how many", "in total", "altogether",
"كم مرة", "عدد", "المجموع", "الإجمالي"), kept every conversation more about
it than the question's background (evidence above zero) once one cleared
the ordinary floor; with none, nothing. Why it was tried: on dev, 71 answer
conversations of the 125 questions with more than one were not shown; 60
stood under the cut and 9 past the shown limit, their best round 3rd to 45th
by evidence, most between 0.01 and 0.04 against the floor of 0.04. Each
names one instance ("we ordered from Uber Eats", "a pottery exhibition")
where the question names the kind ("food delivery services", "art-related
events"). Tried first for every request for every memory, lists too:
Kazma's list questions gained no answer and lost precision (0.507 ->
0.385), and LongMemEval's gains came from counts and totals alone, so lists
kept the floor. With and without on one commit (d1d87f26b and the change):

| | today | gathering |
|---|---|---|
| dev: every answer conversation shown | 0.820 | 0.876 (+12, -0) |
| multi-session / temporal | 0.78 / 0.615 | 0.94 / 0.673 |
| the answer's text in the block | 0.434 | 0.434 |
| precision; memories shown | 0.828; 4.5 | 0.786; 6.1 |
| block tokens p50 / p95 | 1,337 / 1,489 | 1,415 / 1,487 |
| answered, main run (second judge) | 0.69 (0.675) | 0.715 (0.70), +10 / -5 |
| answered, noise run (second judge) | 0.69 (0.675) | 0.69 (0.68), +7 / -7 |
| multi-session answered, main / noise | 0.396 / 0.396 | 0.453 / 0.396 |
| answers that flip between the two runs | 2 | 11 |
| Kazma's benchmark: answers, MRR | | unchanged on all four stores |
| Kazma's precision (list), small store, scale | 0.4872 (0.5071), 0.6752, 0.4379 | 0.4868 (0.5002), 0.6743, 0.4378 |

Not adopted: 5 gains and 3 losses repeat in both runs (two of the losses
are unanswerable count questions answered instead of left), against 11
answers that flip between two runs of the same gathering block. The right
conversations are shown, but the answer's text in the block does not move:
the gathered lines are "possibly related" ones at fixed bounds, cut from
their start, and the instance a count needs is usually further in. A variant
showing a count's conversations oldest first answered 0.675 (multi-session
0.32): it takes the lead room from the best line. Ordinary turns did not
move (Kazma's store p50 675, p95 1,030). Spend: 1.82M prompt and 0.65M
completion tokens (0.58M reasoning), 7.4K of the prompt from Qwen's cache.
Evidence: `docs/audits/evidence/memory_gathering/gathering.json`. What would
reopen it: lines that hold the instance itself, the dated event lines below.
The code is not in the repository (a change to recall's ranking has no
place behind a switch nobody turns on): its patch is kept with the session's
hand-over notes, and the harness's own commands measure it again
(`longmemeval_bench.py retrieve --out`, `scripts/longmemeval_blocks.py`,
`answer --blocks`).

*Dated event lines, the pilot (23 Nov).* The deep pass now keeps what a turn
tells the user did -- went, attended, bought with its price, received, met,
started, finished -- as a fact of its own, with `"on"`, the day it happened
worked out from the turn's day; recall shows it "[on 30 May 2026] user
attended a pottery exhibition at the craft centre". The prompt change is
declared in the extraction lock and waits for the pilot before any
recording. The pilot (`scripts/longmemeval_facts_pilot.py`): 50 dev
questions, a quarter by type with multi-session (16) and temporal reasoning
(16) weighted in (`tests/fixtures/longmemeval/facts_pilot.json`), every
round of their histories (12,322) written as a chat writes it -- the
episode, the quick pass, then the product's own deep-pass task, unchanged,
on qwen3.8-flash at its default reasoning, as the product sends it. Qwen's
explicit context cache holds the deep pass's system prompt
(`llm_provider._with_qwen_cache_marker`, for every Model Studio call). Then
one reader and the same judges, twice (main and noise): today's block
(`kazma`, no facts, as every run so far), the block with the facts the deep
pass wrote (`facts_events`), and the same without the dated event facts
(`facts_no_events`), which tells the event lines from facts at all. To the
full dev set only if the event lines gain in both runs.

| | today | facts, no dated events | facts with event lines |
|---|---|---|---|
| answered, main run (second judge) | 0.60 (0.60) | 0.60 (0.60) | 0.70 (0.70) |
| answered, noise run (second judge) | 0.60 (0.62) | 0.60 (0.58) | 0.72 (0.70) |
| multi-session (16), main / noise | 0.375 / 0.375 | 0.375 / 0.375 | 0.562 / 0.562 |
| temporal reasoning (16), main / noise | 0.562 / 0.562 | 0.50 / 0.50 | 0.625 / 0.688 |
| knowledge update (7) | 0.857 | 1.0 | 1.0 |
| answers that flip between the two runs | 0 | 0 | 1 |
| block tokens p50 / p95 | 1,429 / 1,494 | 1,456 / 1,496 | 1,455 / 1,496 |

The event lines gain in both runs: against today +7 / -2 and +8 / -2 (7
gains and both losses the same in each run), against the same facts without
the dated events +5 / -0 and +6 / -0. Facts alone gain nothing (+2 / -2 in
both runs): the gain is the event lines'. The two losses come with facts at
all: an unanswerable question answered from a fact instead of left, and a
count the facts misled. The stores: 13,900 facts, 838 of them dated events;
a block shows 4.1 facts on average, 0.86 dated events, and 17 of the 50
questions show at least one. Spend: 12,020 deep-pass calls, 33.3M prompt
tokens (19.8M from the cache), 15.7M completion (14.5M reasoning), about
$9.70 at qwen3.8-flash's international prices; 144 rounds (1.2 %) failed
four tries (the product's 60 s timeout, the queue retries them in
Kazma), and 40 replies (0.3 %) answered the turn instead of reading it.
Evidence: `docs/audits/evidence/memory_event_lines/pilot.json`. The rest of
dev (150 questions, 36,515 deep passes) waits for the main session's word,
with its expected tokens.


**The full dev run** (23 Nov, all 200 dev questions, the pilot's stores kept;
`docs/audits/evidence/memory_event_lines/dev.json`): today's block 0.69,
facts without the dated events 0.70 / 0.71, dated event lines 0.755 / 0.765
(main / noise run, the second judge 0.74 / 0.75); +20 / -7 and +22 / -7
against today, 20 gains and 5 losses in both runs; multi-session 0.40 to
0.57 / 0.59. The block's median 1,336 to 1,424 tokens (p95 1,490 to 1,496).
Reviewed before the merge: an event told again that already has its day left
a write transaction open on a default-mode connection (fixed), and the cache
marker now goes to Qwen's own models only. The extraction recording stayed
declared pending until it could be taken on the model the last one was made
on (below).

**The recording** (24 Nov): deepseek-flash, the 40 turns of extraction
dataset v3, 5 askings each, 200 calls (360,945 prompt tokens, 325,968 of them
from DeepSeek's cache, 31,270 completion, about $0.03); locked with
`--new-recording`, which ends the declared change. Before (20 Nov, the prompt
without events) and after, the spread two standard errors:

| | English | Arabic | mixed |
|---|---|---|---|
| found | 1.000 -> 0.990 (0.021) | 0.971 -> 0.971 (0.057) | 1.000 -> 1.000 |
| named as the list names it | 0.863 -> 0.819 (0.025) | 0.941 -> 0.912 (0.083) | 0.720 -> 0.740 (0.049) |
| right | 1.000 -> 0.990 (0.020) | 1.000 -> 0.972 (0.057) | 0.926 -> 1.000 |
| quiet | 1.000 -> 1.000 | 1.000 -> 1.000 | 1.000 -> 1.000 |
| a fact's start | 0.90 -> 0.80 (0.245) | 1.00 -> 1.00 | |
| replaced / ended | 1.00 / 1.00, unchanged | 1.00 / 1.00, unchanged | |

Every fall but one is inside the spread of five askings: one English asking
kept "3 May" for "the 3rd of May" (found and right), one Arabic asking wrote
Noura's name in Latin letters (both of its facts count as unwanted), two
askings of "last month" said on 31 May gave May (one did before). The
English name is past it: 77 of 94 found facts under a listed name against 82
of 95, mostly `loves` for "likes", `occupation_is` for "occupation" and
`prefers_drink` for "morning_drink", twice each; the facts are the same. The
write scenarios all pass as before. What it does not say: none of the 40 turns
tells an event (no reply before or after gives `"on"`), so this set measures
what the prompt change did to the rest of extraction, not the event lines;
those are measured by the pilots. Event turns are dataset v4 (below).

**The pilot on deepseek-flash** (24 Nov, the model live runs on): the same 50
questions, 12,322 rounds, prompt, reader and judges, with stores and replies
of its own (`longmemeval_facts_pilot.py --label deepseek-flash --stop-usd`).
The Qwen pilot's blocks rebuilt at this commit are the same, 50 of 50, so its
answers stand and only the new blocks were asked.

| | today | Qwen: facts / with event lines | DeepSeek: facts / with event lines |
|---|---|---|---|
| answered, main run (second judge) | 0.60 (0.60) | 0.60 (0.60) / 0.70 (0.70) | 0.64 (0.64) / 0.72 (0.74) |
| answered, noise run (second judge) | 0.60 (0.62) | 0.60 (0.58) / 0.72 (0.70) | 0.66 (0.66) / 0.74 (0.76) |
| multi-session (16), main / noise, with event lines | 0.375 / 0.375 | 0.562 / 0.562 | 0.625 / 0.688 |
| temporal reasoning (16), main / noise, with event lines | 0.562 / 0.562 | 0.625 / 0.688 | 0.688 / 0.625 |
| answers that flip between the two runs | 0 | 0 / 1 | 1 / 3 |
| block tokens p50 / p95 | 1,429 / 1,494 | 1,456 / 1,496; 1,455 / 1,496 | 1,458 / 1,500; 1,459 / 1,498 |
| facts written (dated events) | 0 | 13,900 (838) | 9,319 (853) |
| deep passes that failed four tries | | 144 | 0 |
| tokens: prompt (cached), completion (reasoning) | | 33.3M (19.8M), 15.7M (14.5M) | 32.3M (19.1M), 3.2M (2.6M) |
| cost | | about $9.70 | $3.95 off-peak (twice that at peak) |

On live's model the event lines gain in both runs: against today +8 / -2 and
+8 / -1 (7 gains and 1 loss in both), against the same facts without the dated
events +5 / -1 in each (4 gains in both, no loss in both). Facts without the
events gain here too (+3 / -1, +3 / -0; on Qwen +2 / -2), so on this model
part of the gain is the facts'. Between the two extractors +3 / -2 in each run
on 50 questions: no difference to claim. DeepSeek writes a third fewer facts
and the same number of dated events with a fifth of the completion tokens.
The loss in both runs is the one Qwen's had: an unanswerable question answered
from a fact. Each extractor wrote its stores once, so what extraction itself
varies by is not in these numbers; the noise run repeats the answers only.
Answers asked: 300 calls a run, 189K prompt and 73K completion tokens each.
Evidence: `docs/audits/evidence/memory_event_lines/pilot_deepseek.json`.

**The independent review, fixed** (24 Nov; code only, the prompt and the
message are the same, so the recording and every cached reply stand). What it
found at 81b7a2529 and what holds now (`memory/fact_days.py`,
`tests/test_memory_event_lines_review.py`: each point fails on that commit):
an event is a list entry under its own name ("I called my mom" became the
user's name, "moved_to Porto" their home, and an event labelled single-valued
closed the last one); one told again without its day keeps it, and another
event is not closed by it ("bananas" by "fresh vegetables"); the same words on
another day are another occasion, the latest shown first, and with `replaces`
a correction -- when what was told then can have happened on that day; a day
names the turn that gave it, so forgetting that turn takes the day back, and
on a fact the user stated it reads as Kazma's; an event is true on its day
("what did I buy last month?"); a day to come is planned, not a start lost; a
month reads "in"; an event stands in the user's own words; Qwen's cache marks
only a background call of the measured models, and its write is priced.

The cached replies of both pilots through the fixed apply step, round by
round, no model call (a reply's `replaces` resolved by what the fact it named
was; 6 and 2 named one no longer held):

| | Qwen replies, dev 200 | DeepSeek replies, pilot 50 |
|---|---|---|
| dated events current | 2,976 -> 3,049 | 782 -> 790 |
| planned (they had no day) | 0 -> 251 | 0 -> 38 |
| events on a single-valued fact | 21 -> 0 | 11 -> 0 |
| events that were lost to an undated retelling | 95 have their day | 15 have their day |
| another occasion, or a corrected day | 33 | 11 |
| closed by another event before | 10 | 0 |
| events not kept: not in the user's own words | 67 of 3,888 told | 12 of 959 told |
| blocks that change | 45 of 200, 31 of them only "on" -> "in" | 11 of 50, 8 of them only that |
| block tokens p50 / p95 | 1,424 / 1,496 -> 1,425 / 1,496 | 1,459 / 1,498 -> the same |
| questions showing an event line | 69 -> 72 | 19 -> 20 |
| the answer's words in the block | 57 of 129 -> the same | 12 of 37 -> the same |
| answered, main run (second judge) | 0.755 (0.74) -> 0.760 (0.745), +1 / -0 | 0.72 (0.74) -> the same, 0 / 0 |
| answered, noise run (second judge) | 0.765 (0.75) -> the same, 0 / 0 | 0.74 (0.76) -> 0.72 (0.74), +0 / -1 |
| the changed blocks right, main / noise | 34 -> 35 / 35 -> 35 of 45 | 8 -> 8 / 8 -> 7 of 11 |

Only the changed blocks were asked (a block that is the same has the same
answer): 67 answers a run, judged twice. Nothing is lost in both runs; without
the event facts one of 9 changed dev answers is lost in the noise run; the one
DeepSeek answer lost in the noise run is right in the main one (it is one of
the four that flip between the runs), and the one Qwen gain is in the main run
only. Beside the block with no facts the fixed blocks stay where they were:
0.69 -> 0.76 (main) and 0.69 -> 0.765 (noise) on dev, 0.60 -> 0.72 and 0.60 ->
0.72 on the pilot. Read one by one, none of the 14 and 3 blocks that change by
more than the word loses what its question asks; "Which book did I finish a
week ago?" and "What did I do 5 days ago?" now show only that day's. Spend on
Qwen, both runs: 134 answers and 268 judgements, 191,108 prompt tokens for the
reader (48,000 cached) and 82,421 out, 62,850 and 268 for the judges. What fell: the 67 and 12
events. The first rule (half of the event's words in the turn's own words)
left out 103 and 21, and reading them showed days told of something memory
holds, by "it" ("I got it in 2018"), so the held fact's words count when the
day is the user's own (the second review replaced this rule, below); 48 and 4 of the rest stand
in the message where `own_words` leaves them out (finding 19). A start of a
lasting fact ("since 2019") is not carried over to the entry that replaces
it: the new one may be another thing. Evidence:
`docs/audits/evidence/memory_event_lines/review_fixes.json`. The Qwen spend
above counted no cache writes; there were 30 (49,560 tokens, under a cent).

**Event turns, dataset v4** (24 Nov). Fifteen shapes, each in English and in
Arabic: two events of one day with a place and a price, a call with a day, a
move, the same farm on two days, a retelling without its day, a wrong day
corrected, an appointment to come, two kinds of pasted first-person text,
three turns with no event, a habit, someone else's trip, a month, and an event
with no day. Each is scored by the model's reply and by what the store holds
after the product's own write path took it -- the quick pass, the reply
parsed and applied with the facts held, after the chat before it
(`memory/benchmark_events.py`): the event stored, its days exactly right, what
must stand standing, nothing stored that is not the user's. With one part of
the write path put back as it was before the review and the same replies, the
reply's scores do not move and the stored ones fall (seven controls,
`tests/test_memory_benchmark_event_turns.py`). Recorded on deepseek-flash, 5
askings of the 30 turns (150 calls: 273,265 prompt tokens, 246,400 of them
from DeepSeek's cache, 44,923 completion; the balance went from $5.72 to $5.66,
live's own calls in it); the 200 replies to the other 40 turns are the ones
locked before, and their scores are unchanged.

| | English | Arabic |
|---|---|---|
| the event in the reply / its day | 1.000 / 1.000 | 0.980 (0.040) / 0.971 (0.057) |
| right | 0.928 (0.056) | 0.867 (0.008) |
| quiet (nothing to keep, nothing kept) | 0.800 (0.163) | 0.667 |
| stored / its days exactly right | 1.000 / 1.000 | 1.000 / 0.975 (0.050) |
| safe / everything about the turn right | 1.000 / 1.000 | 0.980 (0.040) / 0.973 (0.033) |

The gap between "right" and "safe" is the pasted text: the model reads it as
the user's in most askings, and Kazma's own-words rule keeps all but one out
(finding 20). The one Arabic event missed is one empty reply of five to "I
went to the farm again yesterday". A move told with no held home named
("moved_to Porto", an event) is kept as an event; the quick pass asks about
the home.

**The second review, fixed** (24 Nov, at 174057e52;
`tests/test_memory_event_lines_second_review.py`: 78 of its 95 cases fail on
that commit, English and Arabic). What holds now:

* *One line for an event.* Its occasions are one candidate in every lane of
  recall, on both paths: five visits to the dentist hid "my dentist is Dr
  Sara" from every search and were the background the question was measured
  against. The latest is shown with "and N more", all of them for a list or a
  period question.
* *Whose words.* The first rule asked that half of an event's words stand in
  the user's own words, compared exactly: 30 of 30 English travel sentences
  kept their event and 12 of 30 Arabic. Now an event is somebody else's when
  its words stand in what `own_words` removed and not in what it kept,
  compared with what Arabic writes onto them ("لدبي"), for an event with a
  day and one without (finding 20). A day a paste names and the user does not
  is dropped, the event kept.
* *Its day and its name.* A day the user counted from the turn's own
  ("yesterday") stands against a later turn's unless that turn denies it. A
  single-valued label with a day holds unless the name is an event's verb ("I
  weigh 78 kg today"); a plan that came to pass is filed as what happened.
* *What a retelling closes.* `replaces` on the same day is a correction
  whatever the words; on another day, only of the same thing (a phone is not
  closed by its case). Forgetting the retelling opens what it closed; an
  ending the user confirms retires its bare entity, as a forget does.
* *Own words after a question to Kazma* (finding 19), in both languages.
* *The held list shows an event's day* (message form 5), with a prompt rule.

The same cached replies again, against the first review's stores:

| | Qwen replies, dev 200 | DeepSeek replies, pilot 50 |
|---|---|---|
| dated events current | 3,049 -> 3,060 | 790 -> 795 |
| planned | 251 -> 229 | 38 -> 27 |
| dated events not the user's words | 67 -> 34, and 4 new | 12 -> 2 |
| undated events the same check leaves out (finding 20's verbs) | 66 -> 18 | 21 -> 7 |
| a pasted day dropped, the event kept | 25 | 1 |
| blocks that change (without event facts) | 9 (3) of 200 | 1 (0) of 50 |
| block tokens p50 / p95 | 1,425 / 1,496 -> 1,421 / 1,496 | 1,459 / 1,498 -> the same |
| the answer's words in the block | 57 of 129 -> the same | 12 of 37 -> the same |
| answered, main / noise (second judge) | 0.760 (0.745) / 0.765 (0.75), +0 / -0 in both | 0.72 (0.74) / 0.72 (0.74), +0 / -0 in both |

The 13 changed blocks were asked twice (78 calls, 48,474 prompt tokens, none
cached, and 13,675 out, on Qwen). The replies are to message form 4: the
pilots were not extracted again on form 5. DeepSeek's cost $3.95 on the
shorter prompt and form 5 adds about 4 % to what is not cached, so it would
pass the $4 set for it: **declared pending**. The 34 and 2 left out are pasted
lists and texts to rewrite, "Here's the info:" hand-overs read as label lines,
quotes, "according to", replies the user asks to be written in their name, and
text past `own_words`' 2,000 characters. Evidence:
`docs/audits/evidence/memory_event_lines/second_review.json`.

**The recording again, message form 5** (24 Nov): dataset v4 with one shape
more (a held event spoken of by its day, nothing new to keep; 72 turns), 5 askings,
deepseek-flash: 360 calls, 673,745 prompt tokens, 597,504 of them from
DeepSeek's cache, 81,537 completion; the balance went from $5.66 to $5.60.

| | English | Arabic |
|---|---|---|
| the event in the reply / its day | 1.000 / 1.000 | 0.980 -> 1.000 / 0.971 -> 1.000 |
| right | 0.928 -> 0.894 | 0.867 -> 0.894 |
| quiet | 0.800 -> 0.667 | 0.667 |
| stored / its days exactly right | 1.000 / 1.000 | 1.000 / 0.975 -> 1.000 |
| safe / everything about the turn right | 1.000 / 1.000 | 0.980 -> 1.000 / 0.973 -> 1.000 |

What is stored is right on every asking of every event turn in both languages;
the replies' own "right" and "quiet" are the pasted text, read as the user's
in every asking and kept out by Kazma. On the 40 turns before: found 0.990 ->
1.000 and right 0.990 -> 1.000 in English, 0.971 -> 0.986 and 0.972 -> 0.986 in
Arabic, named 0.819 -> 0.853 and 0.912 -> 0.971; a start 0.80 -> 0.70 in
English (one asking of ten), replaced 1.00 -> 0.90 and ended 1.00 -> 0.80 in
Arabic (one asking each), mixed right 1.000 -> 0.962, each inside the spread
of five askings.

**The third review, fixed** (25 Nov, of 0bbecc655;
`tests/test_memory_event_lines_third_review.py`: 119 of its 233 cases failed on
that commit, English and Arabic; no model call, the prompt and the recording
unchanged). What holds now:

* *An event is left out only when its words stand in text that is clearly
  pasted or another person's* (`memory/pasted_text.py`), dated or not.
  `own_words`, which is for trust, was the gate and lost ordinary statements:
  of the review's 45 openings with three events each, 123 English and 75
  Arabic events of 135 were kept; now 135 and 135, as on main, and 177 and 177
  of 59 openings more in Gulf Arabic and MSA. Of 34 pasted shapes with the
  same events (a mail, a chat, a quote, a text to translate), none is kept in
  either language (12 Arabic were). What is in neither text stays, as Kazma's
  reading: an event told only by the assistant's reply is now the prompt's to
  keep out, as on main.
* *A period or a list shows everything once first* (`recall._in_order`): six
  visits to the dentist hid the eye doctor of a question naming both. The
  occasions with no room are counted ("and 2 more"). Finding 21 with it.
* *A value's own clitics* ("بـ٩٥"), *a correction that names no old value*
  ("sorry, I meant Omar", "آسف أقصد عمر"), *a plan told as done* ("I'm going
  to the concert tomorrow", read as attended: planned), *a model's "9999"*
  (it lost its reply's every fact).
* *Smaller:* what Arabic says a day by ("قبل ثلاث أيام", "الربوع", "خميس
  الماضي", "أول البارح", "شهر ٥", "الصبح"); a "no" before a noun and a denial
  beside "also" move no day; "I also bought a lamp" closes nothing; forgetting
  a retelling brings back the entry it closed from the archive; "missed",
  "acquired", "rented" and other names the pilots gave are events.

The same cached replies again, against the second review's stores:

| | Qwen replies, dev 200 | DeepSeek replies, pilot 50 |
|---|---|---|
| dated events current | 3,060 -> 3,084 | 795 -> 797 |
| planned | 229 -> 231 | 27 -> 28 |
| dated events left out | 34 -> 1 of 3,880 | 2 -> 0 of 953 |
| undated ones left out | 18 -> 1 of 3,893 | 7 -> 0 of 888 |
| blocks that change (without event facts) | 42 (38) of 200 | 10 (7) of 50 |
| block tokens p50 / p95 | 1,421 / 1,496 -> 1,426 / 1,496 | 1,459 / 1,498 -> the same |
| the answer's words in the block | 57 of 129 -> the same, none lost | 12 of 37 -> the same, none lost |

The two events left out are quotations. The first form of the rule left out 9
and 1, and reading them showed 7 and 1 of the user's own (a list's fields read
as a chat's speakers, "write my story: ..."); the first form of the plan rule
made 29 and 11 new plans, nearly all past events whose year was left out
("which I finished on March 20th", told on the 15th), so it asks the sentence
to speak of what is to come. The blocks change by the order of a list's
siblings; the changed ones were not asked again (no model call). Left: two
undated events with a wrong `replaces` and no "also" (17 such closings of
different words in the replays, read one by one: 15 or more were the same
thing told more exactly, so a stricter rule would leave those twice); a bare
mail with no header; one word shared between what the user asks and what is
pasted; two unmarked-paste turns and a prompt line for the next recording.
Evidence: `docs/audits/evidence/memory_event_lines/third_review.json`.

**The re-check of those fixes, fixed** (25 Nov, of f68b21ae5: 0 H, 4 M, 7 L;
`tests/test_memory_event_lines_recheck.py`, English, Gulf and MSA: 117 of its
182 cases failed on that commit; no model call). A forget finishes on its
first call whatever either archive holds (it raised a foreign-key error when
the fact it opened again named an archived predecessor). "Too" and "again"
undo no correction that names the old value. The user's own line is kept
before anything else:

| events kept (main keeps all) | EN | Gulf | MSA |
|---|---|---|---|
| the reviewer's 83 openings x 3: f68b21ae5 | 201 | 192 | 198 |
| ... now, of 249 | 249 | 249 | 249 |
| 78 written before the fix x 3: f68b21ae5 | 204 | 198 | 192 |
| ... now, of 234 | 234 | 234 | 234 |
| 40 written after its first form x 3: that form | 105 | 114 | 114 |
| ... now, of 120 | 114 | 117 | 117 |
| 12 beside a paste x 3: f68b21ae5 | 30 | 30 | 30 |
| ... now, of 36 | 36 | 36 | 36 |
| the reviewer's pastes let in, of 132: f68b21ae5 | 63 | 81 | - |
| ... now (accepted: filed as Kazma's reading) | 69 | 90 | - |

The third row is what to expect of shapes nobody has tried. The cached
replies replayed, against f68b21ae5: the answer's words are in 57 of 129 and
12 of 37 blocks, as before, none lost; current dated events 3,084 and 797,
planned 231 and 28, tokens, all unchanged; one event fewer is left out (2 ->
1); 10 and 2 blocks change, mostly by order. Five facts differ in the stores,
read one by one: a trip told again closes its first telling ("a bit too small"
had read as "too"); a sentence of a text to proofread is let in; a start read
from the user's own resume, and one from a post they drafted, stay; and a
retelling closes another of three occasions that tie.
Left of the re-check: L-d (pastes let in: "My brother sent me this:", a label
alone, "Customer review:", a text above "Translate the above"), L-f (a list of
a kind in Arabic finds nothing: the same on main), L-g (the parts of a message
are quadratic in lines under the 6,000-character cap), and of M3 what is
honestly unclear (a doctor's letter, an order, an offer quoted in the second
person). Evidence: `docs/audits/evidence/memory_event_lines/recheck.json`.

### W6. Ingestion the product's way (M)

The benchmark's memories written as Kazma writes them: the deep pass on
every round (cached by content), weekly summaries, dated event lines. A model
call per round at write time, as every leading system pays; reported as cost.
Fact names (the old W7) are fixed here, where they can move a number.

Step 1, built 2026-11-25: the facts the deep pass is shown are the ones the
turn is about (`memory/held_facts.py`, §15 H). It compared lists of words in
alphabetical order and gave every place to the user's newest facts. Next: one
role for one person and lists by kind, facts found by an Arabic question as by
an English one, what W4 left, and the benchmark written as Kazma writes.

### W8. Scale and speed (M)

LongMemEval-M (downloaded 21 Nov, revision `98d7416c24c7`,
`longmemeval_m_cleaned.json` SHA-256 `9d79e55247...`), sized before it runs
(embedding its rounds on CPU takes hours); recall p50 and p95, tokens and
calls reported with every run; the scale bench at 10 and 100 times the store.

### W9. The outside benchmarks, published (L)

LongMemEval-S report split (once per release, logged), LongMemEval-M,
LoCoMo, BEAM's 1M tier if its licence allows, the English and Arabic set
grown to 200 questions, MemoryAgentBench and GroupMemBench, and poisoning run
after W1 with its three rates (stored, shown, acted on) mapped to Kazma's
real routes (pages, files, forwards). Every number on the benchmarks page,
generated from its result file, with the reader and the baselines beside it.

### W10. Head to head, and the claim (L)

The open frameworks (Mem0, Zep/Graphiti, Letta, and Mastra if its licence
allows) run on the same data with the same reader on the same machine:

* each in its vendor's documented configuration, at a pinned version, its
  configuration published; where a vendor publishes a number for the same
  reader, it is shown beside ours;
* the same write-time model budget, or each one's write cost reported;
* the claim rests on the **transfer sets** (LongMemEval-M, LoCoMo, BEAM),
  where nobody was tuned by us, not on LongMemEval-S, where Kazma was.

**The stop rule, written before the runs:** the claim is not published if
Kazma loses to any measured framework on the transfer sets by more than the
measured noise, or if any product measure is worse than at the start of this
plan.

**The wording**: "the best of the memory frameworks measured, on these
measures, with this reader, on this date". A property only Kazma publishes
(a forget proven complete, Arabic as an equal, poisoning rates) is worded
"the only one measured", not "the best". Hosted assistants' memories
(ChatGPT, Claude, Gemini) cannot be run on these benchmarks and are not part
of the claim.

## 5. Who, and how each stage ends

One builder on recall at a time (the files overlap): the memory session.
Each stage ends with the full suite, the browser tests near it and the
real-use suite, a push and a deploy, the live alarm read a day later, and
the benchmarks page regenerated.

## 6. Decisions (the owner, 21 Nov)

1. **LoCoMo: published.** Its licence (CC BY-NC 4.0, non-commercial,
   attribution) accepted. Downloaded 21 Nov to the benchmark data folder
   outside the repository: `locomo10.json` from `snap-research/locomo` at
   commit `3eb6f2c585f5` (SHA-256 `79fa87e90f04...`) and the published audit
   of its answers (`dial481/locomo-audit` at `9493fb4b4af4`, same licence),
   which found 99 of 1,540 answers wrong and its judge accepting 63 % of
   deliberately wrong answers. Kazma is scored against the original and the
   audited answers, with the attribution the licence asks for, and no LoCoMo
   text in the repository. Before its first run, the mapping of its two
   speakers is written down (which one is the user, or a shared space of
   two).
2. **A second reader: deepseek-v4-pro** where it fits, a column beside
   deepseek-v4-flash on the report split and the outside benchmarks, with its
   own oracle and baselines; nothing is tuned on it.
3. **DeepSeek, any model, any budget the work needs** (21 Nov); the spend is
   reported with each run.
4. **Latency (left to Claude, agreed with the review):** a reranker adds at
   most 150 ms p50 and 400 ms p95 to a recall on this PC, anytime; the large
   reranker ships only if the small one cannot meet the targets and the
   large one fits the budget.

## 7. Found while measuring, to fix (not inside W0)

**First: finding 10, an Arabic user's facts often do not reach the
answer.** Found by the real-use suite on qwen3.7-plus (22 Nov), the owner's
own language; four runs (two before W1a, two with it), read from the
private installs' logs:
* "أسكن في لشبونة هالأيام." ("I live in Lisbon these days") gave no fact
  from the quick pass in any run; only the model's deep pass stored one,
  in some runs. "وين أسكن؟" ("where do I live?") recalled a fact exactly in
  those runs.
* "انتقلت إلى بورتو الشهر الماضي." ("I moved to Porto last month") gave a
  quick-pass fact in every run, and "وين أسكن الحين؟" ("where do I live
  now?") recalled it in 1 of 4: three times the block held only the two
  conversations, both dated the same day, and the model answered with the
  older city (Lisbon) in all three.
In English every step passed in all four. The product cost: an Arabic
user's changed facts do not reach the answer. It may be two causes (the
quick pass reading the first statement; the second statement's fact not
found by the question); the diagnosis says, before anything is built. Scheduled
next after W1a's acceptance, before W1c's runs or anything else (the main
session's decision): diagnose it with no model call, step by step through
`_recall_beliefs` as an English-Arabic pair (what the passes stored for the
statement -- subject, predicate, the value's spelling, its vector -- and
where the Arabic question loses it: content words, the fact lane's meaning
floor, the fact vector's text `user lives_in <obj>`, a script mismatch);
fix where it fails, with a paired gate (the same facts told in English and
in Arabic, asked in both, all recalled; today's code as the negative
control); then the speakers set, the benchmark EN/AR and the product
measures. Two causes, or one W3 also touches, go to the main session
before building.

Diagnosed (22 Nov, no model call: the statements through the product's
quick pass, `recall()` with the real bge-m3, each fact's evidence split
into meaning lift and word coverage): two causes, both decided by the main
session and fixed in two commits.
* **The quick pass voided a place followed by a time word.** A place must
  end where it is read, and "هالأيام" was no Arabic end; English ended a
  place only at "now" and "currently", so "I live in Lisbon these days."
  kept nothing either. And "I moved to Porto." kept nothing in English
  while "انتقلت إلى بورتو" kept the move. Now a time word ends the place in
  both (`time_words.TIME_AFTER_EN`, `present_ar_words`), and "I/We moved
  to X" reads as its Arabic twin. Two locked write scenarios had locked the
  old difference ("We moved to Rotterdam" stayed a question in English,
  "انتقلنا إلى الرياض" replaced the city in Arabic): scenarios v8 expect the
  update in both, with the change log's row and Undo. Gate:
  `tests/test_memory_time_ends_a_place.py`.
* **An Arabic question never matched a fact's words.** A fact's words are
  Kazma's English name ("user lives in بورتو"): an English question gets
  the name ("live", worth the whole fact floor), an Arabic one nothing, and
  its meaning lift is lower against that mostly-English text (0.035-0.047
  against 0.065-0.080 for "where do I live now?" in a one-fact store). Now
  each fact kind's ask-words, in both languages, are read beside its own
  (`fact_names.ASKED_WITH`), and the Arabic words for "now" are not content
  words, as English "now" is not. The vector is unchanged (W3). Gate
  (`tests/test_memory_fact_languages.py`, bge-m3's recorded vectors): 36/42
  -> 42/42, every loss before an Arabic question. Kazma's benchmark, the
  speakers set and the small store: no question moves. Narrowed after the
  first measure: dropping "these days" / "هالأيام" cost q104 its answer's
  lead and "living" matched "for a living", so both stay out; "الحين" after
  "until" ("لحد الحين", so far) stays a content word (q1008's answer says
  "وإلى الحين").

The real-use suite on both sides (22 Nov, qwen3.7-plus, judge qwen3.8-flash,
two runs each; `docs/audits/evidence/memory_real_use/f10_*_qwen*.json`, and
what recall showed at each step, counts only,
`docs/audits/evidence/memory_findings/finding10_real_use_recall.json`):
before (48975ac04) 18/18 and 15/18, after (5fc4150b7) 18/18 and 16/18. The
step the fix is about, the changed fact asked ("وين أسكن الحين؟", "Where do
I live now?"), failed in one of two runs before, in both languages, and in
neither after. Why, from the server logs: before, the quick pass wrote no
home in either language (the time word voided it), so the fact was the deep
pass's reading, written after the turn, and a question asked before it
landed found nothing; after, the quick pass writes the user's own statement
at once, in every run, and a memory tool's later write of the same fact is
refused ("user fact stands"). Left, on both sides: the Arabic question in
other words ("أي مدينة صارت بيتي هالفترة؟") reaches no fact in any run --
"city" and "home" are not ask-words of `lives_in` in either language, and
English finds it by meaning alone; adding them needs its own precision
rows -- and the forgotten fact asked (step 8) fails once on each side
(finding 1). Spend: 149 model calls, 3.99M prompt tokens, 20,210
completion tokens.

City and home, the main session's decision the same day: first-person
forms only, in both languages, and precision rows that find no fact.
`fact_names.ASKED_AS_MINE` / `kinds_said_of_the_user`: "my city", "my home",
"home for me", "I call home"; "مدينتي", "بيتي", "ديرتي" after a question word
or a verb of becoming ("وين بيتي", "صارت بيتي"). The word must be the thing
asked, in both languages: "my home Wi-Fi", "my home address", "in my home",
"my favourite city" and "شبكة بيتي", "عنوان بيتي", "في بيتي", "مدينتي
المفضلة" ask nothing, nor do "which city is the capital", "the home page",
"مدينة الملاهي" and "بيت الشعر". The words alone were not enough. bge-m3 puts
"What is my city?" below an unrelated question's background for "user lives
in Porto" (lift -0.10; the Arabic other-words question -0.23), and a fact's
word coverage weighs 0.06. So a question that says a fact's words of the
user keeps the asker's own fact of that kind, whatever its evidence
(`recall._named_facts`, both fact lanes). Measured with bge-m3's recorded
vectors on the fact-languages set: the 28 city/home rows (seven questions,
told in each language, stores of one and five) hold 28/28 with the rule,
7/28 with the words alone, 3/28 with today's code; the real-use suite's
Arabic other-words question is lost in every store without the rule and
found in every store with it. The precision rows show no fact. Kazma's
benchmark, the speakers set and the small store: no question moves, with
and without the rule on one tree. The first version moved one: the small
store's abstain question q805, "What is my home Wi-Fi network called?",
showed the home. That is why "my home" must be the thing asked, and q805's
Arabic twin is a precision row too. Gate:
`tests/test_memory_fact_languages.py`, with today's code and the words
alone as the negative controls.

1. **A fact told once is kept twice, and forgetting one copy leaves it
   known.** In the real-use suite (`scripts/memory_real_use.py`, 22 Nov)
   "My sister Noura works as a dentist." became two memories: the turn, and
   a note the model wrote itself with `memory_store`. The Memory page's
   search for "dentist" listed both. The first English trial forgot only
   the first the page listed, then asked "What does my sister do for work?"
   in a new chat, and the judge scored the answer wrong: Kazma did not say
   it did not know (that trial kept no reply text). With both forgotten it
   said it did not know, in every later run (the suite now forgets each
   match, as a person would once they see two). A person who forgets "it"
   and is still answered has been told memory forgets when it does not.
   Evidence: `docs/audits/evidence/memory_real_use/w0_baseline.json`
   (`"forgotten": 2` in both languages); a trial's server log (kept outside
   the repository) shows `memory_store` called on the telling turns. To
   decide: whether a note that restates the turn is written at all, and
   whether Forget offers the memories that say the same thing.
   Diagnosed (22 Nov, no model call): `memory_store`'s description
   contradicted itself -- it opened "Use when the user shares personal
   info" and ended "Only use this tool when the user EXPLICITLY asks" --
   and the model followed the opening. Forgetting a turn already takes the
   notes written in it (`forget._chat_notes`); forgetting a note alone
   leaves its turn. The main session's decision (A): `memory_store` writes
   only when the running turn's latest user message, in the user's own
   words, asks to remember (`remember_request.is_remember_request` through
   `own_words`, English and Arabic, finding 16's shape); any other call is
   refused before anything opens (`RefusedBeforeEffect`, "this turn is
   already remembered"), and the description says so, its opening gone.
   Notes stored before are not touched. What each caller that is not a
   chat turn now gets: a swarm worker has no user message, so its
   `memory_store` is refused (`memory_link_entities` still writes a fact
   in the asker's space); a scheduled task (offered it only under
   `read_write`) stores only when its stored prompt, which is its turn's
   user message, asks to remember; an A2A request is offered no write tool;
   a sub-agent's turn reads its goal, the model's words, as its user
   message (as finding 16's forget rule does: said, not changed). A
   rotating fact (a Grok reset) the user mentions without "remember" is no
   longer noted by the model. Gate: `tests/test_memory_store_only_when_asked.py`
   (English and Arabic: a told fact kept once; "remember that" still stored;
   a quoted "remember" not the user's asking; no user message stores
   nothing; today's tool, which keeps the told fact twice, as the control).
   Later work, not built: forgetting a note whose turn is still remembered
   should offer that turn too (the Memory page's Forget, and
   `memory_invalidate` on a note's fact).
2. **Telling Kazma about a move asks for an approval.** "I moved to Porto
   last month." made the model call `memory_invalidate` (danger tier) to
   retire the old city; the turn waited on an approval card. Seen in one of
   the two English runs. Evidence: the trial result's `approval_tools`.
   The main session's decision (A, 22 Nov): the description now says to use
   it only when the user asks to forget something or says a fact is wrong,
   and never to record a change the user told (Kazma updates the fact from
   their words, or asks them since findings 15 and 17); the approval card
   stays as the safety. To measure in the next real-use runs: if a move
   still raises the card, a refusal before the card (B) is the main
   session's next decision.
3. **An Arabic question about "my sister" was read as Kazma's sister.**
   "شنو تشتغل أختي؟" after the fact was forgotten was answered "I don't have a
   sister, I'm Kazma". LongMemEval's abstention judge passed it (it said no
   answer); the reply misread the question.
4. **Recall finds more than plain retrieval at its size, less than ten
   whole rounds.** On the 200 dev questions Kazma shows every answering
   conversation for 82.0 % (4.5 memories, 1,080 tokens median); bge-m3's
   nearest rounds that fit the same block, 42.3 %; as many rounds as Kazma
   showed, 77.3 % (2,310 tokens); ten rounds, 91.8 % (5,148 tokens)
   (`tests/fixtures/longmemeval/retrieval_dev.json`). The report split's
   starting numbers, looked at once, say the same: 82.6 %; 38.4 % at
   Kazma's block; 81.2 % at its count; 90.6 % at ten rounds
   (`retrieval_report.json`). Input to W1a: more of the right
   conversations inside the budget.
5. **A question memory holds no answer to is never answered with
   nothing.** On LongMemEval Kazma shows memories for every one of them:
   0 of 6 on dev (4.7 memories, 982 tokens) and 0 of 24 on the report
   split (4.8 memories, 1,089 tokens); on Kazma's own benchmark 0 of 22
   English and 0 of 4 Arabic (`tests/fixtures/memory_bench/product_measures.json`).
   W3 + W2's to fix: saying nothing when nothing fits. Still open: the
   reranker was measured and not adopted (23 Nov, W3 + W2).
6. **Memory's own model calls are not in the per-call ledger.** Found by
   the Spaces builder's review of every model call (22 Nov) and checked in
   the code: no module under `kazma_core/memory/` and not
   `skills/self_improvement.py` calls `llm_ledger.record_llm_call` or goes
   through `resilient_chat`. Unledgered:
   - the deep pass (`belief_extractor._llm_extract`), a call per turn
     that is not small talk;
   - the weekly summaries and the month and project roll-ups
     (`topic_summaries._default_chat`, used by `topic_rollups` too);
   - entity disambiguation (`entity_resolution`, off by default:
     `entity_llm_disambiguate`);
   - the lesson refiner (`analyze_and_apply_chat_turn`, and
     `_analyze_success` / `_analyze_failure` for the swarm worker);
   - the V1 backfill's fact pass (`backfill_v2`).

   So Kazma's spend reports never show them, the product measure "cost per
   ordinary turn" misses them, and a person's daily limit (Spaces S2-02)
   cannot count them. A product fix, scheduled after W0: ledger each call
   under the person the turn was for (the asker the work's scope binds;
   Kazma's own sweeps name nobody, as the ledger already does). Gate: every
   memory model call is ledgered, read from the source, a planted
   unledgered call as the negative control. The benchmark's recorded
   replies (`benchmark.py`'s recorder) are not the product's calls.
   Fixed with finding 9 (22 Nov): every call above goes through
   `llm_ledger.ledgered_chat`, one call and its row (thread
   `memory:<work>` when none is bound; whose call from the bound scope;
   a failed call an `error` row), so it counts toward the person's daily
   limit too. Entity disambiguation's call never reached a provider: its
   `client.chat(` was a coroutine it never awaited, so the reply it read
   was the coroutine's text and every answer was "skip" (off by default).
   Removed (the main session's decision): a high-stakes near match is
   quarantined for review, as it always was in fact, and no product code
   calls a client's chat without awaiting it
   (`tests/test_coroutines_are_awaited.py`, the old line as the control).
   Its setting `memory.v2.entity_llm_disambiguate` was never in kazma.yaml,
   so nothing is retired; a stored value is read by nothing. Gate:
   `tests/test_memory_calls_ledgered.py` (the deep pass in English and
   Arabic, the summaries' chat and the refiner through the real code,
   today's direct calls as the control; a planted unledgered call found).
7. **Two token estimators disagree on Arabic.** Found fixing the recall
   block's budget (22 Nov). The context trim (`turn_input`), the recall
   block's budget and the product measures count with
   `summarizer.estimate_tokens`: Latin at four characters a token, Arabic at
   about 2.5. The compaction authority (`authority.create_authority`, handed
   to the supervisor graph) counts with `token_counter.TokenCounter`:
   tiktoken's count when the package is installed and knows the model, else
   four characters a token for every script -- and tiktoken is not in this
   checkout's environment. So for an Arabic chat the compaction threshold
   sees about two thirds of the tokens the trim sees. For later: one
   estimator for context, or the reason two stay. Not fixed now.
8. **Every block measure leaves out what a turn adds to each memory.** Found
   building W1c (22 Nov). Kazma ships `memory.v2.explain_recall` on, so a
   chat turn's recall block ends each memory with its sources ("_(via fts5,
   dense)_"); the product measures, the bounds script and LongMemEval's
   block condition all build the block with `explain=False`. Measured with
   and without on one commit, no model call: the product store's block p50 /
   p95 675 / 1,030 tokens without, 698 / 1,068 with; the long store 1,073 /
   1,484 without, 1,096 / 1,485 with, and in 3 of its blocks the budget
   leaves a memory out. The sources are the operator's view (the "Memory
   used" row has its own payload, `build_memory_explain_payload`).
   Decided and fixed the same day (the main session): the tags explain
   retrieval to a person, help no answer, cost tokens and push memories out
   of the budget. The model's block never carries them;
   `memory.v2.explain_recall` keeps feeding the "Memory used" row;
   `format_recall_block` has no option that changes a line, so every
   measure builds the block the model gets. Before and after on one store,
   no model call: the block a turn sent, p50 / p95 698 / 1,068 tokens on
   the product store and 1,096 / 1,485 on the long store; now 675 / 1,030
   and 1,073 / 1,484, and the 3 long blocks keep their memory. The measures'
   own numbers do not move (they always built it so). Gate:
   `tests/test_recall_block_is_what_is_measured.py`. W0's runs are measured
   on this block: the dev runs' block conditions were already built so, and
   Kazma's turn and the real-use suite run after the fix.
9. **The per-call ledger keeps no count of the cache's share.** Found moving
   W0 to Qwen (22 Nov): `llm_calls` (`observability/llm_ledger.py`) has
   prompt, completion and total tokens and no column for the prompt tokens
   the provider served from its cache (DeepSeek's `prompt_cache_hit_tokens`,
   Qwen's and OpenAI's `prompt_tokens_details.cached_tokens`), so Kazma's
   spend reports and the real-use suite's "spent" cannot show it (the
   suite now counts its judge's own, `judge_spent`). It belongs to the same
   ledger fix as finding 6, after W0: one column, filled from either shape,
   summed beside the prompt tokens. Fixed with finding 6 (22 Nov): the
   column `cached_tokens`, read from the call's own usage by one reader
   (`kazma_core.token_usage.token_counts`: DeepSeek's, OpenAI's and Qwen's,
   Anthropic's and the Responses API's shapes); every answered row hands its
   usage over, and every sum gives `prompt_tokens` and `cached_tokens`
   beside each other. Read raw, an Anthropic row's prompt counted none (its
   `input_tokens` leaves the cache out): it counts the whole prompt now.
   The real-use suite's `spent` reads the column, its judge the same reader.
   Not changed: `cost_from_usage` still reads the cached share from
   `prompt_tokens_details` only, so a DeepSeek call the owner prices is
   priced as if nothing came from the cache (main's call). Gate: the same
   file (each shape; today's rows and a planted row with no usage as the
   controls).
11. **"Did I switch to more water" suppresses recall.** Found by W1c's run
   (22 Nov): "For the coffee-to-water ratio in my French press, did I switch
   to more water per tablespoon of coffee, or less?" is read by the intent
   engine as an explicit topic shift (`intent_mode=shift_explicit`: the word
   "switch"), and the supervisor suppresses memory recall for it. The turn
   answered right from its tools. A question about the user's own past
   change is not a change of subject; the shift rules belong to the intent
   engine, not to memory. Not fixed now.
12. **The intent classifier's refinement calls a thinking model for four
   seconds.** Found the same day: on a gray-zone request
   `agent/intent/classify._refine_acts_llm` asks the chat model to classify
   the request, with a 4 s limit and no reasoning switch; qwen3.7-plus thinks
   by default. Measured the same day on the 16 dev questions where it ran
   (`_refine_acts_llm` as the turn calls it, then the same request with no
   limit): it answered in time 0 of 16 -- every one waited the full 4.0 s
   and kept the heuristics' acts; with no limit qwen3.7-plus takes 8.8-24 s
   (p50 14 s) and 470-1,240 completion tokens, nearly all reasoning, and then
   answers the JSON. So on Qwen every gray-zone turn waits 4 s for nothing
   (and the request may still run and be billed after it is abandoned). The
   fix is provider-side: Kazma's reasoning switch reaching Qwen
   (`enable_thinking`), on the owner's list. Also seen: 15 of the 16 were
   gray because the heuristics read "Can you remind me what you said..." as
   a `remind` act (a reminder to book): a memory question, not a reminder.
   Fixed (22 Nov): Kazma's "off" reaches Qwen on Model Studio (any region's
   address, `dashscope*.aliyuncs.com` and `*.qwencloudapi.com`) as
   `enable_thinking: false`, the top-level field Model Studio documents
   (https://www.alibabacloud.com/help/en/model-studio/deep-thinking); no
   effort level is documented for Qwen, so `low` / `high` / `max` are sent
   as nothing and the chat's level on Qwen does what it did. A model that
   refuses the field is asked again without it. The classifier asks for
   "off" wherever the client takes it (DeepSeek too). Gate:
   `tests/test_qwen_reasoning_off.py` (each level on both hosts, look-alike
   hosts as the control, the refusal, the classifier in English and Arabic
   with today's classifier as the control). Settings' chat reasoning card
   said the level did not apply on Qwen (true of `low` / `high` / `max`,
   not of `off`); it now says Qwen takes Off only, in English and Arabic
   (`chat_reasoning.levels_taken`, the API's `takes`; the main session's
   item 3). Measured the same day on 4c5939b23
   (`_refine_acts_llm` as a turn calls it, 8 gray-zone requests, 4 English
   and 4 Gulf Arabic, qwen3.7-plus, with and without the switch): with it,
   8 of 8 answered in time (1.4-3.4 s, median 1.8 s; 0 reasoning tokens;
   274 prompt and 24-69 completion tokens each); without it, 0 of 8, each
   abandoned at 4.0 s. Spend: the 8 answered calls 2,190 prompt and 300
   completion tokens; the 8 abandoned calls' tokens are not reported (a
   request cut off at its limit may still be billed: up to about 1,240
   completion tokens each, from the first measurement). Evidence:
   `docs/audits/evidence/memory_findings/finding12_classifier_qwen.json`
   (ids, timings and token counts only), the script beside it.
13. **"Remind me what you said" is read as a reminder to book.** Found by
   finding 12's measurement (22 Nov): the heuristics' remind rule
   (`agent/intent/heuristics._REMIND_RE`) matches "remind me" and
   "ذكرني" alone, so "Can you remind me what you said about..." and
   "ذكرني شنو قلت" are a `remind` act, and the gray zone sends them to the
   4 s refinement (15 of finding 12's 16). Recalling what was said is not
   booking a reminder. The main session's decision: its own commit, after
   finding 11, with a paired EN/AR gate through the real classifier.
   Fixed the same day (`heuristics._books_a_reminder`): "remind me"
   followed by a question word, "of" or "about" (Arabic: a question word, or
   "ب" before a noun) asks to recall, unless the message names a time to
   book at ("remind me about the dentist tomorrow", "ذكرني بموعد الدكتور
   بكرة" still book). On the 200 dev questions the refinement is now asked
   for none (15 before, every one a recall question). A reminder named with
   no time ("remind me about my appointment") is now no `remind` act: the
   turn's model still books one when asked, with no intent-engine hint.
   Gate: `tests/test_remind_me_what_you_said.py`.
14. **"Work on this" shows the user's employer.** Found measuring finding
   10's fix (22 Nov): "Can you work on this report?" and "اشتغل على
   هالتقرير" show the user's `works_at` fact, before the fix and after it,
   in both languages (English by the word "work" in the fact's own name,
   Arabic by meaning: lift 0.09). A request, not a question about the user.
   The fix's table neither adds nor removes it. For W3 + W2 (relevance) to
   judge; not fixed now. Diagnosed (22 Nov, no model call; the
   fact-languages store with bge-m3 on this machine, both told languages):
   meaning decides, not the words. "Can you work on this report?" shows
   `works_at` on a lift of 0.047-0.063 above the background plus the word
   coverage's 0.03 (0.06 x 0.5: "work"); "اشتغل على هالتقرير" on a lift of
   0.092-0.095 alone, over the floor of 0.06 with no word counted. "Keep
   working on the draft." shows it too; "Please work on the slides tonight."
   and the Arabic "ممكن تشتغل على العرض الليلة؟" and "كمل شغلك على المسودة"
   show it in some stores or none. A word rule ("work on", "اشتغل على")
   would take 0.03 off the English and nothing off the Arabic: unequal, and
   short of the floor in neither. The fix is the evidence rule's (W3 + W2).
15. **A move to a product named alone reads as a home.** Found the same
   day: "We moved to Slack." and "انتقلنا إلى سلاك." store `lives_in` in
   both languages (the Arabic pass refuses only a Latin name after
   "انتقلنا", as a tool; English has no script to tell). "We moved to
   Python 3.12", "to the next item", "to a new office" store nothing in
   either. The update is in the change log with Undo, so the user can take
   it back; a list of product names the pass would refuse is the fix if it
   is seen in use. Fixed the same day, before finding 10 reached main (the
   main session's condition, equal in both languages): a move to a name no
   word calls a place is Kazma's reading (`belief_extractor._kazmas_reading`):
   a question over a home the user stated, Kazma's reading when none is
   held. It updates at once only when the statement says it is a home or a
   place ("moved house to", "the city of", "سكنت في", "نقلنا بيتنا إلى", a
   place head such as "مدينة"); "I live in" / "أسكن في" stays an update.
   Slack, Teams and their Arabic twins ask; Python 3.12, the next item, a
   new office and the Dubai office store nothing. "moved to" is ambiguous: a
   question costs one click, a wrong home is wrong in every later answer.
   The write scenarios are v9 (the moved ones asked, answered Yes, undone).
   Gate: `tests/test_memory_moved_to_asks.py`. The real-use suite on it
   (3405f2f49, two runs, judge qwen3.8-flash): 17/18 and 17/18; its own
   move ("I moved to Porto last month") is now a bare move, and the changed
   fact asked passed in all four language-runs. The question was recorded
   once (English, run 2: the user's Lisbon held, Porto asked); in the
   others the model reached the move itself (it invalidated the old fact,
   and Porto became Kazma's reading, or finding 16 kept the move turn out).
   Missed: the forgotten fact (finding 1) and the Arabic question in other
   words (no "city"/"home" ask-words), once each. Evidence:
   `docs/audits/evidence/memory_real_use/f15_qwen*.json`,
   `docs/audits/evidence/memory_findings/finding15_real_use_recall.json`.
   Spend: 78 model calls, 2.15M prompt tokens, 10,526 completion tokens.
16. **A forget the model makes in an update turn keeps the update out.**
   Found in finding 10's real-use runs (22 Nov, after, run 2, Arabic): told
   "انتقلت إلى بورتو الشهر الماضي", the model called `memory_invalidate` on
   two older memories by memory id (each approved). A forget keeps its own
   turn out of memory (`forget.keep_turn_out`, meant for "forget X"
   requests), so the move itself was kept out: the quick pass wrote no new
   home ("no facts from ... turn 1: kept out of memory"), and recall showed
   no fact for "وين أسكن الحين؟" (the step still passed, from the turn's
   own memory). In the other run the model invalidated a fact, not a
   memory, and the turn was kept. A turn that states a new fact and
   retires an old memory is an update, not a forget request. Fixed the same
   day (the main session's decision): a turn is kept out only when the
   user's own words ask to forget (`memory/forget_request.is_forget_request`,
   English and Arabic: "forget ...", "can you forget ...", "delete ... from
   your memory", "don't save this", "انسى ...", "تقدر تنسى ...", "احذف ...
   من ذاكرتك", "لا تحفظ"; read through `own_words`, with the user's own
   questions kept, so never a "forget" in a quote, someone else's words or
   a text to translate; "I forget", "did I forget", "don't forget" and
   "لا تنسى" ask nothing). Gate: `tests/test_forget_only_when_asked.py`, a
   real supervisor graph with a scripted model calling the real memory
   tool, closed by `close_turn`, EN/AR: an update turn whose model retires
   the old memory (by its id) or the old fact with its source turn still
   stores the new home and keeps its memory; "Forget that I live in
   Lisbon" / "انسى إني ساكن في الكويت" still keeps its own turn out. Today's
   rule, every forget keeping its turn out, is the negative control: the
   update turn loses its new home, in both languages.
17. **A move put to the user as a question is not in recall.** Found
   reading finding 15's real-use runs (22 Nov). Finding 15 turns a bare move
   ("I moved to Porto last month") into a question over the home the user
   stated: a pending change, "Yes, change it" / "No, keep it". Recall reads
   only current facts, so until the user answers, the next turn's recall
   shows the held home (Lisbon) and nothing says a change waits. "Where do I
   live now?" can then be answered with the old home, and the model has no
   reason to ask. In finding 15's four language-runs the changed fact asked
   (step 6) passed. In two of them the pass leaned on finding 16's fault:
   the move turn was kept out of memory, and the model reached Porto another
   way. The robust version, recorded by the main session's decision: an open
   pending change is shown beside its fact in recall, in both languages,
   e.g. "user lives in Lisbon -- the user said they moved to Porto; not
   confirmed yet". The waiting value goes inside the block's data fence,
   Kazma's words outside it. The model can then answer with both and ask.
   Built only if the real-use suite, run twice after finding 16, shows step
   6 dropping. It dropped. Two runs at 08c21984e (finding 16 and city/home
   merged with main; qwen3.7-plus, judge qwen3.8-flash): 16/18 and 15/18.
   Step 6 failed in English in both runs and in Arabic in one, 3 of 4
   language-runs. In all four, step 5 recorded the question (a pending
   change), no turn was kept out and the model made no forget call:
   finding 16's crutch was gone. The one pass found Porto through the move
   turn's own memory. The failures answered with Lisbon. Also seen: step 4
   (the other-words question, city/home) passed in all four, and step 8 (the
   forgotten fact asked) failed once (finding 1). Evidence:
   `docs/audits/evidence/memory_real_use/f16_qwen*.json`,
   `docs/audits/evidence/memory_findings/finding16_real_use_recall.json`.
   Built the same day: recall's dating step reads the newest open pending
   change on each shown fact (`recall._waiting_changes`), and the fact's
   line ends, inside the fence, with "changed, not confirmed yet, to: Porto"
   (or "ended, not confirmed yet"). A recall rule outside the fence says to
   give both values and ask. Not "answer with the change": the change may be
   finding 15's tool ("We moved to Slack"). A decided or expired change is
   not shown, another tenant's never; a store from before the change log
   shows its facts as before. The block is unchanged when nothing waits
   (the product measures hold). Gate:
   `tests/test_memory_waiting_change_in_recall.py`, through the real quick
   pass, `recall` and block, EN/AR; recall reading no change is the negative
   control. Two runs at ecfbd3493: 17/18 and 17/18. The marker reached the
   model in all four language-runs and the model gave both values, but the
   rule as first worded ("give both") had it lead with the held home: "you
   live in Lisbon; you later mentioned Porto, not confirmed yet". The judge
   failed that answer twice and passed it once. The user's last word is
   Porto, so the rule now leads with it: answer with what the user said
   last, say it is not confirmed yet and what it replaces, and ask -- unless
   it plainly is not such a value (finding 15's "We moved to Slack").
   Evidence: `docs/audits/evidence/memory_real_use/f17_qwen*.json`,
   `docs/audits/evidence/memory_findings/finding17_real_use_recall.json`.
   Two runs at 6834360b3: 18/18 and 18/18; step 6 passed in all four
   language-runs. In three of them step 5 recorded the question, recall
   showed the waiting change, and the model answered Porto, said it was not
   confirmed yet, named Lisbon and asked. In the fourth (English, run 1) the
   model retired Lisbon itself and the move became Kazma's reading; the
   turn was kept (finding 16). Evidence:
   `docs/audits/evidence/memory_real_use/f17b_qwen*.json`,
   `docs/audits/evidence/memory_findings/finding17b_real_use_recall.json`.
   Spend of the six runs after finding 16: 146 model calls, 5.40M prompt
   tokens, 24,915 completion tokens; the judge 60 calls, 10,098 prompt
   tokens.
18. **An Arabic "how many X" without "مرة" or "عدد" is not read as a
    request for every memory.** Found building W4's gathering (23 Nov):
    "How many guests will come?" is one (`asks_for_all`, "how many"),
    "كم معزوم بيحضر؟" is not. "كم" with a noun is a count ("كم كتاب
    قريت؟") or an amount ("كم سعر التذكرة؟", "كم عمري؟"), and no word list
    tells them apart reliably; English "how many" has single-fact readings
    too ("How many tiers is the cake?"). Kazma's benchmark holds two such
    Arabic questions (q1004, h042), each answered by one memory. Left as it
    is; measured before anything is built, on the parallel set's count
    questions asked in both languages.
19. **`own_words` dropped what the user says after "What do you think about
    ...?" and "Do you think I ...", in English only. Fixed 24 Nov.** "What do
    you think about my bathroom rugs? I washed them last Sunday." was read as
    someone's words ("think" as a reporting verb, the rest as what they
    reported), and Arabic kept both. A thinking verb whose subject is "you"
    reports nobody (`_asks_kazma_en`, `_asks_kazma_ar`); a question asks about
    the text after it only when it names or points at one ("What do you think
    of this email: ...", "ما رأيك في هذا النص؟ ..."). "Let's add ...", "TV
    shows, I've ...", "recipe ideas that feature ...? I recently ..." and
    "بغيت أقولك:" too. The pastes' security cases hold unchanged
    (`tests/test_memory_own_words.py`); pairs in
    `tests/test_paired_languages.py`.
20. **An event told with no day was not held to the user's own words. Fixed
    24 Nov.** Found by the event turns: asked about pasted text ("صحح النص:
    رحت روما ..."), deepseek-flash gave it as the user's, and a reply that left
    the day out was stored. Measured before finding 19's fix, the check would
    have taken 66 of 2,421 (Qwen) and 21 of 549 (DeepSeek) undated events, most
    of them the user's own; with finding 19 fixed and the second review's rule
    it takes 18 and 7. Evidence:
    `docs/audits/evidence/memory_event_lines/undated_events.json`,
    `second_review.json`.
21. **A list question's sibling facts are read in the order of their ids.**
    Found reading the changed blocks (24 Nov): `_with_list_siblings` takes the
    newest `limit` of a name and hydrates them in id order, so when the block cuts
    them the ones shown are random ("how many fish ...?" shows other
    `interested_in` facts after a rewrite of the same store). **Fixed 25 Nov**
    with the third review's M2: a sibling that holds a word of the question
    first, then the newest (an event by its day), in both languages
    (`recall._list_siblings`).
