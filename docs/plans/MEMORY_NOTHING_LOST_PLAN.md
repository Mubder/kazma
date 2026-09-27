# Memory: nothing lost, everything found

**Owner-approved:** 2026-09-26 ("move on ... never stop until we have everything in place").
**Status:** Stage 1 shipped 2026-09-26. Stage 2 shipped 2026-09-27 through C2b; what is left
(C1, R6, S1, S3) waits on the owner's decision -- each row says what. This file is the
checklist: every item is ticked here in the same commit that lands it, with the test that holds
it. A new session resumes from the first unticked item.

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
| J | Memories stranded in a legacy table (`episodes_archive`, 329 rows) put back where recall looks (found during S1 cleanup) | ☑ `tests/test_memory_legacy_archive.py` |
| S1 | Stage 1 shipped: suite 2 splits, Linux, CI, deployed, proven on live | ☑ commit 3794b7e2, build a1994a70 (see change log) |
| S2 | Stage 2 audit written (section 5) and approved | ☑ approved 2026-09-26 ("clean and do whatever required") |
| R7 | Retrieval benchmark: 1,109 turns + 72 facts, 76 questions; recorded bge-m3 vectors replayed in CI; a versioned ratchet | ☑ `tests/test_memory_benchmark.py` |
| R1 | Relevance floor: every path ranks on evidence (meaning above the question's background + word coverage); nothing injected when nothing is about the question; past-chats fallback revived | ☑ `tests/test_memory_relevance.py` |
| R2 | Content words: EN + Gulf/MSA stopwords, Arabic folding with the article and prefixes, whole words, underscores split | ☑ same file, `tests/test_transcript_recall.py` |
| R4 | Access "rotation" penalty removed (a fact asked about often is not demoted) | ☑ `tests/test_memory_nothing_lost.py` (B) |
| S4 | `kazma.yaml` read from the install root, parsed once per version | ☑ `tests/test_install_yaml.py` |
| S5 | FTS update triggers fire on indexed columns only | ☑ `tests/test_memory_fts_triggers.py` |
| W3 | `/new` promotes the working turn; nothing deletes it | ☑ `kazma-core/tests/test_memory_v2_phase_c_d.py` |
| X1 | Found building R1: an unused `POST /api/settings/memory/clean` deleted memories by pattern (every tool fact, every entity id starting with four hex letters, every turn saying "retroactive"). Removed; every memory hard delete is declared | ☑ `tests/test_memory_deletes.py` |
| X2 | Found building R7: writes to a private database reached the process-wide stores (`mutate_belief(private=True)`), and the golden eval stored facts without vectors | ☑ `tests/test_memory_benchmark.py`, `kazma-core/tests/test_memory_eval_golden.py` |
| S2 | Memory calls off the event loop (supervisor knowledge search, probe, federated search, health card) | ☑ `tests/test_static_gates.py` |
| W2 | A turn the extraction pool cannot take goes to the durable queue (episode and facts), never dropped | ☑ `tests/test_memory_every_turn.py` |
| W1 | Facts follow the order they were said in: an older statement is history, never a replacement; every late writer (queue, deep pass, reconcile) passes the turn's time; reconciled turns get their facts (heuristic + queued deep pass, 60 a pass) | ☑ `tests/test_memory_event_time.py` |
| R3 | Recall shows the model the question AND the answer (`memory/episode_text.py`); it still compares by the question. Embedding the answer was built and measured, and not adopted: precision fell in four categories, answer-only questions gained nothing | ☑ `tests/test_memory_episode_text.py` |
| X3 | Found building R3: the memory Rebuild deleted `data_dir/vector_memory` -- now the Knowledge Library's live store. Removed; the Knowledge Library re-embeds another model's vectors and rebuilds a collection of another size itself | ☑ `tests/test_knowledge_meaning_search.py` |
| W4 | "Remember this" heard in Arabic too (folded, whole words: a ticket, تذكرة, is not تذكر); promotes the turn and is a durable cue | ☑ `tests/test_memory_remember_and_small_talk.py` |
| W5 | Small talk (the words and a short reply) is kept but never recalled, on every episode path; benchmark unchanged | ☑ same file |
| R5 | The graph walk reads the facts around its seeds hop by hop instead of the 800 most important; 1,102-fact test where the old cut missed the chain | ☑ `tests/test_memory_graph_reach_and_hybrid.py` |
| R8 | Hybrid vector search merges the remote index with the local store (local score wins) | ☑ same file |
| C1 | Measured before building (2026-09-27): the live install holds 6 current, user-stated, single-valued facts about the user -- all subscription reset dates, 4 of them past. A profile "from user_explicit facts" would put stale dates in front of the model every turn. Not built: it needs the owner to say what the profile holds (for example an editable "About me" in Settings) | ☐ owner |
| R6 | A reranker (bge-reranker-v2-m3) is a ~2 GB model download: needs the owner's go-ahead before anything is fetched | ☐ owner |
| U1 | The user decides what is kept: forget a memory (a tombstone plus the forget ledger every writer asks -- turn reconcile, recovery, the legacy restore and the past-chats search never bring it back), "don't remember this chat" (web menu, `/memory off`), export | ☑ `tests/test_memory_forget.py` |
| W6 | One fact, one predicate name. Measured on live first: 281 extra current facts shared a subject and value with another under a different name; by meaning (bge-m3) true pairs and different facts overlap ("grok_next_reset" / "grok_personal_next_reset", two accounts, at 0.94), so the rule is the same WORDS -- 15 pairs on live, all true. New facts take the subject's existing name (`mutate_belief`), reconsolidation retires the 13 stored twice among equal sources (the user's word against an inference stays for the user), and the deep pass reuses the names in use and skips one run's status and internal ids. The LLM-judged ADD/UPDATE/DELETE step was not built: what the measurement found is either this rule's or the prompt's | ☑ `tests/test_memory_predicate_names.py` |
| C2 | Weekly topic summaries (§5.7): once a week has ended, one summary per topic -- a chat of 4+ turns, or short chats and notes grouped by meaning (average linkage against the tenant's own bar, identical to scipy's) -- written by the model on the durable queue, two weeks in flight; forgetting a turn empties every summary made from it and rewrites it without; a forgotten summary never returns; fenced and credential-masked; Memory page panel, export, health | ☑ `tests/test_memory_topic_summaries.py` |
| C2b | Recall reads the summaries (§5.7): every active summary ranked on evidence against the question's background among the summaries, floor 0.16 -- measured on 67 live summaries (every "catch me up" question 0.160-0.372, every unrelated one at most 0.132) and on benchmark v3 (117 summaries, "overview" questions, all no-answer questions clean); shown after the history (above it they cost MRR); only active rows | ☑ `tests/test_memory_summary_recall.py`, `tests/test_memory_benchmark.py` (v3) |

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

