# Memory industrial hardening — continuous execution

Status: OPEN -- 2026-10-13

## Objective

Execute the complete Memory audit remediation and qualification plan, with
current evidence for each requirement. Fixing only the witnessed defects, or
getting a green suite, does not make Memory industrial: the existing memory
suites were green with every defect present.

Memory must meet four conditions:

- it says only what is true;
- it forgets everything it is told to forget, in every store that holds a
  copy;
- it stays bounded and honest about failure;
- it is measured on the profiles it claims.

## Baseline

**Code state:** commit `d7e27c194d3f170092a9c405709b9197a2c00161` plus the
uncommitted working tree of 2026-10-13.

- The three audits found no uncommitted change under
  `kazma-core/kazma_core/memory/`.
- At merge time, `memory/backends.py` and `memory/state_backend.py` were
  modified and `db/pg_schema.py` was untracked. These changes route
  Postgres DDL through `db.pg_schema.ensure_schema` and do not change any
  finding.
- `state_backend.py` lines after about line 240 are 4 lower than the
  audits' citations. The merged audit gives the details.

**Audits:**

- Merged: `docs/audits/AUDIT_MEMORY_2026-10-13.md`.
- Parts, in `session-artifacts/audits-2026-10-13/`: `memory-recall.md`
  (MEM-R01..R15), `memory-lifecycle.md` (MEM-L01..L16) and
  `memory-surfaces.md` (MEM-S01..S12).
- Scores, as each part gave them (not combined):

  | Part | Capability and architecture | Demonstrated industrial readiness |
  |---|---|---|
  | Recall | 7/10 | about 4.5/10 |
  | Lifecycle | 7/10 | about 5/10 on the default profile; about 3/10 on Postgres-primary |
  | Surfaces | 7/10 | about 4.5/10 |

- The 43 original findings merge into **36 rows: P1 7, P2 17, P3 12**.

**Witnesses** (in `session-artifacts/audits-2026-10-13/`):

| File | Result | Log |
|---|---|---|
| `memory_recall_probes_2026_10_13.py` | **25 passed** in 7.48 s | `memory_recall_probes.log` |
| `memory_lifecycle_probes.py` | **20 passed** in 7.45 s | `memory-lifecycle-probes.log` |
| `memory_surfaces_probes.py` | **19 passed** in 1.13 s | `memory-surfaces-probes.log` |

Each of the 64 witnesses asserts that a defect is **present**.

- **Witnesses must never become permanent tests that require the defects.**
  Invert each one into a regression that asserts the fixed behaviour, with
  the original defect as its negative control (AGENTS.md §28).
- Source-text witnesses must become behavioural tests where practical. The
  wholly or partly source-text ones are R03 (first), R04 (first), R10, R11,
  R12, S06 and S12.

**Existing memory gates at baseline:**

| Group | Result | Log |
|---|---|---|
| Recall | **185 passed** | `memory_recall_regression.log` |
| Lifecycle | **119 passed, 2 skipped** (the skips need a real Postgres) | `memory-lifecycle-existing-gates.log` |
| Surfaces | **151 passed** | `memory-surfaces-existing-gates.log` |

- The groups overlap (`test_memory_forget.py` is in all three), so the
  counts are not a total.
- These gates are incident locks. Re-point an assertion that a fix changes;
  never delete it. One of them locks in a gap and must be re-pointed:
  `tests/test_memory_mirror_sync.py::test_no_mirror_no_pass`, the
  primary-mode skip (M06).

## Requirements and completion evidence

Rows are ordered by stage. Each ID cell lists the original IDs it covers.
The stage exit gates are below the table and in the merged audit.

