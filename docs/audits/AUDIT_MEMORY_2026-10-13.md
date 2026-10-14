# Memory audit (write and recall, lifecycle, surfaces, merged) — 2026-10-13

> Source reports, witness files and logs: `session-artifacts/audits-2026-10-13/` (local working evidence, not committed). A file named below without a folder is there.

This document merges the three Memory audits of 2026-10-13 into one audit. The
parts stay the detailed record. Every original finding ID is kept, so each
witness can still be traced to its finding.

| Part | File (in `session-artifacts/audits-2026-10-13/`) | Scope | Original IDs | Witness file |
|---|---|---|---|---|
| Write and recall path | `memory-recall.md` | recall ranking, vector and keyword search, extraction, belief mutation, episode writer, embedder, supervisor injection, memory agent tools | MEM-R01 to MEM-R15 | `memory_recall_probes_2026_10_13.py` |
| Maintenance, durability, lifecycle | `memory-lifecycle.md` | worker and schedulers, durable queue, macro sleep, vector repair, recovery, turn reconcile, forget, weekly summaries, Postgres mirror, backups, export | MEM-L01 to MEM-L16 | `memory_lifecycle_probes.py` |
| Surfaces and boundaries | `memory-surfaces.md` | `/api/memory` (both routers), Memory page and console, About me, chat "Memory…" menu, agent memory tools, gateway `/memory`, prompt entry | MEM-S01 to MEM-S12 | `memory_surfaces_probes.py` |

Reviewed state: commit `d7e27c194d3f170092a9c405709b9197a2c00161` plus the
uncommitted working tree of 2026-10-13 (see "Working-tree drift" below).
Tracker: `MEMORY_INDUSTRIAL_HARDENING_GOAL.md` in this folder.

Merged IDs are `M01` to `M36`. Each names the original IDs it covers.

## Verdict

Each part scored its own scope. These are the auditors' engineering
judgments, not benchmark scores. This merge does not compute a combined
score.

| Part | Capability and architecture | Demonstrated industrial readiness |
|---|---|---|
| Write and recall path | 7/10 (recall design and capability) | about 4.5/10 |
| Maintenance and lifecycle | 7/10 (architecture and intent) | about 5/10 on the default single-node profile (local SQLite, optional mirror); about 3/10 on the Postgres-primary / multi-replica profile |
| Surfaces and boundaries | 7/10 (capability and architecture) | about 4.5/10 |

The three parts agree on the shape of the problem. Memory has careful
machinery:

- one evidence ranker;
- exact vector search over every row;
- bilingual content words;
- a forget ledger with tombstones;
- event-time facts;
- a durable queue with leases;
- verified recovery of erased text;
- mirror anti-entropy;
- a benchmark with a ratchet;
- route gates with negative controls.

What fails is a set of promises the product makes, at boundaries that the
existing gates do not measure:

- **Forget** is true on the main path and false at the edges. A forgotten
  fact is still served by search, and its value stays in at least five other
  stores. The weekly-summary pipeline can write a forgotten turn back, or
  bring back a forgotten summary. A forget cut short is reported done. A
  restore undoes forgets. Rewinding a chat does not touch memory.
- **What memory says** can be wrong:
  - Arabic entity names collapse to one id (`entity`), corrupting facts.
  - Writes started by the model carry the user's highest trust and block the
    user's own corrections.
  - Knowledge-library text is stored as the user's words, written from the
    event loop.
- **Failure** is mostly invisible:
  - Failed writes and LLM outages are acknowledged as success.
  - Several sweeps fail at DEBUG.
  - Recall has no deadline.
- **Profiles** beyond the default single operator are not ready:
  - Postgres-primary recall reads a store that nothing repairs, without a
    tier filter.
  - The tenant is resolved one way for turns and another way for the
    surfaces.

All 64 witnesses passed, confirming every witnessed defect. The existing
memory suites were green at the same time: 185, 119 (+2 skipped) and 151
passed. One existing gate locks in a gap: it asserts that the mirror sync is
skipped in Postgres-primary mode.

Each part said what it would withhold a claim for:

- **Recall:** MEM-R01, R02 and R03 fixed and gated, and the benchmark
  measuring the real extraction path.
- **Lifecycle:** MEM-L01 to L05 closed.
- **Surfaces:** any privacy-control or multi-tenant claim, until MEM-S01 to
  S04 are fixed and gated.

Merged position: **do not claim Memory is industrial until the Stage 1 exit
gate passes. Do not claim the Postgres-primary / multi-replica or the
multi-tenant profile until the Stage 2 exit gate passes.**

The strongest single signal is that three independent reviews each found
the same class, forget leaving copies (M01), each with its own witnesses.
The Time Travel and Knowledge audits found further copies from their side
(TT-05; KB-E08 and KB-S03).

After merging, the 43 original findings are 36 merged findings:
**7 P1, 17 P2, 12 P3**. Two of the P1s are profile-scoped:

- M06: the Postgres-primary profile.
- M07: the multi-user and tenant-mode profiles.

Top three on the default profile:

1. M01: Forget does not reach the copies of a value, and one copy is served
   by search.
2. M02: Arabic entity names collapse to `entity`.
3. M03: model-started writes outrank the user.

## Scope and evidence

### Code reviewed by the parts

- **Recall:**
  - `memory/recall.py` (every path), `vector_engine.py`, `query_terms.py`,
    `episode_text.py`, `remember_request.py`;
  - `consolidator.py`, `dual_write.py`, `belief_extractor.py`,
    `belief_mutation.py`, `predicates.py`, `entity_resolution.py`,
    `ego_anchor.py`, `hygiene.py`, `embedder.py`, `backends.py`;
  - the recall parts of `state_backend.py`;
  - `worker_bootstrap._handle_micro_consolidation`, `turn_reconcile.py`,
    `federated_search.py`, `benchmark.py`;
  - `agent/graph_supervisor.py:968-1223`, `agent/tool_builtins/memory.py`,
    `kazma_ui/turn_runtime.close_turn`.
- **Lifecycle:**
  - `worker_bootstrap.py`, `task_queue.py`, `macro_sleep.py`, `reembed.py`,
    `rehydrate.py`, `turn_reconcile.py`, `forget.py`, `topic_summaries.py`,
    `state_backend.py`, `backup.py`, `export.py`, `unified_index.py`,
    `dual_write.py`;
  - `hygiene.invalidate_belief`, the audit and dual-write parts of
    `belief_mutation.py`, `v2_health.py`, `schema_v2.py`, `entity_retire.py`;
  - parts of `chat_history.py`, and the Postgres-primary episode path in
    `recall.py`;
  - `backup/universal.py`, `db/pg_backup.py`, the `app.py` shutdown wiring;
  - the memory guide, the disaster-recovery guide, and AGENTS.md §15 (A–L),
    §21, §27, §28, §35 and §41;
  - `docs/plans/MEMORY_REMAINING.md` and `MEMORY_NOTHING_LOST_PLAN.md`. None
    of the lifecycle findings is in their backlog. R6 (the reranker) and the
    S1 live cleanup are the only open items there.
- **Surfaces:**
  - `kazma_ui/memory_api.py` (27 routes), `routes_direct/memory.py`
    (25 routes), `routes_direct/_shared.py`;
  - `templates/memory.html`, `components/memory_console.html`,
    `static/js/memory.js` (spot-read), and `static/js/memory_console.js`. All
    38 `innerHTML` sinks were enumerated, about 25 read, and every sink that
    renders memory-derived text was checked.
  - the Settings About me and tenant-mode controls; the chat "Memory…" menu
    and the delete-with-forget flow (`chat.js`);
  - the agent memory tools and their HITL tiers; the gateway `/memory`
    command;
  - prompt entry (`graph_supervisor.py`, `graph_helpers.py`, `recall.format_*`,
    `profile.py`);
  - `forget.py`, `hygiene.py`, `unified_index.py`, `federated_search.py`,
    `belief_mutation.py`, `self_hub.py`;
  - the CSRF middleware and RBAC prefixes, the memory guide and
    `MEMORY_REMAINING.md`.

No part changed product code, tests, docs, CI or configuration. No part
started or reloaded a server. No part read the live install.

### Fresh checks run by the parts (2026-10-13, Windows 11, Python 3.12.9)

| Run | Result | Time | Log |
|---|---|---|---|
| Recall witnesses | **25 passed** | 7.48 s | `memory_recall_probes.log` |
| Lifecycle witnesses | **20 passed** | 7.45 s | `memory-lifecycle-probes.log` |
| Surfaces witnesses | **19 passed**, 1 deprecation warning | 1.13 s | `memory-surfaces-probes.log` |
| Recall's existing gates: `test_memory_relevance`, `_benchmark`, `_event_time`, `_forget`, `_episode_text`, `_predicate_names`, `_graph_clean`, `_every_turn`, `_tool_provenance`, `_summary_recall`, `_nothing_lost`, `test_recall_fresh_install` | **185 passed**, 1 deprecation warning | 51.68 s | `memory_recall_regression.log` |
| Lifecycle's existing gates: `test_memory_forget`, `test_memory_topic_summaries`, `test_memory_mirror_sync`, `test_memory_rehydrate`, `test_task_queue_lease`, `test_memory_nothing_lost` | **119 passed, 2 skipped** (the two need a real Postgres) | 32.16 s | `memory-lifecycle-existing-gates.log` |
| Surfaces' existing gates: `test_memory_routes_tenant_scope`, `test_memory_forget`, `test_memory_about_me`, `test_memory_deletes`, `test_memory_admin_api` | **151 passed**, 1 deprecation warning | 33.57 s | `memory-surfaces-existing-gates.log` |
| FTS5 cost micro-benchmark (standalone sqlite3, 20,000 rows × 60 words, one MATCH with N OR'ed prefix terms) | 5 / 50 / 500 / 2,000 terms: 1.3 / 10.8 / 143.6 / 668.2 ms | — | `fts_term_cost_microbench.log` (script `fts_term_cost_microbench.py`) |
| Lifecycle scratch experiment (stdlib sqlite3 only) | A `pages=100` online backup of a 6 MB WAL database, with a writer committing every 2 ms, restarted **778 times** in 2 s and finished only when the writer stopped | — | No artifact kept (run in the auditor's scratchpad) |

About these runs:

- **64 witnesses in total.** Each asserts that an observed defect is
  *present*, so a pass confirms a finding; it does not certify health.
  - Stores are temporary (`tmp_path`, `KAZMA_DATA_DIR` overrides).
  - Embedders and LLMs are fakes.
  - No network, Postgres, Docker or live data is used.
  - Lifecycle witnesses that need a race inject the interleaving
    deterministically. Two scale a time threshold: L04 (300 s to 1 s) and
    L09 (poll interval 2 s to 0.05 s).
- **The existing-gate groups overlap.** `test_memory_forget.py` ran in all
  three groups and `test_memory_nothing_lost.py` in two, so the
  455 executions are not unique tests.
- **The recall part ran at below-normal priority**, because a full suite was
  running on the machine.
- **The existing gates do not contradict the findings.** None of them covers
  a witnessed defect.
- **One existing gate locks in a gap.** `tests/test_memory_mirror_sync.py:259-265`
  (`test_no_mirror_no_pass`, verified during the merge) asserts that the
  sync is skipped in Postgres-primary mode (M06).

### Checks made during this merge

The merge ran no test, server or container. It read code to confirm the
load-bearing claims. These were verified in the current tree:

| Claim | File:line |
|---|---|
| The four slug fallbacks `or "entity"` | `memory/entity_resolution.py:48`, `memory/belief_mutation.py:151`, `memory/dual_write.py:93`, `memory/backfill_v2.py:70` |
| `unified_index.py` defines only `_conn`, `upsert_unified` and `search_unified`, with no delete | `memory/unified_index.py:32, 44, 81` |
| The unified index is said to hold current facts only (code comment) | `memory/belief_mutation.py:431-433` |
| `_forget_fact_rows` blanks `object` on `beliefs` rows only | `memory/forget.py:433-456` |
| The "already forgotten" branch re-runs only the fact step | `memory/forget.py:267-273` |
| The KB promotion is called inline in the async supervisor | `agent/graph_supervisor.py:1137-1145` |
| Neither `promote_kb_hits_to_episodes` nor `mirror_episode` is in `_LOOP_STALL_HELPERS` | search of `tests/test_static_gates.py` |
| `multi_user_enabled` is in `_LOOP_STALL_HELPERS` | `tests/test_static_gates.py:934` |
| `FENCED_TOOL_FUNCTIONS` does not list `tool_builtins/memory.py` | `tests/test_static_gates.py:1811` and search |
| Agent tool writes are labelled `user_explicit` | `agent/tool_builtins/memory.py:363, 565, 584` |
| Extraction defaults to `llm_inferred` | `memory/belief_extractor.py:533, 609` |
| HITL tiers: `memory_store` write, `memory_link_entities` write, `memory_invalidate` danger (also in the canonical list and `kazma.yaml`) | `safety/hitl.py:145, 231, 212, 373`; `kazma.yaml:183` |
| The rebuild branch of the summary store has no status predicate; the insert branch is guarded | `memory/topic_summaries.py:612-619` (rebuild), `:636` (insert guard `WHERE memory_summaries.status = 'retired'`) |
| The native backup copies in batches and claims a consistent snapshot without blocking writers | `memory/backup.py:59` (`pages=100, sleep=0.01`), docstring `:1-7` |
| The universal backup copies in one step | `backup/universal.py:488-515` (`pages=-1`) |
| The memory mirror tables are not in `KAZMA_PG_TABLES` | `db/pg_backup.py:59-88` lacks `kazma_episodes` and `kazma_beliefs`, which are created at `memory/state_backend.py:208, 223` |
| The mirror sync is skipped in primary mode | `memory/state_backend.py:1003-1004` in the current tree; `:1007-1008` at the commit |
| The slice gate scans `vector_engine.py` only | `tests/test_memory_nothing_lost.py:536-553` |
| `entities.id` is a global primary key | `memory/schema_v2.py:148-149` |
| `memory_api._conn()` runs `ensure_primary_schema` on every call | `kazma_ui/memory_api.py:203-210` |
| The O4 note says the per-request DDL was fixed (it applies only to `open_memory_db`) | `routes_direct/_shared.py:74-80` |
| The local vector upsert issues `UPDATE … SET embedding` then `commit()` on its connection | `memory/backends.py:274` (`LocalSqliteVectorBackend`), around `:330-333` |
| The "nothing is written" comment | `memory/dual_write.py:350-353` |
| The "Lock only the local SQLite writes" comment | `memory/dual_write.py:371-374` |
| The reembed rule "The old vector stays until its replacement is written" | `memory/reembed.py:403-404` |
| Guide claims | `docs/docs/guide/memory-and-rag.md:238, 305-309, 321-325, 327-328, 359-362, 389` |
| UI claim "Download everything Kazma remembers, as JSON" | `kazma_ui/i18n/catalog/memory.py:425` |

**Corrections to the parts, made during the merge:**

1. **`memory_invalidate` requires approval.** MEM-R02 says
   `memory_invalidate` "similarly lets the model retire real user facts
   without approval". In the code it is `danger` tier (`safety/hitl.py:212`,
   canonical list `:373`, `kazma.yaml:183`), so it goes through an approval
   card. The rest of MEM-R02 stands: `memory_store` and
   `memory_link_entities` are `write` tier and need no card.
2. **The "nothing is written" quotation is a code comment, not AGENTS.md.**
   The recall part's AGENTS.md claims table attributes the quotation
   "nothing is written -- not the row, and not the embedding" to AGENTS.md
   §15I. The text is the
   comment at `dual_write.py:350-353`. It is listed below under code comments.
3. **The "consistent snapshot without blocking" claim is not in §21F.**
   MEM-L06 attributes this claim to AGENTS.md §21F. §21F describes the
   universal backup, which already copies in one step. The claim is the
   `memory/backup.py:1-7` docstring. The guide also calls native backups
   "the restore SoT" (`memory-and-rag.md:238`).
4. **Two report figures differ slightly from the logs.** This merge uses the
   log values.
   - MEM-R04: the report says 6,030 FTS terms; the log says
     `RECALL_FTS_TERMS=6029`.
   - MEM-R14: the report says 0.007 s for a 20-word seed; the log says
     `short20=0.006`.

### Working-tree drift since the parts

All three parts reported no uncommitted change under
`kazma-core/kazma_core/memory/`. At merge time `git status` shows:

- uncommitted changes in `memory/backends.py` and `memory/state_backend.py`;
- an untracked `kazma-core/kazma_core/db/pg_schema.py`.

The changes route Postgres DDL through `db.pg_schema.ensure_schema`. They do
not change the logic of any finding. They do shift line numbers:

- In `state_backend.py`, lines after about line 240 are 4 lower than the
  cited ones. For example, the primary-mode skip cited at `1007-1008` is now
  at `1003-1004`, and `search_episodes` cited at `476` is now at `472`.
- In `backends.py`, the shift starts after line 756 (`PgvectorBackend`). It
  does not reach `LocalSqliteVectorBackend` (line 274).

`db/pg_backup.py` and `config_store.py` were already modified when the
lifecycle part read them. Line numbers in this audit are the parts' (at
`d7e27c19`) unless marked as verified or current.

