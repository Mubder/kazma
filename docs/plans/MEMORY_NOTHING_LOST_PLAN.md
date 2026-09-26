# Memory: nothing lost, everything found

**Owner-approved:** 2026-09-26 ("move on ... never stop until we have everything in place").
**Status:** Stage 1 in progress. This file is the checklist: every item is ticked here in the
same commit that lands it, with the test that holds it. A new session resumes from the first
unticked item.

Related: [`MEMORY_REMAINING.md`](MEMORY_REMAINING.md) (what shipped before),
[`docs/audits/AUDIT_MEMORY_SYSTEM_2026-08-24.md`](../audits/AUDIT_MEMORY_SYSTEM_2026-08-24.md),
[`docs/docs/guide/memory-and-rag.md`](../docs/guide/memory-and-rag.md). This is not a rewrite:
every change is inside the existing V2 engine.

---

## 0. Status

| Item | What | State |
|---|---|---|
| A | Exact meaning search over every memory (episodes + beliefs) | ☑ `tests/test_memory_nothing_lost.py` (A) |
| B | Fact recall: meaning search always runs; relevance-aware ranking | ☑ same file (B) |
| C | Archive keeps the text; archived memories recallable (weighted); revived on use | ☑ same file (C) + `test_memory_v2_phase3.py` |
| D | Recover the erased archived memories (verified matches only) | ☑ `tests/test_memory_rehydrate.py` |
| E | Past-chats fallback reads the real chat store, no row cap | ☑ `tests/test_chat_history.py`, `tests/test_transcript_recall.py` |
| F | Vector repair every 15 min (missing, wrong size, old model) | ☑ `tests/test_memory_nothing_lost.py` (F) |
| G | Memory health shows searchable / pending / archived / unrecovered | ☑ same file (G) |
| H | Every conversation turn reaches memory (found during Stage 1) | ☑ `tests/test_memory_every_turn.py` |
| I | Nothing repoints live memory: the golden eval runs on its own database (found during Stage 1) | ☑ same file |
| S1 | Stage 1 shipped: suite 2 splits, Linux, CI, deployed, proven on live | ☑ commit 3794b7e2, build a1994a70 (see change log) |
| S2 | Stage 2 audit written (section 5) and approved | ◐ written; awaiting approval |
| S2+ | Stage 2 improvements (added to this table from the audit) | ☐ |

---

## 1. What Kazma's memory is (2026-09-26)

- **One local database**, `kazma-data/memory_state.db` (hot reads), plus `memory_ops.db` (task
  queue, audit). Backed up every 6 h (`native_backup`), offsite through restic. Optional Postgres
  state mirror and remote vector index (pgvector / Qdrant); the live install uses neither.
- **Episodes** (conversation memories): every chat turn is mirrored as one episode -- the
  user's message and the final answer (`consolidator._mirror_turn_to_v2`). Tiers:
  `working` (24 h) -> `episodic` -> `recall` (important *and* used) -> `archived` (30 days
  unrecalled, `macro_sleep`).
- **Beliefs** (facts): subject-predicate-object triples extracted from turns; bi-temporal
  (`valid_from/valid_until`, `ingested_at/invalidated_at`); functional predicates supersede;
  superseded beliefs move to `beliefs_archive` after 180 days.
- **Entities**: nodes the beliefs connect (graph, PPR multi-hop).
- **Vectors**: each row carries its `embedding` (bge-m3, 1024 float32) and
  `embedding_model_version`.
- **Recall per turn** (`memory/recall.py`): FTS5 + dense + PPR + session bias, RRF-fused for
  episodes; top 5 episodes and top 5 beliefs injected, fenced as untrusted data.
- **Background**: 6 h sleep cycle (tier moves, archival), 24 h reconsolidation (dedupe,
  re-embed <=100 missing vectors), 15 min maintenance sweeps.
- **Past-chats fallback** (`memory/transcript_recall.py`): searched when memory finds nothing.

Live, 2026-09-26: 300 active + 76 archived episodes; 321 current beliefs (473 incl. superseded);
every active row embedded with `BAAI/bge-m3`.