| ID | Required result | Evidence required | State |
|---|---|---|---|
| **Stage 1** | **Memory holds only what is true, and Forget reaches everything (default profile)** | | |
| M01 (P1): MEM-S01, MEM-S02, MEM-L03, MEM-L11 | Forget, invalidate, supersede, archive and Knowledge-library delete leave no copy of the value or its vector in any memory store, index or queue payload, and no search surface returns it. `unified_items` is retired, or kept as a derived index cleaned on every source change. A memory store registry gives each table its forget behaviour, export section or declared exclusion, retention and growth bound; `snapshots.db` is listed (TT-05). Backups, exports and snapshots are named as the erasure horizon, with their retention, next to Forget and in the guide. | A forget-completeness token scan over every table and text column of both memory databases, seeded through product writers with `ops_conn`, plus local and remote fact-vector absence. Invalidate, archive and library delete, each followed by an empty `federated_search`. A table-classification gate and an `upsert_unified` writer↔retraction gate. Negative controls: the current `_forget_fact_rows`, the upsert-only `unified_index.py`, a planted unclassified table. The S01, S02 and L03 witnesses inverted. AGENTS §15I and §19 E2, the guide (`memory-and-rag.md:305-309`), the UI text and the `belief_mutation.py:431-433` comment corrected. | Open |
| M02 (P1): MEM-R01 | Entity ids preserve every script. One id helper, with no constant fallback; the other three slug copies deleted. Existing `entity` rows are repaired from provenance or quarantined. | A bilingual extraction test (Arabic, Persian, CJK, emoji) through `_apply_beliefs_to_v2` and `mutate_belief`: distinct ids, values preserved, no cross-subject supersede. An AST gate on the single helper, with the current slug rule as negative control. A repair procedure tested on a copy. The live read-only sizing and the repair are run by the owner. | Open |
| M03 (P1): MEM-R02 (+ MEM-S07 input screening) | `user_explicit` comes only from the user's own channel. Agent writes are labelled agent-asserted and screened for injection. An agent write that supersedes or retires a user fact needs a commitment or HITL decision. | A test that a tool-initiated write never blocks a later user correction. An injection corpus (pages, emails, documents) is refused and cannot create or protect a user fact. Negative control: the current labels. AGENTS §20 updated with the provenance ladder. | Open |
| M04 (P1): MEM-R03 | No memory write runs on the event loop. Knowledge text is never stored as a chat turn's user text: the promotion is removed, or moved off the loop, stored as a separate kind, excluded from extraction and summaries, and forgotten on library delete (KB-S03). | An event-loop SQLite and embedder tracer over a real `supervisor_node` turn with library injection. Recall after a promotion shows no Knowledge text as history. `_LOOP_STALL_HELPERS` extended. Negative control: the current code. | Open |
| M05 (P1): MEM-L01 | A summary is never stored or re-activated from a forgotten turn, or over a forgotten summary. | The inverted L01 witnesses. A static gate: every statement that sets a `memory_summaries` row active carries a status predicate. Negative control: the current `_store`. The guide (`memory-and-rag.md:359-362`) holds afterwards. | Open |
| M08 (P2): MEM-L05 | A forget interrupted at any step, or racing a write, a keep-out or recovery, converges within one maintenance pass. Recovery and every rebuilder (`scan_old_sessions.py`, TT-05) honour the ledger. | A journaled forget. A fault-injection matrix that raises after each step, followed by one sweep, after which M01's completeness test passes. The inverted L05 witnesses. Negative control: the sweep disabled. AGENTS §15I ("EVERY copy", "finished by asking again") made true. | Open |
| M09 (P2): MEM-R10 | Undo, edit, replay/restore and abort forget, or mark superseded, the turns they rewind. The deep pass checks that its episode is still live. Decided together with TT-03. | A real-graph rewind-then-recall test for each rewind path: the seven state rewriters the witness lists. Negative control: the current behaviour. | Open |
| M10 (P2): MEM-L07 | Restoring an older memory generation, or importing a migration bundle, does not undo forgets. Snapshots are covered too (TT-05). | An append-only forget journal outside `memory_state.db`, backed up separately, and replayed at boot and after a restore or import. A test that restores a pre-forget backup and boots: no re-creation by reconcile, mirror tombstones kept. A runbook step and a drill check. Negative control: the same run without the replay. | Open |
| M22 (P2): MEM-S05 | Export covers every tenant memory table minus declared exclusions. It is streamed. Forgotten items appear as "when, never what". | A table↔section gate derived from M01's registry, with a planted-table negative control. A streaming and size test. The UI (`memory.py:425`) and the guide (`:321-325`) corrected. | Open |
| **Stage 2** | **Model and profile boundaries** | | |
| M11 (P2): MEM-R13, MEM-S07 | Every memory tool output that reaches the model is fenced and shows the display text. "Degraded" is distinct from "empty". About me's write scope matches its authority: per user in multi-user mode, or admin-only. | `tool_builtins/memory.py` added to `FENCED_TOOL_FUNCTIONS`. A behavioural test that a planted override string arrives inside `<kazma:data …>`. Negative control: the current output. An RBAC / per-user About me test. | Open |
| M07 (P1, multi-tenant): MEM-S03 | One tenant resolver for turns, routes, tools, gateway and probe. About me, the ledger, forget and export act on the tenant the user's turns are written under, in every offered mode. Until then, non-`shared` modes are refused under enforcement. "Also forget" reports `forgotten: 0`. | A matrix: mode {shared, per_platform, per_user} × enforcement {off, on} × principal {unbound, bound}. Negative control: the current two-resolver code. | Open |
| M21 (P2, multi-tenant): MEM-S04, MEM-R15 (entity-key item) | No tenant can read, write or probe another tenant's entity row through any route or tool. Entities are keyed by `(tenant_id, id)`, designed with M02's helper. | The tenant gate extended to every agent memory tool and to renaming one's own self entity. The S04 and R15-tenant witnesses inverted. Negative control: the current tool. AGENTS §16 states the key. | Open |
| M06 (P1, Postgres-primary): MEM-L02, MEM-R11 | In Postgres-primary mode recall never returns a forgotten or non-recallable tier, and a missed tombstone is repaired within one sweep through a durable outbox. Mirror tables are backed up, or declared rebuildable and verified at boot. Recall has a parity contract with a relevance-ordered keyword channel. | A real-Postgres test (`@pytest.mark.postgres`, disposable container) with an injected mirror-write failure. The `KAZMA_PG_TABLES` and boot-verification diff. `test_no_mirror_no_pass` re-pointed. The benchmark under the Postgres-primary role, within a stated delta of local. The slice gate widened to `state_backend.py`. Negative controls: the current skip and the recency slice. AGENTS §15K and §21A made true. | Open |
| **Stage 3** | **Bounded, durable and observable** | | |
| M12 (P2): MEM-R09, MEM-L09 | Failed episode writes and LLM deep passes are retried with backoff and counted. "Provider unavailable" is distinct from "empty result". Dead letters are kept until acknowledged, counted in health and alerted. | Fault-injected mirror and LLM-outage tests: the task stays pending with backoff, then completes after recovery. `mirror_fail` / `deep_pass_failed` counters and health asserted. Negative control: the current `return True`. | Open |
| M13 (P2): MEM-L04 | Each queued task executes once. Memory-write tasks are not starved by maintenance. | The inverted L04 witness. A lane latency test: `post_turn_memory` behind two long maintenance tasks completes within a stated bound. Negative control: the batch claim. | Open |
| M14 (P2): MEM-R06, MEM-L08 | No embedding, network or remote-backend call runs inside a memory SQLite write transaction or `_mutation_lock`. The access bump is off the turn path, or has a short timeout. | A lock and transaction probe inside every embed, mirror, graph and vector hook used by `belief_mutation`, `macro_sleep`, `hygiene` and `forget`. Recall latency under a held write lock below a stated bound. Negative control: the current code. | Open |
| M15 (P2): MEM-R05 | One vector write per row, guarded by tier, under the writer's lock. No commit of a caller's transaction. | A spy test: one embedding, lock held, no mid-transaction commit. A forget-race test. Negative control: the current code. The `dual_write.py` comments made true. | Open |
| M16 (P2): MEM-R07 | Recall has a deadline. One embedding attempt per recall. An embedder circuit breaker, shown in health. "Degraded" distinct from "empty". | Hanging- and failing-embedder tests with time bounds. A log-count test: one failure log per breaker period. Negative control: the current code. | Open |
| M17 (P2): MEM-R04 | Recall and the other consumers receive the text parts only, capped. | A photo turn through the supervisor node, with recall's argument asserted. Negative control: the current `str(content)`. | Open |
| M18 (P2): MEM-R08 | The extraction embedding budget does not depend on the entity count. | The embed-call count at 60 and at 6,000 entities is equal. Negative control: the current code. | Open |
| M19 (P2): MEM-L06 | Every SQLite snapshot copy completes under continuous writes. | The inverted L06 witness through the product helper. A class gate banning `backup(pages>0)`. Negative control: `pages=100`. The docstring and the guide (`:238`) made true. | Open |
| M20 (P2): MEM-L10 | Every sweep, enqueue and export failure surfaces at WARNING after N passes, and in health: oldest pending and processing age, and last success per sweep and per task type. | The inverted L10 witnesses, plus enqueue and export cases. Negative control: the current swallow. | Open |
| M25 (P3): MEM-R14 | Recall cost is bounded in question length. | A timing test on a seeded store: a 2,000-word query within 2× of a 20-word query. | Open |
| M27 (P3): MEM-L12 | A failing reconciled turn is quarantined after N attempts, and the cursors advance. | The inverted L12 witness. | Open |
| M28 (P3): MEM-L13 | The embedding rebuild never leaves rows without a vector when the model cannot load. Pre-rebuild copies have retention. | The inverted L13 witness. | Open |
| M29 (P3): MEM-L14 | Tier transitions are compare-and-set. | An interleaving test with a forget between the read and the write. | Open |
| **Stage 4** | **Measured, recoverable and operable** | | |
| M24 (P2): MEM-R12 | A held-out question set. An extraction benchmark with Arabic and mixed subjects. Scale tiers with a latency gate. An explicit abstention target, and a reviewable trade-off rule. | A published report per release, on the tuning and the held-out sets, for both backends. Recorded extraction fixtures. The §15G ratchet rules followed. | Open |
| M23 (P2): MEM-S06 | Every memory and every summary can be reached for Forget from the UI. | An e2e test that forgets the 31st-oldest memory through the UI, in English and Arabic. Paging with "N of total", search, and Forget on probe and federated hits. The guide (`:389`) made true. | Open |
| M26 (P3): MEM-R15 (other items) | Remember phrases matched at word boundaries. The dead `mirror_belief` removed. An ordered functional lookup, with an at-most-one-current check. An idempotent deep-pass marker. A locked embedder singleton. | Unit tests per item. An orphan-function check. | Open |
| M30 (P3): MEM-L15 | The export output is portable and complete, and failures are reported. | The inverted L15 witness. An audit-log pagination test. | Open |
| M31 (P3): MEM-L16 | Sync, archival and exact search have measured, documented limits. | A benchmark at 10× and 100× the live size, with the hardware stated. Incremental sync with a periodic full verification. | Open |
| M32 (P3): MEM-S08 | Hygiene, forget, the counts and the probe report what actually happened. | The inverted S08 witnesses. The probe renders summaries. An Arabic error-localization test. | Open |
| M33 (P3): MEM-S09 | Graph exports are well-formed and flag truncation. | A parse test with `"`, `&`, `<` and RTL text. Negative control: `saxutils.escape`. | Open |
| M34 (P3): MEM-S10 | GET memory routes execute no writes. Nothing reads the settings store on the loop. | A GET write-trace gate. Cross-module stall-helper inference, with a control. AGENTS §35 holds for this path. | Open |
| M35 (P3): MEM-S11 | Every operator memory mutation is attributable (actor, tenant, ids, no values). Correct HTTP status codes. No raw exception text. | A recorder gate over the mutating routes, with a planted-route control. The daily digest counts Forgets. `safe_error` on unlink. | Open |
| M36 (P3): MEM-S12 | Database text is escaped in every HTML attribute. One set of length limits on memory writes. A tenant-scoped entity FTS. A request body size limit. A confirmed `/memory off`. | A DOM test with a hostile entity type. Bounds tests. A gateway test. | Open |