### Not run (by any part or by this merge)

- No live install, live logs or live databases. So the prevalence on live of
  these defects is unknown:
  - collapsed `entity` rows (M02);
  - tool-written facts (M03);
  - promoted Knowledge chunks (M04);
  - double-run tasks (M13);
  - native backup durations (M19);
  - the sizes of `unified_items` and `memory_audit_log` (M01).
- No real Postgres, pgvector, Qdrant, Neo4j, embedding model or LLM, and no
  Docker. MEM-L02 and L08 used stand-ins. MEM-R11 is source-level. The two
  real-Postgres mirror-sync tests were skipped.
- No browser or e2e run, including the Arabic page tour.
- No full suite. No soak.
- No multi-process queue claims, no `kill -9` crash tests, no POSIX run.
  Windows only.
- The benchmark was not re-recorded or re-swept.
- The TUI and CLI memory surfaces were not reviewed.

## What is already good

| Area | Assessment | Part |
|---|---|---|
| Relevance ranking | One `_rank_by_evidence` for local and Postgres-primary episodes, beliefs and weekly summaries. It scores lift over the question's background, applies a floor and a gap, labels weak hits, and falls back to words-only. A real step past rank fusion, and measured. | Recall |
| Vector search | Exact search over every comparable row (sqlite-vec or NumPy). It ignores other models' and other sizes' vectors and zero vectors, filters by tenant and tier, and never writes. | Recall |
| Bilingual query handling | English and Gulf/MSA stopwords, Arabic folding shared with Documents, whole-word prefix matching, Arabic "remember" phrases. | Recall |
| Tenant scoping | Every local recall statement filters by tenant; so do summaries and the mirror top-up. The route gate enumerates every `/api/memory` route from the app's registrars and calls it with another tenant's ids, with negative controls. Tenant resolution fails closed (`__unscoped__`), and undo tokens are bound to the tenant that minted them. | Recall, Surfaces |
| Forget design | Tombstones instead of deletes. A ledger written under every chat key. The writer, swarm bridge, legacy restore and past-chats search consult it. Summaries are emptied and rebuilt, Postgres mirror tombstones are written, and the agent's notes are tied to their chat. `tests/test_memory_forget.py` has negative controls. | All three |
| Bi-temporal facts | Statements are ordered by when they were said; an older statement is recorded as history; functional and set semantics are explicit. | Recall |
| Per-turn fencing | The recall block is fenced with its rules outside the fence. The transcript fallback is fenced. Weekly summaries pass `filter_injection`. The extractor rejects override phrases. | Recall, Surfaces |
| Event loop (mostly) | Recall, federated search and transcript search run off the loop. Post-turn work runs in a bounded pool with a durable-queue fallback. Every `memory_api` route is an async shell with a `_..._sync` body in `to_thread`. The gate traces SQLite on a running loop for routes and all 10 agent tools. | Recall, Surfaces |
| Measurement | A deterministic benchmark with recorded bge-m3 vectors, a ratchet with its own negative control, and a dataset built by a script. | Recall |
| Boot and cadences | `start_memory_worker()` starts every scheduler. One switch turns all of them off under pytest. The maintenance runner isolates failing sweeps and logs at WARNING (`worker_bootstrap.py:1015-1046`). | Lifecycle |
| Durable queue core | `BEGIN IMMEDIATE` claims, per-claim lease tokens, renewal, rejection of stale acks, dead-lettering of crash-looping rows, each with tests (`task_queue.py:231-436`, `tests/test_task_queue_lease.py`). | Lifecycle |
| Split databases | `memory_state.db` (hot reads) and `memory_ops.db` (queue, audit), both WAL with a busy timeout. The kept `dual_write` connection writes only inside `with conn:` (§35). | Lifecycle |
| Nothing lost | Archival keeps text and vectors. Recovery restores only verified text, and an outage is never a verdict. Turn reconcile has a settle window, composite cursors and idempotent counting. Backups are read with `immutable=1`. | Lifecycle |
| Mirror anti-entropy | `sync_state_mirror` compares digests in both directions and never deletes or pulls mirror-only rows. Region policies are counted, and `fail_closed` alerts. | Lifecycle |
| Backups overall | The universal backup copies every DB in one step. Restic retention is time-based. A restore drill exists. | Lifecycle |
| UI escaping | Memory-derived text is escaped at every `innerHTML` sink that renders it. One defense-in-depth gap remains (M36). | Surfaces |
| Mutation boundary | CSRF covers every non-GET `/api/` route. RBAC requires operator+ for mutations and admin for queue, reconsolidate and eval. The destructive agent tools are danger tier. | Surfaces |
| Delete discipline | `test_memory_deletes.py` holds every `DELETE FROM episodes/beliefs/entities` to a declared reason. | Surfaces |
| i18n and direction | Catalog labels, plural forms through `kazmaCount`, `translate="no"` on content, `<bdi>` around ids, and lists laid out in their own language. | Surfaces |
| Verified true by the recall part | Small talk is left out on all three episode paths. The forgotten tier is excluded and its texts emptied. Late writers pass the turn time. The hybrid vector search merges with the local score winning. The access bump is skipped inside diagnostics. The ratchet refuses to be lowered. | Recall |

## Ranked findings