## 2. What is broken (measured on the live install, 2026-09-26)

1. **Episode meaning search saw an arbitrary slice.** `VectorEngine._fetch_candidate_embeddings`
   fetched `LIMIT limit*16` rows with no `ORDER BY` (the limit was multiplied by 4 twice). At
   recall's `limit=5` that is 240 rows: 60 of the 300 active episodes -- the newest -- were
   never compared. Every search also created and dropped a temporary vec0 table on the shared
   connection.
2. **Belief meaning search saw the 400 "most important"** (`dense_belief_candidate_cap`), ran
   only when keyword search found fewer than `limit` beliefs, and the final ranking was
   importance x confidence x trust: relevance to the question played no part.
3. **Archiving erased.** `_ARCHIVE_EPISODE_SQL` nulled `user_text`/`assistant_text` and kept a
   stub; every recall path filtered `tier IN ('working','recall','episodic')`. After 30 days
   without being recalled a memory was gone. 76 already were.
4. **The past-chats fallback read `chat_sessions.db`** -- on a Postgres install an old file
   (5 sessions, last written 2026-07-21) -- and scanned only the 400 most recent rows.
5. **Vector repair**: missing vectors re-encoded at most 100 a day; after an embedding-model
   switch every older row stayed in the old vector space until someone ran "Rebuild".

## 3. Stage 1 -- nothing lost, everything findable

Rules for every item: a behavioural test at scale (the answer is the newest / least important /
archived row among 1,000+), a negative control showing the old code fails it, a static gate
where the defect has a shape, docs in the same commit.

### A. Exact meaning search (`memory/vector_engine.py`)
- `search()` (episodes) and new `search_beliefs()` score **every** eligible row:
  `vec_distance_cosine(embedding, ?)` in SQL over the whole tenant/tier/validity set
  (sqlite-vec, a core dependency; 20k rows ~21 ms), NumPy chunked exact fallback.
- Comparable rows only: same dimension, same model version (`NULL`/`''` = legacy same model).
- Read-only: no temporary tables, no DDL, no commits. Zero vectors (NULL distance) excluded.
- `RECALLABLE_TIERS` is the one tier list; `LocalSqliteVectorBackend` honours `kind="belief"`
  for search and upsert and stamps the model version.
- Gate: no candidate fetch in the vector engine may carry a `LIMIT` without ordering by distance.

### B. Fact recall (`memory/recall.py::_recall_beliefs`)
- Dense (exact, B-A) always runs, not only when keyword search is thin.
- Ranking: weighted RRF over the channels -- meaning 2, keyword and bridge 1,
  graph walk 0.5 and capped to its top results -- x a standing band of at most
  +5 % (p = importance x confidence x trust) x the existing hub-rotation penalty.
- Unchanged: validity filters, functional (subject, predicate) dedupe, supersession.
- `dense_belief_candidate_cap` removed.
- *Built differently from the first draft, because the tests failed it:* a
  prior of `0.5 + p/(1+p)` (0.5x-1.5x) is worth about 100 ranks under RRF, so
  an important fact at rank ~99 beat the most relevant one; and the graph walk
  as a full channel ranked every fact about "user" (nearly all of them),
  burying the meaning match under keyword noise it had amplified.

### C. Archive = cold, not deleted (`memory/macro_sleep.py`, `recall.py`)
- `_ARCHIVE_EPISODE_SQL` moves the tier and fills an empty summary; it never nulls text.
- Archived rows keep their vector (local and remote index; the remote copy is re-tagged, not
  deleted).
- Every recall path searches archived; fused scores x `memory.v2.archived_recall_weight`
  (default **0.98**, not the 0.7 first planned: fused scores are reciprocal ranks
  1.6 % apart, so 0.7 was ~25 places -- an archived exact match lost to
  moderately similar active memories and would almost never surface).
- An archived episode recalled within `episodic_ttl_days` returns to `episodic` at the next
  sleep cycle.