## Stage exit gates

A stage closes only when every row in it has current evidence of its full
scope **and** its exit gate passes. The full text of each gate is in the
merged audit, under "Staged path".

1. **Stage 1:**
   - forget completeness after faults and races;
   - no search surface returns forgotten, invalidated or deleted text;
   - the bilingual extraction suite passes;
   - the injection corpus cannot create or protect a user fact;
   - zero memory writes on the event loop;
   - rewinds and restores keep forgets;
   - Export ⊇ registry.
2. **Stage 2:**
   - the fence gate passes;
   - the tenant matrix passes, and the tenant gate covers 100% of routes and
     tools;
   - real-Postgres primary-mode repair, tier filter, backup and benchmark
     delta pass.

   Until this gate passes, the Postgres-primary and multi-tenant profiles
   are not claimed.
3. **Stage 3:**
   - p95 recall latency within budget at 10× and 100×, with the hardware
     stated;
   - recall bounded with the embedder down or hanging;
   - every injected failure retried and visible;
   - no double-run in a 6-hour-sweep simulation;
   - no deep extraction lost to a 10-minute outage;
   - snapshot copies bounded under writes;
   - no slow I/O inside write transactions.
4. **Stage 4:**
   - published tuning and held-out scores for both backends;
   - restore drills on both profiles, with RPO and RTO;
   - declared table bounds;
   - a multi-day soak with restarts;
   - the EN/AR browser forget e2e test.