**Priorities** (the parts' definitions, aligned):

- **P1:** fix before claiming industrial readiness, the forget guarantee or
  the named profile. These change what memory says or keeps, or stall every
  user.
- **P2:** significant correctness, availability, recoverability, failure
  visibility or measurement work.
- **P3:** hardening, smaller correctness issues and scaling limits.

**Merge rules:**

- A merged finding takes the highest priority among its sources.
- "Profile" says where a finding applies:
  - **default** is the single-operator profile: local SQLite, optional
    Postgres mirror, `shared` tenant mode, tenants not enforced;
  - **PG-primary** is `state.role=primary` / multi-replica;
  - **multi-tenant** is multi-user, production, or non-`shared` tenant
    modes.
- The order within a priority is this merge's engineering judgment.

**Single-operator posture.** The Kazma threat model records one operator per
install. The surfaces part reports the multi-tenant findings anyway, because
the codebase offers and gates those modes. This merge keeps them, scoped by
profile.

### Index

| ID | P | Finding | Original IDs | Profile | Stage |
|---|---|---|---|---|---|
| M01 | P1 | Forget, invalidate and library delete do not reach the copies of a value; one copy (`unified_items`) is served by search | MEM-S01, MEM-S02, MEM-L03, MEM-L11 | all; full on default | 1 |
| M02 | P1 | Non-Latin entity names collapse to the id `entity`; Arabic facts supersede each other and lose their values | MEM-R01 | all | 1 |
| M03 | P1 | Writes the model starts carry the user's highest trust and are not screened; the user cannot correct them | MEM-R02 (+ MEM-S07 input screening) | all | 1 |
| M04 | P1 | Knowledge-library chunks are written into chat memory, on the event loop, as the user's words | MEM-R03 | all | 1 |
| M05 | P1 | The weekly-summary pipeline writes forgotten content back and can resurrect a forgotten summary | MEM-L01 | all | 1 |
| M06 | P1 (profile) | Postgres-primary recall reads an unrepaired, unfiltered, recency-sliced, unbacked store | MEM-L02, MEM-R11 | PG-primary (mirror top-up also on default with mirror) | 2 |
| M07 | P1 (profile) | Surfaces and turns resolve the tenant differently; About me, "Don't remember", Forget and Export miss the user's memory | MEM-S03 | multi-tenant | 2 |
| M08 | P2 | Forget does not converge (cut short, raced, refilled by recovery, remote deletes never retried) | MEM-L05 | all | 1 |
| M09 | P2 | Rewinding a chat (undo, edit, replay, abort) does not rewind memory | MEM-R10 | all | 1 |
| M10 | P2 | Restoring `memory_state.db` silently undoes forgets and mixes generations | MEM-L07 | all | 1 |
| M11 | P2 | Recalled memory reaches the model unfenced through `memory_search`; About me is unfenced system text with operator-level write scope | MEM-R13, MEM-S07 | all (About me scope: multi-tenant) | 2 |
| M12 | P2 | Failed memory writes and LLM deep passes are acknowledged as success; the queue turns outages into permanent loss and hides dead letters | MEM-R09, MEM-L09 | all | 3 |
| M13 | P2 | The durable queue runs claimed tasks twice; maintenance starves memory writes | MEM-L04 | all | 3 |
| M14 | P2 | Embedding and network I/O run inside memory write transactions and the process-wide mutation lock | MEM-R06, MEM-L08 | all | 3 |
| M15 | P2 | Each vector write bypasses the writer's guards, is written twice, and can commit the caller's transaction | MEM-R05 | all | 3 |
| M16 | P2 | Recall has no deadline; embedder failures are neither memoized nor latched | MEM-R07 | all | 3 |
| M17 | P2 | On a photo turn the recall query is the Python repr of the message, base64 included | MEM-R04 | all | 3 |
| M18 | P2 | Each extraction embeds every entity name of the tenant; the name cache thrashes past 4,096 names | MEM-R08 | all | 3 |
| M19 | P2 | The native memory backup, the declared restore source of truth, restarts under writes | MEM-L06 | all | 3 |
| M20 | P2 | Several lifecycle failures are invisible above DEBUG; health lacks queue age and last success | MEM-L10 | all | 3 |
| M21 | P2 (profile) | Global `entities.id` lets one tenant write to and probe another tenant's entities | MEM-S04, MEM-R15 (entity-key item) | multi-tenant | 2 |
| M22 | P2 | "Export downloads everything Kazma remembers" is not true | MEM-S05 | all | 1 |
| M23 | P2 | Forget is reachable from the UI only for the newest 30 memories and 40 summaries | MEM-S06 | all | 4 |
| M24 | P2 | The quality measurement is narrower than the claims built on it | MEM-R12 | all | 4 |
| M25 | P3 | The cost of a long question is unbounded | MEM-R14 | all | 3 |
| M26 | P3 | Smaller write-path correctness items | MEM-R15 (other items) | all | 4 |
| M27 | P3 | One failing reconciled turn stops fact extraction for good | MEM-L12 | all | 3 |
| M28 | P3 | The embedding rebuild drops vectors before it knows it can encode; its pre-rebuild copy is never refreshed | MEM-L13 | all | 3 |
| M29 | P3 | `macro_sleep` writes tiers from a stale read | MEM-L14 | all | 3 |
| M30 | P3 | The nightly export is not a portable text snapshot | MEM-L15 | all | 4 |
| M31 | P3 | Scale bounds are implicit | MEM-L16 | all | 4 |
| M32 | P3 | The UI reports success or counts that are not true | MEM-S08 | all | 4 |
| M33 | P3 | The GraphML export is malformed for any fact with a quote; graph exports truncate silently | MEM-S09 | all | 4 |
| M34 | P3 | Read routes write; undo reads the settings store on the event loop | MEM-S10 | all | 4 |
| M35 | P3 | Operator memory changes leave no trail; failures look like success over HTTP | MEM-S11 | all | 4 |
| M36 | P3 | Defense in depth and input bounds | MEM-S12 | all | 4 |

Coverage check: all 43 original IDs appear above. MEM-R15 appears twice,
split by item: the entity-key item is in M21 and the remaining items are in
M26.

---

### M01 — P1: Forget, invalidate and library delete do not reach the copies of a value; one copy, `unified_items`, is served by search

**Original IDs:**

- MEM-S01 (P1)
- MEM-S02 (P1)
- MEM-L03 (P1)
- MEM-L11 (P3)

**Cross-area:**

- KB-E08 and KB-S03: deleted libraries keep serving their text.
- KB-S02: unified rows crowd Knowledge hits out of injection.
- TT-05: snapshots keep forgotten and deleted chats.

**Profile:** every profile; applies in full to the default profile.

**Evidence:**

- `_forget_fact_rows` sets `object = ''` and `metadata.forgotten` on `beliefs`
  rows only (`forget.py:433-456`, verified). It keeps `metadata_json` and
  `embedding`.
- **`unified_items`** (`memory_ops.db`):
  - Every non-private belief write upserts `"subject predicate object"`
    (`belief_mutation.py:459-471`), and so does every Knowledge chunk
    (`stores/knowledge.py:1014-1025`).
  - `unified_index.py` has no delete, invalidate or expiry path anywhere
    (verified: `:32, 44, 81` are its only functions).
  - `federated_search` returns the rows on every call
    (`federated_search.py:192-215`). That includes the supervisor's KB-only
    call, `POST /api/memory/v2/federated-search`
    (`routes_direct/memory.py:293-325`), the Memory page's Federated button
    (`memory_console.js:1195-1223`) and the Dashboard.
  - The comment "the graph and the unified index hold current facts only"
    (`belief_mutation.py:431-433`, verified) is false.
- **`memory_audit_log`:**
  - Values are written into `reason` and the state JSON
    (`belief_mutation.py:219-257, 816-821, 995-1000`).
  - No DELETE or redaction exists.
  - It is exported every 6 hours (`export.py:137-158`).
- **`beliefs_archive`:**
  - Forget reads `beliefs` only (`forget.py:396-410`).
  - Facts moved by `macro_sleep` (superseded and older than 180 days,
    `macro_sleep.py:245-268`) or by Hygiene → "Archive invalidated beliefs"
    (`memory_api.py:2241-2280`) keep their full JSON, value and source turn.
- **Vectors:**
  - The local fact `embedding` is kept.
  - Forget's remote deletes cover episode ids only (`forget.py:313, 526`),
    although belief vectors are upserted remotely too
    (`belief_mutation.py:575-600`).
  - Vector repair ignores non-current beliefs (`reembed.py:348-351`), so
    nothing replaces a stale fact vector.
- **Operator edits:** `metadata.operator_edit.previous.{subject,predicate,object}`
  (`memory_api.py:1508-1516`) survives forget.
- **Queue payloads:** a deferred `post_turn_memory` task carries the
  question and the answer, up to 16,000 characters each
  (`consolidator.py:328-337`). Terminal rows are kept for 7 days
  (`task_queue.py:477-497`).
- **Export:** keeps a forgotten fact's subject, predicate, chat and turn
  (`memory_api.py:2466-2477`), against "when, never what".
- **Not reached, and not documented as an erasure horizon:**
  - native backups (10 generations), universal backups (7), restic (up to
    12 monthly);
  - `exports/`;
  - `memory_state.db.pre_reembed` (M28);
  - time-travel snapshots. TT-05 verifies that delete and forget never touch
    `snapshots.db`. Checkpoints were not verified in depth.
- **Knowledge half:**
  - Chunks of deleted libraries stay in `unified_items` under tenant
    `default`. The memory parts found this by code reading. KB-E08 witnessed
    it (`DELETED_KB_TEXT_SEARCHABLE_VIA_UNIFIED_INDEX=True`).
  - Unified rows score a fixed 0.4, against Knowledge RRF scores of about
    0.016–0.033. With `limit_kb=3` they can push every Knowledge hit out of
    injection. MEM-L11 raised this from code reading; KB-S02 witnessed it
    (`test_kbs02_unified_hits_push_kb_out_of_auto_inject`).
- **Retention:** `unified_items`, `memory_audit_log` and `beliefs_archive`
  have no retention. Mirror-only rows are kept forever, by design (MEM-L11).

**Failure scenario:**

1. A user forgets "where I work". The Memory page shows it gone.
2. A Federated search for "acme" still returns `user works_at acme`.
3. The next export rewrites the value into `kazma_audit_latest.jsonl`, and
   backups keep it for months.

The same holds for every invalidated, superseded or corrected fact, and for
a Knowledge library the operator deleted. The UI says Forget removes the
memory and "the facts it learned from it" (`memory_console.js:1348`).

**Witnesses** (all passed):

- `memory_surfaces_probes.py`:
  - `test_mem_s01_a_forgotten_fact_is_still_found_by_federated_search`
    (`FORGOTTEN_FACT_SERVED_BY_FEDERATED_SEARCH=True`)
  - `test_mem_s02_forget_keeps_the_fact_value_in_the_audit_log_and_its_vector`
    (`FORGOTTEN_VALUE_IN_AUDIT_LOG=True; FORGOTTEN_FACT_EMBEDDING_KEPT=True`)
  - `test_mem_s02_forget_cannot_reach_an_archived_copy_of_the_fact`
    (`FORGOTTEN_VALUE_IN_BELIEFS_ARCHIVE=True`)
  - `test_mem_s02_forget_keeps_an_operator_edit_history_value`
    (`FORGOTTEN_VALUE_IN_OPERATOR_EDIT_METADATA=True`)
- `memory_lifecycle_probes.py`:
  - `test_l03_the_unified_index_still_returns_a_forgotten_fact`
  - `test_l03_the_audit_log_keeps_a_forgotten_facts_value`
  - `test_l03_a_forgotten_fact_keeps_its_vector`
  - `test_l03_a_forgotten_turns_archived_fact_is_left_in_beliefs_archive`
- MEM-L11 has no witness of its own. Its Knowledge half is witnessed in the
  KB audits.

**Fix direction:**

- **A memory store registry**, in the style of `store_registry` (§37). It
  lists every table, column and external index that can hold memory-derived
  text. For each it declares:
  - its erase step for Forget;
  - its behaviour on invalidate, supersede, archive and Knowledge delete;
  - its export section or declared exclusion (used by M22);
  - its retention and growth bound.

  Forget, "Don't remember", Export and library delete walk that registry.
  The Time Travel merge asks that `snapshots.db` be on it.
- **`unified_items`:** retire it (it serves an operator LIKE search). Or make
  it a derived index, removed in the same transaction or by a durable queued
  task, with reads filtered through the source of truth and a repair sweep
  as one `_MAINTENANCE_SWEEPS` entry (§15B).
- **The other copies:**
  - In `memory_audit_log`, redact values to ids and hashes for forgotten
    fact ids, and keep the event.
  - Rewrite the `beliefs_archive` rows.
  - Null the fact embedding and delete the remote fact vector.
  - Drop the operator-edit previous values.
  - Scrub queue payloads that name the turn.
  - Make the export say "when, never what".
- **Erasure horizon:** name backups, exports and snapshots as the erasure
  horizon, with their retention, next to the Forget button and in the guide.
  Replay forgets after a restore (M10).

**Gate and negative control:**

- **Behavioural:** a forget-completeness test.
  1. Enumerate every table and text column of both memory databases from
     `sqlite_master`, plus the registered derivative stores.
  2. Seed through the product writers, passing `ops_conn`.
     `test_memory_forget.py:143-158` never passes it.
  3. Plant a unique token, forget, and scan every column for the token.
  4. Assert that no fact vector remains, locally or remotely.

  Then do the same for invalidate, archive and library delete, each followed
  by `federated_search` returning nothing of the value.
- **Static:**
  - Every memory table is classified as erased-by-forget,
    holds-no-turn-text or retention-only, with a reason, a retention and a
    bound. A new table fails until it is classified.
  - Every `upsert_unified` writer has a retraction path.
- **Negative controls:** today's `_forget_fact_rows`, the upsert-only
  `unified_index.py`, and a planted unclassified table.

### M02 — P1: Non-Latin entity names collapse to the single id `entity`; Arabic facts supersede each other and lose their values

**Original ID:** MEM-R01. **Profile:** all.

**Evidence:**

- The slug rule `re.sub(r"[^a-z0-9_]+", "_", …)` followed by `or "entity"`
  has four copies (fallback lines verified):
  - `entity_resolution.py:41-48`
  - `belief_mutation.py:145-151`
  - `dual_write.py:86-93`
  - `backfill_v2.py:64-70`
- `mutate_belief` slugs every subject (`belief_mutation.py:330`).
- `resolve_entity` tier 1 matches `id = canonical`
  (`entity_resolution.py:136-151`).
- `_apply_beliefs_to_v2` replaces an object with the canonical id when the
  object is not already an entity name (`belief_extractor.py:589-597`).

**Failure scenario:** every all-Arabic subject becomes `entity`.

- "شركة الخليج located_in الكويت" is superseded by "مطعم البحر located_in دبي".
- Once the first Arabic object entity exists, "user works_at أرامكو" is
  stored as `user works_at entity`, and recall injects it as a Known Fact.
- Mixed names lose their Arabic part: "أحمد Smith" becomes `smith`.
- The graph shows one hub called "entity".
- Because `entities.id` is global (M21), the collision crosses tenants.
- The benchmark cannot see any of this (M24).

**Witnesses:** both have English negative controls that stay correct.

- `test_r01_arabic_subjects_share_one_slug_and_supersede_each_other`
  (`ARABIC_SUBJECTS_COLLAPSE=True`)
- `test_r01_arabic_object_is_replaced_by_the_shared_entity_id`
  (`ARABIC_OBJECT_REWRITTEN_TO_entity=True`)

**Fix direction:**

- One Unicode-aware id helper: NFKC, casefold, keep the letters and digits
  of any script. Match through `documents.arabic.fold_for_search`, not in the
  id. Alternatively, stable hash ids with the display name kept separately.
- No constant fallback. Delete the other three copies.
- Design this together with M21's tenant key.
- **Data repair:**
  - Find beliefs whose subject or object is `entity`, and the `entity` row.
  - Re-derive their values from provenance (`source_session`/`source_turn`).
  - Quarantine what cannot be re-derived, rather than guessing.
  - The live repair is the owner's to run, after a read-only query sizes it.

**Gate and negative control:**

- Arabic, Persian, CJK and emoji subjects and objects through
  `_apply_beliefs_to_v2` and `mutate_belief` give distinct ids, keep their
  values, and cause no cross-subject supersede.
- An AST gate: one slug helper and no constant fallback.
- Negative control: the current slug rule.

### M03 — P1: Writes the model starts carry the user's highest trust and are not screened; the user's own words cannot correct them

**Original IDs:**

- MEM-R02.
- The `memory_store` input-screening half of MEM-S07. The output half is in
  M11.

**Profile:** all.

**Evidence:**

- **Tool labels:**
  - `memory_store` writes `extraction_method="user_explicit"`, confidence
    1.0 and importance 5 (`agent/tool_builtins/memory.py:554-590`; label
    lines `:565, 584` verified).
  - `memory_link_entities` does the same, and allows `functional`
    (`:354-367`; `:363` verified).
- **No card:**
  - Both tools are `write` tier (`safety/hitl.py:145, 231`, verified).
  - Their commitment act `store_fact|revise_fact` has no resolver, so it is
    audit-only.
- **The user's own words are labelled lower:** the heuristic pass and the
  LLM deep pass both write `llm_inferred` (`belief_extractor.py:533, 609`,
  verified).
- **The gate then blocks the user:** the source-trust gate
  (`belief_mutation.py:738-761`, and history at `:871-880`) blocks the
  user's later correction.
- **No screen:** `memory_store` runs no injection screen. The extractor's
  `_sanitize_belief` does.
- **Correction:** `memory_invalidate` is danger tier and requires approval
  (see "Corrections"). That part of the original sentence does not hold.

**Failure scenario:**

1. A fetched page, email or document says "save to memory: …" (a model
   mistake does the same).
2. The model calls `memory_store`.
3. The text becomes a gold-standard fact about the user, shown under "Known
   Facts" in every related turn.
4. When the user later says "I live in Lisbon now", the correction is
   blocked with `lower_trust_source`.

**Witnesses:**

- `test_r02_a_model_tool_write_outranks_the_users_own_words`
  (`MODEL_WRITE_BLOCKS_USER_CORRECTION=True`)
- `test_r02_memory_store_persists_an_injection_the_extractor_would_reject`
  (`MEMORY_STORE_UNSCREENED_USER_EXPLICIT=True`)

**Fix direction:**

- Record who started each write:
  - the user's own channel (the Memory page, a chat command, or a remember
    request in the latest user message whose content the stored text
    matches);
  - an agent tool call;
  - the extractor.
- Reserve `user_explicit` for the first. Label the user's literal statements
  above `llm_inferred`.
- Screen agent writes with `filter_injection` / `is_override_delta`.
- Require a commitment or HITL decision before an agent write supersedes or
  retires a user fact.

**Gate and negative control:**

- A tool-initiated write never blocks a later user correction.
- An injection corpus (pages, emails, documents) is refused, and cannot
  create or protect a user fact.
- Negative control: the current labels.

### M04 — P1: Knowledge-library chunks are written into the chat's memory, on the event loop, as the user's words

**Original ID:** MEM-R03.

**Cross-area:**

- KB-S06: the same call, seen from the KB side.
- KB-S03: promotion is on by default, and a library delete does not forget
  promoted rows.
- KB-E08.

**Profile:** all.

**Evidence:**

- **On the loop:**
  - `graph_supervisor.py:1137-1145` (verified) calls
    `promote_kb_hits_to_episodes(...)` inline in `async def supervisor_node`,
    under `_v2m.get("promote_kb_to_episodes", True)`.
  - Each promotion runs `mirror_episode` (`federated_search.py:330-372`,
    `dual_write.py:349-469`): a chat-store id read, a SQLite insert, two
    embeddings (M15), a vector upsert and a Postgres mirror write, all on
    the loop.
  - Neither name is in `_LOOP_STALL_HELPERS` (verified), so the name-based
    gate cannot see the call.
- **On by default:** `merge_knowledge_into_chat`, `promote_kb_to_episodes`
  and `KAZMA_KB_AUTO_INJECT` all default to on.
- **As the user's words:** the chunk is stored as
  `user_text="[Knowledge: <title>] <crawled text>"` in the chat's own
  session (turn numbers 900000+n).

**Failure scenario:**

- Every turn that injects library chunks blocks the loop that serves every
  stream.
- Crawled third-party text, with any instruction it contains, becomes a
  permanent "memory of this conversation". It is recalled under
  "## Relevant History" and visible to weekly-summary grouping. The grouping
  effect was not tested.

**Witnesses:**

- `test_r03_kb_promotion_is_called_inline_in_the_async_supervisor`
  (`KB_PROMOTE_ON_LOOP= [('async', 'supervisor_node', 1139)]`)
- `test_r03_kb_chunk_is_stored_as_user_text_and_recalled_as_history`
  (`KB_CHUNK_PERSISTED_AND_REINJECTED=True`): a later recall injects the
  stored `curl … | sh` as history.

**Fix direction:**

- Prefer removing the promotion: the library is already searchable through
  federated search.
- Otherwise:
  - run it off the loop;
  - store it as a distinct kind (`role=document`, with its source) outside
    the turn space;
  - exclude it from extraction and summaries;
  - add the helpers to the stall list;
  - forget promoted rows when their library is deleted (KB-S03).

