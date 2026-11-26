<!-- AGENTS.md, piece 15g-memory-benchmarks. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
- **Measured, not asserted (R7):** `kazma_core/memory/benchmark.py` seeds a
  private database the product's way -- turns via `episode_row`, facts via
  `mutate_belief(private=True)`, which keeps every write in that database (no
  state mirror, graph or remote vector index), and (v3) weekly
  summaries as `topic_summaries` stores them, a summary answering when it
  stands for an answering turn -- and asks the real `recall()`. CI replays bge-m3's recorded vectors
  (`tests/fixtures/memory_bench/vectors.npz`); `thresholds.json` is a
  ratchet stamped with the dataset version: `python scripts/memory_bench.py
  lock` raises it and refuses to lower it, `lock --rebaseline` is for a new
  dataset version only. A change to the dataset's texts needs `vectors`
  re-recorded with the real model. The Dashboard's golden eval seeds facts
  the same way; a case marked `needs_meaning` is skipped without an
  embedder and must pass with one. **Dataset v5 (2026-10-14, Memory audit
  M24)** adds a second set of 40 questions (`SECOND_SET`: no threshold is
  swept on it; the builder refuses one that repeats a tuning question; a
  `mixed` category asks across languages), scored apart
  (`report["second"]`, gated as `second.*`). **It is not held out**
  (renamed 2026-11-20, benchmark audit R8, the owner's decision 4): two
  decisions were taken by looking at it and it is gated in the same lock,
  so it stays gated as a second check, and only a sealed set (another
  session's questions, scored on request) may be called held out
  (`test_nothing_calls_the_second_set_held_out`: the page, the guide, the
  builder and the benchmark's docstring; the old words as the negative
  control). **The sealed set** (2026-11-20, R8, decision 4;
  `docs/plans/MEMORY_SEALED_SET.md`) is written by a session that does not
  build or tune recall, as `tests/fixtures/memory_bench/sealed_questions.json`.
  CI checks its shape only (`sealed_problems`: 8 questions or more in each of
  `SEALED_CATEGORIES`, Arabic in each but keyword, every gold id in the
  dataset, no repeat of a question the benchmark asks), never its scores:
  `scripts/memory_bench.py sealed --look "why"` is the only way it is scored
  (its question vectors recorded with the real model once, in
  `sealed_vectors.npz`), every look is logged in the lock's `sealed` section
  (date, reason, commit, the file's hash, the scores) and kept by every
  `lock`, and a look with `--decided` spends the set until its questions are
  new. The page prints the last look apart. Gate:
  `tests/test_memory_benchmark_sealed.py` (a planted set; a test that scores
  the sealed file as the negative control). An extraction benchmark
  (`benchmark_data/extraction.json`, `run_extraction_benchmark`: 72 turns
  EN/AR/mixed; the heuristic pass in CI, the model's deep pass from
  recorded replies, `tests/fixtures/memory_bench/extraction_llm.json`,
  recorded with `scripts/memory_extraction_record.py --out <fixture>
  --limit N` run by an install's own Python (it loads that install's
  `.env`; `memory_bench.py` stays env-free) -- it costs a call a turn and
  asking; a turn with no recording is an error, never "the model was
  down"). **Dataset v3 asks what part C2 changed** (2026-11-20,
  `memory_bench.EXTRACTION_C2_TURNS`, ten turns EN/AR, `new_in: 3`): a
  fact's start from the turn's day, the turn before read for context only,
  a held fact replaced, a fact ended, planted text kept out. A gold fact
  names what it asks (`since`, `replaces`, `ended`) and the model's pass
  scores it per language (`dated`, `replaced`, `ended`; `_score_c2`); a
  fact said to have ended finds only an ending (`_value_matches`). A drift
  check leaves out, named (`llm_waiting`), the turns added after its
  recording's `extraction_version`. A relabel is a file holding every
  locked reply (`reply_hashes`), re-keyed or extended. **The recording**
  (2026-11-24, the dated event lines' prompt; the one before, 2026-11-20,
  is in git): deepseek-flash, all 72 turns of v4, 5 askings each, 360
  calls; the file says what the provider counted (`usage`: 673,745 prompt
  tokens, 597,504 of them cache hits, 81,537 completion tokens, 62,583 of
  them reasoning) and names the message each turn was asked (`asked`, which
  a drift check replays by after a later message form, `_reply_key`). A
  recording of an earlier version that today's only added turns to goes on
  with those (`record_llm_extraction`). **Dataset v4 asks what an event
  leaves stored** (2026-11-24, `memory_bench.EXTRACTION_EVENT_TURNS`, sixteen
  shapes EN/AR, `new_in: 4`): a turn with `stored` is an event turn
  (`benchmark_events.is_event_turn`), scored apart from the others (the
  quick pass reads no events, and the v3 scores stay comparable) by its reply
  (`found`, `right`, `quiet`, and `on`, the day) and by what the store holds
  after the product's write path took the reply, with the chat before it
  written too (`benchmark._tell`, the write scenarios' own path): `stored`,
  `day` (the dated entries carry exactly these days), `safe` (the user's own
  facts stand, nothing pasted or someone else's is stored, what must be asked
  is asked) and `whole`. With other facts held about the turn than the model
  was shown (its `held`), nothing of it is right. The lock holds them
  as `events.<lang>.<score>`, compared exactly on the same recording; the
  event's name is not scored. Gate: `tests/test_memory_benchmark_event_turns.py`
  (a reader's replies whole in both languages; each part of the write path
  put back leaves the reply's scores where they were and the stored ones
  fall). The recording of
  2026-10-15 is kept as `extraction_llm_2026-10-15.json`: the Spaces R3
  study and the 2026-11 audits' evidence measured it. Gate:
  `tests/test_memory_benchmark_c2_turns.py` (answers with no start,
  replacement or ending, and a scorer that ignores `ended`, as negative
  controls). **Scored by value since 2026-11**
  (dataset v2, benchmark audit R1): per pass and language `found` (a gold
  fact's whole value -- equal, or whole words of it -- under its subject, or
  the relation from the other side, under any name that does not turn it
  around: a negative or past name finds only a negative or past fact),
  `named` (of those, under a listed name), `right` (kept facts not unwanted:
  a turn's `extras` are true, its `avoid` entries are never right and find
  nothing; the name is scored apart) and `quiet`; the old strict reading
  stays in each row as `recall`/`precision`, ungated. **A recording says
  what it is** (`_recording_identity`: its replies' hash, model, provider,
  date, askings, the system prompt's hash) and a recording of another prompt
  is refused (`StaleRecordingError`, also at replay time); a message the
  recording holds no reply for is its own failure, never "the model is
  down". The gate and the lock compare the locked prompt hash too: the same
  replies relabelled as another prompt's are refused, flag or not, also
  when they are keyed by another message (`replies_sha256`, the replies
  alone, `_same_replies`). **Several askings a turn** (benchmark audit R2,
  2026-11-20): a recording holds a list of replies a turn; every asking is
  scored, a score is the total over the askings (`_total_over_askings`) and
  its spread two standard errors of theirs (`_spread`, kept in the lock as
  `spread`); a new recording is locked only with `ASKINGS_STANDARD` (5)
  askings a turn and a finished recorder (`memory_bench._new_recording_problems`);
  the write scenarios replay the first asking. The recorder
  (`record_llm_extraction`, `scripts/memory_extraction_record.py --out PATH
  --limit N`) asks through the product's own `_llm_extract`, every turn's
  first asking before any second, saves after every reply with the usage
  the provider reported (`usage`) and the message each turn was asked
  (`asked`), continues its file without asking again what it has, and
  stops at its call limit (`complete`: false). Gates: `tests/test_memory_extraction_record.py`, the
  askings cases in `tests/test_memory_benchmark.py` (today's one-reply file
  refused as a new recording, the first asking alone as the controls). The lock
  (`_extraction_lock`): the quick pass is a ratchet; the model's pass
  compares exactly on the same recording, a new recording is locked only with
  `lock --new-recording "why"` and replaces the old scores exactly (written
  in `recordings`), no recording is a failure, and `lock --rebaseline`
  resets the extraction part when its dataset version changes. Gates in
  `tests/test_memory_benchmark.py`, each with its negative control (a
  renamed extractor keeps `found`; "Keep nothing" as the prompt is refused;
  the old max-of-two floor). **A prompt change waits for its recording,
  declared** (2026-11-20, framework audit part C2: its prompt changes land
  first and are recorded once, with the owner's yes). `lock --prompt-changed
  "why"` writes `extraction.pending_recording` (the prompt and message form
  it waits for, the recording it replaces, each change dated with its
  reason; `memory_bench._declare_prompt_change`); until a recording of
  today's prompt ends it, `benchmark._check_recording` answers `previous` and the
  model's pass and the write scenarios replay the old recording by turn
  through today's parser and scorer (a drift check, compared exactly), and
  the benchmarks page says those rows do not measure the new prompt. An
  undeclared change is refused as before. The deep pass's message has one
  builder for the product and the benchmark (`belief_extractor.turn_message`),
  and a recording says its form (`message_shape`; none is form 1); a new
  form is `belief_extractor.MESSAGE_SHAPE` bumped and declared like a prompt
  change. Gate: `tests/test_memory_pending_recording.py` (an undeclared
  change and a sanitizer that drops every fact as negative controls).
  **Write scenarios** (`benchmark_data/writes.json`,
  `run_write_scenarios`, 2026-11): a few turns of one chat through the write
  path in the live order (the quick pass, `extract_and_apply_beliefs_sync`;
  then the deep pass, `extract_beliefs_for_turn` with the names in use and
  `_apply_beliefs_to_v2` at the turn's time, both `private=True`), the
  model's reply to each turn recorded (an extraction turn of the same text)
  or written in its shape, and the facts that must be current and must not.
  Categories `update`, `kept_as_said`, `short`, `someone_else`, each in
  English and Arabic. Locked as they are, failures included (`writes` in
  `thresholds.json`: the share passing per category and language, and the
  failing ids): a fix is locked in, and a scenario that passed never fails
  again (`_writes_lock`; the open trust gate as the negative control).
  Explicit abstention targets (`TARGETS`, 0.6, goals not
  gates); a reviewed trade (`lock --trade "why"`, `trade_problems`: no
  score falls more than 0.05, one rises 0.05 or more, abstention never
  falls; recorded in `trades`); and two Postgres measurements under two
  names: the Postgres-primary keyword path (`tests/test_memory_pg_primary.py`,
  the Postgres job, within `PRIMARY_DELTA` of local) and the pgvector path
  (`tests/test_memory_benchmark_pg.py`, the pgvector job, held to the lock's
  `pgvector` floors both ways). The report (both backends, the second set, extraction) is
  the guide's "Benchmark report"; the measured limits at 10x and 100x are
  the guide's "Measured limits" (`tests/test_memory_scale_bench.py`).
- **A hit at the edge stops the lock** (2026-11-20, benchmark audit R4, the
  owner's decision 3). The recorded vectors keep one byte a number, so the
  model's vector is anywhere within half a rounding step of the recording,
  and another machine's model differs by about that much: q601 cleared the
  belief floor by 0.0004 and flipped in 7 of 30 such moves (audit F5).
  `lock` replays the benchmark `EDGE_DRAWS` (10) times with every recorded
  number moved inside its rounding (`_MovedEmbedder`, seeded,
  `EDGE_MOVE` = half a step) and refuses when a hit flips, naming the
  question and its margin, unless the question is on the edge list (`lock
  --edge QUESTION "why"`, `edges` in `thresholds.json`: margin, draws
  flipped, reason, since): the only exit. Never move a threshold to save one
  question. A question that no longer flips leaves the list, one that does
  not flip cannot be named, a new dataset version starts the list again;
  the questions whose shown memories change inside the rounding are listed
  in the lock (`shown`). The margin is each answer's distance to the cut
  recall applied (the floor, or the gap behind the best), read from the
  ranking itself (`recall.trace_evidence`, bound by the benchmark only),
  positive exactly when the hit holds. CI asks the draws again for the
  questions within `EDGE_WATCH` of the cut (at least three times the largest
  move a lock measured), so a change that brings a question to the edge
  without moving a score is seen. A float recording has no rounding to move
  inside: `_MovedEmbedder` refuses it until its spread is measured. Gate:
  `tests/test_memory_benchmark_edges.py` (today's dataset without q601 on
  the list refuses the lock; an unnamed flip, a wrong margin and a stale
  entry each fail the CI check).
- **pgvector in CI, measured both ways; a named skip fails** (2026-11-20,
  benchmark audit R7 and R11, the owner's decision 5). A second CI job,
  "Tests (pgvector)", runs the tests marked `pgvector` on
  `pgvector/pgvector:0.8.7-pg18` (pinned by digest), beside the plain
  `postgres:18` job (the server live runs); a test may carry both marks.
  `scripts/postgres_suite.py --marker` picks the job, and `MUST_RUN` names
  the tests each job exists for: one that skips, fails or does not run fails
  the job (read from pytest's JUnit report through `safe_xml`). Both
  Postgres jobs install `ci-light`: without numpy the Postgres-primary
  benchmark skipped on every run while AGENTS.md quoted it. **Exact against
  approximate is measured, not decided by default**: the pgvector benchmark
  asks every question with the product's search (the planner may answer
  from the HNSW index) and with index scans off on the test's own connection
  (`EXACT_OPTIONS`; the product is unchanged), and prints both
  (`PGVECTOR_MEASUREMENT`, shown by `-rP`). The product's mode is held to
  the lock's `pgvector` floors both ways; floors not marked `verified` (as
  the run made by hand on 2026-10-14 was) report a difference instead of
  failing, since every CI job is a required check.
  `memory_bench.py lock-pgvector --from LOG --run "<the run>"` locks the
  floors from a run's measurement and marks them verified, keeping the
  other search's scores (`exact_for_the_record`), which the benchmarks page
  compares. Locked from CI run 44732367729 (2026-11-20): exact and
  approximate scored the same on every measure. Gates:
  `tests/test_postgres_suite.py` (pytest alone calls the planted skip
  green: the negative control) and `tests/test_memory_benchmark_pg.py`'s
  rules without a database (the same lower run fails against verified floors
  and is only reported against unverified ones).
- **The benchmarks page says what a number rests on** (2026-11-20,
  benchmark audit R12, the owner's decision 6). The extraction lock writes
  `rests_on` (`extraction_rests_on`: per pass and language the turns, the
  gold facts, the turns with nothing to keep, and for the model's pass the
  askings, turns times the recording's askings a turn), and
  `scripts/benchmarks_page.py` prints it in a "Rests on" column; a
  model-pass row the lock does not count is refused, never printed. The page
  also prints each edge-list hit from the lock (its question, margin and
  flips). Gates: `test_the_lock_counts_what_each_extraction_row_rests_on`
  (the lock's count is the replay's; a typed count fails) and
  `tests/test_benchmarks_page.py` (a lock without the count refuses the
  page, a changed count changes it).
- **The unrelated chat says each turn once; four times the pool is
  measured too** (2026-11-21, benchmark audit R9, dataset v8). v7's pool
  was 1,060 turns of 74 messages, about fourteen copies each, and the
  thresholds were chosen on it. v8's pool is the 74 hand-written turns and
  turns a model wrote for it (`tests/fixtures/memory_bench/pool_turns.json`,
  `scripts/memory_pool_generate.py`: nothing about the person asking, no
  persona name), each once, 300 sessions (`kind: pool`); the scale pool
  (`scale` in the dataset: 900 more sessions and their weekly summaries)
  makes `benchmark.run_scale_benchmark`, locked as `scale`
  (`benchmark.SCALE_SCORES`, `memory_bench._scale_lock`). The roll-ups of
  the new pool are the planner's groups with the product's own texts
  (`ROLLUP_PROJECTS_V8`, `ROLLUP_TEXTS_V8`, written by
  `topic_rollups.rebuild_rollup`). **v5 to v7 replay exactly**: a retired
  item keeps its place in the file with `retired_in`;
  `benchmark.load_dataset` returns today's view and keeps the whole lists
  under `history`, which `dataset_as_of` and `memory_fingerprint(as_of=)`
  read. `memory_bench.py vectors` reuses the file's vectors for texts it
  holds (`--fresh` embeds all). Measured: precision 0.581 -> 0.487 and no
  unanswerable question empty (0.278 before): unrelated turns of the
  question's topic clear the floor once nothing is a copy. **Chosen again,
  unchanged** (2026-11-21, `scripts/memory_threshold_sweep.py`, evidence in
  `docs/audits/evidence/memory_thresholds_v8/`): every floor, gap and weight,
  chosen on half the questions (by category and script) with the other half
  kept to judge it, on all four stores the benchmark builds -- the store, four times
  its unrelated chat, a small store per question, the speakers' shared
  space -- by a rule fixed in the script: no answer lost on any store, then
  more unanswerable questions empty (one question is a tie), then precision
  plus MRR, then the fewest moves, and a value moves only where its grid
  neighbours lose no answer either. A turns' floor from 0.05 loses answers
  at four times the store, a gap of 0.14 or less in small stores; on the
  store alone floor 0.06 and gap 0.04 would have raised precision 0.488 ->
  0.605 (judging half 0.439 -> 0.555) and lost five answers elsewhere (judging
  half four). A sweep on one store is never the evidence for a threshold.
  Chosen a third time on 22 Nov by the memory plan's rule (`--by sets`:
  chosen on the first set alone, the second set kept apart to judge): no
  value moved, no answer lost or gained on either side. Only a judged run
  logged in `docs/audits/evidence/memory_thresholds_v8/choices.json`
  (`judge --record "why"`, `log_choice`) may set the thresholds; that run is
  its first entry. The split of `run_benchmark` into `_seeded_store` and
  `_ask_questions` left its output byte-identical (the whole report, every
  question, `helper_split_identical.json` in the same folder).
  The scale run holds within 0.02 of hit rate and MRR, precision 0.05
  lower. Gate: `tests/test_memory_benchmark_pool.py` (every
  turn once, v7's pool failing the same check as the control; v7's
  fingerprint; nothing about the person, a planted turn as control; the
  scale lock).
- **LongMemEval, measured against the world** (2026-11-21, the memory
  framework audit's part E, decision 7). `scripts/longmemeval_bench.py`:
  `sample` (100 of the 500 questions, stratified, ids in
  `tests/fixtures/longmemeval/sample.json`, seeded by hash), `split` (the
  memory plan's W0: dev 200, the sample and 100 more answerable questions,
  used to choose; report 300, never used to choose, a run on it needs
  `--look "why"` and is logged in `tests/fixtures/longmemeval/report_looks.json`;
  `tests/fixtures/longmemeval/split.json`), `retrieve` (with the free
  proxies, no model: every answer session shown, and an answer that is a
  word or a phrase found in the rounds shown, `answer_in_shown`, and in the
  block itself, `answer_in_block`; and the plain retrieval baselines,
  `plain_top`: bge-m3's 10 nearest rounds by plain cosine and BM25's 10,
  each also a reader condition, read whole)
  (each history written as the product writes a turn, one memory per round,
  dated; the real `recall()`; no model call), `answer` (one reader, the
  histories: Kazma's recall block, the same rounds read whole, the whole
  history, the oracle and the two plain baselines; LongMemEval's own judge
  prompts; the reader at the reasoning Kazma's chat runs, `--reasoning
  low` by default, with room for it, an answer cut at its limit counted;
  `--judge2` a second judge on every answer, its agreement in the result;
  a verdict kept with the model that gave it (`verdict_of`), so `rejudge`
  puts the judges in another order from the cache with no model call
  (qwen3.8-flash judges first since 22 Nov: it gave the model-read answers'
  verdict on 86 % against 60 %), `judge` asks a judge added later for the
  verdicts it lacks, and a cache keeping verdicts by place is refused until
  `tag-judges` names them; `--run` a run's own cache and result file;
  `--conditions`; a call the API
  refuses is asked again on the next run, never kept as an answer or a
  verdict, `answer_entry`; `--conditions kazma_turn` also asks each question
  through Kazma's own chat turn, `scripts/longmemeval_turn.py`, the memory
  plan's W1c: the supervisor graph with the agent's base prompt dated at the
  question's date, the recall block as a turn sends it, `memory_search`,
  `memory_read` and `memory_list` only, the run's level as
  `agent.chat_reasoning`, at most three tool rounds, nothing written after;
  its calls, tokens, tool requests and seconds in the result, one turn at a
  time), `noise` (two runs of one build: the answers that
  flip, `noise_dev.json`), `reading-sheet` and `model-read` (the answers
  read beside the judges, their disagreements first, labelled model-read:
  `model_read.json`), `translate` and `parallel` (40 questions' 84 answer
  conversations in Gulf Arabic and MSA, one store, each question asked in
  its language). The data lives in a folder outside the repository (`--data`,
  checked by its SHA-256), read as untrusted and never committed; the
  result files (`retrieval.json`, `answers.json`, `parallel.json`) hold ids
  and scores only, and the benchmarks page prints them, refusing an
  end-to-end run without its two baselines. Measured: answer shown 91.5 %;
  answered 0.47 from the block, 0.77 read whole, 0.72 from the history cut
  to fit (440,000 characters; since 22 Nov `FULL_MAX_CHARS` is None, the
  reader's window holds the longest history, and the page names a cut run
  "History cut to fit"), 0.94 oracle; Arabic 87.5 % against English 85.0 %. On dev (22 Nov) Kazma's
  recall shows the answer's round for 90.2 % and every answering
  conversation for 82.0 % (4.5 memories, 1,080 tokens median). The plain
  baselines are measured at its size too (`plain_scores`: whole rounds
  within Kazma's median and 95th-percentile block, `size_budgets`; as many
  rounds as Kazma showed; ten): within its median block bge-m3 finds 70.1 %
  and 42.3 %, with as many rounds 89.7 % and 77.3 % (2,310 tokens), and
  passes Kazma only at ten rounds (94.8 % and 91.8 %, 5,148 tokens). On the
  six questions with no answer Kazma still shows 4.7 memories. The report
  300's starting numbers, one logged look (`retrieval_report.json`): Kazma
  92.4 % and 82.6 %; bge-m3 within its median block 70.3 % and 38.4 %, as
  many rounds 88.0 % and 81.2 %, ten rounds 97.5 % and 90.6 %; none of
  the 24 questions with no answer shows nothing. Result
  rows are written one line each (`dump_result`, `compact_row`). Most missed answers were
  shown but cut by the block's per-memory bounds (`limits.RECALL_SHOW_*`).
  The bounds measured (`scripts/recall_block_bounds.py`, evidence in
  `docs/audits/evidence/recall_block_bounds/`): 0.48 with the fixed
  bounds; every round shown whole 0.59, the budget raised to 4,000 tokens
  0.58; the best round at up to 1,500 characters around the question's
  words and the others at 500 (250 when the 1,500-token budget needs it),
  list answers too, 0.74, no line left out. That shape is built as W1a
  (room by rank, §15F, 2026-11-22) and **not yet accepted**: acceptance
  waits for W0's end-to-end dev answering and its noise run. With and
  without it on one commit, no model call: on dev 200 the answer's text is
  in the block for 0.434 of the 129 questions with a worded answer (0.302
  with the fixed bounds; 0.450 have it in the rounds shown), 17 gained and
  none lost; the block's tokens p50 1,080 -> 1,337, p95 1,317 -> 1,489,
  with the whole-block budget and room for strong hits only; making the
  block 0.34 -> 2.5 ms p50, 9.5 ms at most. Room for every hit had 0.442
  (one more answer, held by a possibly related hit) for p50 1,373. The opening kept
  (W1a's second piece) gained the one answer the stretch alone lost (a
  name in a memory's first sentence) for 8 tokens a question on average;
  its rule (no opening when the stretch alone starts inside it) was set
  after reading the one dev answer a plain opening lost. Kazma's benchmark EN/AR,
  an ordinary turn's block (675 / 1,030 tokens), the small store and the
  speakers set are unchanged (no memory there passes the fixed bounds).
  Gate: `tests/test_longmemeval_harness.py` (a history without its evidence
  round finds nothing: the control; the page's refusal with its control; no
  dataset text in a result file), `tests/test_longmemeval_turn.py` (a
  scripted stand-in model: the base prompt and the question's date first, a
  cut memory read whole, three tool rounds and no fourth with five as the
  control, the question's clock in recall with the real clock as the
  control, a refused call never judged).
- **Dataset v9: the facts under the names the product holds** (2026-11-26,
  the memory plan's W6, the framework audit's F-C (5)). v8 seeded the user's
  role as `job_title` and name as `name`, names no writer of the product keeps
  (`users_names`, `name_to_file` file them as `current_role` and `name_is`), so
  the benchmark measured a store no user has and hid a miss. From v9
  (`benchmark.NAMED_AS_THE_PRODUCT_FROM`) `_seed` files each fact as the
  extraction pass does. No memory changes, so the fingerprint and the sealed
  set's `ids_hold_since` hold, and v5 to v8 replay exactly (they seed as
  before). Three facts move (f01, f04, f34); `memory_bench.py vectors`
  embedded three texts.
  - **The miss it showed:** q601 "what is it that I do for a living?" was lost
    under the product's names. The role phrase asked only "do I do for a
    living". It now takes "I do for a living", and MSA "لكسب عيشي/رزقي"
    (`fact_names.ASKED_AS_MINE`). With that, v9 against v8 loses no question
    of the benchmark (183), the small store (164) or the scale run (183).
  - **What moves:** h022 ("شنو فصيلة دمي؟") and h024 ("What's my brother's
    name?") still find their answer, second where it was first: the renamed
    name fact ("user name_is Sami") stands nearer them by meaning. q006
    precision is a little lower, q1008 (small store) ranks higher. Locked
    again with `--rebaseline`: overall precision 0.4872 to 0.4868, second set
    MRR 0.7574 to 0.7347 (mixed 0.9048 to 0.7619, its two questions h022 and
    h024 of seven), small-store precision 0.6752 to 0.6771, scale second-set
    MRR 0.7305 to 0.7078, the speakers' scores up (the steps before, first
    measured here).
  - **The edge list is empty.** q601, the audit's witness at 0.0004, is found
    by its words now, far from the cut, and no hit flips inside the rounding.
    The edge gate's controls plant a flip on the hit nearest the cut
    (`tests/test_memory_benchmark_edges.py`).
  - Gate: `tests/test_memory_benchmark_v9.py` (the product's names from v9,
    v8 seeding as before as the control; every user fact of the dataset held
    under the name a product writer gives it; q601's phrase in EN, Gulf, MSA).
- **The product measures and the real-use suite** (2026-11-22, the memory
  plan's W0 and section 2.1: every memory change is gated on these before
  an outside score counts). `scripts/memory_product_measures.py`, on the
  retrieval benchmark's store with its recorded vectors: Kazma's benchmark
  in English and Arabic apart; the tokens the recall block adds to a turn
  (`format_recall_block` as the supervisor adds it, counted by
  `summarizer.estimate_tokens`; **the block the model gets is the block
  every measure builds**, 2026-11-22, the plan's finding 8: no line
  carries the channels a memory was found by -- `memory.v2.explain_recall`
  shows them in the turn's "Memory used" row -- and `format_recall_block`
  has no option that changes a line; gate
  `tests/test_recall_block_is_what_is_measured.py`, the real supervisor
  against the measures, today's tags-in-block as the control), on the store, at four times its
  unrelated chat, and where memories are as long as live's (`long`, since
  2026-11-22: the store, each conversation lengthened to a length drawn
  from live's episode lengths, `tests/fixtures/memory_bench/live_lengths.json`
  -- percentiles only, read by the script's `live-lengths` from a native
  backup of live's memory database opened immutable, never the live file --
  and padded with stopwords after its own text, so recall chooses as on the
  store); recall's time; the embedding model's time
  (`--real-embedder`); the small store. Kept in
  `tests/fixtures/memory_bench/product_measures.json` with Kazma's own share
  of the first token (`tests/e2e/test_first_token_latency.py`), shown on the
  benchmarks page. The block's tokens are deterministic and held to the
  record (`tests/test_memory_product_measures.py`, a bigger block as the
  control): a change to the block records its new numbers in the same
  commit. Baseline: a median 675 tokens a turn (711 English, 590 Arabic),
  1,030 at the 95th; no question without an answer shows nothing; recall
  96 ms, the embedding 76 ms, Kazma's first-token share 0.115 s. Where
  memories are as long as live's (answers a median 1,351 characters, 4,088
  at the 95th): 1,146 / 1,659, the largest 1,922, with W1a (872 / 1,352 /
  1,644 with the fixed bounds). The block's 1,500-token cap then bounded a
  quarter of its lines' characters, so the rules, the fence and Arabic's
  shorter tokens fell outside it and the estimate passed 1,500; since
  2026-11-22 it bounds the whole block (§15F). With room for strong hits
  only: 1,073 / 1,484 with W1a, 872 / 1,350 with the fixed bounds; split by
  the question's top hit (`block_tokens_by_evidence`), p50 strong 1,133
  against 869, weak 881 against 881, no answer 1,027 against 892 (an
  unanswerable question's top hit is often rated strong: W3 + W2's).
  `scripts/memory_real_use.py` is the browser real-use suite, run by hand at
  every stage (it needs DeepSeek and the embedding model): a private Kazma
  from this checkout (`kazma serve`, the environment scrubbed of every Kazma
  and credential variable, a temporary data folder), reached by the sidebar
  in English and in Arabic: two facts told, asked in a new chat, asked in
  other words, changed, forgotten on the Memory page (every memory its
  search finds), and never said; each answer judged with LongMemEval's
  prompts; the result (pass or fail, tokens, no reply text) in
  `docs/audits/evidence/memory_real_use/`. Baseline: every step passed in
  both languages. What it showed: a fact told once is often kept twice (the
  turn, and the model's own `memory_store` note), and with one of the two
  forgotten the fact was still answered (the first trial); a move made the
  model call `memory_invalidate`, which asks for approval; the abstention
  judge passed an Arabic reply that read "my sister" as Kazma's own. Gate:
  `tests/test_memory_real_use.py` (every kind of step in both languages, a
  suite missing one as the control; nothing of this machine's install in
  the server's environment; no reply text in a kept result).