Do not silently remove, shorten or mark complete a soak or a measurement
that is still outstanding.

## Start here

**Read first:**

1. This file and `AUDIT_MEMORY_2026-10-13.md`: the index, the claims
   tables, and the "Corrections" list (for example, `memory_invalidate` is
   already danger-tier).
2. The part report and the witness file for the rows you take.
3. AGENTS.md:
   - §15 A–L (memory), §16 (multi-tenant memory), §20 (commitment and
     source trust), §21A (PG tables), §26 F/G (errors, fencing);
   - §28 (negative controls), §35 (class gates, loop stalls), §37 (store
     registry);
   - the Testing & Validation section.
4. `docs/docs/guide/memory-and-rag.md`, `docs/plans/MEMORY_REMAINING.md`
   and `docs/plans/MEMORY_NOTHING_LOST_PLAN.md`.
5. **Stage 1 code:**
   - `memory/forget.py`, `unified_index.py`, `federated_search.py`;
   - `belief_mutation.py`, `entity_resolution.py` (and the slug copies in
     `belief_mutation.py`, `dual_write.py` and `backfill_v2.py`);
   - `dual_write.py`, `topic_summaries.py`, `rehydrate.py`, `export.py`;
   - `agent/tool_builtins/memory.py`, `agent/graph_supervisor.py:968-1223`;
   - `kazma_ui/memory_api.py`.