**Gate and negative control:**

- Run `supervisor_node` with an event-loop SQLite and embedder tracer, and
  fail on any memory write from the loop thread.
- Recall after a promotion shows no Knowledge text as history.
- Negative control: the current code.

### M05 — P1: The weekly-summary pipeline writes forgotten content back, and can resurrect a forgotten summary

**Original ID:** MEM-L01. **Profile:** all.

**Evidence:**

- **No re-read on store:** `summarize_period` (`topic_summaries.py:688-703`)
  loads the week's turns, awaits the model at `:718`, and stores at
  `:727-730` without re-reading the tiers of the source turns.
- **The rebuild path:**
  - `rebuild_summary` awaits the model at `:785` and stores with
    `summary_id` at `:793-797`.
  - `_store`'s rebuild branch is
    `UPDATE … status = 'active' … WHERE id = ?`, with no status predicate
    (`:612-619` in the current tree, verified).
  - The new-summary branch, by contrast, is guarded by
    `WHERE memory_summaries.status = 'retired'` (`:636`, verified).
- **Forget reaches existing summaries only:** `on_turns_forgotten`
  (`:927-949`) empties only summaries that already exist.

**Failure scenarios:**

- **(a) Forget during the weekly run.** There is one model call per topic,
  up to 24 a week. A turn forgotten during the run is written into a summary
  stored **active**, and recall injects it under "## Weekly Summaries".
- **(b) Forget the turn, then the summary.**
  1. The user forgets a turn; its summary goes to `rebuild`.
  2. While the rebuild awaits the model, the user forgets the summary itself.
  3. The rebuild overwrites the tombstone and sets it back to `active`.

**Witnesses:**

- `test_l01_a_turn_forgotten_while_its_week_is_summarized_is_written_into_an_active_summary`
- `test_l01_a_forgotten_summary_is_resurrected_by_its_rebuild`

**Fix direction:** make the store a compare-and-set.

- Inside the write transaction, re-read the tiers of the sources and ask the
  ledger. Drop forgotten turns. Skip or retire a group that falls below
  `min_turns`.
- Make the rebuild `UPDATE … WHERE id = ? AND status = 'rebuild'`, and check
  the rowcount.
- Optionally, add a generation counter that forget bumps.

**Gate and negative control:**

- The inverted witnesses.
- A static gate: every statement that sets a `memory_summaries` row active
  carries a status predicate.
- Negative control: the current `_store`.

### M06 — P1 (Postgres-primary / multi-replica profile): recall reads a store that nothing repairs, without a tier filter, by a recency slice, and that is not backed up

**Original IDs:**

- MEM-L02 (P1, profile-scoped)
- MEM-R11 (P2)

**Profile:**

- PG-primary.
- The recency-sliced keyword channel is also the mirror top-up on the
  default profile when a mirror is configured.

**Evidence:**

- **Nothing repairs a missed tombstone (MEM-L02):**
  - `sync_state_mirror` returns early when Postgres is primary
    (`state_backend.py:1007-1008` at the commit, `:1003-1004` in the current
    tree, verified).
  - `tests/test_memory_mirror_sync.py:259-265` asserts that skip (verified).
  - Mirror writes are best effort. Any exception returns `False` at DEBUG
    (`state_backend.py:274-320, 322-369`), and `remirror_episode_by_id`
    swallows it (`:923-931`). Forget relies on these calls
    (`forget.py:308-313`).
- **No tier filter (MEM-L02):**
  - Primary recall (`recall.py:405-480`) takes candidates from
    `search_episodes` (`state_backend.py:476-535`) and `fetch_episodes`
    (`:595-`). Neither has a tier predicate.
  - Only `archived` is down-weighted (`recall.py:475-479`).
- **Stale text pushed (MEM-L02):** `dual_write.mirror_episode` pushes the
  text from its own arguments after the local insert
  (`dual_write.py:449-466`). A forget committed between the two is
  overwritten in Postgres.
- **Not backed up (MEM-L02):** `KAZMA_PG_TABLES` (`db/pg_backup.py:59-88`,
  verified) lacks `kazma_episodes` and `kazma_beliefs`
  (`state_backend.py:208, 223`, current tree). So does the boot-time table
  verification.
- **A different recall (MEM-R11):**
  - `_recall_postgres_primary` takes no `session_id`
    (`recall.py:269-275`), so there is no session bias.
  - `_pg_primary_beliefs` (`:516-571`) lacks the provenance bridge and the
    graph walk that `_recall_beliefs` uses (`:831-839`).
  - There is no access bump on that path.
  - The keyword candidates are a substring `LIKE` scan,
    `ORDER BY created_at DESC LIMIT limit×5`. The mirror top-up uses the
    same function.
  - The §15F slice gate scans `vector_engine.py` only
    (`tests/test_memory_nothing_lost.py:536-553`, verified).
  - The benchmark runs `local_only=True`.

**Failure scenarios:**

- **Missed tombstone.** Postgres blips while a user forgets a memory, or
  while archival or a tier move runs. The local tombstone commits and the
  mirror keeps the text. No sync runs in primary mode, so recall injects the
  forgotten memory indefinitely.
- **Unbacked rows.** Rows that other replicas wrote exist only in Postgres
  and are outside the dump. Losing the table (the 2026-08-14 incident class)
  loses them.
- **Unreachable memories.** An older memory that only its words would find
  is out of reach once newer rows share a word.
- **Lost channels.** "Where do I live?" loses the bridge from the turn to
  its fact, and same-chat continuity is lost.

**Witnesses:**

- `test_l02_primary_recall_returns_a_memory_the_install_forgot_and_no_sync_repairs_it`.
  A fake state store stands in for Postgres; it was not run against a real
  Postgres.
- `test_r11_postgres_primary_path_lacks_local_channels`. Source-level.
  Log: `PG_PRIMARY_PARITY_GAPS=session_bias,belief_bridge,graph_walk,recency_keyword_slice,access_bump`.

**Fix direction:**

- **Repair:** keep anti-entropy in primary mode for rows this install owns:
  push local tombstones and tiers, never pull.
- **Filter:**
  - Add `tier IN RECALLABLE_TIERS` to the Postgres search and fetch.
  - Cross-check the local tier and ledger for ids this install holds.
- **Write durably:**
  - Replace best-effort mirror writes with a durable outbox: a
    `memory_ops.db` row in the same transaction, drained with retry.
  - Push the current row, not the caller's arguments.
- **Back up:** add the mirror tables to `KAZMA_PG_TABLES`, or declare them
  rebuildable with a documented procedure. Verify them at boot in the
  primary role.
- **Recall parity:**
  - Write a parity contract: the same channels, or documented degradations.
  - Use a relevance-ordered keyword index (tsvector, or `pg_trgm` with
    ranking).
  - Widen the slice gate to every candidate fetch, `state_backend.py`
    included.

**Gate and negative control:**

- A real-Postgres test (`@pytest.mark.postgres`, the Postgres CI job): forget
  with an injected mirror-write failure. One sync pass in primary mode must
  repair it, and recall must exclude the memory.
- The benchmark under the Postgres-primary role stays within a stated delta
  of the local scores (see M24).
- Re-point `test_no_mirror_no_pass`; do not delete it.
- Negative controls: the current skip and the current recency slice.

### M07 — P1 (multi-user and tenant-mode profiles): surfaces and turns resolve the tenant differently, so About me, "Don't remember this chat", Forget and Export silently miss the user's own memory

**Original ID:** MEM-S03.

**Profile:** multi-tenant. It does not apply in `shared` mode with tenants
not enforced.

**Evidence:**

- **Turns** use `memory/config.resolve_tenant_id` (`config.py:427-467`):
  - `shared` gives `"default"`;
  - `per_platform` gives `"web"` or `"telegram"`;
  - `per_user` gives `"web:<uid|session>"`.

  It is called from `sse_chat/__init__.py:1014-1018` and
  `agent_handler/store.py:243-248`. The supervisor reads About me under
  `state["tenant_id"]` (`graph_supervisor.py:333`).
- **Surfaces** use `memory_api._memory_tenant_id` (`memory_api.py:230-274`):
  `"default"` unless enforcement is on, otherwise the principal's tenant.
- **Two more resolvers:** the probe and federated routes use
  `require_tenant_id()`, and the tools use `hitl.get_current_tenant_id`.
- **Settings offers the modes** and recommends `per_user` "for multi-user
  SaaS" (catalog `settings.py:1418-1437`).

**Failure scenarios:**

1. **`per_user` mode:** About me is saved under `default` and read under
   `web:sess-1`, so the model never sees it. The page still says "Kazma
   reads this at the start of every reply".
2. **`per_platform` mode:** "Don't remember" writes the ledger under
   `default`, but `_run_turn_memory` asks under `web`. The chat is still
   remembered, while the menu reports "not remembered".
3. **Enforced `shared` mode:** a tenant-bound principal's turns are written
   under `default`. Their page shows nothing of their own, Forget answers
   `not_found`, Export is empty, and About me is saved where no turn reads
   it.
4. **"Also forget" on chat delete** toasts success when
   `forgot.forgotten == 0` (`chat.js:7059-7066`).

**Witnesses:**

- `test_mem_s03_about_me_saved_in_settings_never_reaches_a_per_user_turn`
  (`ABOUT_ME_NOT_SHOWN_IN_PER_USER_MODE=True`)
- `test_mem_s03_dont_remember_this_chat_is_a_no_op_in_per_platform_mode`
  (`CHAT_KEPT_OUT_STILL_REMEMBERED=True; MENU_SAYS_NOT_REMEMBERED=True`)
- `test_mem_s03_enforced_shared_mode_surfaces_cannot_reach_turn_memories`
  (`SURFACE_TENANT!=TURN_TENANT=True; OWN_MEMORY_NOT_FORGETTABLE=True`)

**Fix direction:**

- One function, `memory_tenant_for(principal, platform, sender, session)`,
  for turns, routes, tools, the gateway and the probe.
- Route actions that name a chat (ledger, forget) take the tenant the chat's
  turns are written under, read from the episodes or the chat store, never
  from the caller.
- Until that exists, refuse to save non-`shared` modes under enforcement.
- Report `forgotten: 0` as "nothing found".

**Gate and negative control:**

- A matrix: mode {shared, per_platform, per_user} × enforcement {off, on} ×
  principal {unbound, bound}. In every cell:
  - About me saved through a route is visible to `get_about(turn tenant)`;
  - a ledger row stops `chat_remembered(turn tenant)`;
  - a forget finds the turn's episode.
- Negative control: the current two resolvers.

### M08 — P2: Forget does not converge

**Original ID:** MEM-L05.

**Related:**

- The forget race in MEM-R05, tracked in M15.
- The Forget toast that ignores `remote_vector_deleted:false`, in M32.

**Cross-area:** TT-05. `scripts/scan_old_sessions.py` re-extracts beliefs
from snapshots under tenant `default` without asking `refuses_write`. The
Time Travel audit marks this gap as the memory audits'.

**Profile:** all.

**Evidence:**

- **(a) A forget cut short is reported done.**
  - `invalidate_belief` commits the caller's connection (`hygiene.py:238`).
    So the ledger and tombstones commit before the fact, summary, mirror and
    remote steps (`forget.py:292-313`).
  - The retry branch (`forget.py:267-273`, verified) finishes only the facts
    and answers `already: true`.
  - Summaries stay active, and remote vectors and mirror tombstones are not
    retried.
- **(b) A keep-out can land between check and insert.**
  - `mirror_episode` checks the ledger (`dual_write.py:354-370`), releases
    its lock, then inserts (`:375-399`). A "don't remember this chat" plus
    forget-past landing between the two is lost.
  - The remote upsert (`:421-438`) and the state mirror (`:449-466`) are
    also unguarded against a concurrent forget.
  - Nothing re-applies the ledger to stored rows.
- **(c) Recovery can refill an erased copy of a forgotten turn.**
  - Recovery relies on the row's own tier (`rehydrate.py:100-103`) and
    never asks the ledger.
  - `forget_episode` collects copies by question hash
    (`forget.py:279-287`). An erased copy with NULL text cannot match.
  - Recovery then restores the forgotten turn from the chat store as an
    archived, recallable memory.
- **(d) Remote deletes are never retried.** `_delete_remote_vector` logs that
  a failure "can be retried" (`forget.py:474-477`). Nothing retries it.

**Witnesses:**

- `test_l05_a_forget_cut_short_is_reported_done_with_its_summary_still_active`
- `test_l05_a_chat_kept_out_while_a_turn_is_written_keeps_that_turn`
- `test_l05_recovery_refills_an_erased_copy_of_a_forgotten_turn`

**Fix direction:**

- Make forget a journaled, idempotent operation: write an intent row first,
  make every step re-runnable, and have the "already" path re-run all steps.
- Add a forget-convergence entry to `_MAINTENANCE_SWEEPS` (15 minutes) that
  applies the ledger to stored rows:
  - tombstone refused rows;
  - re-empty summaries sourced from forgotten episodes;
  - retry remote deletes and mirror tombstones.
- `rehydrate` and `scan_old_sessions.py` ask `refuses_write`.
- Writers push the current row, not their arguments.

**Gate and negative control:**

- A fault-injection matrix that raises after each forget step. After one
  sweep pass, M01's completeness test must pass.
- The inverted witnesses.
- Negative control: the same matrix with the sweep disabled.

### M09 — P2: Rewinding a chat does not rewind memory

**Original ID:** MEM-R10.

**Cross-area:**

- TT-03: restore rewrites only the checkpoint; the transcript and the
  scratchpad keep later turns.
- TT-04: MEM-R10's seven state rewriters are the set TT-04's lock must
  cover.
- TT-05.

**Profile:** all.

**Evidence:**

- These functions rewrite graph state:
  - `_handle_undo` (`kazma_gateway/agent_handler/graph.py:2118`)
  - `_handle_edit` (`:2177`)
  - `slash_commands._cmd_replay`
  - the web replay restore (`replay_routes`,
    `time_travel.apply_snapshot_to_thread`)
- None of them touches `memory.forget`, the episode or its facts.
- The witness enumerates the seven innermost state rewriters, and none
  touches memory:
  - `graph.py:_handler_body`
  - `_handle_undo`
  - `_handle_edit`
  - `slash_commands.py:_cmd_replay`
  - `replay_routes.py:create_replay_router`
  - `sse_chat/__init__.py:abort_chat_turn`
  - `time_travel.py:apply_snapshot_to_thread`

**Failure scenario:**

- An undone answer, or an edited question, stays in memory: its episode,
  its heuristic facts and its later LLM facts.