### J. Nothing stranded in a table recall does not read (added 2026-09-26, found during the S1 cleanup)
- On 2026-08-02 and 08-03 a one-off operation (in no commit) moved 329 episodes -- July's
  carried-over memories (280), 39 `memory_store` notes, 10 chat turns -- into a table it
  created, `episodes_archive`, and 326 entities into `entities_archive`. No code reads
  either table.
- `memory/legacy_tables.py` restores each episode as a cold memory (tier `archived`, own id,
  text, vector, tenant and time; `created_at` / `archived_at` read in Unix seconds or julian
  days, both of which the operation wrote), skipping an id already held and a text the
  tenant already holds under another id (21 on live). Runs first in the 15-minute recovery
  sweep; health warns while any remain. The legacy table is not touched.
- `entities_archive` stays: 11 junk "concepts" (bare numbers, file names) and 315
  "memory_chunk" rows that are 200-character truncations of memories held in full (256)
  or of smoke-test notes (59).
- Found while testing it, for Stage 2 (R1/R2): a memory whose vector matches the question
  exactly lost to five unrelated notes. The keyword channel's LIKE fallback matched the
  question's `out` inside `about`, every such note ranked in two channels (keyword + the
  graph walk seeded from them), and rank fusion scores ranks, not similarity. The test
  scenario goes into the R7 benchmark as a required case.

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
| S1 | Test and probe data live in the production stores. | Live memory: episodes in `rt-thread-1/2` ("run echo -- Done. Tool said: git version…"); Postgres chat store: tenants `t1`, `tenant-alpha`, `tenant-beta` (2026-08-14); an episode under tenant `web:<uuid>`. Swarm store: `task-hitl-1`, `task-paused-2`, `task-hitl-restart` from `tests/test_swarm_task_store.py`, created 2026-08-14 18:58 -- the incident the root `conftest.py` DB shield was built for, so that route is closed (the shield pins SQLite; no test reads a `.env` since 2026-09-25; processes tools start no longer inherit the DSN since 2026-09-26, AGENTS §26 I). | M | One-off cleanup (owner's call -- it deletes); then trace how each got there and gate it (tests may not reach a live DSN; tenant ids come from one resolver). | Gate per route found. |
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

### 5.6 U1 detail -- the user decides what Kazma remembers (2026-09-27)

What exists: the Memory page edits and invalidates facts and entities. A
memory of a conversation (an episode) can be listed but not forgotten, nothing
keeps a chat out of memory, and there is no export to take away.

1. **Forgetting is a tombstone, never a row delete** (`memory/forget.py`, the
   one home). The chat store still holds the conversation: a deleted episode
   would be re-created by turn reconcile within 15 minutes, and a row with its
   text gone looks erased to the recovery pass, which refills it. Forgetting
   an episode:
   - empties its question, answer and summary (`''`, never NULL: NULL text is
     what recovery treats as erased) and drops its vector; tier `forgotten`
     (not in `RECALLABLE_TIERS`: never recalled, archived or promoted);
     `metadata.forgotten` stamped;
   - records the turn in the forget ledger (`memory_forgotten`: tenant, chat
     key, turn number, question hash) under EVERY key of its chat -- a chat
     has a session id and a thread id, and writers use either;
   - invalidates the facts extracted from that turn (`source_session` /
     `source_turn`);
   - reaches the Postgres mirror (the tombstone) and the remote vector index
     (the vector is deleted).
2. **"Don't remember this chat"** is a ledger row for the whole chat.
   `dual_write.mirror_episode` -- the one episode writer -- refuses a ledgered
   turn or chat before it writes anything (it embeds, upserts the remote
   index and mirrors to Postgres even when its local insert is ignored), so
   the live path, turn reconcile and the swarm bridge are covered at one
   point; the post-turn worker extracts no facts from such a chat; the
   past-chats search skips the chat and reads a chat without its forgotten
   turns. Web: a toggle on the chat; chat apps: `/memory off` / `/memory on`.
3. **Export** (`GET /api/memory/v2/export`): a JSON download of the caller's
   tenant -- facts (current and past, with provenance), episodes, entities,
   and what was forgotten (when; never the text).
4. **Provenance shown**: an episode names its chat; a fact names the chat
   and turn it came from.
5. The Memory page gets a Memories panel: the newest memories, each with
   Forget, and Export.

Gates: after a forget, recall, turn reconcile, recovery and the past-chats
search neither return nor rebuild it (a behavioural test each, with a negative
control); every product site that inserts an episode is declared (like the
delete gate); the tenant gate walks the new routes.

**For W6, measured on live the same day:** turn reconcile's fact pass added
1,366 facts in its first three hours (1,225 from the LLM deep pass). Among
them: one fact under two predicate names (`slack_id` / `slack_user_id`),
transient state (a research run's `pipeline_state: pending`, a config
mismatch since fixed), internal ids (channel ids). The update decision step
W6 plans is where the first class goes; the second and third are the
extraction prompt's to skip, measured on the same turns.

### 5.7 C2 detail -- weekly topic summaries (2026-09-27)

**The gap, measured on live.** Recall injects at most five turns and five
facts. Topics ran far past that: ShipX 264 turns in 95 chats (107 and 115 in
its two busiest weeks), the fitness app's naming 108 turns in one week, the X
posts 89, email 214 over ten weeks. "Where are we with ShipX?" was answered
from whichever five turns ranked first.

**Grouping, measured before it was chosen (eleven live weeks).**
- Average linkage (UPGMA) over the stored question-first vectors; my numpy
  version reproduces scipy's clusters exactly on every week (26 ms for 498
  turns).