6. **Gates your fixes will touch:**
   - `tests/test_memory_forget.py`: declares every episode insert site.
   - `tests/test_memory_deletes.py`: declares every hard delete.
   - `tests/test_memory_mirror_sync.py`: declares every
     `UPDATE episodes SET tier/text`; re-point `test_no_mirror_no_pass`.
   - `tests/test_memory_routes_tenant_scope.py`.
   - `tests/test_memory_nothing_lost.py`: the slice gate.
   - `tests/test_static_gates.py`: `_LOOP_STALL_HELPERS`,
     `FENCED_TOOL_FUNCTIONS`.
   - `tests/test_memory_benchmark.py`, `tests/test_store_registry.py`,
     `tests/test_debt_ratchet.py`.
7. **Cross-area trackers** in this folder: `AUDIT_TIME_TRAVEL_2026-10-13.md`
   and `TIME_TRAVEL_INDUSTRIAL_HARDENING_GOAL.md` (TT-03, TT-05), and
   `AUDIT_KNOWLEDGE_2026-10-13.md` (KB-E08/KB-S03, KB-S02, KB-S06, KB-S01).
   Fix a shared defect once, with one gate serving both trackers.

**Run rules:**

- **Priority.** Run every test process at below-normal priority, because the
  live server runs on this machine. `scripts/fast_test.py` lowers its own
  processes.
- **Test order.** Run targeted tests per change: the inverted witnesses, the
  touched gates and their siblings. Run the full suite **once** at the end,
  with `python scripts/fast_test.py` and **no `--chunks`**. `fast_test`
  skips `tests/e2e`, so also run the e2e tests the rows need (M23, and the
  Arabic page tour after any template or i18n change).
- **The live install.** Never touch the live install at `C:\Users\balfa\kazma`:
  no edits, no copies, no reads by tests. Never start, stop or reload the
  Kazma server. The live data repair and sizing in M02, M03 and M04 are the
  owner's to run.
- **Postgres.** Use disposable, resource-capped containers only (a throwaway
  `postgres:16` with memory and CPU limits, removed afterwards), never the
  live database container. Mark real-Postgres tests `@pytest.mark.postgres`.
- **The memory benchmark ratchet** (AGENTS.md §15G):
  - `tests/fixtures/memory_bench/thresholds.json` is stamped with the
    dataset version.
  - `python scripts/memory_bench.py lock` raises floors and refuses to lower
    them.
  - `lock --rebaseline` is only for a new dataset version.
  - Any change to the dataset's texts needs `vectors` re-recorded with the
    real model (bge-m3). CI replays the recorded vectors.
  - M24's held-out and extraction sets are new dataset content.
- **Fix the class, not the instance** (§28, §35). Each fix lands with:
  - a behavioural test;
  - a class gate over its siblings, where one exists;
  - a negative control that shows the gate failing on the old code.

  A debt-ratchet count that goes down is locked into the baseline in the
  same change.
- **Docs move with the code.** Correct AGENTS.md, the guide and the UI text
  in the same change as the code they describe. The claims tables in the
  merged audit list each one.
- **Completion.** No row is complete without current evidence of its full
  scope. Preserve tenant scope, HITL paths, the forget ledger, Arabic text
  policy and line endings. Fix equivalent sibling paths together.
- **No commits or pushes** unless the user asks.

## Execution log

- 2026-10-13: Tracker opened from the merged audit. No remediation started.