### D. Recover the erased (`memory/rehydrate.py`)
- Candidates: archived episodes whose `user_text` and `assistant_text` are both NULL.
- A candidate is restored only when a source turn reproduces its stored stub **exactly**
  (the Python twin of the old archive expression). Sources, in order: the chat store
  (`kazma_chat_sessions` / `chat_sessions.db`), LangGraph checkpoint history (every version of
  the `messages` channel), and the stub itself when provably untruncated.
- Never overwrites text that exists; idempotent; bounded per pass; counts what stays
  unrecovered (the stub remains searchable). Runs on the maintenance cadence.
- Measured before building: chat store 15 exact, checkpoints 36 exact (45 chat-derived);
  6 of 31 memory-tool notes complete in the stub.
- As built: the episode id (SHA-256 of session, turn, first 512 characters) is
  the verification, with the stub; earlier backups of `memory_state.db` are a
  source (a same-row copy that reproduces the stub is the erased text); also
  `noted` beliefs (incl. `beliefs_archive`), `memory_store` tool calls
  (resumable scan) and the knowledge library. Dry run on copies of the live
  data: 75 of 76 restored in full (72 from the 2026-08-13 snapshot, 2 from
  checkpoints, 1 complete stub), the 76th a compaction summary that had lost
  nothing; 0 unrecovered, 0 ambiguous; 2.4 s.

### E. Past-chats fallback (`memory/transcript_recall.py`)
- One backend-aware chat reader (Postgres `kazma_chat_sessions` or SQLite `sessions`), shared
  with D. No 400-row window: Postgres prefilters by term in SQL, SQLite scans all rows.

### F. Vector repair (`memory/reembed.py`, `worker_bootstrap._MAINTENANCE_SWEEPS`)
- Every 15 min, time-boxed (~20 s): re-encode rows (all tiers, active beliefs) whose vector is
  missing, the wrong size, or from another model -- in place, never blanked first; remote index
  updated. `global_reconsolidation._reembed_missing` calls the same function (one copy).

### G. Visibility (`memory/health.py`, Settings / Dashboard)
- Counts: searchable by meaning, pending repair (by reason), archived, erased-unrecovered.

### H. Every conversation reaches memory (added 2026-09-26, found while building A-G)
- Measured on live: of 1,174 chat-store turns, 1,004 had no memory episode (877 web,
  127 gateway). Since 2026-08-08 (commit b0c3527b moved the post-turn hand-over out of
  the graph into the gateway handler to stop a UI flicker) no web chat turn was written
  to memory; the live log showed 0 web episodes after 2026-09-17's last Telegram-thread
  one and the past-chats fallback firing 0 times in 8 days.