- After `/edit`, both versions exist under the same turn number.
- The user believes the content is gone.

**Witness:** `test_r10_rewinds_leave_long_term_memory_untouched`
(`REWIND_DOES_NOT_TOUCH_MEMORY=True`).

**Fix direction:**

- A rewind forgets the turns past the restored point by provenance
  (`forget_episode` and the turn's facts), or marks them superseded.
- The deep pass checks that its episode is still live before applying.
- Decide this once with TT-03. Either memory is rewound, or the UI states
  that memory is kept. One test can serve both gates.

**Gate and negative control:**

- Undo, edit, replay and abort through a real graph; recall must not return
  the rewound content.
- Negative control: the current behaviour.

### M10 — P2: Restoring `memory_state.db` silently undoes forgets and mixes generations

**Original ID:** MEM-L07.

**Cross-area:** TT-05. The Time Travel merge notes that a restored or
imported `snapshots.db` raises the same question.

**Profile:** all.

**Evidence:**

- **The ledger is restored with the memories.** The ledger lives in the same
  file as the memories (`schema_v2.py:210-218`). No forget journal exists
  outside it.
- **Reconcile rebuilds the turns.** Turn reconcile re-creates an episode,
  then its facts, for any chat-store turn without one
  (`turn_reconcile.py:122-160, 197-231`). The chat store is not rolled back
  with the memory file.
- **The mirror loses its tombstones.** Mirror sync pushes local rows that
  differ over the mirror's newer tombstones (`state_backend.py:1029-1066`).
- **Nothing checks for this.** The disaster-recovery guide
  (`disaster-recovery.md:262-300`) has no step for it, and the restore drill
  checks integrity only (`backup/restore_drill.py:15, 166`).

**Failure scenario:** after corruption, yesterday's `memory_state.db` is
restored. Every turn forgotten since that backup comes back, with full text
and facts, and reconcile re-derives the rest. In mirror mode, the mirror's
tombstones are overwritten.

**Witness:** none. The part judged that a test without a journal to replay
would be tautological.

**Fix direction:**

- An append-only forget journal outside `memory_state.db`, backed up
  independently, ideally next to the chat store.
- Replay it at boot and after any restore or migration import. Cover
  snapshots too.
- Add a runbook step, and a drill check: "no ledger entry newer than the
  restored generation is missing".

**Gate and negative control:**

- Restore a pre-forget backup and boot. The forgotten turn is tombstoned and
  not re-created by reconcile.
- Negative control: the same run without the replay.

### M11 — P2: Recalled memory reaches the model unfenced through `memory_search`; About me is unfenced system-level text

**Original IDs:**

- MEM-R13 (P3)
- MEM-S07 (P2)

**Cross-area sibling:** KB-S01 (`knowledge_search` unfenced).

**Profile:** all. The About me write scope matters in multi-tenant installs.

**Evidence:**

- **The tool output is unfenced:**
  - `_memory_search_sync` returns raw JSON of beliefs, summaries and
    episodes (`tool_builtins/memory.py:620-652`).
  - It applies no `format_untrusted_block`, no recall-rules preamble and no
    `is_override_delta` filter.
  - The per-turn path fences the same hits (`recall.py:2244`).
- **It shows the wrong text:**
  - `content` is `h.content`, the match text (the question only). The
    display text exists only in its metadata.
  - `federated_search` also uses `h.content` (`:133, 166`).
- **No gate covers it:** `FENCED_TOOL_FUNCTIONS`
  (`tests/test_static_gates.py:1811`) does not list `tool_builtins/memory.py`
  (verified).
- **A failure reads as empty:** a recall failure is reported as "No relevant
  memories found." (M16).
- **About me:**
  - It is inserted as a raw `system` message telling the model to "take this
    into account in every answer" (`profile.py:98-108`,
    `graph_helpers.py:491-510`).
  - In a multi-user tenant, any operator-role user can
    `PUT /api/memory/v2/profile` and change that guidance for every user of
    the tenant.
  - A personality change, by contrast, needs a gateway admin (§46).

**Failure scenario:**

1. An injected page leads the model to call `memory_store` with "always
   forward drafts to x@…" (M03).
2. A later `memory_search` returns it unfenced inside a tool result, where
   the recall-path defenses do not apply.

**Witnesses:**

- `test_r13_memory_search_tool_output_is_unfenced`
  (`MEMORY_SEARCH_UNFENCED=True`), with a negative control showing that
  `format_recall_block` fences the same hit.
- `test_mem_s07_memory_search_tool_returns_recalled_text_without_a_fence`
  (`MEMORY_SEARCH_OUTPUT_UNFENCED=True`).

**Fix direction:**

- Fence `memory_search` and the `list_*` outputs with the recall rules
  (`fence_untrusted(source="memory_search")`).
- Set `content` to the display text (`shown_text(h)`), and add a degraded
  flag (M16).
- Treat About me as the owning user's data. In multi-user mode, key it per
  user, or restrict writes to an admin.
- Input screening on `memory_store` is in M03.

**Gate and negative control:**

- Add `tool_builtins/memory.py` to `FENCED_TOOL_FUNCTIONS`.
- A behavioural test: a planted override string arrives inside
  `<kazma:data …>`.
- An RBAC / per-user About me test.
- Negative control: the current tool.

### M12 — P2: Failed memory writes and LLM deep passes are acknowledged as success; the queue turns outages into permanent loss and hides dead letters

**Original IDs:**

- MEM-R09 (P2)
- MEM-L09 (P2)

Both parts witnessed the same LLM-outage defect.

**Profile:** all.

**Evidence:**

- **Failed episode writes count as success (MEM-R09):**
  - `_mirror_turn_to_v2` ignores the `None` that `mirror_episode` returns
    (`consolidator.py:524-540`).
  - `mirror_episode` swallows every exception at DEBUG
    (`dual_write.py:329-330, 470-472`).
  - `_run_turn_memory` therefore returns True and counts `ok`
    (`consolidator.py:418-465`). Its docstring promises that False means "a
    deferred turn is then retried".
  - `run_deferred_turn_memory` returns True.
  - The `micro_consolidation` task enqueued for the missing episode ends as
    "episode gone".
- **An LLM outage drops the deep pass (both parts):**
  - On an LLM failure, `extract_beliefs_with_llm` returns `None`
    (`belief_extractor.py:238-240`).
  - The handler falls back to heuristics and returns True
    (`worker_bootstrap.py:377-422`; the lifecycle part cites `:395-417`).
    The deep pass is never retried, and reconcile will not redo it, because
    the episode exists.
- **The queue has no backoff (MEM-L09):** `_ack` returns a failed task to
  `pending` at once (`task_queue.py:394-400`), so three attempts happen
  within about three poll intervals.
- **Dead letters vanish (MEM-L09):**
  - Terminal rows, dead letters included, are deleted after 7 days
    (`task_queue.py:477-497`).
  - Health degrades only while five or more failed rows exist
    (`v2_health.py:303-304`), so it clears itself as they are purged.

**Failure scenario:** the provider is down for ten minutes in a busy hour.

- Every turn's LLM fact pass is marked completed with heuristic facts only,
  never to be redone.
- Episode writes that fail during the outage raise the `ok` metric. Turn
  reconcile heals them about 10 minutes later.

**Witnesses:**

- `test_r09_turn_memory_reports_success_when_the_episode_was_not_written`
  (`MIRROR_FAILURE_REPORTED_AS_OK=True`)
- `test_r09_llm_outage_consumes_the_deep_pass`
  (`DEEP_PASS_CONSUMED_ON_LLM_OUTAGE=True`)
- `test_l09_deep_extraction_during_an_llm_outage_is_acked_as_done`
- `test_l09_the_queue_retries_immediately_and_dead_letters_in_seconds`
  (poll interval scaled from 2 s to 0.05 s)

**Fix direction:**

- Return typed write results, and False on a transient failure.
- Tell "model unavailable" (retry) apart from "model returned nothing".
- Add `next_attempt_at` with exponential backoff.
- Keep dead letters until acknowledged, counted in health and alerted.
- Add `mirror_fail` and `deep_pass_failed` counters, shown in health.

**Gate and negative control:**

- A fault-injected mirror and an LLM outage each produce a retry and a
  metric.
- During an injected outage the task stays pending with backoff, and it
  completes after recovery.
- Negative control: the current `return True`.

### M13 — P2: The durable queue runs its own claimed tasks twice, and maintenance starves user-facing memory work

**Original ID:** MEM-L04. **Profile:** all.

**Evidence:**

- **The claim is bigger than the slots:**
  - `_claim_batch` claims up to 8 rows (`task_queue.py:231`, verified) and
    marks them all `processing`.
  - `_run` executes `max_concurrency=2` (`:120`, verified) and blocks the
    claim loop on the semaphore.
- **A waiting row has no lease:**
  - `_leases` is filled only when `_process` starts (`:327-328`), and
    renewal ticks every 20 s (`:194`).
  - The next claim re-claims any row stale for more than 300 s (`:275-302`)
    and dispatches it again. The first run's ack is discarded, because its
    token was rotated.
- **There are no lanes:**
  - `native_pg_backup` (`pg_dump` timeout 1,800 s) and `restic_maintenance`
    (`check` timeout 3,600 s per repository) share the two slots, first in
    first out, with `post_turn_memory` and `micro_consolidation`.
  - The six-hourly sweep enqueues six or more tasks at once
    (`worker_bootstrap.py:740-762`).

**Failure scenario:**

- Tasks dispatched more than about five minutes after their claim run twice.
  Examples: `nightly_export` and `connector_health` behind the backup and
  restic.
- Deferred chat turns wait behind a restic check.
- To check on live: two `nightly_export done` lines within minutes of one
  sweep.

**Witness:** `test_l04_a_task_claimed_in_a_batch_and_dispatched_late_runs_twice`.
The stuck threshold is scaled from 300 s to 1 s; renewal stays at its real
20 s tick.

**Fix direction:**

- Claim no more rows than free slots, or register and renew leases at claim
  time.
- Separate lanes or priorities for memory writes and maintenance.
- Idempotency keys for side-effecting handlers.

**Gate and negative control:**

- The inverted witness: each task runs exactly once.
- A lane test: a `post_turn_memory` task enqueued behind two long
  maintenance tasks completes within a stated bound.
- Negative control: the current batch claim.

### M14 — P2: Embedding and network I/O run inside memory write transactions and the process-wide mutation lock

**Original IDs:**

- MEM-R06 (P2)
- MEM-L08 (P2)

These are one class: slow I/O inside a SQLite write transaction. They share
one gate.

**Profile:** all. The `macro_sleep` half needs a Postgres mirror.

**Evidence:**

- **Embedding under the write lock (MEM-R06):**
  - `_mutate_functional` opens `BEGIN IMMEDIATE`
    (`belief_mutation.py:668-673`).
  - `_insert_belief` then embeds (`:581`) and upserts remotely (`:587-600`)
    inside that transaction, under the process-wide `_mutation_lock`
    (`:369`).
  - The OpenAI-compatible embedder may take 2 × 60 s per call
    (`embedder.py:316-382`).
  - Recall's access bump is a write on the turn path
    (`recall.py:1951-2007`). It waits for the busy timeout, then is dropped
    at DEBUG.
- **A Postgres round trip per archived fact (MEM-L08):**
  - In `macro_sleep.py:245-278`, the INSERT and DELETE open the implicit
    transaction.
  - `unmirror_belief_to_state` runs per row before the commit at `:278`.
  - Each call opens a new psycopg connection with a 5 s connect timeout
    (`state_backend.py:162-170, 377-394, 908-913`).
  - The cached backend is returned without re-checking its availability
    (`:861-889`).

**Failure scenario:**

- All belief writes in the process serialize behind one embedding. The
  4-thread extraction pool behaves as one for belief writes.
- A turn's recall waited **5.54 s** while another connection held the write
  lock (measured), and its access bump was dropped. That also starves the
  access-based archiving clock.
- The first sweep past the 180-day horizon can hold `memory_state.db`
  write-locked for up to 5 s per archived fact while Postgres is slow.

**Witnesses:**

- `test_r06_belief_embedding_runs_while_the_write_lock_is_held`
  (`EMBED_UNDER_WRITE_LOCK= [(True, 'database is locked')]`)
- `test_r06_recall_waits_on_a_writer_and_silently_drops_its_access_bump`
  (`RECALL_WAITED_S=5.54; BUMP_DROPPED=True`)
- `test_l08_macro_sleep_holds_the_write_lock_while_it_unmirrors_each_archived_fact`

**Fix direction:**

- Compute vectors before `BEGIN`, or write them after the commit with a
  guarded update.
- In `macro_sleep`: collect the ids, commit, then mirror. Or write an outbox
  row in the same transaction (M06).
- Take the access bump off the turn path (queue or batch it), or give it a
  short busy timeout.

**Gate and negative control:**

- A lock and transaction probe inside every embed, mirror, graph and vector
  hook used by `belief_mutation`, `macro_sleep`, `hygiene` and `forget`.
  The probe must find the database free and no transaction open.
- Recall latency while another connection holds a write lock stays below a
  stated bound.
- Negative control: the current code.

### M15 — P2: Each vector write bypasses the writer's own guards; the vector is written twice and can commit the caller's transaction

**Original ID:** MEM-R05. **Related:** M08(b). **Profile:** all.

**Evidence:**

- **A second, unguarded write:**
  - `dual_write.py:401-446` makes a guarded first write under `self._lock`
    (`embedding IS NULL AND tier != 'forgotten'`).
  - It then encodes the same text again and calls
    `get_vector_backend(self._primary).upsert(...)`.
  - That is `LocalSqliteVectorBackend.upsert` (`backends.py:274`;
    `UPDATE … SET embedding` then `commit()` at about `:330-333`, verified).
    It runs on the shared mirror connection, without the lock and without
    the tier guard.
- **A commit inside the caller's transaction:** the belief path does the
  same. `_insert_belief` runs `get_vector_backend(conn).upsert`
  (`belief_mutation.py:587-606`), which commits the caller's
  `BEGIN IMMEDIATE`.
- **The comments say otherwise:**
  - `dual_write.py:371-374` says "Lock only the local SQLite writes".
  - `:350-353` says "nothing is written -- not the row, and not the
    embedding".

**Failure scenario:**

- **Lost transaction boundary:** two threads share one connection, and one
  commits while the other is inside `with conn:`.
- **Forgotten memory re-indexed:** a forget landing between the ledger check
  and the second write gets its vector back.
- **Double cost:** every episode over 256 characters is embedded twice.
- **Two current values:** on the history path (`belief_mutation.py:895-903`),
  a crash after the early commit can leave two current values for a
  functional fact.

