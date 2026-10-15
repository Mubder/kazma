# Remaining work, 2026-10-15

The owner's instruction (2026-10-15): close every remaining item and bug,
using the live install to test, so that nothing is left when the work stops.
This page is the one list. Each item names where it came from, what closes it
and where it stands. An item is closed only with its evidence.

Decisions the owner made that day: every decision is taken by the best
industry solution, replacing a dependency where that is what it takes
(downloads approved for that); D6 handoff is a product feature; D8 semantic
routing and the Refiner are fixed, not removed; Q04 (independent review) stays
for the owner's own review; the pre-deploy backup copy was deleted (moved to
the Recycle Bin); kazma.ai allows Cloudflare Web Analytics.

## Code

| Item | Source | What closes it | State |
|---|---|---|---|
| Two cron alert-loss windows | Scheduled soak | An alert outbox in the state change's own transaction, drained after commit; real-process kill regressions | Done: `tests/test_cron_alert_outbox.py` (real-process kills in both windows, the old order as control), AGENTS §16 E2 |
| One snapshot step over its cap after a crash | TT-G3 soak | Insert and eviction in one transaction; the soak's allowance removed | Done: `tests/test_time_travel_capture_atomic.py` (real-process kill matrix, the old two transactions as control) |
| AGENTS §18 B migration note | TT-09 | §18 B matches `path_rewrite` / `importer` | Done: `test_agents_18b_names_what_the_import_rewrites` |
| Semantic routing | SW-E10, D8 | One embedding space, explicit vectors, off the loop, keyword fallback said | Done: `tests/test_swarm_semantic_routing.py` (Kazma's embedder, exact cosine, a relevance floor; 19/24 specialist tasks right vs 5 by keywords, 1/11 unrelated misrouted vs 7 with no floor) |
| The pipeline Refiner | SW-E15, D8 | Its output reaches the result; tokens and cost in the ledger | Done: `tests/test_swarm_refiner.py` (`synthesized_output`, the ledger, fenced, bounded; fan-out and consult synthesis accounted the same way) |
| Catch-all autoscaler instance never reused | SQ01 soak | An idle catch-all instance takes the next unmatched task | Done: `tests/test_swarm_autoscaler_reuse.py` (6 unmatched tasks run on 1 instance; never-reuse as control); the soak's spawns halved |
| Handoff end to end | SW-E13, D6 | A shipped worker hands off through a tool; guards intact | Done: `tests/test_swarm_handoff_tool.py` (a shipped in-process worker's model hands off through `handoff_to_worker`; depth and visit guards; four handoff defects found on the way, each with its control) |
| A stop during a task's admission write strands it | Swarm SQ01 24-hour soak (53rd restart) | The stop recorded as the run's interruption; an approval stopped there reopens its checkpoint | Done: `tests/test_swarm_stop_at_admission.py` (shutdown, cancel and a caller giving up; a refused admission; an approval stopped while its write lands, fails or loses to another run; the raised stop as control; a gate over every admission write). The 30-minute soak on the fix: 4,782 submissions, 14 restarts, 0 violations |
| Chaos runs | SQ04 | Fault injection per case with the recorded outcome | Done: `scripts/swarm_chaos.py`, 11 cases recorded in the Swarm tracker (SQLite locked, Postgres down, Temporal unreachable or required, shutdown mid-pattern); `tests/test_swarm_chaos.py`. An approval the store could not record now leaves the checkpoint open (`503 approval_not_recorded`) |
| Knowledge retrieval quality | KQ01 | Versioned EN/AR benchmark, explicit search and auto-inject, ratchet | Done: `tests/test_knowledge_benchmark.py` (224 floors locked, dataset v1). It found abstention 0.00 on both paths; an evidence rule now abstains on 7 of 14 unanswerable questions and lifts precision 0.19 -> 0.71 |
| A crawl seeded with a private address | 2026-10-14 deploy | Refused at admission on every surface, EN/AR | Done: `tests/test_knowledge_crawl_admission.py` (every starter, a gate over them, the old admission as control), browser EN/AR |
| Knowledge deviations (KB-S01, KB-E03, KB-E12) | Knowledge tracker | Each accepted with its reason, or the letter implemented | Done: KB-S01 implemented (Kazma's lines outside the fence); KB-E03 and KB-E12 accepted with reasons |
| Dropped Chroma collections leave vectors on disk | KB-Q01 harness | Orphan segment folders removed when nothing holds them | Done: removed at client start, after every drop and at each repair pass (`chroma_local.remove_orphan_segment_folders`, `release_dropped_vectors`); `tests/test_knowledge_vector_lifecycle.py`, `tests/test_knowledge_vectors.py`, `tests/test_knowledge_store.py`, `tests/test_knowledge_soak_harness.py` (strict: each a violation now), each with its negative control; two strict 5-minute soaks passed (`docs/audits/evidence/kb_q01_soak_5min_after_fixes_run{1,2}_2026_10_15.json`) |
| Chroma cannot read a small collection after a sweep | KB-Q01 harness | Root cause fixed (a newer chromadb, persistence settings or recovery) | Done: the cause is Chroma's index pool evicting an unpersisted small index; the limits it sizes the pool from are raised, and an index still lost is rebuilt from Chroma's log (`_hnsw_read`, counted). chromadb 1.5.9 is the floor. No process failed to read a collection in the two strict soaks. Left: past 6 indexes in one shard (~100 libraries) an evicted large index reloads stale until its next write |
| A deleted library's words stay in the FTS index | KB-Q01 harness | Purged after a library delete | Done: `delete_library` ends with `purge_search_index` (bounded FTS5 merge); `tests/test_knowledge_store.py` |
| Coverage reported before a restored store has vectors | KB-Q01 harness | Health says unknown/rebuilding until measured | Done: a coverage record names the vector store it measured (`store_id`); another store's, or none, reads `unknown` until the next pass; `tests/test_knowledge_vectors.py` |
| Meaning recall at default HNSW settings | KB-Q01 harness | Tuned HNSW parameters, measured | Done: 48 neighbours, construction 400, search 256 (40/40 planted answers at 10k chunks in EN, AR and mixed in 11 of 12 runs, vs 31-39 at Chroma's defaults); older collections widened and rebuilt from their own vectors by the repair pass |
| Knowledge follow-ups from KB-Q01 | KB-Q01 harness | A dropped collection's preview text and rows leave `chroma.sqlite3`'s free pages; an evicted large index in a crowded shard never reloads stale | Done: no text stored beside vectors, older collections rebuilt without it, a compaction after deletes (a planted word gone from every byte; the compaction removed as control); Kazma's own index pool (no eviction; 1,030 of 1,180 found at the default pool, all at Kazma's), a 500-library ceiling refused in EN/AR; the library and model filters dropped when not needed (353 -> 6 ms at 100k); `tests/test_knowledge_vector_text_and_pool.py` |
| Meaning recall at 100k chunks in one library | KB follow-up measurement | A search breadth that grows with the collection, measured | Queued: exact search ranks 40/40, the index finds 32 at breadth 256, 40 at 2,000 (19.5 ms) |
| Entities keyed by (tenant_id, id) | M21 | Composite key, every statement, a migration | Done in code: `tests/test_memory_entity_key.py` (70 tests, a source gate over every entity statement, two tenants through every route and tool, the migration on an old-key copy); the live migration is queued |
| Tool-written facts still labelled the user's | M03 | Relabel tool; run on live after review | Tool done (`scripts/memory_relabel_provenance.py`, `tests/test_memory_relabel_provenance.py`); the live run is queued |
| Held-out abstention 0/8 | M24 | One principled signal measured under the trade rule, or the attempt recorded | Measured, not adopted: an unknown-word signal raises held-out abstention to 4/8 but breaks the trade rule (paraphrase, Arabic, noise, update answers lost; MRR 0.844 -> 0.774); every variant inside the rule leaves it at 0. Recorded in the guide's benchmark report and M24 |
| Arabic-Indic digits under OCR | Q02, D2 | The best local engine measured and integrated, Tesseract as fallback | Done: a neural engine (PP-OCRv5 detection, EasyOCR `arabic_g1`, a letter vote; ONNX Runtime in the worker): ar-ocr words and numbers 1.00 vs Tesseract 0.66 / 0.33, 17 of 19 fidelity cases qualified (16 with Tesseract); `tests/test_document_ocr_neural.py` (21, each with a control), real Docker 23 passed. Left: `ar-ocr-table` (one word), `en-ocr-degraded`, Arabic decimal separators |
| Web chat has no wall-clock turn budget | Full audit 2026-09-30 | The web honours the same budget setting as the chat apps | Done: `kazma_core/agent/turn_budget.py`, one rule for every surface (Settings -> System, the variable, `/long` lifts it; an approval's wait never counted), `tests/test_turn_budget.py` (one reader; every graph run inside the budget; the old pump as control), `tests/e2e/test_turn_time_limit.py` (the real route) |
| Temporal durable path | SQ03, SW-E16, D9 | Time-skipping test server (download approved), Temporal timeouts | Done locally, not committed: `tests/test_swarm_temporal_durable.py` (25 on the real time-skipping server, a child process killed mid-step; seven defects fixed, each with its control). Its CI job waits on the billing lock |
| A new task whose RUNNING row cannot be written ran without it | SQ04 follow-up | Admission retried, then refused (`task_store_unavailable`, 503 + Retry-After, EN/AR) | Done, not committed: `tests/test_swarm_chaos.py` (the run-anyway admission as control), chaos recorded again (13 cases) |
| The swarm soak harness test flaked | SQ01 | The cause found and fixed | Done, not committed: a shared-breaker refresh put an older state back (`tests/test_swarm_breaker_shared_refresh.py`), and a 12 s restart starved the 24 s run (the run's duration now counts load only) |
| A memory backup older than a table could not be restored | Memory soak, cycle 420 | The restore brings the copy's schema up to date before it reads it | Done: `test_a_backup_from_before_a_table_restores_and_keeps_its_forgets` (the old restore as control) |
| Model-pass extraction recording | M24 | `scripts/memory_bench.py extraction --record`, then `lock` | Queued (after the Memory merge) |
| The LLM ledger row is written on the event loop | Swarm routing agent | Every ledger write from async code off the loop, a static gate | Implemented (`arecord_llm_call`); merging |
| Tests that load the real embedder or reach a real provider | Swarm routing agent | Stubbed, and a permanent guard (no non-loopback socket, no real model, unless marked) | Implemented (`tests/_egress_guard.py`, ~70 test files stubbed); merging |
| A daily job fired twice on one local day | Scheduled 28-day soak on c4e8f51c: 2 of 67k occurrences, 224 restarts | The cause found from the ledger; a product fix or a precise check fix, the 28-day soak passing | Done: not a product defect. Both jobs were edited to a later time the same day and fired as asked; the check counted a job's fires per day across timings. It now counts per series (timing between edits), with controls that a real double fire is still caught; the 28-day soak passed (68,397 occurrences, 251 restarts, 0 violations) |
| A slow `docker info` paged the owner | Live log 2026-10-15 00:05 | A slow answer soon after a good one is not an outage | Done: `tests/test_required_capability_readiness.py` (slow after an answer, never answered, past the grace, answered down; the old 2 s rule as control) |
| Every backup failed to copy the held writer lock | Live log | The registry keeps it out of backups | Done: `tests/test_universal_backup_held_files.py` (the lock held while the backup runs; the old copy as control) |
| WeasyPrint warns about its own stylesheet at every render | Live log | Only those selectors filtered | Done: `tests/test_weasyprint_log.py` |
| A prune reported Time Travel's disk use growing | Full suite 2026-10-15 | `-shm` not counted as disk use | Done: `tests/test_snapshot_retention_actually_prunes.py` |
| A deleted library's words left in the keyword index | KB-Q01 72-hour soak, cycle 76 | The purge removes every deleted word; a purge cut short is finished | Done: root cause FTS5 drops a delete marker only when a merge writes its oldest segment; `db/fts5_purge.py` checks the structure and rebuilds; Memory's Forget purge and the Chroma compaction share it; a durable purge mark; `tests/test_fts5_purge.py`, child kills in `tests/test_knowledge_store.py`; the failing run's replay passes 81 cycles |
| A malformed document id answered 400 with a WARNING | Live probe 2026-10-15 | It reads like an unknown id (404) | Done: `InvalidIdError`, `tests/test_document_malformed_ids.py` (the old mapping as control) |
| Documents refused on a strict install: no broker | Live readiness 2026-10-15 (`container_required` on, `process` profile, no broker) | The guard runs the broker, the profile is a Settings switch, one command builds the image for the code | Done in code: `tests/test_guard_document_broker.py`, `tests/test_document_worker_profile.py`, `tests/test_document_worker_image.py`, `tests/js/test_settings_document_profile.js`, real Docker `tests/test_document_broker_guard_docker.py` (broker ready 5.2 s, a real PDF through the container 1.25 s, a killed broker back in 9.3 s); the live switch is the owner's (below) |

## Measurements (soaks)

| Soak | Started | Ends | Report |
|---|---|---|---|
| Memory (72 h) | 2026-10-14 20:22 UTC | 2026-10-17 | `C:\Users\balfa\kazma-soak\2026-10-14\memory\report.json` |
| Documents Q03 (48 h) | 2026-10-14 20:22 UTC | 2026-10-16 | `...\2026-10-14\documents\report.json` |
| Swarm SQ01 (24 h) | 2026-10-14 21:36 UTC | 2026-10-15 | `...\2026-10-15\swarm\run\report.json` |
| Scheduled Stage 5 (7 fake days) on c4e8f51c | 2026-10-14 | passed | `...\2026-10-15\scheduled\run\report.json` |
| Scheduled (28 fake days, 8,000 jobs) on c4e8f51c | 2026-10-14 | failed: 2 double fires, being investigated | `...\2026-10-15\scheduled-28d\run\report.json` |
| Scheduled Stage 5 on b52b2fab (the alert outbox) | 2026-10-15 | passed: 5,228 occurrences each ended once, 260 alerts = 257 expected + 3 recorded redeliveries, 0 violations | `...\2026-10-15\scheduled-b52b2fab\run\report.json` |
| Time Travel TT-G3 on 680c5367 | 2026-10-14 22:08 UTC | stopped after 258 clean cycles (the code changed) | `...\2026-10-15\time-travel\run\report-old-code-680c5367.json` |
| Time Travel TT-G3 (72 h) on 87b0cea8 | 2026-10-15 | 2026-10-18 | `...\2026-10-15\time-travel-87b0cea8\run\report.json` |
| Knowledge KB-Q01 (100k, 1M, multi-day) | after its defects are fixed | | |

SQ05 (published limits) is tied to the Swarm soak's numbers when it ends.

## Owner-held

| Item | Why it is the owner's |
|---|---|
| Q04 independent security review | The owner reviews it later (decided 2026-10-15) |
| GitHub Actions billing lock | The account's payment; no CI runs until it is fixed |
| Cloudflare Web Analytics switch | The site allows it and the policy says so (kazma.ai `f2d7564`, served 2026-10-15 with a clean console in EN and AR), but Cloudflare injects no beacon today: Web Analytics is off for the project. Turning it on (Cloudflare dashboard: Web Analytics, automatic setup for kazma.ai) is the owner's |
| Switch the live install to the container profile | After the deploy: `.venv\Scripts\python.exe scripts\document_worker_image.py build`, then Settings → Documents → *Use the container profile*, then restart the guard once (the running guard predates broker supervision; `docs/docs/ops/document-processing.md`, "Restart the guard") |

## Done today

- kazma.ai allows Cloudflare Web Analytics; the privacy policy (EN, AR) says
  what it collects (`Mubder/KazmaAI` `f2d7564`).
- The pre-deploy backup copy (176 MB, with a vault copy) moved to the
  Recycle Bin.
