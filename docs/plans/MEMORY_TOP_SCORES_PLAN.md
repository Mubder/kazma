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
Neighbouring rounds are not added (recall's joined follow-ups keep their
own line). With and without it on one commit, no model call: on dev the
answer's text is in the block for 0.434 of the worded questions (0.302
without; 0.450 in the rounds shown), 18 gained and one lost; the block's
tokens p50 1,075 -> 1,350 and p95 1,317 -> 1,907 there; an ordinary turn's
block, Kazma's benchmark EN/AR, the small store and the speakers set
unchanged. Accepted only on W0's end-to-end dev answering, its noise run,
the second judge and the model-read answers, all waiting for DeepSeek
credit.

### W1c. The product path as the only path (M)

The benchmark answers through Kazma's real turn: base prompt, recall rules,
facts, summaries, the memory tools (`memory_read` to open a cut memory), the
shipped reasoning level, at most three tool rounds, with tokens, calls and
time reported. This is how searching again arrives without a model in the
recall path (decision 14 stays).

### W5 + W1b. One passage unit (M)

A passage of about 256 tokens per stretch of a long memory, in a table of
its own (`docs/plans/MEMORY_REMAINING.md`, "Passage vectors"), each naming
its memory so a forget follows it. It serves three things built once: the
stretch that matched shown in the block (W1b), the reranker's input (W3),
and meaning search over a long memory's middle (W5).

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

### W4. Questions across many conversations (M)

Gathering ("how many", "every time", "all my trips") every matching round
with its date, sorted in time; dated event lines per conversation (a few
lines each, the date said and the date meant, each naming its source turns
so a forget follows); a changed value's history with the conversations that
stated it. Target: every answer session shown ≥ 90 %.

### W6. Ingestion the product's way (M)

The benchmark's memories written as Kazma writes them: the deep pass on
every round (cached by content), weekly summaries, dated event lines. A model
call per round at write time, as every leading system pays; reported as cost.
Fact names (the old W7) are fixed here, where they can move a number.

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
2. **Telling Kazma about a move asks for an approval.** "I moved to Porto
   last month." made the model call `memory_invalidate` (danger tier) to
   retire the old city; the turn waited on an approval card. Seen in one of
   the two English runs. Evidence: the trial result's `approval_tools`.
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
   W3 + W2's to fix: saying nothing when nothing fits.
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
