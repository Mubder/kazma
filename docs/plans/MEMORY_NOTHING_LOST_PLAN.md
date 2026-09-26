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
| A | Exact meaning search over every memory (episodes + beliefs) | ☐ |
| B | Fact recall: meaning search always runs; relevance-aware ranking | ☐ |
| C | Archive keeps the text; archived memories recallable (weighted); revived on use | ☐ |
| D | Recover the erased archived memories (verified matches only) | ☐ |
| E | Past-chats fallback reads the real chat store, no row cap | ☐ |
| F | Vector repair every 15 min (missing, wrong size, old model) | ☐ |
| G | Memory health shows searchable / pending / archived / unrecovered | ☐ |
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
- Ranking: RRF over the channels (FTS, dense, bridge, PPR) x a bounded prior
  `0.5 + p/(1+p)` (p = importance x confidence x trust) x the existing hub-rotation penalty.
- Unchanged: validity filters, functional (subject, predicate) dedupe, supersession.
- `dense_belief_candidate_cap` removed.

### C. Archive = cold, not deleted (`memory/macro_sleep.py`, `recall.py`)
- `_ARCHIVE_EPISODE_SQL` moves the tier and fills an empty summary; it never nulls text.
- Archived rows keep their vector (local and remote index; the remote copy is re-tagged, not
  deleted).
- Every recall path searches archived; fused scores x `memory.v2.archived_recall_weight`
  (default 0.7).
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

### E. Past-chats fallback (`memory/transcript_recall.py`)
- One backend-aware chat reader (Postgres `kazma_chat_sessions` or SQLite `sessions`), shared
  with D. No 400-row window: Postgres prefilters by term in SQL, SQLite scans all rows.

### F. Vector repair (`memory/reembed.py`, `worker_bootstrap._MAINTENANCE_SWEEPS`)
- Every 15 min, time-boxed (~20 s): re-encode rows (all tiers, active beliefs) whose vector is
  missing, the wrong size, or from another model -- in place, never blanked first; remote index
  updated. `global_reconsolidation._reembed_missing` calls the same function (one copy).

### G. Visibility (`memory/health.py`, Settings / Dashboard)
- Counts: searchable by meaning, pending repair (by reason), archived, erased-unrecovered.

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