**Witnesses:**

- `test_r05_episode_vector_written_twice_outside_the_mirror_lock`
  (`VECTOR_UPSERT_UNLOCKED_UNGUARDED_DOUBLE=True`): lock not held, same
  connection, two embeddings, a forgotten row's vector back.
- `test_r05_local_vector_upsert_commits_the_callers_belief_transaction`
  (`UPSERT_COMMITS_CALLER_TXN= [(True, False), (True, False)]`).

**Fix direction:**

- One vector write per row: reuse the first vector, inside the guarded
  statement.
- A local backend never commits a connection it does not own.
- Upsert remotely only for remote backends, with the tier guard there too.

**Gate and negative control:**

- One embedding per write.
- The lock is held for every statement on the mirror connection.
- No commit happens inside a caller's transaction.
- A forget-race test.
- Negative control: the current code.

### M16 — P2: Recall has no deadline, and embedder failures are neither memoized nor latched

**Original ID:** MEM-R07. **Profile:** all.

**Evidence:**

- `_encode_query` caches successes only (`recall.py:584-613`). One local
  recall encodes the question at up to four sites.
- The supervisor awaits recall with no timeout (`graph_supervisor.py:1027`).
- The remote embedder times out at 60 s, with 2 attempts
  (`embedder.py:316-382`).
- The local `_ensure_model` never latches a failure (`embedder.py:225-252`).
- `memory_search` reports "No relevant memories found." on a failure
  (`memory.py:650-652`).

**Failure scenario:**

- With a hanging remote embedder, one recall can take about 8 minutes
  (4 × 2 × 60 s) before the first model call. This is computed from the
  configured timeouts, not measured.
- A broken local install logs 4 or more WARNINGs per recall, and may retry a
  multi-GB model load on every call.
- The model cannot tell "empty" from "broken".

**Witnesses:**

- `test_r07_a_failed_question_embedding_is_retried_within_one_recall`
  (`QUERY_EMBED_ATTEMPTS_PER_RECALL=4`)
- `test_r07_a_missing_embedder_is_retried_and_logged_on_every_call`
  (`EMBEDDER_FAILURE_NOT_LATCHED=True`; 3 of 3 calls warn)

**Fix direction:**

- A recall deadline (for example 2–3 s) with partial results.
- Memoize a failure within one recall.
- An embedder circuit breaker with backoff, shown in health.
- Report "memory degraded" distinctly from "empty".

**Gate and negative control:**

- A hanging embedder leaves recall bounded by its deadline.
- One embedding attempt per recall.
- One failure log per breaker period.
- Negative control: the current code.

### M17 — P2: On a photo turn, the recall query is the Python repr of the message, base64 included

**Original ID:** MEM-R04. **Profile:** all.

**Evidence:**

- `graph_supervisor.py:345-349` sets
  `last_user_content = str(m.get("content", ""))`, then
  `_recall_query = last_user_content` (`:356`) and `recall(...)`
  (`:1027-1034`).
- Vision turns carry a list with a `data:` URI
  (`kazma_gateway/agent_handler/attachments.py:337-345`).
- `agent/turn_input.extract_latest_user_text` already reads multimodal
  messages correctly.

**Failure scenario:** a 150 KB photo makes recall embed a 200,125-character
string and build a 6,029-term FTS query.

- In the micro-benchmark, 2,000 terms on 20,000 rows cost 668 ms per search,
  and recall runs two searches. All of it comes before the first model call.
- The same string feeds the language lock, the model router and intent
  classification. These were not audited.

**Witnesses:**

- `test_r04_supervisor_recall_query_is_str_of_message_content`
  (`RECALL_QUERY_IS_STR_CONTENT=True`)
- `test_r04_recall_embeds_and_keyword_searches_the_base64_payload`
  (`RECALL_FTS_TERMS=6029; EMBEDDED_CHARS=200125`)

**Fix direction:**

- Use `extract_latest_user_text`.
- Cap the query's characters and terms (M25).

**Gate and negative control:**

- A photo turn through the real supervisor node hands recall the text only.
- Negative control: the current `str(content)`.

### M18 — P2: Each extraction embeds every entity name of the tenant, and the name cache thrashes past 4,096 names

**Original ID:** MEM-R08. **Profile:** all.

**Evidence:**

- `_gather_candidates` (`belief_extractor.py:507-521`) runs at the start of
  every `_apply_beliefs_to_v2` that has any belief, whether or not a name
  needs resolving. Tier 2 then compares distances in a Python loop.
- The local short-text cache holds 4,096 entries (`embedder.py:188-189`).
- The fact text is embedded twice (`belief_mutation.py:581, 593`).
- A comment in the code records "93 names, seconds of CPU per turn" on live.

**Failure scenario:**

- The extraction cost grows linearly with the number of entities, and every
  lookup misses once there are more than 4,096 names.
- That holds one of the four extraction slots, behind the mutation lock
  (M14).

**Witnesses:**

- `test_r08_each_extraction_embeds_every_entity_name`
  (`ENTITY_NAMES_EMBEDDED=60; BELIEF_TEXT_EMBEDS=2`)
- `test_r08_short_text_cache_thrashes_once_names_exceed_it`
  (`LRU_THRASH_AT=4106`: 8,212 model calls over two passes). Its negative
  control at 4,086 names makes the second pass free.

**Fix direction:**

- Build candidate vectors lazily, only when a name needs resolving.
- Persist name vectors on `entities` or in the vector index, and query the
  nearest instead of scanning.
- Reuse one vector per text.

**Gate and negative control:**

- The embed-call count at 60 entities equals the count at 6,000.
- Negative control: the current code.

### M19 — P2: The native memory backup, the declared restore source of truth, restarts under writes

**Original ID:** MEM-L06. **Profile:** all.

**Evidence:**

- **The batched copy:** `memory/backup.py:59` copies with
  `src.backup(dst, pages=100, sleep=0.01)` (verified).
- **The claims:** its docstring (`:1-7`, verified) says it "sees a
  consistent snapshot without blocking writers". The guide says "Native
  `.db` backups remain the restore SoT" (`memory-and-rag.md:238`, verified).
- **The known fix, applied elsewhere only:** `backup/universal.py:485-520`
  documents a measured live failure of this pattern (2026-09-13). It fixed
  it with a one-step copy, `pages=-1` (`:488-515`, verified), for the
  universal backup only.
- **A second user of the same copy:** `backup_one` is also the migration
  importer's pre-swap safety copy (§18).

**Failure scenario:**

- `memory_state.db` is written by every turn and every 15-minute sweep.
- The batched copy restarts on each write, finishes only in a quiet window,
  and holds a queue slot meanwhile.
- The universal backup still captures the database. That protects
  recoverability, not the claim.

**Evidence of the failure:**

- `test_l06_native_memory_backup_cannot_finish_while_the_source_is_written`.
  On a 2,000-row WAL database with a writer every 2 ms, the batched copy
  completes only after the writes stop at 2.0 s. The one-step copy completes
  while the writes continue.
- The scratch experiment: 778 restarts in 2 s.

**Fix direction:**

- One snapshot-copy helper (one-step `backup()` or `VACUUM INTO`), used by
  every copier.
- A class gate that bans `pages=` values above 0 in product code (§35).

**Gate and negative control:**

- The inverted witness, through the product helper.
- Negative control: `pages=100`.

### M20 — P2: Several lifecycle failures are invisible above DEBUG

**Original ID:** MEM-L10. **Profile:** all.

**Evidence:**

- **Locked-database failures look like idle sweeps:**
  - `run_vector_repair_pass` and `run_rehydrate_pass` catch
    `sqlite3.OperationalError`, "database is locked" included, and log at
    DEBUG (`reembed.py:527-529`, `rehydrate.py:750-752`).
  - The runner's WARNING (`worker_bootstrap.py:1034-1046`) never sees them.
- **Enqueue failures are ignored:**
  - `enqueue_task` swallows its errors at DEBUG and returns `None`
    (`task_queue.py:104-106`).
  - The schedulers ignore the result
    (`worker_bootstrap.py:661-665, 740-765, 1105-1116`).
  - A broken `memory_ops.db` stops macro sleep, backups and reconsolidation
    silently, while health shows an empty queue.
- **Export failures count as success:** the nightly export swallows failures
  at DEBUG, and its handler returns True (`export.py:91-158`;
  `worker_bootstrap.py:1293-1331`).
- **Health is missing the signals:** it shows no age of the oldest pending
  task, no processing age, and no last success per task type
  (`v2_health.py:283-305`).

**Witnesses:**

- `test_l10_vector_repair_reports_a_locked_database_as_nothing_to_do`
- `test_l10_recovery_reports_a_locked_database_as_nothing_to_do`

**Fix direction:**

- WARNING after N consecutive busy or locked passes.
- A last success per sweep and per task type, shown in health.
- The schedulers check the enqueue result and alert.
- Health shows the oldest pending and processing ages.
- The export returns a success flag.

**Gate and negative control:**

- The inverted witnesses, plus enqueue and export cases.
- Negative control: the current swallow.

### M21 — P2 (multi-tenant): global `entities.id` lets one tenant write to and probe another tenant's entities

**Original IDs:**

- MEM-S04 (P2)
- The entity-key item of MEM-R15 (P3 under the single-operator posture)

**Related:** M02, whose collapse into `entity` crosses tenants through the
same key.

**Profile:** multi-tenant.

**Evidence:**

- **The key:** `entities.id` is `TEXT PRIMARY KEY` (`schema_v2.py:148-149`,
  verified).
- **A second tenant writes into the first's row:** a second tenant's entity
  with the same slug is silently not created, and its alias hash is appended
  to the first tenant's row (`entity_resolution.py:308-341`).
- **Rename writes the shared hub:** renaming one's own self entity calls
  `self_hub.ensure_user_hub` without a tenant (`memory_api.py:783-797`). It
  updates the global `user` row whoever owns it (`self_hub.py:174-193`).
- **The merge tool skips the tenant check:**
  - The agent merge tool reads both entity rows without a tenant check
    (`tool_builtins/memory.py:209-268`).
  - It adds the source's aliases to the target and stamps `merged_into` on
    the source.
  - The route version checks the tenant (`memory_api.py:1058-1066`).
- **Rename is an existence oracle:** renaming another tenant's id answers
  `not_found`, while an unused id answers `created`.
- **Id squatting:** any tenant can create `user` first.
- **The gate does not cover it:** the tenant gate exercises only
  `memory_invalidate` among the tools
  (`test_memory_routes_tenant_scope.py:693-713`). Its rename case uses
  another tenant's id, never one's own self entity.

**Failure scenario:**

- Tenant alpha renames their own "You" entity. Tenant beta's hub then shows
  alpha's private name.
- Alpha's agent merges `beta_proj` into `alpha_proj`. Beta's later facts
  then resolve onto alpha's entity id.

**Witnesses:**

- `test_r15_entity_ids_are_global_so_a_second_tenant_writes_into_the_firsts_row`
  (`CROSS_TENANT_ENTITY_ALIAS_WRITE=True`)
- `test_mem_s04_renaming_your_own_self_entity_rewrites_another_tenants_hub`
  (`CROSS_TENANT_HUB_WRITE=True`)
- `test_mem_s04_agent_merge_tool_redirects_another_tenants_entity`
  (`AGENT_TOOL_CROSS_TENANT_MERGE=True`)
- `test_mem_s04_rename_is_an_existence_oracle_for_other_tenants_ids`
  (`ENTITY_ID_EXISTENCE_ORACLE=True`)

**Fix direction:**

- Key entities by `(tenant_id, id)`, or namespace ids per tenant. Design
  this with M02's id helper.
- Pass the tenant through `ensure_user_hub`.
- Every tool that takes an entity id calls the routes' `_row_tenant_ok`
  rule.

**Gate and negative control:**

- Extend the tenant gate to every agent memory tool, and to renaming one's
  own self entity.
- Negative control: today's tool.

### M22 — P2: "Export downloads everything Kazma remembers" is not true

**Original ID:** MEM-S05. **Profile:** all.

**Evidence:**

- **What it exports:** `export_memory` (`memory_api.py:2448-2524`) exports
  `beliefs`, non-forgotten `episodes`, `entities`, `memory_forgotten`,
  summaries and `memory_profile`.
- **What it omits:**
  - `beliefs_archive`
  - `memory_audit_log`, which holds values (M01)
  - `unified_items`
  - `procedural_dags`
  - `entity_merges`
  - `graph_associations`
  - the legacy `episodes_archive` rows not yet restored
- **No bound:** the body is built in memory with `indent=1` and no size
  bound.
- **The claims:**
  - UI: "Download everything Kazma remembers, as JSON" (catalog
    `memory.py:425`, verified).
  - Guide: `memory-and-rag.md:321-325` (verified).

**Failure scenario:**

- A "what do you hold about me" request gets a file that omits stored
  personal data.
- After Hygiene archives the history, the exported past facts shrink, though
  the data is still held.

**Witness:** `test_mem_s05_export_omits_archived_facts_the_store_still_holds`
(`EXPORT_OMITS_ARCHIVED_FACTS=True`).

**Fix direction:**

- Derive the export sections from M01's registry.
- Stream the export.
- Say what is excluded, and why.

**Gate and negative control:**

- A gate compares the memory tables that carry `tenant_id` with the export
  sections, each exclusion declared.
- Negative control: a planted extra table.

### M23 — P2: Forget is reachable from the UI only for the newest 30 memories and 40 summaries

**Original ID:** MEM-S06. **Profile:** all.

**Evidence:**

- **Memories:** `loadV2Memories` fetches `/api/memory/v2/episodes?limit=30`
  with no `offset`, no search and no "N of total"
  (`memory_console.js:1323-1371`). The route supports `offset` and returns
  `total` (`routes_direct/memory.py:870-925`).
- **Summaries:** fetched with `?limit=40` (`memory_console.js:1383`).
- **Other surfaces:** probe and federated hits have no Forget action.
- **The claims:** the guide promises "Pagination + real counts | every list
  tab" (`memory-and-rag.md:389`, verified), and "Forget removes one".

**Failure scenario:** the live install holds about 1,700 memories and about
100 summaries (AGENTS §15J). Older ones cannot be forgotten from the UI.

**Witness:** `test_mem_s06_memory_page_lists_only_the_newest_30_memories_and_40_summaries`
(`FORGET_REACHES_ONLY_NEWEST_30=True`).

**Fix direction:**

- Paging with "N of total".
- Text search over the episodes FTS.
- A Forget action on probe and federated hits.

**Gate and negative control:**

- A browser test, in English and Arabic, seeds 31 or more memories, forgets
  the oldest through the UI, and checks the ledger.