- By meaning alone the result mixed topics: follow-ups dominate the owner's
  chats ("recheck it again I have made some changes", "schedule them", "try
  again"), and their vectors say how the user talks, not what about -- an
  audit chat, a reminders chat and an X-posts chat merged into one group (40
  turns at the 0.525 bar, 32 at 0.55). Chat segments merged by centroid did
  worse (one 64-turn group).
- Chats are task-sized (the week's largest ran 33-117 turns), so a chat with
  4+ turns that week is one topic (a multi-subject chat gets a sentence per
  subject). The rest -- short chats, the 269 legacy single-turn sessions from
  the V1 migration, the agent's 162 notes -- is grouped by meaning; on W31
  that gave ShipX builds (36), ShipX phases (33), repo status (17), memory
  health (14), resets (8).
- The bar is the tenant's: the 90th percentile of pairs of its memories,
  0.524-0.527 from 200 to 1,676 memories. A week's own percentile (0.52-0.61,
  and 0.91-1.0 on weeks of repeated test prompts) split weeks of few topics.
- A question with fewer than two content words follows its chat's previous
  turn; small talk and copies of a turn are left out.
- Found on the first live run (week 30: 12 summaries, three topics twice):
  the V1 migration's single-turn copies ("User: ... Assistant: ..." in
  `legacy-*` sessions) repeat turns turn reconcile later wrote from the chat
  store -- 206 of 269. A copy of a turn memory holds is left out (week 30:
  121 turns became 82, 12 topics 9), and runs carry a version: a week an
  older version did is summarized again, its summaries retired first.
