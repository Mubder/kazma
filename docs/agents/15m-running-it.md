<!-- AGENTS.md, piece 15m-running-it. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
**M. Running it: what recall did, the pages it raises, the search past a
size, the sweeps' cursors, and the person's own view** (2026-11-21, the
memory framework audit's part H, findings F14 and F15; no model calls).
- **One recall record per turn** (`memory/recall_records.py`, table
  `memory_recall_records` in `memory_ops.db`): the supervisor's iteration-0
  recall writes the ids it showed, empty, degraded, failed and its latency
  (`recall_records.record_recall`, off the loop; a recall that raises is
  recorded failed and raises on). `respond_node` judges "used"
  (`recall_records.judge_used`: a fact whose value's words are all in the
  answer, a turn or summary sharing three words with the answer that the
  question does not hold); "corrected" is read later (`signals`: a shown
  fact the user replaced, removed or forgot within two days; a replacement
  only by the user's own words). Records are kept 30 days, 50,000 rows at
  most, the row's space with it; no text is stored.
- **Three pages, each once per outage, each with its recovery**
  (`memory/quality_watch.py`, table `memory_incidents`, catalog
  `memory.extraction_failing` / `memory.extraction_back`,
  `memory.recall_degraded` / `memory.recall_back`, `memory.recall_slow` /
  `memory.recall_speed_back`): the deep pass failing (three failures since
  the last that answered, the first ten minutes back or more,
  `quality_watch.deep_pass_outcome`, written by the
  memory worker's deep pass only when the model was asked), most of the last
  twenty recalls degraded, the slowest twentieth of the last twenty over
  8 s. An incident is a row: it pages when opened and is quiet until it
  closes, whatever the cadence; the dead-letter page of a deep-pass task
  is held while the extraction incident is open
  (`task_queue._report_dead_letters`). Memory health shows the week's
  signals and the open incidents (`v2_health._quality`, degraded with
  `memory_incident`); the daily digest has a "Memory:" line and lists an
  open incident under Needs attention. Gate:
  `tests/test_memory_quality_signals.py` (a week of failed extraction pages
  once; the old cadence, one page an hour, as the control).
- **Search past a size narrows, then scores exactly**
  (`memory/vector_codes.py`). Exact search stays the truth: a sign code
  per vector (`memory_vector_codes`, filled by the sweep "memory vector
  codes", `vector_codes.fill_codes`; a trigger deletes a code when its row's
  vector, model or space changes, or the row goes) picks the candidates by
  Hamming distance -- ties at the cut kept, a row with no code yet always a
  candidate, the engine's own predicate (space, tier, model) applied first
  -- and `VectorEngine._rescore` scores them exactly; past
  `vector_codes.NARROW_FROM` (20,000 coded rows) only. The first narrowed
  search and every `vector_engine.NARROW_AUDIT_EVERY`th also read every row
  and use the full answer when they differ, said in memory health
  (`vector_engine.narrowing_health`). Gate: `tests/test_memory_narrowing.py`
  (the benchmark's answers equal with narrowing forced on; the codes'
  estimates without the re-score as the control, which changes 134).
- **Every memory sweep has a budget and a cursor** (`memory/sweep_cursors.py`,
  `sweep_cursors.SWEEPS`, held to the runner both ways). A sweep declared
  `changes` is skipped while nothing it reads changed since a pass found
  nothing to do (`sweep_cursors.idle_skip`; the counter `memory_change_seq`,
  kept by triggers on episodes, facts, entities and summaries, in the
  change's own transaction; an access bump is no change; the vector
  repair's cursor names the embedding model). Forget convergence, turn
  reconcile and the mirror and graph syncs keep their own cursors (`own`).
  The runner times every pass and says one over its budget at WARNING
  (`worker_bootstrap._run_sweeps`, `sweep_health`). Gate:
  `tests/test_memory_sweep_budgets.py` (an idle pass reads the same at four
  times the store; without the cursor it grows with it: the control).
- **What Kazma knows about you** (`/memory/you`, `memory/about_you.py`,
  `GET /api/memory/v2/you`, `POST .../you/{belief_id}/correct|end`; reached
  from the Memory page). The asker's own facts only (`user`, or their entry
  in a shared space, Spaces S1-11), each with its source (you, Kazma's
  reading, a memory tool, a tool), since when, the start the person named
  and what it was before; Correct writes the person's words
  (`user_explicit`, an update the Memory page's Changes can undo), "No
  longer true" ends it. Refusals are codes the page words, EN/AR. Gates:
  `tests/test_memory_about_you.py` (the ownership check taken out as the
  control), `tests/e2e/test_memory_about_you_browser.py` (by the sidebar,
  EN/AR, the database read after each).
- **The Memory page's quick recall check** (it was "golden eval",
  `memory/eval_golden.py`; the benchmarks audit's R10): every case runs
  among the fixture's twenty unrelated memories
  (`eval_golden._distractors`), and passes only when the answer is shown
  and none of theirs is. Gate: `kazma-core/tests/test_memory_eval_golden.py`
  (a recall that shows everything fails every case; without the unrelated
  memories it passed every one: the control). With the real embedder it
  passed 2 of 6 (recall showed 3 to 5 unrelated turns as "possibly
  related"); 6 of 6 since the small-store background below.
- **A small store judges its turns as a full store would** (2026-11-21,
  `recall._question_background`). Below a full meaning pool
  (`recall._DENSE_POOL`), ranks 4-15 are nearly the whole store, so the
  background was the average of unrelated memories and the nearest unrelated
  turns always cleared the floor; in small stores made from the benchmark no
  unanswerable question came back empty. The turns' background is now at
  least the store's own mean plus `recall._TAIL_OFFSET` (how far a full
  store's background sits above a small store's mean, its 5th percentile,
  bge-m3 only; `scripts/small_store_offsets.py`; on dataset v8 it measures
  0.115, and 0.093 stays: chosen again with v8's thresholds, 0.115 loses an
  answer on the speakers set). Only with at least `recall._SMALL_TAIL_MIN` (5) memories
  past the top three: in a store of a few memories that all match, their
  mean is the answers' own, and raised above it every answer was dropped
  (the full suite: an archived and an active memory of one question, a
  space's one matching memory). Not for a list question,
  not for facts (measured on facts it lowered the speakers benchmark's fact
  hit rate, 0.936 to 0.871), not for another embedding model. The main
  benchmark is unchanged (full pools); the speakers benchmark's precision
  rose (0.577 to 0.608); the small-store run (`benchmark.run_small_store_benchmark`,
  locked as `small`): every answer shown 0.978 -> 0.964 (two hard cases:
  q1001, thirty near-identical wedding turns; h004, "boss" for "manager"),
  precision 0.620 -> 0.694, unanswerable questions empty 0 -> 0.04 (the
  facts lane still shows unrelated facts there: a follow-up). The Spaces R3
  study was run again with it (`docs/plans/SPACES_R3_MEMORY_STUDY.md` says
  which numbers moved: every design's precision rose, today's single entry
  lost one hit). Gate: `tests/test_memory_small_store.py` (the old
  background, and the rule with no size guard, as the controls).