- Negative control: the current panel.

### M24 — P2: The quality measurement is narrower than the claims built on it

**Original ID:** MEM-R12. **Profile:** all.

**Evidence:**

- **It skips the real write path:**
  - The benchmark seeds facts directly through `mutate_belief`
    (`benchmark.py:94-113`). Extraction, entity resolution, the throttle and
    slugging never run, so M02 is invisible.
  - Episodes are inserted without the writer.
  - It uses one tenant, no session, and the local path only.
- **Dataset v4:**
  - 1,120 turns (the test docstring says 1,109)
  - 69 facts, with 0 non-ASCII subjects and 8 Arabic objects
  - 128 summaries
  - 89 questions, 3–18 per category
- **Tuning, not generalization:**
  - The thresholds were swept on the same set (`recall.py:956-1127`), with
    no held-out split.
  - The locked floors include `abstain.hit_rate = 0.2778`: 13 of the 18
    unanswerable questions still inject something.
  - Overall precision is 0.644.
- **The ratchet freezes trade-offs:** it works per score, so a deliberate
  trade-off needs a new dataset version.
- **Latency is not gated.**

**Witness:** `test_r12_benchmark_does_not_exercise_extraction_or_arabic_subjects`
(`ABSTAIN_HIT_RATE=0.2778; QUESTIONS=89; FACTS=69`).

**Fix direction:**

- A held-out question set.
- An extraction benchmark: recorded LLM outputs through
  `_apply_beliefs_to_v2`, with Arabic and mixed subjects.
- Scale tiers (10× and 100×) with a latency budget.
- An explicit abstention target, and a weighted objective with per-category
  floors, so trade-offs can be reviewed.
- A run on the Postgres-primary role (M06).

**Gate and negative control:**

- Extraction-level gold facts per turn.
- Negative control: the current slug rule.
- The §15G ratchet rules apply: new dataset content means a new dataset
  version, with vectors re-recorded using the real model.

### M25 — P3: The cost of a long question is unbounded

**Original ID:** MEM-R14. **Profile:** all.

**Evidence:**

- **FTS:** `_fts_match_query` has no term cap (`recall.py:1469-1480`). A
  2,000-term MATCH measured 668.2 ms on 20,000 rows, and recall runs it for
  both episodes and beliefs.
- **Graph walk:** the seed is O(tokens × names), with tokens not
  de-duplicated (`recall.py:1808-1831`). Log: `short20=0.006`,
  `long1500=0.220` (seconds), `names=3000`.
- **Summaries:** `_cosines` scores every summary in pure Python, 1,024
  dimensions each, on every recall (`recall.py:1310-1326`).

**Witnesses:**

- `test_r14_graph_walk_seed_cost_grows_with_question_length_times_names`
- The micro-benchmark log.

**Fix direction:**

- Cap the query to its N rarest terms.
- De-duplicate tokens.
- Seed the graph walk from an FTS index over entity names.
- Use NumPy or sqlite-vec for summaries.

**Gate and negative control:**

- Recall at 2,000 words stays within 2× of recall at 20 words.
- Negative control: the current code.

### M26 — P3: Smaller write-path correctness items

**Original ID:** MEM-R15, every item except the entity key (that one is in
M21).

**Profile:** all.

**Evidence:**

- **The remember detector over-matches.** It matches English phrases as
  substrings (`remember_request.py:21-28, 55`). "Can you denote that…" and
  "Do you remember that film?" become `recall`-tier memories with importance
  3 or more (`dual_write.py:153-158`).
- **A dead, unsafe writer.** `dual_write.mirror_belief`
  (`dual_write.py:235-306`) has no product caller, and writes past the trust
  gate, the ledger and supersede.
- **An arbitrary current fact.** The functional "existing" lookup is
  `LIMIT 1` with no `ORDER BY` (`belief_mutation.py:698-704`).
- **The deep pass is not idempotent.** There is no "already consolidated"
  marker, and the `remember_turn` de-duplication is process-local
  (`consolidator.py:188-233`).
- **An embedder singleton race.** The singleton is created without a lock
  (`embedder.py:565-662`).

**Witness:** `test_r15_remember_detector_matches_substrings`
(`REMEMBER_SUBSTRING_MATCH=True`). The rest are source-level.

**Fix direction:**

- Match whole phrases at word boundaries.
- Delete `mirror_belief`.
- `ORDER BY` in the lookup, plus a check that at most one current functional
  row exists.
- A per-episode `consolidated_at` and `extraction_version`.
- Lock the singleton.

**Gate and negative control:**

- Unit tests per item.
- An orphan-function check.

### M27 — P3: One failing reconciled turn stops fact extraction for good

**Original ID:** MEM-L12.

**Evidence:** in `turn_reconcile.py:197-231`, an exception keeps the facts
cursor before the turn, by design. It also skips saving the reconcile cursor
(`:250-265`). There is no attempt count and no quarantine.

**Witness:** `test_l12_a_poison_turn_blocks_every_later_reconciled_turn`.

**Fix direction:** an attempt count per episode. After N attempts, mark it
`facts_failed`, advance, and log a WARNING with the episode id.

**Gate:** the inverted witness.

### M28 — P3: The embedding rebuild drops vectors before it knows it can encode, and its pre-rebuild copy is never refreshed

**Original ID:** MEM-L13.

**Evidence:**

- `rebuild_embeddings` NULLs every old vector and commits before it loads
  the model (`reembed.py:223-240`). This contradicts the module's own rule,
  "The old vector stays until its replacement is written"
  (`reembed.py:403-404`, verified).
- `.pre_reembed` is written once and kept forever (`reembed.py:172-187`).
  It is a full copy outside backup retention, including text forgotten
  later (M01).

**Witness:** `test_l13_rebuild_drops_every_old_vector_before_it_knows_it_can_encode`.

**Fix direction:**

- Re-encode in place, as the repair pass does.
- Keep a timestamped pre-rebuild copy, with retention.

**Gate:** the inverted witness.

### M29 — P3: `macro_sleep` writes tiers from a stale read

**Original ID:** MEM-L14.

**Evidence** (code reading): `macro_sleep.py:151-243` reads all rows,
decides in Python, then runs `UPDATE episodes SET tier=? WHERE id=?` with no
expected-tier predicate.

- A forget committed in between has its `forgotten` tier overwritten with
  `episodic`. The text stays empty.
- A recall bump in between is archived anyway.

**Witness:** none.

**Fix direction:** add `AND tier = <expected>`, and count mismatches.

**Gate:** an interleaving test, with a forget between the read and the
write.

### M30 — P3: The nightly export is not a portable text snapshot

**Original ID:** MEM-L15.

**Evidence:**

- `export.py:69-104` dumps `SELECT *` from episodes. `default=str` writes
  each embedding BLOB as a Python `b'...'` literal.
- The audit export takes only the newest 20,000 rows (`:143-146`).
- Failures are logged at DEBUG. Reporting them is in M20.

**Witness:** `test_l15_episode_export_holds_python_bytes_reprs`.

**Fix direction:**

- Exclude embeddings, or encode them explicitly.
- Paginate the audit log.
- Report counts.

**Gate:**

- The inverted witness.
- An audit pagination test.

### M31 — P3: Scale bounds are implicit

**Original ID:** MEM-L16.

**Evidence** (code reading):

- Every 15 minutes, mirror sync reads every local episode's full text and
  every mirror digest (`state_backend.py:1025-1053`).
- Each mirrored row opens a new Postgres connection
  (`state_backend.py:162-170, 274-320`).
- `macro_sleep` scans every episode per tenant (`:151-157`).
- No ceiling is documented beyond the plan's 20k-row figure for exact
  search.

**Fix direction:**

- Incremental sync through a change cursor, with a periodic full
  verification.
- The shared connection pool.
- Published, measured limits.

**Gate:** a benchmark at 10× and 100× the live size, with the hardware
stated.

### M32 — P3: The UI reports success or counts that are not true

**Original ID:** MEM-S08.

**Evidence:**

- **Hygiene:** `hygiene_run` always returns `ok: True`
  (`memory_api.py:2283-2318`), even when the archive step returned an error.
  The page then toasts "Hygiene complete" (`memory.js:817-818`).
- **The Episodes chip** counts forgotten tombstones and retired copies
  (`memory_api.py:476-478`; `memory.js:183`).
- **The Forget toast** ignores `remote_vector_deleted:false`
  (`memory_console.js:1355-1361`).
- **"Archive invalidated beliefs (N)"** counts superseded history too, and
  archives at most 2,000 per run without saying so
  (`memory_api.py:2188-2192, 2253-2257`).
- **The probe panel** drops `weekly_summaries` (`memory_console.js:925-960`).
- **English server strings in the Arabic UI:** the About me limit message
  (`profile.py:77`), `not_found`, and "protected entity: …".

**Witnesses:**

- `test_mem_s08_hygiene_run_says_ok_when_the_archive_failed`
  (`HYGIENE_OK_OVER_FAILED_ARCHIVE=True`)
- `test_mem_s08_episodes_chip_counts_forgotten_tombstones`
  (`EPISODES_CHIP_COUNTS_FORGOTTEN=True`)

**Fix direction:**

- Compute an aggregate `ok` from the results of the actions.
- Count only recallable tiers.
- Show partial forgets as partial.
- Label caps.
- Render summary hits in the probe panel.
- Return error codes, and localize them on the client.

**Gate:**

- The inverted witnesses.
- An Arabic browser assertion on the About me over-limit message.

### M33 — P3: The GraphML export is malformed for any fact with a quote, and both graph exports truncate silently

**Original ID:** MEM-S09.

**Evidence:**

- `xml.sax.saxutils.escape` does not escape `"`, but the output goes into
  `id`, `source` and `target` attributes (`routes_direct/memory.py:1123-1130`).
- Both exports cap at `limit` (default 500, at most 2,000), with no
  truncation flag.

**Witness:** `test_mem_s09_graphml_export_is_malformed_for_a_fact_with_a_quote`
(`GRAPHML_EXPORT_MALFORMED=True`).

**Fix direction:**

- Use `quoteattr`, or build the XML with ElementTree.
- Mark truncation, or stream the whole graph.

**Gate and negative control:**

- Parse both formats with `"`, `&`, `<` and RTL text.
- Negative control: `saxutils.escape`.

### M34 — P3: Read routes write, and undo reads the settings store on the event loop

**Original ID:** MEM-S10.

**Evidence:**

- **Read routes write:**
  - `memory_api._conn()` runs `ensure_primary_schema` on every request
    (`memory_api.py:203-210`, verified).
  - That includes `_backfill_entity_counts`, which UPDATEs stale entities.
    So GET `/vocab`, `/entities` and `/admin/summary` take the write lock
    and write.
  - The "audit O4" note in `routes_direct/_shared.py:74-80` (verified) says
    this was fixed. The fix applies only to `open_memory_db`.
- **Undo reads settings on the loop:**
  - The call chain is `undo_action` (async, on the loop) → `_consume_undo`
    → `_memory_tenant_id` → `multi_user_or_production` →
    `multi_user_enabled` → `get_config_store().get("platform.users")`
    (`memory_api.py:505, 156, 252`).
  - `multi_user_enabled` is in `_LOOP_STALL_HELPERS`
    (`tests/test_static_gates.py:934`). The gate does not follow
    cross-module chains, and the SQL trace cannot see the settings store's
    pre-opened connection.

**Witnesses:**

- `test_mem_s10_a_read_route_writes_the_memory_database`
  (`GET_ROUTE_WRITES=True`)
- `test_mem_s10_undo_reads_the_settings_store_on_the_event_loop`
  (`UNDO_SETTINGS_READ_ON_LOOP=True`)

**Fix direction:**

- Use `open_memory_db` in `memory_api`.
- Resolve the tenant inside the thread for undo, or add the helpers to the
  stall list.

**Gate:**

- Extend stall-helper inference across imported Kazma helpers, with a
  control.
- A GET-route write trace in the tenant gate.

### M35 — P3: Operator memory changes leave no trail; failures look like success over HTTP

**Original ID:** MEM-S11.

**Evidence:**

- **No trail:**
  - No `memory_api` mutation writes an audit row or an INFO log naming the
    actor. Only undo logs (`memory_api.py:511`).
  - `forget_episode` logs nothing on success.
- **Internals reach the client:** `invalidate_belief` logs failures at DEBUG
  and returns `str(exc)[:300]` (`hygiene.py:313-320`). That text reaches
  API clients through unlink (`memory_api.py:1359, 1419`).
- **Failures answer 200:** most routes answer HTTP 200 with
  `{"ok": false}`, `not_found` included.

**Witness:** none (source-level).

**Fix direction:**

- One `record_memory_action(actor, tenant, action, ids)` writer, with ids
  and no values, plus metrics.
- Real 404, 409 and 422 status codes.
- `safe_error` instead of raw exception text.

**Gate and negative control:**

- A recorder gate over every mutating route.
- Negative control: a planted route that skips the recorder.
- The daily digest counts Forgets.

### M36 — P3: Defense in depth and input bounds

**Original ID:** MEM-S12.

**Evidence:**

- **Unescaped attributes:** the graph filter builds `id` and `data-fk`
  attributes from database text without escaping
  (`memory_console.js:4332-4339, 4374`). `backfill_v2.py:485-497` copies
  free-form V1 entity types, so migrated installs can carry arbitrary types.
- **No length caps:**
  - The link route and tool fields have no length cap.
  - `memory_store` current-fact objects are uncapped.
  - `mutate_belief` has no object cap, while the edit route caps at
    200/120/4000.
  - Graph-group fields are unbounded.
- **Tenant filter applied late:** the entity FTS pre-query takes
  `LIMIT 2000` across all tenants before the tenant filter
  (`memory_api.py:583-589`).
- **No body-size limit:** request bodies are read with no size limit.
- **`/memory off`:** in a chat app it is an irreversible forget, with no
  confirmation. Any member may use it when the allowlist is empty
  (`slash_commands.py:377-399`; `changes_global_config` excludes it).

**Witness:** `test_mem_s12_graph_filter_markup_puts_db_entity_types_in_attributes_unescaped`
(static; `UNESCAPED_ATTRIBUTE_FROM_ENTITY_TYPE=True`).

**Fix direction:**

- Escape every attribute value.
- One set of length limits, enforced in `mutate_belief`.
- Apply the tenant filter inside the FTS subquery.
- A body-size limit.
- Confirmation for `/memory off`, or an admin in group chats.

**Gate:**

- A DOM test with a hostile entity type.
- Bounds tests.
- A gateway test.

## AGENTS.md claims that do not match the code

Each row names the merged finding it belongs to. "Verified" means this merge
read the cited code. The other citations come from the parts.