- Result on live history: 104 topics over ten weeks (24 at most a week),
  then about 10 a week. deepseek-flash: a turn of 27K tokens cost
  $0.004, so the backlog costs cents.

**What holds.** Each summary lists its turns; forgetting one empties the
summary at once and rewrites it without (retired below four turns); a
forgotten summary is never written again, even when a late turn joins its
topic; the model's text passes the prompt fence and a credential mask. Local
database only (not mirrored; rebuilt from turns).

**C2b: recall reads them.** Measured before the thresholds were set:
- On live (67 summaries, bge-m3, 23 real questions): against the question's
  background among the SUMMARIES, the answering summary of every aggregate
  question scored 0.160-0.372 and the best summary for every unrelated
  question at most 0.132 ("what's my dog's name" met the fitness app's
  naming through the word "name", 0.132). The episodes' background does not
  work: bge-m3 puts a paragraph 0.14-0.19 below a turn for the same
  question, which sank true summaries below zero.
- On the benchmark, dataset v3: the persona's five topics summarized by hand
  in the live model's style, every week's unrelated chat summarized by topic
  (112, so the pool is a real install's size -- with ten summaries a
  question's background sat far lower than live), six "overview" questions
  and three no-answer questions phrased as overviews. At floor 0.16 all 18
  no-answer questions stay clean (0.15 let one in, 0.12 three).
- Shown ABOVE the history, summaries pushed answering turns down (MRR 0.875
  -> 0.868); below it MRR is unchanged and precision rose 0.673 -> 0.681. The
  benchmark's topics are one or two turns, which recall already finds, so it
  gains little there; the gain is on live, where a topic ran to 100+ turns.

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