- `turn_runtime.close_turn`, the closer every transport runs, hands a finished turn to
  `consolidator.remember_turn` once (terminal, not paused, the post-turn record's turn
  index equal to the state's). Nothing else may call it; the gateway's own call is gone.
- `extract_turn_texts` pairs a question with ITS answer; the episode turn number is the
  turn index, so a repeated question is two memories (it collided on the iteration count).
- `memory/turn_reconcile.py` (15 min, ~60 s): every chat-store turn older than 10 minutes
  without an episode gets one, with its own time. Episodes only: replaying old
  statements through functional supersede would overwrite newer facts. Dry run on a copy
  of live: 1,010 turns from 276 conversations, idempotent, resumable.

### I. Nothing repoints live memory (added 2026-09-26, found while building H)
- `POST /api/memory/v2/eval/golden` (Dashboard) ran `eval_golden.run_golden_eval` in the
  live server: it rebound the process-wide `paths.primary_memory_db` to a temp file and
  called `dual_write.reset_mirror()` before seeding through the shared writer, which then
  stayed on the temp file after the run. Every episode written meanwhile, and every one
  after until a restart, went to a file nobody read. It also ran on the event loop.
- Now: seeding and recall use the eval's own connection (`recall(conn=...)`); it refuses
  the live database; its temp file is removed; the route runs it in a thread. Class gate:
  no product code rebinds a `kazma_core.paths` function or resets the writer.

### Rollout
Memory suite (baseline 320 passed / 2 skipped) -> new tests -> full suite split 4 and 7 ->
Linux (Docker) -> CI -> push -> live pull + guard reload -> **proof on live**: every live
episode and belief is the top hit for its own vector; recovery report; fallback sees Postgres
chats; health counts.

## 4. Stage 2 -- audit, then industry-grade

**Method.** Read every memory module (`kazma_core/memory/*`, the agent tools, the UI routes,
the gateway paths), trace each data flow end to end, and compare against Letta/MemGPT, Mem0,
Zep/Graphiti, LangMem, ChatGPT memory and Claude memory. Each finding: evidence (file:line or
live measurement), severity, effort, and the fix with its test. The audit is written to
section 5 and presented for approval before building.

**Areas (starting list, the audit adds to it):**
- User control: see, search, edit, forget and export individual memories; "don't remember
  this"; per-memory provenance.
- Extraction quality: heuristic vs LLM extraction, ADD/UPDATE/DELETE decisions (Mem0-style),
  set-valued facts and contradictions.
- Time: point-in-time questions ("what did I think in August"), relative dates, decay.
- Consolidation: reflection / summaries from episodes into semantic memory.
- Entity resolution: one entity under many names, merges, graph health.
- Retrieval quality: reranking, query rewriting, temporal filters, multi-hop.
- Evaluation: a real benchmark at scale (LongMemEval / LoCoMo style) with recall@k and MRR,
  replacing the 6-case golden set as the regression gate.
- Performance: FTS trigger rewrites on every access bump (`episodes_fts_au` fires on any
  column), recall latency budget, index growth.
- Observability: metrics per channel, hit rates, alarms.
- Safety: memory poisoning, PII, tenant isolation, backups and restore rehearsal.
- Scale path: exact search to ~100k rows, then an ANN index; when the remote index is used.

## 5. Stage 2 audit findings

Written 2026-09-26 after Stage 1 shipped (build a1994a70). Each finding: evidence, severity,
the fix, and the test or gate that would hold it. **Nothing below is built until approved.**
Severity: **H** = memory is wrong, missing or unsafe; **M** = quality or cost; **L** = polish.

### 5.1 Retrieval quality

| # | Finding | Evidence | Sev | Fix | Holds it |
|---|---|---|---|---|---|
| R1 | **No relevance floor.** Recall always returns its top k, so every turn injects 5 facts + 5 episodes whether or not they relate to the question, and the past-chats fallback (fires only on an EMPTY recall) never runs. | Live log 2026-09-19..26: 77 of 77 recalls injected exactly "5 beliefs, 5 episodes"; transcript fallback fired 0 times. | H | Calibrated floors: cosine floor for the meaning channel (per embedding model, measured), fused-score floor for the rest; inject only what clears it; nothing clears -> fallback. | Benchmark R7 no-answer cases; injected-but-unrelated rate metric. |
| R2 | Keyword channel ORs every token of 2+ chars, stopwords included ("is", "the", "at"), and fusion is rank-based, so memories sharing only common words take ranks. | `recall._fts_match_query` (recall.py:1116); a Stage 1 test had to route around it. | M | EN + AR stopword lists; Arabic folding with `documents/arabic.fold_for_search` on both index and query (the knowledge FTS already does this, memory FTS does not). | Tests with stopword-only overlap; Arabic variant queries. |
| R3 | Episode vectors embed the summary, else the question -- never the answer. "What did you tell me about X" misses when X is only in the answer. | `dual_write.mirror_episode` embeds `summary or user or assistant`. | M | Embed question + answer (bounded), or a second answer vector; re-encode through the repair pass. | Benchmark cases answered only in the assistant text. |
| R4 | Hub-rotation penalty multiplies a fused score by up to 0.6 -- about 40 places under RRF -- so a popular fact can lose to barely related ones. | recall.py:895. | M | Make rotation a tie-break band (like standing, <=5 %), or rotate only among near-equal hits. | Test: the most relevant high-access fact stays first. |
| R5 | The graph walk loads only the 800 most important facts -- another importance cap on a retrieval channel. | recall.py:1452 `max(max_nodes*4, 400)`. | M | Seed-local neighbourhood query (edges touching the seeds, then hop) instead of a global top-N load. | At-scale test like Stage 1's 1,200 facts. |
| R6 | No reranker. | -- | M | Optional cross-encoder rerank of the fused top ~30 (bge-reranker-v2-m3 pairs with bge-m3); off by default until the benchmark shows gain. | Benchmark delta recorded. |
| R7 | **Evaluation is a 6-case golden set.** Nothing measures recall@k, MRR or no-answer precision at scale. | `tests/fixtures/memory_golden.json`. | H | A benchmark in the LongMemEval / LoCoMo style: synthetic conversations + anonymised real-shaped ones, single- and multi-session questions, temporal, update, and no-answer cases; recall@5, MRR, abstention accuracy; a CI job with thresholds. Every retrieval change above is judged by it, not by hand. | The benchmark job. |
| R8 | Hybrid vector search returns remote hits without merging local ones: rows not yet in the remote index are invisible whenever the remote answers anything. | backends.py:906. | L | Merge local and remote by id (local wins on conflict). | Test with a partially filled remote. |

### 5.2 Writing memories

| # | Finding | Evidence | Sev | Fix | Holds it |
|---|---|---|---|---|---|
| W1 | Facts are replayed by ingestion time: functional supersede closes the active belief at "now". An old statement written late overwrites a newer fact -- the reason Stage 1's turn reconcile writes episodes only. | belief_mutation._mutate_functional (valid_until=now). | H | Event-time assertions: `mutate_belief(asserted_at=...)`; an assertion older than the active belief lands already-superseded (history), never replaces it. Then reconciled turns can get their facts too. | Tests: old-then-new and new-then-old orders give the same current fact. |
| W2 | When the 4-thread extraction pool is full, a turn's facts are skipped for good (no retry). Turn reconcile now restores the episode, not the facts. | consolidator.py:354 "V2 extract pool full -- skipping". | H | Put the turn on the durable task queue instead of dropping it. | Test: saturated pool -> facts still extracted. |
| W3 | `/new` in chat apps DELETES the conversation's latest turn from memory (the working-tier episode). Turn reconcile re-adds it within 25 minutes, but deleting was never right. | session_commands.py:118 -> consolidator.clear_working_memory. | M | Demote working -> episodic on `/new`; delete nothing. | Test: `/new` keeps every episode. |
| W4 | "Remember this" is recognised only in English. | dual_write.py:277. | M | Arabic phrases (تذكر، لا تنسى، احفظ …) and a test per language. | Test table. |
| W5 | Filler turns ("Hi", "Hello") become episodes and take recall slots. | `is_filler_turn` exists but gates only fact extraction. | L | Mark filler episodes low-importance / exclude from injection (keep them stored). | Test. |
| W6 | Extraction is heuristic + an LLM pass per turn; duplicates are merged only on exact subject-predicate-object. | global_reconsolidation._merge_duplicate_beliefs. | M | Mem0-style decision step (ADD / UPDATE / DELETE / NONE against the nearest existing facts by meaning), with the Stage 1 trust gate kept. | Benchmark update cases. |

### 5.3 Safety, isolation, hygiene

| # | Finding | Evidence | Sev | Fix | Holds it |
|---|---|---|---|---|---|
| S1 | Test and probe data live in the production stores. | Live memory: episodes in `rt-thread-1/2` ("run echo -- Done. Tool said: git version…"); Postgres chat store: tenants `t1`, `tenant-alpha`, `tenant-beta` (2026-08-14); an episode under tenant `web:<uuid>`. | M | One-off cleanup (owner's call -- it deletes); then trace how each got there and gate it (tests may not reach a live DSN; tenant ids come from one resolver). | Gate per route found. |
| S2 | Several memory paths block the event loop: the supervisor's per-turn knowledge search, the `memory_search` tool, the memory probe and federated-search routes. | graph_supervisor.py:1053; tool_builtins/memory.py:588; routes_direct/memory.py (probe, federated-search). | H | Add `recall`, `federated_search`, `build_v2_health` to `_LOOP_STALL_HELPERS`; the gate then finds every call site; each moves to `asyncio.to_thread`. | tests/test_static_gates.py. |
| S3 | Deleting a chat keeps its memories. | No memory cleanup on session delete. | M | Owner decision: keep (memory outlives chats, like ChatGPT) or cascade; either way the UI says which, and "forget this chat" exists. | Test of the chosen rule. |
| S4 | The embedding config is re-read -- YAML parsed from the process's working directory -- on every recall. | embedder.py:483 `Path("kazma.yaml")`. | M | Resolve kazma.yaml from the install root (AGENTS §38 CWD rule) and cache the config with the ConfigStore's invalidation. | CWD gate entry; a timing test. |
| S5 | Every recall's access bump rewrites 10 FTS rows: `episodes_fts_au` fires on ANY column update. | schema_v2.py:461. | M | `AFTER UPDATE OF user_text, assistant_text, summary_text` (and the same for beliefs); migration recreates the triggers. | FTS integrity + a row-count test. |

### 5.4 What leading memory systems do that Kazma does not yet

| Capability | Letta / MemGPT | Mem0 | Zep / Graphiti | LangMem | ChatGPT / Claude memory | Kazma after Stage 1 | Proposed |
|---|---|---|---|---|---|---|---|
| Always-in-context profile ("core memory") | yes (memory blocks) | -- | user summary | yes | saved memories | none -- the user's name competes in recall each turn | C1: a small, user-editable profile block from `user_explicit` facts, injected every turn |
| Temporal facts | -- | partial | bi-temporal edges | -- | -- | bi-temporal schema, ingestion-ordered writes | W1 |
| Update decisions | self-edit | ADD/UPDATE/DELETE | edge invalidation | yes | model-managed | functional supersede + trust gate | W6 |
| Consolidation / reflection | summaries | -- | community summaries | background reflection | -- | none | C2: weekly per-topic summaries from episodes into semantic memory, fenced and reviewable |
| Relevance gating | tool-driven | score threshold | reranker | threshold | model-decided | none (R1) | R1, R6 |
| User control | edit blocks | API | API | API | view / edit / forget / off per chat | admin UI for facts and entities | U1: per-memory view / edit / forget / export in the UI, "don't remember this chat", provenance shown |
| Evaluation | -- | LoCoMo | LongMemEval / DMR | -- | -- | 6 cases | R7 |

### 5.5 Proposed order (each step: plan detail -> build -> tests -> suite -> deploy -> live proof)

1. **R7 benchmark** first -- every later change is measured against it.
2. **R1 relevance floor** (+ fallback revived), **S2 loop blocking**, **W2 durable extraction**, **W3 `/new`**, **S5 triggers**, **S4 config** -- correctness and cost.
3. **W1 event-time facts**, then extract facts for the reconciled turns.
4. **R2 stopwords + Arabic folding**, **R3 answer-aware vectors**, **R4/R5 caps**, **R8**, **W4**, **W5**.
5. **C1 core profile**, **U1 user control**, **W6 update decisions**, **R6 reranker**, **C2 consolidation**.
6. **S1 cleanup** and **S3 chat-deletion rule** -- owner decisions (they delete data or set policy).

---

## Change log
- 2026-09-26: plan written and approved; Stage 1 started.
- 2026-09-26: items A-G built and tested (see the table); full suite 10,729 passed.
- 2026-09-26: item H added and built (web turns had not reached memory since 2026-08-08).
- 2026-09-26: item I added and built (the golden eval could repoint live memory writes).
- 2026-09-26: Stage 1 shipped (3794b7e2, live build a1994a70). Suite 10,744 passed (4- and
  7-way), Linux memory set 214 passed, Postgres-marked tests on postgres:16, CI. Live, first
  15-minute pass: 71 vectors re-encoded (0 unsearchable left); 75 erased memories restored in
  full (0 unrecoverable); turn reconcile began (71 turns in its first 60-second pass, the rest
  over the next passes); every one of 447 episodes and 321 facts is the top hit for its own
  vector (or tied with an identical copy); a new web chat turn became a memory in ~3 s.
- 2026-09-26: Stage 2 audit written (section 5), for approval.
