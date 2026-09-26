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
| S1 | Stage 1 shipped: suite 2 splits, Linux, CI, deployed, proven on live | ☐ |
| S2 | Stage 2 audit written (section 5) and approved | ☐ |
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

(Written here when the audit is done.)

---

## Change log
- 2026-09-26: plan written and approved; Stage 1 started.
- 2026-09-26: items A-G built and tested (see the table); full suite 10,729 passed.
- 2026-09-26: item H added and built (web turns had not reached memory since 2026-08-08).
- 2026-09-26: item I added and built (the golden eval could repoint live memory writes);
  deploy and live proof next.