| # | AGENTS.md | Claim | What the code does | Finding |
|---|---|---|---|---|
| 1 | §15I | The facts a forgotten turn produced "are invalidated and lose their value (`object = ''`, `metadata.forgotten`)". | The value survives in `unified_items` (searchable), `memory_audit_log`, `beliefs_archive`, the local and remote fact vectors, operator-edit metadata and queue payloads. `_forget_fact_rows` touches `beliefs` only (`forget.py:433-456`, verified). | M01 |
| 2 | §15I | Export includes "what was forgotten (when, never what)". | Export keeps forgotten facts' subject, predicate, chat and turn (`memory_api.py:2466-2477`). | M01 |
| 3 | §19 E2 | "A deleted document is never still searchable". | Knowledge chunks stay in `unified_items` after a library delete and are served by federated search. Code reading in MEM-S01 and L11; witnessed in KB-E08 and KB-S03. | M01 |
| 4 | §15J | "A summary the user forgets is a tombstone its week never writes again"; "a forgotten summary stays a tombstone". | The rebuild branch sets `status = 'active'` with no status predicate (`topic_summaries.py:612-619`, verified) and resurrects a forgotten summary. | M05 |
| 5 | §15J | `forget_episode` calls `on_turns_forgotten` before it commits: "the summary is emptied at once". | A summary being generated during the forget is stored active, sourced from the forgotten turn. | M05 |
| 6 | §15K | "Every tier or text change reaches the mirror." | Mirror writes are best effort (return False at DEBUG). In Postgres-primary mode the sync that would repair a miss is skipped (`state_backend.py:1003-1004` current tree, verified). | M06 |
| 7 | §21A | "A new shared-state PG table MUST be added to that Python list or it silently stops being backed up." | `kazma_episodes` and `kazma_beliefs` (`state_backend.py:208, 223`) are not in `KAZMA_PG_TABLES` (`pg_backup.py:59-88`, verified). | M06 |
| 8 | §15G | "One ranking, every path". | True for the ranker. The Postgres-primary candidate channels differ: no session bias, no fact bridge, no graph walk, no access bump, and a recency `LIKE` slice. | M06 |
| 9 | §15F | Gate: "no `LIMIT` in a candidate fetch unless it orders by distance". | The gate reads `vector_engine.py` only (`tests/test_memory_nothing_lost.py:546-553`, verified). The Postgres keyword fetch is `ORDER BY created_at DESC LIMIT`. | M06 |
| 10 | §15I (C1) | About me: "The supervisor reads it on every call". | In `per_user` mode it is saved under `default` and read under `web:<session>`, so it is never shown. | M07 |
| 11 | §15I | "Don't remember this chat": "the writer refuses its turns, the post-turn worker … extracts no facts". | In `per_platform` mode the ledger is written under `default` and asked under `web`, so the chat is still remembered. | M07 |
| 12 | §15I | Forgetting "does it to EVERY copy of the turn". | Copies are collected by question hash. An erased NULL-text copy is missed, and recovery refills it. | M08 |
| 13 | §15I | "a forget cut short (`invalidate_belief` commits as it goes) is finished by asking again." | The "already" branch re-runs only the fact step (`forget.py:267-273`, verified). Summaries, remote deletes and mirror tombstones are not retried. | M08 |
| 14 | §16 | "every `/api/memory` route reads, counts and changes only the request tenant's rows -- another tenant's id reads as not found". | Renaming one's own self entity writes the global `user` hub row (`memory_api.py:783-797` → `self_hub.py:174-193`). Rename answers `not_found` for another tenant's id and `created` for an unused one, so it is an existence oracle. | M21 |
| 15 | §15G (S2) | "Memory never runs on the event loop". | The Knowledge promotion runs `mirror_episode` inline in `async def supervisor_node` (`graph_supervisor.py:1139`, verified). | M04 |
| 16 | §35 | "The settings store is never used on the loop". | `undo_action` reaches `get_config_store().get` on the loop through a cross-module chain the gate does not follow (`memory_api.py:505, 156, 252`). | M34 |
| 17 | §15G (R3) | `display_text` "is what the injected block and the tool/probe outputs show". | `memory_search` returns the match text (the question only) as `content`. The display text is only in metadata (`tool_builtins/memory.py:620-652`). | M11 |
| 18 | §20 C | Source-trust gate: a `user_explicit` belief "may NOT be superseded by a lower-trust (`llm_inferred`/`system_tool`) source". | The gate works, but the labels are inverted. Agent tool writes are `user_explicit` (`tool_builtins/memory.py:363, 565, 584`, verified). The user's own statements are `llm_inferred` (`belief_extractor.py:533, 609`, verified). | M03 |
| 19 | §15B | "a sweep failing every pass is a feature that is off" (so the runner logs at WARNING). | Vector repair and recovery catch "database is locked" at DEBUG (`reembed.py:527-529`, `rehydrate.py:750-752`). The runner never sees it. | M20 |
| 20 | §26 F | Errors do not carry internals; handlers return `safe_error`. | `invalidate_belief` returns `str(exc)[:300]` (`hygiene.py:313-320`), which reaches API clients through unlink (`memory_api.py:1359, 1419`). | M35 |

**Omissions** (true, but missing the consequence that matters):

- **§16** notes "`entities.id` is a global PK, not per-tenant". It does not
  state the consequences:
  - a second tenant's alias is written into the first tenant's row;
  - cross-tenant rename and merge;
  - M02's `entity` collapse crosses tenants.

  (M21, M02.)
- **§15K** states that the sync is "Skipped … when Postgres is the primary
  store", and `test_no_mirror_no_pass` locks that in. It does not say that
  nothing then repairs a missed tombstone. (M06.)
- **§15I** lists the ledger's consumers (writer, swarm bridge, legacy
  restore, past-chats). Recovery (`rehydrate`), which restores text, does
  not ask the ledger. (M08.)

**Other docs, UI text and code comments that do not match the code:**

| Where | Claim | Finding |
|---|---|---|
| `docs/docs/guide/memory-and-rag.md:305-309` (verified) | Forget: "Kazma keeps nothing it said -- the question, the answer, the search vector -- and the facts it learned … stop being used." | M01 |
| `memory_console.js:1348` | Forget removes the memory and "the facts it learned from it". | M01 |
| `belief_mutation.py:431-433` comment (verified) | "the graph and the unified index hold current facts only". | M01 |
| `memory-and-rag.md:321-325` (verified); catalog `memory.py:425` (verified) | Export "downloads everything Kazma remembers … and when things were forgotten (never what)"; "Download everything Kazma remembers, as JSON". | M22, M01 |
| `memory-and-rag.md:327-328` (verified) | "A forgotten memory stays forgotten: nothing that rebuilds memory from your chat history brings it back." Contradicted by recovery refilling an erased copy, by `scan_old_sessions.py` (TT-05) and by restores. | M08, M10 |
| `memory-and-rag.md:359-362` (verified) | "Forget one and Kazma never writes it again"; "the summary is emptied at once". | M05 |
| `memory-and-rag.md:389` (verified) | "Pagination + real counts \| every list tab". | M23 |
| `memory-and-rag.md:238` (verified); `memory/backup.py:1-7` docstring (verified) | Native `.db` backups "remain the restore SoT"; the copy "sees a consistent snapshot without blocking writers". | M19 |
| `dual_write.py:350-353` comment (verified) | "nothing is written -- not the row, and not the embedding, remote vector or mirror below". | M08, M15 |
| `dual_write.py:371-374` comment (verified) | "Lock only the local SQLite writes", which implies every local write is under the lock. `LocalSqliteVectorBackend.upsert` is a local write and commit outside it. | M15 |
| `consolidator._run_turn_memory` docstring | Returns False when the turn should be retried. It returns True when the episode was not written (`consolidator.py:418-465`). | M12 |
| `routes_direct/_shared.py:74-80` (verified) | The per-request DDL is fixed (audit O4). That applies only to `open_memory_db`. | M34 |
| `reembed.py:403-404` (verified) | "The old vector stays until its replacement is written." `rebuild_embeddings` does not follow it. | M28 |
| About me page text | "Kazma reads this at the start of every reply". Not true in `per_user` mode. | M07 |
| Time Travel docs and catalog (TT-03) | "Later turns are gone", and "as if the later turns never happened". Memory keeps them. | M09 (cross-area) |

## Staged path to industrial readiness

Each stage closes when every row in it meets its "Evidence required" in the
tracker, and the stage exit gate below passes. Every negative control must
fail on the old code. AGENTS.md and the guide are corrected in the same
change as the code they describe.

### Stage 1 — Memory holds only what is true, and Forget reaches everything (default profile)

**Rows:** M01, M02, M03, M04, M05, M08, M09, M10, M22.

**Exit gate:**

- **Forget completeness.** The token scan covers every table and text column
  of both memory databases, every registered derivative store, and the local
  and remote vectors. It passes after a fault injected at every forget step,
  and after the M05 and M08 race interleavings.
- **Search surfaces.** After invalidate, archive or Knowledge-library
  delete, no search surface returns any part of the value.
- **Bilingual extraction.** An extraction suite in Arabic, Persian, CJK and
  mixed scripts preserves values, with no `entity` fallback.
- **Injection.** A prompt-injection corpus (pages, emails, documents) cannot
  create or protect a user fact, and a tool write never blocks a user
  correction.
- **Event loop.** A real supervisor turn with library injection makes zero
  memory writes on the event loop.
- **Rewinds.** Undo, edit, replay and abort leave nothing recallable.
- **Restores.** Restoring a pre-forget generation and booting keeps every
  forget.
- **Export.** Export ⊇ registry minus declared exclusions.

### Stage 2 — Model and profile boundaries

**Rows:** M11, M07, M21, M06.

**Exit gate:**

- **Fencing.** The fence gate lists `tool_builtins/memory.py`, and a planted
  override string never reaches the model outside a fence.
- **Tenants.**
  - The tenant mode × enforcement × principal matrix passes.
  - The tenant gate covers 100% of routes and tools, renaming one's own self
    entity included.
- **Postgres-primary.** A real-Postgres test in primary mode shows:
  - a missed tombstone repaired within one sweep;
  - recall excluding non-recallable tiers;
  - mirror tables backed up, or declared rebuildable and verified at boot.

  The Postgres-primary benchmark stays within a stated delta of the local
  one.
- **Until this gate passes,** the Postgres-primary and multi-tenant profiles
  are not claimed. MEM-S03's interim fix applies: refuse non-`shared` modes
  under enforcement.

### Stage 3 — Bounded, durable and observable

**Rows:** M12, M13, M14, M15, M16, M17, M18, M19, M20, M25, M27, M28, M29.

**Exit gate:**

- **Latency.** Measured p95 recall latency stays under a stated budget at
  10× and 100× today's live store, with the hardware stated. It stays
  bounded with the embedder down or hanging.
- **Failures.** Every injected failure (mirror, LLM, embedder, locked
  database, enqueue, export) produces a retry where retryable, plus a
  WARNING and a health signal.
- **Queue.**
  - No task runs twice in a 6-hour-sweep simulation with long handlers.
  - A deferred turn completes within a stated bound during maintenance.
  - A ten-minute injected provider outage loses no deep extraction.
- **Backups.** A snapshot copy under continuous writes finishes in bounded
  time.
- **Transactions.** No embedding or network call runs inside a memory write
  transaction (lock probe).

### Stage 4 — Measured, recoverable and operable

**Rows:** M24, M23, M26, M30, M31, M32, M33, M34, M35, M36.

**Exit gate:**

- **Published scores.** Recall, extraction and abstention scores, on both
  the tuning and the held-out set and for both backends, are retained for
  each release.
- **Restore drills** on both profiles (SQLite-only, and mirror or primary)
  restore a pre-forget generation, boot, and verify forgets, reconcile and
  mirror state. RPO and RTO are measured.
- **Bounds.** Every memory table has a declared bound. Sync, archival and
  exact search have measured limits.
- **Soak.** A multi-day soak, restarting every few hours (the live pattern),
  shows no stuck cursor, no growing pending age and no duplicate side effect.
- **Surfaces.**
  - A browser e2e in English and Arabic forgets the oldest memory through
    the UI.
  - The Arabic over-limit message is shown in Arabic.
  - GraphML parses with hostile strings.
  - The GET-route write trace is empty.
  - The daily digest counts Forgets.

**Owner actions outside the stages:**

- The live data repair for M02 (`entity` rows), and the sizing of M03's
  tool-written facts and M04's promoted chunks, need a read-only query of the
  live database by the owner first. The parts did not read live data.
- R6 (the reranker) and the S1 live cleanup stay in
  `docs/plans/MEMORY_REMAINING.md`. They are not re-reported here.

## Limits

- **Environment.**
  - No live install, live logs or live databases were read, so production
    prevalence is unknown for every finding.
  - No real embedding model, LLM, Postgres, pgvector, Qdrant, Neo4j or
    Docker was used.
  - MEM-L02 and L08 used stand-ins, MEM-R11 is source-level, and the two
    real-Postgres mirror-sync tests were skipped.
- **Timing.**
  - MEM-R07's worst case is computed from configured timeouts.
  - MEM-R14's and the micro-benchmark's timings are specific to this machine.
  - The L04 and L09 witnesses scale time thresholds down. The ordering
    defects do not depend on the scale.
- **Method.**
  - Concurrency was witnessed with deterministic single-process probes, not
    load tests.
  - No soak, no multi-process queue claims and no `kill -9` crash tests were
    run.
  - Windows 11 only.
- **Partly reviewed or not reviewed:**
  - the TUI and CLI memory surfaces;
  - parts of `memory.js` and `memory_console.html`;
  - global reconsolidation and entity-merge internals;
  - procedural DAGs, graph hygiene and entity count repair (read for deletes
    only);
  - the `chat_history` Postgres path;
  - `current_facts`, `procedural.py`, the Neo4j and Qdrant backends;
  - the language-lock, router and intent consumers of `last_user_content`;
  - stale `tier` tags in a remote vector index;
  - checkpoint content as a forget remnant (the snapshot half is verified by
    TT-05).
- **Witnesses** confirm defects. They do not measure prevalence, and the
  green existing suites do not establish industrial readiness.
- **Priorities** are engineering judgments under the single-operator posture.
  A multi-tenant deployment raises M21 and M03.
- **This merge:**
  - It ran nothing. It read code only to verify the claims marked
    "verified".
  - Merged priorities take the highest source priority, and the order within
    a priority is this merge's judgment.
  - Where a merged finding combines parts, each part's evidence and
    witnesses are kept.
  - Corrections to the parts are listed under "Checks made during this
    merge".
