<!-- AGENTS.md, piece 15a-memory-worker. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 15. V2 Memory Worker & Schedulers (`kazma-core/kazma_core/memory/worker_bootstrap.py`)

The V2 cognitive engine has a background maintenance tier: a durable task
queue drained by a worker, plus **eight** fire-and-forget asyncio scheduler
loops started from `start_memory_worker()`. These were the subsystem that
silently lost its backup/export runs (the routines existed but nothing called
them) — read this before touching the background memory path.

**A. `start_memory_worker()` is the single boot entry — it starts EVERY
scheduler.** Called from `app.py` startup (wrapped in try/except so it
can't block boot). It registers handlers + `start_worker()` + all
`_start_*_scheduler()` calls in that function. If a new scheduler is added,
register/start it HERE or it will never run (the exact gap that previously
left backups/export inert). Current boot list:

- `_start_macro_sleep_scheduler()`
- `_start_backup_export_scheduler()`
- `_start_reconsolidation_scheduler()`
- `_start_commitment_gc_scheduler()`
- `_start_session_purge_scheduler()`
- `_start_daily_digest_scheduler()`
- `_start_firing_ledger_scheduler()`
- `_start_restore_drill_scheduler()`

**B0. A job's wait is counted from its last run, never from boot.** The live
install restarts several times a day; a scheduler that sleeps a whole
interval from process start never runs there. The daily digest was not sent
once in the week to 2026-09-28, and the backup sweep, skipping a fresh backup
at boot, waited six hours from boot (a nine-hour gap). Long jobs stamp their
last run (`observability/cadence.py`, the ledger and drill their own keys)
and sleep only the rest of the interval. `tests/test_scheduler_cadence.py`
boots every scheduler `start_memory_worker` starts on a virtual clock with
its job due in 30 minutes and fails unless it runs within 40; a new
scheduler needs a case there.

**B. Distinct cadences (do not collapse them):**
- **6h `macro_sleep`:** rule-based tier demotion/promotion (TTLs,
  importance, access) and archival (`macro_sleep.py:run_macro_sleep`).
  First sweep 60s after boot. **Archival is cold storage, never deletion**
  (2026-09-26): `_ARCHIVE_EPISODE_SQL` moves the tier and fills an empty
  summary with a stub; the question, the answer and the vector stay, and
  no statement may set episode text to NULL (gate in
  `tests/test_memory_nothing_lost.py`). Only rows stale on BOTH clocks are
  archived (created past the TTL *and* not recalled within it — every chat
  turn is importance 1 and can never be promoted, so the recall clock is
  what keeps an in-use memory). Recall searches every tier in
  `vector_engine.RECALLABLE_TIERS`; an archived hit ranks just behind an
  equally matching active one (`memory.v2.archived_recall_weight`, 0.98 --
  2 % off its evidence score, a tie-breaker, never a reason to lose it), and
  a recalled archived memory returns to episodic at the next sweep. Moves reach the state mirror; a remote vector
  index is re-tagged, never told to delete. There is no decay score:
  `compute_retention` was removed 2026-09-23 (per-second λ, no reader).
- **6h backup/export** (`_BACKUP_EXPORT_INTERVAL_HOURS = 6`, not 24):
  enqueues `native_backup` + `nightly_export` + `native_pg_backup` →
  native `sqlite3.backup()` of both memory DBs (`backup.py`) + JSONL/GraphML
  dumps (`export.py`) + a filtered `pg_dump` of Kazma's Postgres
  shared-state tables (§21). First sweep 120s after boot. Kept separate
  from macro_sleep so a slow disk on backup can't stall decay. The export is
  a portable text snapshot, whole (2026-10-13, Memory audit M30): no vector
  column, other bytes as base64 (it wrote Python `b'...'` literals), every
  table paged through (the audit log stopped at its newest 20,000 rows), a
  count per piece; a piece that fails fails the task (`_ExportResult.failed`,
  M20 -- failures were logged at DEBUG and counted done).
- **Lifecycle failures are seen** (2026-10-13, Memory audit M20): the
  schedulers enqueue through `worker_bootstrap._enqueue_checked` (a refused
  task is a WARNING and the ops alert `memory.enqueue_failed`; the result was
  ignored, and a broken `memory_ops.db` stopped every cadence while health
  showed an empty queue). Vector repair and recovery let a locked or busy
  database reach the runner (they reported it as nothing to do); the runner
  logs it at INFO for two passes, WARNING from the third, and every sweep's
  last outcome is in memory health (`sweeps`, `sweep_health()`), with the
  queue's oldest pending and running ages and each task type's last success
  (`queue`). Three failed passes in a row degrade health (`sweep_failing`).
  Gate: `tests/test_memory_lifecycle_signals.py`.
- **24h reconsolidation:** `global_reconsolidation` (dedupe + re-embed of
  beliefs, subject-hash partitioned).
- **15-min commitment GC:** TTL expiry + tiered retention (§20). Every
  sweep on this cadence is one entry of `_MAINTENANCE_SWEEPS`, in this order:
  commitment GC, artifact GC, HITL-gate TTL, the retention of decided gates,
  tool receipts, finished task ledgers, model calls (§56), ended YOLO
  windows -- `safety/yolo.py`; a pre-TTL flag with no end time counts as
  ended, it had kept one live chat's approvals off since July --, memory
  task-queue purge, swarm task retention — `swarm.task_retention_days`,
  default 30, 0 keeps all —, pipeline log retention, swarm approval rows,
  run incident retention (§52), checkpoint retention (§41), the supervisor
  watch (§39), the document worker broker (§19 E1), guard dependencies
  (§39), malware signatures
  (§19 E), dependency advisories (§57), model failover drill (§38), provider
  health (§60), erased space keys (§70, S4-07), chat moves and file moves (§70, S2-12), the space door
  report (§70 (1), S4-01 step 1: the database doors' counts added to `space_doors.json`), forget
  convergence (§15I), memory vector repair, memory recovery,
  retired knowledge promotions and memory turn reconcile (§15F), memory
  text fill (§15F), memory mirror sync (§15K), memory graph sync (§15K2), weekly topic summaries (§15J), entity count repair
  (§15L) and knowledge vector repair (§24F, last), run by ONE isolated
  runner so a failing sweep never stops the rest -- and is logged at WARNING
  (it was DEBUG: a sweep failing every pass is a feature that is off). A new periodic cleanup is a
  new entry there, never a new loop (`tests/test_swarm_task_retention.py`).
- **Session purge, daily digest, weekly firing ledger, restore drill:**
  started here too; do not assume "the four original loops" is the set.
All `enqueue_task(...)` loops: the durable worker drains the actual work, so
a failed enqueue cannot kill the cadence and a failed handler is
retried/bounded by the queue.

**C. Handler registration is idempotent via separate module-level flags.**
`register_v2_handlers()` guards on `_registered` (macro_sleep /
entity_merge / micro_consolidation); `register_backup_export_handlers()`
guards on `_backup_export_registered` (native_backup / nightly_export /
native_pg_backup). The underlying `register_handler()` is a plain dict
assignment (idempotent overwrite), but the flags avoid re-churning on
re-boot / repeated calls and let the backup handlers register
independently of the core V2 set.

**D. The durable queue lives in `memory_ops.db` (`task_queue.py`).** Bounded
retries: `max_attempts` (6 for a memory write, `MEMORY_LANE`; 3 for
maintenance) then dead-letter (`status='failed'`); stuck `processing` rows
past 300s are reclaimed. Per-task short-lived SQLite connections (no WAL
contention with chat reads on `memory_state.db`). `enqueue_task` is
best-effort and never raises. Since 2026-10-13 (Memory audits M12, M13):
- **A failed attempt waits its backoff** (`next_attempt_at`, `_retry_delay`:
  30 s, x4 each time, at most an hour). It went back to `pending` at once:
  three attempts in three polls, so a ten-minute provider outage
  dead-lettered every deep pass of it.
- **A dead letter is kept until the operator retries or clears it** (the
  Memory page's queue): `purge_completed_tasks` deletes completed rows only.
  One dead letter degrades memory health (`status_detail: dead_letters`) and
  raises the ops alert `memory.dead_letter`. They were deleted after a week,
  and health (five or more) cleared itself.
- **A failure is a failure.** `dual_write.write_episode` says `written`,
  `refused` (the user keeps the turn out) or `failed`; a failed write raises
  `EpisodeWriteFailed` in the post-turn pipeline, counts `mirror_fail`, and
  the live thread hands the turn to the queue (`post_turn_memory`) -- the
  None was ignored and the turn counted `ok`. The deep pass tells "the model
  is unavailable" (`belief_extractor._llm_extract`: no client, or the call
  raised -- retried, `deep_pass_failed`) from "it answered with nothing"
  (done); an outage ended as a heuristic-only "done", never redone.
- **A lane claims no more rows than it has free slots, and memory writes
  have their own lane** (`MEMORY_LANE`: post-turn memory, the deep pass,
  entity merges, summary rebuilds). The worker claimed eight and ran two:
  a row that waited past the stuck threshold was claimed again and ran
  twice, and a pg_dump or a restic check held up a user's turn.
Gate: `tests/test_memory_queue_retries.py` (each with its negative control:
the ignored None, the one-None extractor, no backoff, the old purge, the old
batch-claim loop, one lane).
- **A handler that fails says why** (2026-11-20): `return
  failed_because("...")` (`task_queue.failed_because`, always False) puts the
  reason in the task's `error_log`, which the Running work card, the
  Dashboard's incident (its bell text is `name: reason`, `runs.notify`) and
  the Memory page's queue show; a reason given in a worker thread is kept, and
  it is masked (`redact_secrets`). A bare `return False` recorded "handler
  returned False": live 2026-11-19 a failed Postgres dump copy said nothing
  more. Gate: `tests/test_task_failure_reasons.py` (no registered handler
  returns a bare False, a planted one as the control).

**D2. No slow call under a memory write lock; one vector per row** (2026-10-13,
Memory audits M14, M15, `memory/vector_write.py`). No embedding, Postgres
mirror, graph or vector-index call runs inside a memory SQLite write
transaction, `belief_mutation._mutation_lock` or the episode writer's lock:
a fact was embedded inside the mutation's `BEGIN IMMEDIATE` under the
process-wide lock (every belief write waited on one embedder call, up to two
minutes), the superseded fact's graph delete and mirror tombstone ran there,
and `macro_sleep` made a Postgres round trip per archived fact inside its
transaction. Now `mutate_belief` writes the vector after the commit
(`_store_belief_vector`), the superseded edge and tombstone after it too, and
`macro_sleep` unmirrors after its commit. A row's vector is embedded ONCE
(`encode_vector`, no lock held), written by one guarded `UPDATE`
(`write_local_vector`: no vector yet and the row still holds what was embedded;
it commits only a transaction it began), and sent to the remote index only
(`index_remotely` through `backends.get_remote_vector_backend`, taken back out
if a forget landed meanwhile). An episode was embedded twice, the second write
ran on the shared connection without the lock or the tier guard (a forgotten
memory got its vector back), and `LocalSqliteVectorBackend.upsert` committed
the caller's transaction; no memory writer upserts through it now. Recall's
access bump waits at most 50 ms for the write lock and keeps its counts when
the database is busy (`recall.flush_access_bumps`, written by the next recall
or `macro_sleep`) -- it waited the full busy timeout (5.54 s measured) on the
turn path and dropped them. Gate: `tests/test_memory_io_outside_locks.py`
(a probe in every embed, mirror, graph and vector hook used by
`belief_mutation`, `macro_sleep`, `hygiene`, `forget` and the episode writer
finds the database free and no memory lock held; one embedding per write; the
forget race; a gate on local upserts; each with its negative control).

**E. The split-DB design is load-bearing.** `memory_state.db` (hot reads:
beliefs, episodes, entities, procedural DAGs) is isolated from
`memory_ops.db` (cold writes: task queue, audit log) precisely so background
consolidation/backup writes don't WAL-contend with chat recall reads. Do
not merge them or route queue writes at the primary DB.

