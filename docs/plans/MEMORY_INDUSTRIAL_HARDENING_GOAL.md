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
| M01 (P1): MEM-S01, MEM-S02, MEM-L03, MEM-L11 | Forget, invalidate, supersede, archive and Knowledge-library delete leave no copy of the value or its vector in any memory store, index or queue payload, and no search surface returns it. `unified_items` is retired, or kept as a derived index cleaned on every source change. A memory store registry gives each table its forget behaviour, export section or declared exclusion, retention and growth bound; `snapshots.db` is listed (TT-05). Backups, exports and snapshots are named as the erasure horizon, with their retention, next to Forget and in the guide. | A forget-completeness token scan over every table and text column of both memory databases, seeded through product writers with `ops_conn`, plus local and remote fact-vector absence. Invalidate, archive and library delete, each followed by an empty `federated_search`. A table-classification gate and an `upsert_unified` writer↔retraction gate. Negative controls: the current `_forget_fact_rows`, the upsert-only `unified_index.py`, a planted unclassified table. The S01, S02 and L03 witnesses inverted. AGENTS §15I and §19 E2, the guide (`memory-and-rag.md:305-309`), the UI text and the `belief_mutation.py:431-433` comment corrected. | Implemented 2026-10-13 — `unified_items` retired (writers in `belief_mutation`/`stores/knowledge`, reader in `federated_search`, module deleted; `ensure_ops_schema` drops the table). Forget now takes the fact's value, vector and every metadata field but provenance (operator-edit `previous` included), rewrites `beliefs_archive` copies, redacts `memory_audit_log` values to a hash (event kept), empties queued `post_turn_memory` payloads, scrubs the legacy `episodes_archive`/`entities_archive` copies, deletes the remote vectors of the turn's facts, and merges the FTS5 indexes (`purge_search_index`: the old segments kept the words). Tool records keep argument names only (`strip_argument_values` rewrites old rows); the recorder's tuple rows that froze procedure counts fixed. Registry `memory/memory_stores.py` (every table of both databases + external stores: erase class, forget step, export section or exclusion, retention, bound; `snapshots.db` listed; `ERASURE_HORIZON`). Evidence: `tests/test_memory_store_registry.py` — one word through every writer with `ops_conn`, forget (turn and chat), every column of every `sqlite_master` table of both databases scanned, FTS shadow blobs included, local and remote fact vectors absent; negative controls the pre-fix forget (7 places) and the missing purge; table gate with a planted table; unified writer/reader gate. `tests/test_memory_forget_witnesses.py` — S01, S02 ×3, L03 inverted; invalidate, archive and library delete each followed by an empty `federated_search`. AGENTS §15I, the guide and the Forget dialog (EN/AR, with the erasure horizon) corrected. The interrupted/racing forget is M08 |
| M02 (P1): MEM-R01 | Entity ids preserve every script. One id helper, with no constant fallback; the other three slug copies deleted. Existing `entity` rows are repaired from provenance or quarantined. | A bilingual extraction test (Arabic, Persian, CJK, emoji) through `_apply_beliefs_to_v2` and `mutate_belief`: distinct ids, values preserved, no cross-subject supersede. An AST gate on the single helper, with the current slug rule as negative control. A repair procedure tested on a copy. The live read-only sizing and the repair are run by the owner. | Implemented 2026-10-13 (live repair is the owner's) — one id rule, `entity_resolution.slug`: NFKC, casefold, letters/marks/digits of every script kept, a label with no letter gets an id from its own text, an empty label raises (`mutate_belief` refuses it); ASCII ids unchanged (checked against the old rule). The copies in `belief_mutation`, `dual_write`, `backfill_v2` and `memory_api` are now the one helper; the alias hash folds through `documents.arabic.fold_for_search`; the sibling ASCII rules (`current_facts.normalize_service_slug`, `graph_hygiene._same_words_key`) use it. Repair: `memory/entity_repair.py` + `scripts/memory_entity_repair.py` (`size` read-only; `apply --db --yes [--no-queue]`): collapsed facts quarantined (rows kept), their turns queued for the deep pass, a turn that says "entity" left as ambiguous, the `entity` node retired when empty. Evidence: `tests/test_memory_entity_ids.py` — R01 witnesses inverted (English controls kept); Persian, CJK and emoji through `_apply_beliefs_to_v2`: distinct ids, values kept, no supersede; AST gate (no `a-z0-9` keep-list, no `"entity"` fallback in memory code; the old rule as negative control; the names bound to the one helper); repair sized then applied on a copy, idempotent, re-extraction under fixed ids. AGENTS §15L and the guide ("Names in any language", the repair steps). Owner: run `size` on the live store, then the repair |
| M03 (P1): MEM-R02 (+ MEM-S07 input screening) | `user_explicit` comes only from the user's own channel. Agent writes are labelled agent-asserted and screened for injection. An agent write that supersedes or retires a user fact needs a commitment or HITL decision. | A test that a tool-initiated write never blocks a later user correction. An injection corpus (pages, emails, documents) is refused and cannot create or protect a user fact. Negative control: the current labels. AGENTS §20 updated with the provenance ladder. | Implemented 2026-10-13; the relabel tool for the facts the old tools wrote built 2026-10-15 (the live run is the owner's) — `memory/provenance.py` is the ladder: `user_explicit` only from the user's channel (Memory page; a memory tool on a turn whose latest user message is a remember request holding ≥80 % of the stored content words, EN/AR); `user_stated` for the heuristic pass over the user's own message; `system_tool`, `agent_asserted`, `llm_inferred` below. The gate (`may_overrule`, both the supersede and the history path) lets a user fact yield only to user words, except the model pass correcting its own turn's heuristic reading. `memory_store` / `memory_link_entities` screen with `filter_injection`/`is_override_delta` (refused, nothing stored), label `agent_asserted` otherwise, and answer `user_fact_stands` instead of replacing a user fact; the backfill's model re-extraction is `llm_inferred`; the same-word merge counts both user labels. Evidence: `tests/test_memory_provenance.py` — R02 witnesses inverted; EN/AR remember requests; paraphrase and bare "remember this" stay the agent's; every corpus case the screen should catch is refused, and every case (caught or not) can neither become a user fact nor outlast "I live in Lisbon now"; same-turn correction allowed, another turn's blocked; negative control the old gate; AST gate: only the Memory page and the private golden eval write `user_explicit`, the old tool call caught. AGENTS §20 (ladder) and the guide updated. Relabel (2026-10-15): `memory/provenance_relabel.py` + `scripts/memory_relabel_provenance.py` (`size` read-only, ids never values; `apply --db --yes [--ops-db]`, copy to `<db>.pre_relabel` first). A `user_explicit` fact is a tool's when a `memory_store_tool` note of its tenant names its chat turn (or, before notes named their chat on 2026-09-27, holds its value within 5 s), or when it names a chat turn (only the tools wrote `user_explicit` with one); it is kept when the Memory page edited it (`metadata.operator_edit`), a 2xx operator action named it (`memory_audit_log` `operator_action`, M35), or its turn's stored question asks to remember those words (`provenance.agent_write_source`, today's rule, so post-fix writes stay); everything else stays and is counted unattributed (page links, the V1 backfill, a pre-2026-09-27 link-tool write: nothing recorded says which). Relabelled to `agent_asserted` with that trust weight, one transaction, one `provenance_relabel` audit row per fact (labels, never values), idempotent; an unreadable audit log refuses. Evidence: `tests/test_memory_relabel_provenance.py` (every case on a seeded store: old and chat-named notes, a link, an asked-to-remember turn, a page edit, a 2xx and a 404 operator action, the page's link, the backfill, another tenant, untouched `user_stated`/`llm_inferred`; size shows no value; negative controls: before the relabel a tool's `works_at` blocks a later model reading, after it it is superseded; without the operator trail the kept facts would be relabelled) |
| M04 (P1): MEM-R03 | No memory write runs on the event loop. Knowledge text is never stored as a chat turn's user text: the promotion is removed, or moved off the loop, stored as a separate kind, excluded from extraction and summaries, and forgotten on library delete (KB-S03). | An event-loop SQLite and embedder tracer over a real `supervisor_node` turn with library injection. Recall after a promotion shows no Knowledge text as history. `_LOOP_STALL_HELPERS` extended. Negative control: the current code. | Implemented 2026-10-13 — the promotion is removed: no `promote_kb_hits_to_episodes`, no `memory.v2.promote_kb_to_episodes` default, route field or Settings toggle (EN/AR merge hint now says library text never enters chat memory), no recovery from the library. Copies an earlier build left are retired by the maintenance sweep "retired knowledge promotions" (`forget.retire_knowledge_promotions`: tombstone, no ledger row, summaries rebuilt, FTS purged, mirrors and remote vectors updated). `mirror_episode` and the sweep are loop-stall helpers. Evidence: `tests/test_memory_kb_never_in_memory.py` — a real `supervisor_node` call that injects library text under an SQLite write tracer and an embedder tracer on the loop thread: no write, no embedding, no library text in episodes; recall after the turn shows none; negative control the old promotion call on the loop (the tracer sees its INSERT); retirement idempotent and ledger-free; the cadence lists the sweep; only the retirement names the old source. Knowledge tracker log notes the shared halves (KB-E08/KB-S03 promotion, KB-S06 supervisor). AGENTS §15B/§15F and three guide pages corrected |
| M05 (P1): MEM-L01 | A summary is never stored or re-activated from a forgotten turn, or over a forgotten summary. | The inverted L01 witnesses. A static gate: every statement that sets a `memory_summaries` row active carries a status predicate. Negative control: the current `_store`. The guide (`memory-and-rag.md:359-362`) holds afterwards. | Implemented 2026-10-13 — `topic_summaries._store` is a compare-and-set inside one `BEGIN IMMEDIATE`: the source turns are re-read (`_live_turn_ids`, the recallable tiers); a text written from a turn forgotten meanwhile is never stored — a new topic's remaining turns, when still ≥ `min_turns`, wait as an empty `rebuild` row with only those sources — and a rewrite is `UPDATE … WHERE id = ? AND status = 'rebuild'` with the rowcount checked (a forgotten or retired summary is never reactivated). The vector is made before the transaction. `summarize_period` counts `forgotten_meanwhile`. Evidence: `tests/test_memory_summary_store_cas.py` — both L01 witnesses inverted (the forgotten turn never written, the remaining four rebuilt without it; a summary forgotten during its rebuild stays a tombstone); a turn forgotten during a rebuild leaves it waiting, then rebuilt without it; negative control the old unguarded store resurrects the summary; static gate: every UPDATE/INSERT that sets a `memory_summaries` row active carries a status predicate, the old statement caught. AGENTS §15J and the guide (weekly summaries) updated |
| M08 (P2): MEM-L05 | A forget interrupted at any step, or racing a write, a keep-out or recovery, converges within one maintenance pass. Recovery and every rebuilder (`scan_old_sessions.py`, TT-05) honour the ledger. | A journaled forget. A fault-injection matrix that raises after each step, followed by one sweep, after which M01's completeness test passes. The inverted L05 witnesses. Negative control: the sweep disabled. AGENTS §15I ("EVERY copy", "finished by asking again") made true. | Implemented 2026-10-13 — a journaled forget (`memory/forget_journal.py`, its own file `memory_forget_journal.db`): every forget, chat forget, keep-out, "remember again" and summary forget is appended before anything changes; `forget_episode` runs EVERY step each time (`_forget_turn`: copies, notes, ledger, tombstones, legacy, facts, archive, active summaries of every forgotten copy, FTS purge, mirror, remote, ops) and marks its entry applied only after the last; the maintenance entry "forget convergence" (first memory sweep, also at boot) applies pending entries in order (stops at a failure), enforces the ledger on stored rows (`forget.enforce_ledger`: copies of a forgotten turn, rows written into a kept-out chat after the keep-out, a chat's past forgotten in the last day, active summaries from a forgotten turn) and retries failed remote deletes (`forget_remote_pending`). The writer's ledger check and insert are one `BEGIN IMMEDIATE`; it pushes the current row to the mirror and deletes a remote vector it upserted for a row forgotten meanwhile. Recovery asks the ledger before refilling; `scripts/scan_old_sessions.py` too. A tombstone keeps a note's chat/turn. Evidence: `tests/test_memory_forget_converges.py` — a fault after each of 10 steps, then one pass: the M01 completeness scan (both databases and the journal, local and remote vectors, nothing pending) passes; negative control without the pass fails at every step; the three L05 witnesses inverted (summary emptied on retry; a keep-out on another thread waits for the writer's transaction and then forgets the row; recovery marks the erased copy "forgotten"); a row slipped past a keep-out forgotten by the pass; gate: only the forget code passes `journal=False` (planted route caught). AGENTS §15I. Snapshots' own copies remain TT-05 |
| M09 (P2): MEM-R10 | Undo, edit, replay/restore and abort forget, or mark superseded, the turns they rewind. The deep pass checks that its episode is still live. Decided together with TT-03. | A real-graph rewind-then-recall test for each rewind path: the seven state rewriters the witness lists. Negative control: the current behaviour. | Implemented 2026-10-13 (decided with TT-03: memory is rewound) — `memory.forget.rewind_memory` is called by `/undo` and `/edit` (gateway; transforms now in `agent/rewind.py`), by every restore (`time_travel.apply_snapshot_to_thread`, used by `/replay <n>` and the web replay route) and by abort (gateway and web): the turns taken back are forgotten by position — episodes, notes, facts — under every chat key and tenant that holds them, journaled, with ledger rows from the old questions so a deferred write, the deep pass or turn reconcile from the unchanged transcript cannot restore them; a fork leaves the original chat's memory. The deep pass applies only while its turn is still remembered (`worker_bootstrap._apply_if_still_remembered`). Reading the pre-restore state never blocks a restore. Evidence: `tests/test_memory_rewind.py` — undo, edit, restore and abort through a real LangGraph graph with recall after (rewound content gone, earlier turns kept, no second version after an edit, an aborted turn's later write refused); a fork keeps the original's memory; negative control the rewrite alone; the deep pass skips a forgotten turn; AST gate: every product function calling `.aupdate_state(` calls `rewind_memory` or is declared not a rewind (the supervisor watchdog's reflection note), the old undo caught. Reply texts, the slash-command reference, the guide and AGENTS §12 D2 updated; the Time Travel tracker log notes the decision |
| M10 (P2): MEM-L07 | Restoring an older memory generation, or importing a migration bundle, does not undo forgets. Snapshots are covered too (TT-05). | An append-only forget journal outside `memory_state.db`, backed up separately, and replayed at boot and after a restore or import. A test that restores a pre-forget backup and boots: no re-creation by reconcile, mirror tombstones kept. A runbook step and a drill check. Negative control: the same run without the replay. | Implemented 2026-10-13 (one design with M08) — the append-only forget journal lives outside `memory_state.db` (`<data dir>/memory_forget_journal.db`, declared in `store_registry` as `bundle`: universal backups and migration bundles carry it; the native memory backups copy only the memory databases), and the applied marks live inside it, so a restored older copy lacks the marks of later forgets and the boot/15-minute pass applies them again before turn reconcile and the mirror sync run (sweep order gated). Evidence: `tests/test_memory_forget_converges.py` — a pre-forget copy restored over `memory_state.db`: the pass forgets the turn again and turn reconcile does not rebuild it; negative control without the replay: reconcile and recall keep it; a keep-out then "remember again" replayed in order; the restore drill reports the forgets a backup would replay (`memory:forget_journal`, read-only). Disaster-recovery runbook: the journal in "What to protect", a restore step and a drill-checklist line. A migration import is replayed at the next boot. Snapshots (`snapshots.db`) restore: the memory side asks the ledger at every writer; the snapshots' own copies remain TT-05 |
| M22 (P2): MEM-S05 | Export covers every tenant memory table minus declared exclusions. It is streamed. Forgotten items appear as "when, never what". | A table↔section gate derived from M01's registry, with a planted-table negative control. A streaming and size test. The UI (`memory.py:425`) and the guide (`:321-325`) corrected. | Implemented 2026-10-13 — the export's sections are the registry's (`memory_api._EXPORT_QUERIES` must equal `memory_stores.exported_sections()`, else it refuses): About me, facts, archived facts, fact changes (the audit log, forgotten values redacted), memories, weekly summaries, entities, entity merges, graph groupings, procedures, forgotten; `not_included` names each excluded table with why. Forgotten facts (live and archived) carry only when. Streamed from a sync generator, 500 rows a batch. Evidence: `tests/test_memory_store_registry.py` — section set equals the registry, a section without a reader refuses (control), every table with a `tenant_id` names its section or exclusion with a planted-table control, 5,000 memories streamed in batches, the route streams (no Content-Length); the S05 shape (archived facts present) asserted. UI text (`memory.py`) and the guide corrected |
| **Stage 2** | **Model and profile boundaries** | | |
| M11 (P2): MEM-R13, MEM-S07 | Every memory tool output that reaches the model is fenced and shows the display text. "Degraded" is distinct from "empty". About me's write scope matches its authority: per user in multi-user mode, or admin-only. | `tool_builtins/memory.py` added to `FENCED_TOOL_FUNCTIONS`. A behavioural test that a planted override string arrives inside `<kazma:data …>`. Negative control: the current output. An RBAC / per-user About me test. | Implemented 2026-10-13 — `memory_search`, `memory_list_beliefs`, `memory_list_entities` and `memory_admin`'s list actions return what memory holds inside the untrusted-data fence (`_fence_result` → `fence_untrusted(source="memory_search")`; own messages unfenced); `memory_search` shows each hit's display text (question and answer), and a recall failure answers "unavailable … NOT an empty result" instead of "No relevant memories found."; the sibling `knowledge_search` (KB-S01) fences the crawled hits with its citation directive outside. About me: changing it is admin-only under RBAC (`platform_rbac.role_allows`: PUT/POST/PATCH/DELETE `/api/memory/v2/profile`), reading stays open (per-user About me is not built). Evidence: `tests/test_memory_tools_fenced.py` — a planted override reaches the model only inside `<kazma:data …>` through `memory_search`, `memory_list_beliefs`, `memory_admin list_beliefs` and `knowledge_search`; the display text shown; degraded ≠ empty; negative control the old raw output carries it; RBAC: operator/viewer refused, admin allowed, GET open, the rest of `/api/memory` unchanged. `FENCED_TOOL_FUNCTIONS` covers `tool_builtins/memory.py` and `knowledge.py`, and the gate reads tools registered inside `register_*` functions. AGENTS §15I/§26G and the guide updated |
| M07 (P1, multi-tenant): MEM-S03 | One tenant resolver for turns, routes, tools, gateway and probe. About me, the ledger, forget and export act on the tenant the user's turns are written under, in every offered mode. Until then, non-`shared` modes are refused under enforcement. "Also forget" reports `forgotten: 0`. | A matrix: mode {shared, per_platform, per_user} × enforcement {off, on} × principal {unbound, bound}. Negative control: the current two-resolver code. | Implemented 2026-10-13 (per-user About me / per-user memory under enforcement not built; refused instead) — one enforcement answer, `memory.config.memory_tenant_enforced`, used by turns and surfaces; under enforcement `resolve_tenant_id` returns the principal's tenant in every mode (the surfaces' tenant), and saving a non-`shared` mode is refused (`settings_validation`, `TENANT_MODE_KEY`, also inside a `memory` mapping); not enforced, the page's "Don't remember" ledger rows (`refuses_write`/`forgotten_turns` read the install's `default` rows too) and About me (`profile.about_for_turn`, install fallback only when not enforced) reach every tenant's turns, and Forget from the page (`default`) reaches every tenant. The chat delete toasts "Kazma held no memories of it" when the forget found nothing (EN/AR, bridged). The Settings hint no longer recommends per-user mode for multi-user installs. Evidence: `tests/test_memory_tenant_matrix.py` — every reachable cell of mode × enforcement × principal: About me saved through the route is what the turn is shown, a "Don't remember" through the route stops the turn's writer, Forget through the route finds the turn's memory; non-shared modes refused under enforcement (single and mapping saves); negative control the old shared-mode resolver under enforcement (not_found). AGENTS §16 updated |
| M21 (P2, multi-tenant): MEM-S04, MEM-R15 (entity-key item) | No tenant can read, write or probe another tenant's entity row through any route or tool. Entities are keyed by `(tenant_id, id)`, designed with M02's helper. | The tenant gate extended to every agent memory tool and to renaming one's own self entity. The S04 and R15-tenant witnesses inverted. Negative control: the current tool. AGENTS §16 states the key. | Implemented 2026-10-15 (the migration of an existing store is the owner's to run) — first, 2026-10-13, the boundary held by rules: `_create_entity` never appends to another tenant's row; `self_hub.ensure_user_hub(tenant_id=…)` writes only the caller's own `user` row (the install's `default` tenant may write any; the rename and the graph route's hub heal pass the caller's tenant); rename promotes only a node of the caller's own facts, so another tenant's id and an unused one both answer `not_found` (no existence oracle); the agent merge tool applies the routes' tenant rule. Evidence: `tests/test_memory_entity_tenancy.py` — R15 and the three S04 witnesses inverted; a node of the caller's own graph still promoted; every agent memory tool that takes an id (merge, delete, invalidate, and memory_admin's merge/delete/invalidate), run as tenant alpha against beta's ids, leaves beta's rows unchanged; negative control: the install's own tenant changes them. Then, 2026-10-15, the key: `entities` is keyed by `(tenant_id, id)` and `entity_merges` names its entities by the pair (composite foreign keys; `schema_v2.ENTITIES_TABLE_DDL`); every entity statement (about a hundred, in `entity_resolution`, `entity_counts`, `entity_protection`, `entity_retire`, `self_hub`, `belief_extractor`, `ego_anchor`, `graph_backend`, `graph_hygiene`, `v2_health`, `worker_bootstrap`, `memory_api`, `routes_direct/memory`, the agent tools) names its tenant, and which row a request means is one answer (`memory/entity_key.entity_owner`: the caller's own; the install's view its own first, else the one tenant holding it). Each tenant has its own `user` hub. Migration: `scripts/memory_entity_key.py` (`size` read-only: rows by tenant, merge rows naming no entity of their own tenant, facts naming an entity only another tenant holds; `apply --db --yes`: `snapshot_copy` to `<db>.pre_entity_key`, then both tables rebuilt in one transaction with every column, row, id, tenant and rowid, indexes and the search index rebuilt, idempotent); every statement works on both keys, memory health reports `entity_key`. The Postgres mirror holds no entities (checked: `state_backend` has none). Evidence: `tests/test_memory_entity_key.py` — the source gate (every entity statement in product code and scripts names its tenant, an id-addressing WHERE names it there; cross-tenant reads declared; planted old statements as the negative control); two tenants holding `shared_node` through every memory route of the app (the route gate's requests plus twin requests, enumerated: a route naming an entity in its path or body must have one) and every agent memory tool taking an entity (enumerated from signatures), beta unchanged and unseen, alpha's own twin renamed/protected/flagged/deleted/merged; negative controls: an owner lookup answering the other tenant (the gate reports beta changed) and the old key (the twin insert collides); each tenant's own hub; the migration on a copy of a store the product's writers seeded under the old key (both tenants, merges, groupings, a fact the old key left without an entity), rows/ids/rowids/merges unchanged, integrity ok, idempotent, then recall, the graph route, a merge route call and the old merge row's decision work, and beta gets its own row of a name the install holds; the script (size, refusal without `--yes`, copy first, idempotent). `tests/test_memory_entity_tenancy.py` updated (R15: the second tenant gets its own row; S04: alpha's own hub synced, beta's untouched). AGENTS §16 and §15L, the guide |
| M06 (P1, Postgres-primary): MEM-L02, MEM-R11 | In Postgres-primary mode recall never returns a forgotten or non-recallable tier, and a missed tombstone is repaired within one sweep through a durable outbox. Mirror tables are backed up, or declared rebuildable and verified at boot. Recall has a parity contract with a relevance-ordered keyword channel. | A real-Postgres test (`@pytest.mark.postgres`, disposable container) with an injected mirror-write failure. The `KAZMA_PG_TABLES` and boot-verification diff. `test_no_mirror_no_pass` re-pointed. The benchmark under the Postgres-primary role, within a stated delta of local. The slice gate widened to `state_backend.py`. Negative controls: the current skip and the recency slice. AGENTS §15K and §21A made true. | Done 2026-10-13: the sync pass runs in primary mode (the local store is the outbox, one pass repairs a missed tombstone); the mirror's search and fetch read recallable tiers only, the keyword channel orders by words held; recall drops what the local tombstones and forget ledger name; `kazma_episodes`/`kazma_beliefs` in `KAZMA_PG_TABLES`, required at boot when memory mirrors to Postgres. Evidence: `tests/test_memory_pg_primary.py` on a disposable capped postgres:16 (6 passed); benchmark primary vs local: hit rate 0.9253 = 0.9253, MRR 0.8444 = 0.8444, precision 0.6277 vs 0.6443 (delta held at 0.02; the index-down control falls to 0.8009/0.7488). The old recency slice scores the same as the new channel on the benchmark (the index finds what it dropped); the targeted test holds it. |
| **Stage 3** | **Bounded, durable and observable** | | |
| M12 (P2): MEM-R09, MEM-L09 | Failed episode writes and LLM deep passes are retried with backoff and counted. "Provider unavailable" is distinct from "empty result". Dead letters are kept until acknowledged, counted in health and alerted. | Fault-injected mirror and LLM-outage tests: the task stays pending with backoff, then completes after recovery. `mirror_fail` / `deep_pass_failed` counters and health asserted. Negative control: the current `return True`. | Done 2026-10-13: `write_episode` returns written/refused/failed and a failed write raises in the pipeline (`mirror_fail`, the live thread defers the turn to the queue); the deep pass tells the model unavailable (retried, `deep_pass_failed`) from an empty answer (done); `next_attempt_at` backoff 30 s x4 to an hour, 6 attempts for memory writes; dead letters kept until retried or cleared, one degrades health and alerts `memory.dead_letter`. Evidence: `tests/test_memory_queue_retries.py` (R09 and L09 witnesses inverted, recovery after the outage, empty answer done; negative controls: the ignored None, the one-None extractor, no backoff, the old purge). Memory page strip shows `d` (deep pass) |
| M13 (P2): MEM-L04 | Each queued task executes once. Memory-write tasks are not starved by maintenance. | The inverted L04 witness. A lane latency test: `post_turn_memory` behind two long maintenance tasks completes within a stated bound. Negative control: the batch claim. | Done 2026-10-13: each lane claims no more rows than its free slots and registers the lease at the claim; memory writes (`MEMORY_LANE`) have their own lane. Evidence: `tests/test_memory_queue_retries.py` — the L04 witness inverted (each task runs once), a `post_turn_memory` task behind two 2-second maintenance tasks completes in under 0.5 s; negative controls: the old batch-claim loop runs the task twice, one lane makes the turn wait over 1.5 s. Idempotency keys for side-effecting handlers are not added: with no late dispatch, a reclaim happens only after a crash or a 300 s silent handler |
| M14 (P2): MEM-R06, MEM-L08 | No embedding, network or remote-backend call runs inside a memory SQLite write transaction or `_mutation_lock`. The access bump is off the turn path, or has a short timeout. | A lock and transaction probe inside every embed, mirror, graph and vector hook used by `belief_mutation`, `macro_sleep`, `hygiene` and `forget`. Recall latency under a held write lock below a stated bound. Negative control: the current code. | Done 2026-10-13: the fact's vector after the commit, outside the mutation lock (`_store_belief_vector`); the superseded edge and mirror tombstone after the commit; `macro_sleep` unmirrors after its commit; the access bump waits at most 50 ms and keeps busy counts (`flush_access_bumps`, written by the next recall or `macro_sleep`). Evidence: `tests/test_memory_io_outside_locks.py` — a lock-and-transaction probe in every embed, mirror, graph and vector hook of `belief_mutation` (new, supersede, history, set), `hygiene`, `forget`, the episode writer and `macro_sleep` finds the database free and no lock held; recall under a held write lock returns in under 1.5 s and both recalls are counted; negative control: the old in-transaction embedding seen by the probe |
| M15 (P2): MEM-R05 | One vector write per row, guarded by tier, under the writer's lock. No commit of a caller's transaction. | A spy test: one embedding, lock held, no mid-transaction commit. A forget-race test. Negative control: the current code. The `dual_write.py` comments made true. | Done 2026-10-13: `memory/vector_write.py` — one `encode_vector`, one guarded `write_local_vector` (commits only its own transaction; under the episode writer's lock), the remote index only through `get_remote_vector_backend` (taken back out after a forget); the forget, macro_sleep and rebuild paths use the remote-only getter too. Evidence: `tests/test_memory_io_outside_locks.py` — one embedding per episode and per fact, the episode vector written under the lock, no commit of the caller's transaction, a forget landing during the embedding leaves no local vector and deletes the remote copy, no memory module upserts through the local backend; negative controls: the local backend's upsert commits and re-vectors a forgotten row, a planted local upsert. The `dual_write.py` comments now describe the code |
| M16 (P2): MEM-R07 | Recall has a deadline. One embedding attempt per recall. An embedder circuit breaker, shown in health. "Degraded" distinct from "empty". | Hanging- and failing-embedder tests with time bounds. A log-count test: one failure log per breaker period. Negative control: the current code. | Done 2026-10-13: a per-recall memo embeds each text at most once (a failure included) in a bounded daemon thread waited on until `memory.v2.recall_deadline_s` (5 s); past it, keywords only and `RecallResult.degraded`; `memory_search` says incomplete, not empty; `_EmbedderBreaker` in both embedders (30 s doubling to 10 min, no call or log while open) shown in memory health. Evidence: `tests/test_memory_recall_deadline.py` — one attempt per recall with a failing embedder (keywords still answer), a 3 s hanging embedder bounded under 1.5 s by a 0.5 s deadline, one log per breaker period for a broken local install, no request while a remote breaker is open, health DEGRADED with `embedder_unavailable`, the tool's degraded answer; negative controls: the body without its memo (2+ attempts) or deadline (waits the whole call), a breaker that never opens (a log per call), a real empty answer |
| M17 (P2): MEM-R04 | Recall and the other consumers receive the text parts only, capped. | A photo turn through the supervisor node, with recall's argument asserted. Negative control: the current `str(content)`. | Done 2026-10-13: the supervisor reads `extract_latest_user_text(messages, image_marker=False)` (text parts only) for recall, the language lock, the router and intent; recall bounds the question (M25). Evidence: `tests/test_memory_question_bounds.py` — a photo turn through the real `supervisor_node` hands recall only "what breed is this dog?" (no base64, under 200 characters); negative control: the old `str(content)` is over 200,000 characters with the payload |
| M18 (P2): MEM-R08 | The extraction embedding budget does not depend on the entity count. | The embed-call count at 60 and at 6,000 entities is equal. Negative control: the current code. | Done 2026-10-13: entities store their name vector (`entities.embedding`: at creation, on an exact match, and by the vector repair pass for older rows); tier 2 queries the nearest (`VectorEngine.nearest_entities`, sqlite-vec or NumPy) instead of embedding every name; the fact text is embedded once (M15). Evidence: `tests/test_memory_entity_name_vectors.py` — an extraction's embed calls are identical at 60 and 6,000 entities (its subject, object, fact and hub link), a name still merges onto an entity by its stored vector, repair stores missing name vectors and stops without an embedder; negative control: the old pre-pass embeds all 60 names |
| M19 (P2): MEM-L06 | Every SQLite snapshot copy completes under continuous writes. | The inverted L06 witness through the product helper. A class gate banning `backup(pages>0)`. Negative control: `pages=100`. The docstring and the guide (`:238`) made true. | Done 2026-10-13: `kazma_core/db/sqlite_snapshot.snapshot_copy` (one-step `backup()`) is the one copier: native memory backup, universal backup, the rebuild's pre-copy, the restore route, the X shadow script. Evidence: `tests/test_sqlite_snapshot.py` — the L06 witness inverted through `backup_one` (under 1 s with a writer every 2 ms), a class gate over every product module and script (no `.backup(` outside the helper, planted-copy control); negative control: `pages=100` finishes only after the writer stops at 2 s. The `backup.py` docstring and the guide's "restore SoT" now hold. `test_backup_reports_the_real_loss` re-pointed to the helper |
| M20 (P2): MEM-L10 | Every sweep, enqueue and export failure surfaces at WARNING after N passes, and in health: oldest pending and processing age, and last success per sweep and per task type. | The inverted L10 witnesses, plus enqueue and export cases. Negative control: the current swallow. | Done 2026-10-13: schedulers enqueue through `_enqueue_checked` (WARNING + ops alert `memory.enqueue_failed`); `enqueue_task` failures at WARNING; vector repair and recovery let a locked or busy database reach the runner, which logs INFO for two passes then WARNING and records each sweep (`sweep_health`); health shows `sweeps`, the queue's oldest pending/running ages and last success per task type, and degrades on three failed passes; a failed export piece fails the task. Evidence: `tests/test_memory_lifecycle_signals.py` — both L10 witnesses inverted (and "no such table" stays quiet as the control), the busy-pass threshold, a refused enqueue warns and alerts, an AST gate that no scheduler enqueues unchecked (planted control), health ages/successes/sweeps, a failed export piece returns False and a whole one True |
| M25 (P3): MEM-R14 | Recall cost is bounded in question length. | A timing test on a seeded store: a 2,000-word query within 2× of a 20-word query. | Done 2026-10-13: `recall()` reads at most 2,000 characters of the question (start and end), the keyword query and graph-walk seeds at most 32 words, each once. Evidence: `tests/test_memory_question_bounds.py` — on a seeded store (1,500 facts, 2,000 memories) recall at 2,000 words took 1.6x recall at 20 words (median of 5; gate 2x); negative control: with the bounds lifted it took 87x |
| M27 (P3): MEM-L12 | A failing reconciled turn is quarantined after N attempts, and the cursors advance. | The inverted L12 witness. | Done 2026-10-13: a turn failing `_FACT_ATTEMPTS` (3) passes is marked `metadata.facts_failed` with a WARNING and the cursor advances; attempts persist in the reconcile state; a locked database raises and counts nothing. Evidence: `tests/test_memory_lifecycle_cas.py` — the L12 witness inverted (turns 2 and 3 get their facts after three passes), attempts persisted by the real pass; negative control: a locked database never sets a turn aside |
| M28 (P3): MEM-L13 | The embedding rebuild never leaves rows without a vector when the model cannot load. Pre-rebuild copies have retention. | The inverted L13 witness. | Done 2026-10-13: the rebuild loads the embedder first, re-encodes in place (rows that fail keep their old vector, counted `skipped_rows`), and takes `memory_state.db.pre_reembed` again at every rebuild (atomic replace; the erasure horizon's "until the next rebuild" now holds). Evidence: `tests/test_memory_lifecycle_cas.py` — the L13 witness inverted (no model: the vector stays, no copy made), a rebuild re-encodes in place and refreshes a day-old copy |
| M29 (P3): MEM-L14 | Tier transitions are compare-and-set. | An interleaving test with a forget between the read and the write. | Done 2026-10-13: every tier move is compare-and-set on the tier and last touch the sweep read (`_ARCHIVE_EPISODE_SQL` included); `moves_skipped` counts the rows that changed. Evidence: `tests/test_memory_lifecycle_cas.py` — a forget committed between the sweep's read and its write stays forgotten (`moves_skipped == 1`); negative control: the old statement overwrites it |
| **Stage 4** | **Measured, recoverable and operable** | | |
| M24 (P2): MEM-R12 | A held-out question set. An extraction benchmark with Arabic and mixed subjects. Scale tiers with a latency gate. An explicit abstention target, and a reviewable trade-off rule. | A published report per release, on the tuning and the held-out sets, for both backends. Recorded extraction fixtures. The §15G ratchet rules followed. | Partial 2026-10-14. Done: dataset v5 with 40 held-out questions (incl. cross-language `mixed`), vectors re-recorded with bge-m3, `lock --rebaseline`; extraction benchmark (30 EN/AR/mixed turns) gated for the heuristic pass, with a record/replay harness for the model's pass; abstention targets (0.6) and the reviewed trade rule (`lock --trade`, `trade_problems`, recorded); Postgres-primary run with pgvector and its floors; scale tiers with a latency gate (M31's `test_memory_scale_bench.py`); the report in the guide ("Benchmark report", "Measured limits"). Measured findings: heuristic extraction Arabic recall 0.00 -- FIXED 2026-10-14 (`memory/arabic_facts.py`: Arabic 0.64, mixed 0.60, English 0.46, precision and quiet 1.0; floors raised; `tests/test_memory_arabic_facts.py`); held-out abstention 0/8 (tuning 5/18) -- not fixed: a 16-setting floor sweep found no setting inside the trade rule that moves the held-out set (guide, "Benchmark report"); it needs a signal of what is asked about. 2026-10-15, one such signal measured and not adopted: a question naming a word no memory of the tenant holds keeps only strong matches. It reaches the target (tuning 0.28 -> 0.67, held out 0 -> 0.50) but removes true weak answers with the same evidence as the near misses (paraphrase hit 1.0 -> 0.875, Arabic 1.0 -> 0.75, noise 1.0 -> 0.667, update 1.0 -> 0.857, overall MRR 0.844 -> 0.774): `trade_problems` refuses it, and so every variant chosen on the tuning set (summaries left out; only when nothing strong is shown); the variant inside the rule (two thirds of the words known) leaves held-out abstention at 0. Recorded in the guide's "Benchmark report"; no product change. Open: the model's extraction replies are not recorded (owner: `python scripts/memory_bench.py extraction --record`, one call per turn on the configured model, then `lock`); "per release" means re-running this report at each release (no release automation added). Evidence: `tests/test_memory_benchmark.py` (held out scored apart, builder refuses repeats, trade-rule refusals with an accepted trade as control, extraction ratchet with an empty extractor as control, replay harness), `tests/test_memory_benchmark_pg.py`. |
| M23 (P2): MEM-S06 | Every memory and every summary can be reached for Forget from the UI. | An e2e test that forgets the 31st-oldest memory through the UI, in English and Arabic. Paging with "N of total", search, and Forget on probe and federated hits. The guide (`:389`) made true. | Done 2026-10-14. Both routes page (`offset`, `total`); memories searchable by every word (`q`, `_every_word_match`, EN/AR); Forget on probe and "Search both stores" memory/summary hits (`_forgetEpisode`/`_forgetSummary`). Gates: `tests/test_memory_forget_reachable.py` (page walk; first page alone as control; a Forget list loading without an offset fails, with its control); `tests/e2e/test_memory_forget_older.py` (oldest of 35 forgotten via the page, EN by Older, AR by search; ledger + tier). Guide "Forget one memory" rewritten. |
| M26 (P3): MEM-R15 (other items) | Remember phrases matched at word boundaries. The dead `mirror_belief` removed. An ordered functional lookup, with an at-most-one-current check. An idempotent deep-pass marker. A locked embedder singleton. | Unit tests per item. An orphan-function check. | Done 2026-10-13: whole-word English phrases ("do/did you" questions excluded); `dual_write.mirror_belief` and its helpers deleted (the kept-connection test re-pointed to `write_episode`); an ordered functional lookup closing extra current rows; a durable deep-pass marker (`metadata.deep_pass`); a locked embedder singleton. Evidence: `tests/test_memory_write_path_items.py` — a unit test per item, each with its negative control (the substring rule, the unordered `LIMIT 1`, the marker cleared, an unlocked build), and an orphan check that every name `dual_write` exports has a product caller |
| M30 (P3): MEM-L15 | The export output is portable and complete, and failures are reported. | The inverted L15 witness. An audit-log pagination test. | Done 2026-10-13: the export pages every table through `fetchmany` (no newest-20,000 slice), leaves vectors out, writes other bytes as base64, and reports counts and failed pieces (`_ExportResult`). Evidence: `tests/test_memory_lifecycle_signals.py` — the L15 witness inverted (no `embedding`, plain text), 25,000 audit rows exported whole (the old export stopped at 20,000), a failed piece fails the task |
| M31 (P3): MEM-L16 | Sync, archival and exact search have measured, documented limits. | A benchmark at 10× and 100× the live size, with the hardware stated. Incremental sync with a periodic full verification. | Done 2026-10-14. Trigger-fed outbox (`memory_mirror_outbox`) drained each pass; full comparison daily (`memory.v2.mirror_verify_hours`); pooled connections; batched episode pushes (`mirror_episode_rows`). Measured at 1×/10×/100× (1,700 / 17,000 / 170,000 memories + as many facts) with the machine stated: guide "Measured limits". Found by the benchmark and fixed: macro sleep with the mirror on pushed each move on its own connection (336 s at 10× → 7 s). Published ceiling: recall (exact search) 8.2 s a question at 100×. Evidence: `tests/test_memory_mirror_incremental.py` (class gate on mirrored columns with control; incremental vs full digest reads; per-row vs batch; retry/drop; down keeps the queue; no mirror empties it; region conflict; real-PG pool count vs a connection each), `tests/test_memory_scale_bench.py` (opt-in, limits). Not done: an approximate vector index (the stated next step past ~40,000 memories). |
| M32 (P3): MEM-S08 | Hygiene, forget, the counts and the probe report what actually happened. | The inverted S08 witnesses. The probe renders summaries. An Arabic error-localization test. | Done 2026-10-13: hygiene's `ok` aggregates its actions; the Episodes chip counts recallable tiers; the archive reports `remaining` and `cap`, labelled; partial forgets say so; the probe renders weekly summaries and a degraded search; `not_found`, `protected`, `about_too_long` are codes the page words (EN/AR catalog). Evidence: `tests/test_memory_page_truth.py` — both S08 witnesses inverted (the tombstone count kept as the control), the cap, the codes; `tests/e2e/test_about_me_limit_arabic.py` — the over-limit message in Arabic in a real browser, with no English left (the English page as the instrument's control) |
| M33 (P3): MEM-S09 | Graph exports are well-formed and flag truncation. | A parse test with `"`, `&`, `<` and RTL text. Negative control: `saxutils.escape`. | Done 2026-10-13: GraphML built with ElementTree, invalid XML characters dropped; both formats carry `truncated` (JSON field, GraphML graph data, `X-Kazma-Truncated` header). Evidence: `tests/test_memory_graph_export.py` — both formats parse with a quote, `&`, `<`, Arabic and a tab; a graph past its limit says so; negative control: `saxutils.escape` inside an attribute fails to parse |
| M34 (P3): MEM-S10 | GET memory routes execute no writes. Nothing reads the settings store on the loop. | A GET write-trace gate. Cross-module stall-helper inference, with a control. AGENTS §35 holds for this path. | Done 2026-10-13: `memory_api._conn()` uses `open_memory_db` (schema once per process); the graph search runs read-only; undo resolves its tenant in a thread; the memory worker's alerts, the offsite retry and `extract_and_apply_beliefs`' apply moved off the loop. Evidence: `tests/test_memory_routes_tenant_scope.py` — every memory GET route traced, no write (the old opener under a stale count as the control); `tests/test_static_gates.py` — cross-module stall inference over the memory path, the undo chain as the negative control (invisible to the in-module inference). Scope: the memory path; product-wide the inference names ~130 other sites, left to their areas |
| M35 (P3): MEM-S11 | Every operator memory mutation is attributable (actor, tenant, ids, no values). Correct HTTP status codes. No raw exception text. | A recorder gate over the mutating routes, with a planted-route control. The daily digest counts Forgets. `safe_error` on unlink. | Done 2026-10-13: `kazma_ui/memory_actions.MemoryActionRecorder` (ASGI, mounted with the memory API) logs and audits every memory mutation (route, actor, tenant, ids, status; no values) and maps `ok: false` answers to 404/409/422/500; the digest counts forgets; `invalidate_belief` uses `safe_error`; the page's catch paths word the codes. Evidence: `tests/test_memory_actions.py` — every mutating memory route (38) requested and recorded, the planted control (the same routes without the recorder) caught; status codes; an audit row and log line with the id and no value; no exception text; the digest's forget count. The tenant gate re-pointed to accept a handler's own 4xx |
| M36 (P3): MEM-S12 | Database text is escaped in every HTML attribute. One set of length limits on memory writes. A tenant-scoped entity FTS. A request body size limit. A confirmed `/memory off`. | A DOM test with a hostile entity type. Bounds tests. A gateway test. | Done 2026-10-13: `memory/limits.py` enforced first in `mutate_belief` and by the edit, link and group routes; a 256 KiB memory body limit (413, declared or streamed); the tenant inside the entity FTS subquery; `_attr` for every database value in a console attribute; `/memory off confirm`. Evidence: `tests/test_memory_input_bounds.py` — each limit refused and the limit itself written (control), link/group 422s, a declared and a streamed oversized body 413 with a small one read (control), a tenant's own entity found past 2,100 of another tenant's matches (the old subquery as control), `/memory off` changes nothing until confirmed; `tests/e2e/test_memory_hostile_type.py` — a hostile entity type renders as text with no event handler in a real browser (it caught a self-recursive escaper in this very change before it shipped) |

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
- 2026-10-13, Stage 1 (M01, M02, M03, M04, M05, M08, M09, M10, M22):
  implemented with behavioural tests, class gates and negative controls (each
  row's state cell). New stores: `memory_stores` (the memory store registry)
  and the forget journal (`memory_forget_journal.db`, declared in
  `store_registry`). Exit-gate items, each with its test: forget completeness
  after faults (a fault after each of ten steps, then one pass) and races
  (`test_memory_forget_converges.py`); no search surface returns forgotten,
  invalidated, archived or deleted-library text (`test_memory_forget_witnesses.py`;
  `unified_items` retired); the bilingual extraction suite
  (`test_memory_entity_ids.py`); the injection corpus
  (`test_memory_provenance.py`); no memory write on the loop during a library
  turn (`test_memory_kb_never_in_memory.py`); rewinds and restores keep
  forgets (`test_memory_rewind.py`, the restore replay); Export ⊇ registry
  (`test_memory_store_registry.py`). Owner actions: M02 `size` then the
  repair on the live store; M03 relabelling of facts earlier tool calls
  stored as `user_explicit`, if wanted. Full suite and e2e at the end of the
  plan.
- 2026-10-13, Stage 2 (M06, M07, M11, M21): M06, M07 and M11 implemented
  (state cells); M21 partial (the composite entity key is a schema change
  across every writer and the graph; the boundary holds by rules today, the
  owner decides the migration). Exit gate: the fence gate
  (`test_memory_provenance.py` and M11's tool fences) passes; the tenant
  matrix (`test_memory_routes_tenant_scope.py`, every route enumerated from
  the app) passes; real-Postgres primary-mode repair and tier filter
  (`test_memory_pg_primary.py`) pass; the benchmark delta between the two
  paths is measured (M24, 2026-10-14: tuning hit rate 0.895 Postgres-primary
  against 0.925 local). The multi-tenant profile is not claimed while M21 is
  partial.
- 2026-10-13, Stage 3 (M12-M20, M25, M27-M29): implemented (state cells).
  Exit gate: recall bounded with the embedder down or hanging
  (`test_memory_recall_deadline.py`); injected failures retried and visible
  (`test_memory_queue_retries.py`, `test_memory_lifecycle_signals.py`); no
  task run twice (`test_task_queue_lease.py`, `test_memory_queue_retries.py`)
  and no stale tier move over a newer change (`test_memory_lifecycle_cas.py`);
  no deep extraction lost to an outage (`test_memory_queue_retries.py`: a
  model outage defers, never counts as "nothing to learn"); snapshot copies
  bounded under writes (`test_sqlite_snapshot.py`); no slow I/O inside write
  transactions (`test_memory_io_outside_locks.py`). Recall latency at 10x and
  100x with the hardware stated: measured 2026-10-14 (M31, guide "Measured
  limits") as medians -- 0.67 s and 8.2 s a question; p95 was not taken
  separately, and 100x is over an interactive two-second budget (the
  published ceiling is about 40,000 memories).
- 2026-10-13/14, Stage 4 (M23, M24, M26, M30-M36): implemented, M24
  partial (state cells). Exit gate: tuning and held-out scores published for
  both backends (guide "Benchmark report"); declared table bounds
  (`memory_stores`, every table with `bound`); the EN/AR browser forget e2e
  (`tests/e2e/test_memory_forget_older.py`). NOT done: restore drills on both
  profiles with RPO/RTO (no drill was run on a Postgres-primary memory
  profile), and the multi-day soak with restarts (not run; a session cannot
  hold one). Stage 4 does not close until both are run.
- 2026-10-14, Stage 4 follow-up (the owner's "proceed": everything that does
  not need the owner):
  - **Restore drills on both profiles, with RPO and RTO: done.**
    `tests/test_memory_restore_drill.py` (local) and
    `tests/test_memory_restore_drill_pg.py` (Postgres primary, real
    `pg_dump`/`pg_restore` on a disposable postgres:16, 1 CPU / 1 GB). At
    1,700 turns: local RPO = nothing a chat turn holds (850 rebuilt), RTO
    0.95 s at the restore; Postgres-primary RPO = nothing (850 pushed back),
    RTO 0.11 s at the next pass. A forget made after the backup holds in
    both. Guide "Restore drills". The first runs found three defects, each
    fixed with its negative control: turn reconcile's cursor in the settings
    store (a restored memory database never rebuilt the chats changed since
    its backup; now `memory_reconcile_state` in `memory_state.db`); the
    restore route left forgets made since active until the next 15-minute
    pass (`restore_memory_backup` applies the journal and reconciles at
    once); a restored Postgres mirror waited for the daily comparison
    (`_mirror_went_back`, a 5,000-row pass budget, and a comparison still
    pushing runs again next pass). Also: `KAZMA_BACKUPS_DIR` naming a folder
    not yet made failed every native memory backup (it is made now).
  - **The soak harness: built, piloted; the multi-day run is not done.**
    `scripts/memory_soak.py` (`tests/test_memory_soak_harness.py`, with a
    deleted memory as the negative control). Pilot: 60 cycles, 10 killed,
    13 restores, 15 backups, 197 chat turns, 54 forgets, 3 chats kept out --
    no violation. Two pilot failures were the harness's own (a
    case-sensitive token check; turn numbers advanced by killed cycles) and
    are fixed. The multi-day run needs days of this machine: the owner's.
  - Stage 4 exit gate now: published scores (done), restore drills with
    RPO/RTO (done), declared table bounds (done), the EN/AR browser forget
    e2e (done); the multi-day soak (NOT run). Stage 4 does not close until
    it is.
  - Also done: `scripts/reembed.py` runs the product's
    `rebuild_embeddings` and no longer deletes the Knowledge Library's
    vector store (`tests/test_reembed_keeps_knowledge_vectors.py`).
  - Verification, this working tree: full suite 15,403 passed, 178 skipped,
    4 failed -- the metrics receipt (commit-time step), two timing tests that
    pass on their own (`test_sqlite_snapshot` batched-copy control,
    `test_research_runtime` deadline cleanup; the machine was loaded), and
    one real failure, fixed and re-run (a test reading the reconcile cursor
    from the settings store). Postgres suite on a disposable postgres:16:
    412 passed, 10 skipped (the container removed after). CI's ruff gate
    clean.
- 2026-10-14, plan close-out (M23, M24, M31 finished this day; every row has
  a state). Verification, this working tree:
  - full suite (`python scripts/fast_test.py`, 8 chunks): 15,358 passed, 176
    skipped, 1 failed -- `test_metrics_receipt_current`, which needs the new
    files staged and `python scripts/generate_metrics.py --write
    --require-collected` before a commit (not staged here: no commit was
    asked for). No poison file. Six chunks passed their 1,360 s budget on a
    loaded machine and every file then passed in the runner's recovery.
  - the first full run's 12 failures were this plan's own leftovers and are
    fixed: three phase-1 tests of the removed `mirror_belief` (re-pointed to
    `mutate_belief`), two source locks on `_enqueue_checked`, the literal
    search reading fenced tool output, the env index, the e2e files CI must
    run (added to `ci.yml`), the debt baselines, and
    `extract_and_apply_beliefs` with a thread-bound connection (it now
    refuses loudly instead of applying nothing). The documents certification
    test shared one certification run (three ran it, 267 s) -- it was poison
    under the 120 s per-test limit.
  - e2e: the memory page, chat delete/forget, soft navigation, page load,
    the three new files, the Arabic page tour and text direction -- 29
    passed (the About me test waits for the saved text before typing; it
    raced the page's load under a busy run).
  - Postgres suite (`scripts/postgres_suite.py`, disposable postgres:16, 1
    CPU / 1 GB, removed after): 401 passed, 19 skipped. The pgvector
    benchmark ran on a disposable `pgvector/pgvector:pg16` (removed after).
  - ruff CI gate (`--select E9,F63,F7,F82,F841,B033` on the packages): clean.
- 2026-10-15, the open Memory items (M21, M03 relabel, M24 abstention):
  - **M21 implemented**: entities keyed by `(tenant_id, id)`, every entity
    statement scoped (a source gate holds it), each tenant its own hub, and
    `scripts/memory_entity_key.py` to migrate a store made under the old key
    (copy first, one transaction, idempotent). The multi-tenant profile's
    entity boundary no longer rests on rules alone once a store is migrated;
    an unmigrated store keeps the 2026-10-13 rules, and health says which.
    Owner action: `size`, then `apply` on the live store when Kazma is idle
    (a single-tenant install gains nothing it needs today).
  - **M03 relabel built**: `scripts/memory_relabel_provenance.py`. Owner
    action: `size` on the live store, then `apply` when Kazma is idle.
  - **M24 abstention**: one signal of what is asked about measured with the
    trade rule and not adopted (row M24, the guide's "Benchmark report").
