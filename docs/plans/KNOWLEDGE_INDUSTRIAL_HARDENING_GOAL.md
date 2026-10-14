# Knowledge Library industrial hardening

Status: OPEN -- 2026-10-13

## Objective

Execute the complete Knowledge Library (KB) audit remediation and qualification plan, with evidence for each requirement. The plan covers the KB engine and the KB surfaces. Industrial readiness is not achieved by fixing only the reproduced defects or by obtaining a green unit suite. Each row needs its stated evidence, including a negative control that shows the new gate fails on the old behaviour.

## Baseline

**Code state.** The uncommitted working tree of 2026-10-13, on top of commit `d7e27c194d3f170092a9c405709b9197a2c00161`.

**Audit.** `docs/audits/AUDIT_KNOWLEDGE_2026-10-13.md` is the merged audit: 26 findings, 8 P1 (KB-S04 only for multi-user deployments), 12 P2 and 6 P3. It merges two halves:

- `session-artifacts/audits-2026-10-13/kb-engine.md` (engine): 6/10 capability and architecture, about 3.5/10 demonstrated industrial readiness;
- `session-artifacts/audits-2026-10-13/kb-surfaces.md` (surfaces): 6/10 and about 4/10.

These scores are engineering judgments, not certifications.

**Witnesses** (in `session-artifacts/audits-2026-10-13/`):

| File | Result | Log |
|---|---|---|
| `kb_engine_probes_2026_10_13.py` | 15 passed in 5.37 s | `kb-engine-probes.log` |
| `kb_surfaces_probes.py` | 13 passed in 0.73 s | `kb-surfaces-probes.log` |
| `kb_surfaces_kazmasave_probe.js` (node) | 1 passed | `kb-surfaces-js-probe.log` |

The witnesses **assert the old defects**. A pass confirms a defect and certifies nothing. They must never become permanent tests that require the defects to remain. Invert each one into a regression test of the fixed behaviour, with the old behaviour as its negative control.

**Existing KB suites at baseline:**

- Engine group: 116 passed, 9 skipped (Postgres parametrizations, no pool configured); log `kb-engine-regression.log`.
- Surfaces group: 40 passed; log `kb-surfaces-existing-gates.log`.
- The two groups overlap on `tests/test_knowledge_store.py` (16 tests).
- Every audited defect was present while these suites passed, so none of the findings is gated at baseline.

**Not run at baseline:**

- the full suite;
- a browser tour of `/knowledge` in EN or AR;
- Postgres;
- real crawls, Chromium, or remote providers;
- the real embedding model (bge-m3);
- an end-to-end `kazma migrate` run or universal-backup restore;
- multi-process Chroma;
- any load, latency or soak measurement;
- a live prompt capture.

## Requirements and completion evidence

Rows are ordered by the merged audit's stages:

1. Model boundary and deletion.
2. Silent loss and durability.
3. Truthful surfaces and bounded inputs.
4. Cost, visibility, ownership and tenancy.
5. Qualification.

Each ID keeps the audit's original identifier, so the witness for a row is the one named in the audit entry.

| ID | Required result | Evidence required | State |
|---|---|---|---|
| KB-S01 (P1, stage 1) | Every KB text that reaches a model is fenced: the `knowledge_search` tool, `kazma mcp` exposure, swarm workers and auto-inject. Page-authored fence breakers (`</kazma:data>` in a header or title) are sanitized. Kazma's own scaffolding (index, score, URL label, citation) stays outside the fence. | `FENCED_TOOL_FUNCTIONS` entry for `kazma-core/kazma_core/agent/tool_builtins/knowledge.py` (search `True`; list, create and ingest `False`, each with a reason). A behavioural injection test, EN and AR, including the MCP path: the payload appears only inside the fence. Negative control: the current formatter. | Open |
| KB-S08 (P2, stage 1) | One Knowledge injection per turn. The supervisor merge reuses the transport turn note or is removed, and promotion is out of the prompt path. | A graph-level test: the first LLM payload of a turn contains exactly one `<kazma:data source="knowledge"` block. Negative control: the current transport plus supervisor double path. | Open |
| KB-S02 (P1, stage 1) | Unified rows never displace a Knowledge injection. Each store is ranked on its own scale with its own budget, never against a constant. The `include_*` flags mean what they say. The explain payload ("Memory used") lists exactly the chunks that were injected. | A real-store inject test in which both stores hold the chunk yields a non-empty fenced block. The explain payload equals the injected chunk ids. `include_knowledge=False` returns no `kb:` rows. Negative control: the fixed `0.4` unified score (`memory/federated_search.py:206`). | Open |
| KB-E08 / KB-S03 (P1, stage 1) | Delete, archive and page purge remove or retire every derived copy of the library's text, and a library delete stops its crawls and tells the Documents platform. The copies are: unified-index rows (under the correct tenant, or no longer written), promoted chat-memory episodes (through the forget tombstone machinery, never a silent hard delete), workspace provenance files (moved under the data dir and tied to the library's lifecycle) and the Documents index record. Promotion is opt-in and carries library and chunk provenance. | Delete, archive and purge tests, after each of which the library's text is absent from: federated search (API and supervisor), the explain payload, the `unified_items` table, memory recall and the provenance folder. The unified and promotion writers are declared in `tests/test_memory_deletes.py` and the forget gate. Negative control: the current delete. | Open |
| KB-S09 (P2, stage 1) | Only http(s) seed and source URLs are stored, in every writer (`POST` and `PATCH` routes, `knowledge_create_library`, `knowledge_ingest_url`). URLs are rendered as links only through one safe-href helper. A library is created only after its first fetch is admitted. | Store-boundary scheme tests over `javascript:`, `JaVaScRiPt:`, `data:`, `vbscript:` and whitespace-prefixed forms, in every writer. A template gate requiring every content `:href` to go through the helper. Negative control: the current store. | Open |
| KB-E05 (P2, stage 2) | Repeated sections on one page keep every position (`chunk_index` or an occurrence ordinal in the id). The reported count is truthful, and an unchanged page is skipped. | The same page with repeated sections, ingested twice: every position is stored, the count is truthful, and the second pass reports unchanged. Legacy ids are migrated by re-ingest. Negative control: the position-free id. | Open |
| KB-E02 / KB-S12 (P1, stage 2) | Page publication is one transaction (old or new version, never a mix) with typed outcomes. A write failure fails the page, and the job ends `partial`, never `completed` with a success toast. Delete answers 409 while a crawl owns the library, or cancels and joins the crawl. A crawl stops when its library is gone, and no orphan Chroma collection is created. An ambiguous KB page can be replayed on explicit resume. | Statement-level fault injection and a child-process kill matrix (before and after the purge): old or new only, truthful counters (failures never counted as skips), page `failed`, job `partial`. A delete-during-crawl test. A class gate: every KB writer goes through one publication path. Negative control: the current `index()` leaves a partial page and the job `completed`. | Open |
| KB-E03 (P1, stage 2) | Vectors carry the active embedder's identity (class, model, dimension, provider/base-URL digest), taken from the instance, never from configuration. The collection records the same identity and is refused or rebuilt at open. A model switch converges in one sweep. Health reports coverage as "active chunks whose vector carries the active identity", with an explicit "rebuilding" state. | Drills: a config change without restart, then a restart, then a fallback to local, each at the same and at a different dimension. After one sweep, every vector carries the active identity and queries come from the active space. Health under-reports until then. Negative control: the configuration-name tag. | Open |
| KB-E04 (P1, stage 2) | The crawl registry writes only the changed job (one row per job, or a table, with a per-job CAS). Terminal rows are evicted by bytes as well as count, never active ones. An operator purge exists. Each job's bytes are bounded at admission below the registry budget. | A full-registry admission test: a new 1,000-page crawl starts and runs while retained terminal jobs fill the old budget. A constant bytes-per-page-update bound. Four concurrent crawls with no `journal is busy`. Negative control: the single-value `acquisition.crawls` registry. | Open |
| KB-E10 (P2, stage 2) | The KB has its own database file (for example `knowledge.db`), declared in the store registry, backup and migration. It has an admission quota on the KB's own pages and a compaction step. Reads never fail on the quota. Decide this layout before KB-E01. | At the quota, ingest is refused before fetch with a typed error while reads keep working. Compaction after deletion restores ingest. Store-registry and backup declaration gates. Negative control: the current shared-`settings.db` ceiling, which makes the KB unopenable. | Open |
| KB-E01 (P1, stage 2) | Migration carries libraries, chunks, FTS (with folded Arabic) and document-backed chunks, and a fresh target opens its KB. No importer-local KB DDL exists, and import goes through the store's own schema. | Subprocess export→import round trips from a SQLite source and from a Postgres-settings source. On the target: a library, a chunk, a keyword hit and a folded Arabic hit, and a document-backed chunk are readable, and the repair sweep rebuilds the vectors. A class gate on importer DDL. Negative control: the current `_merge_kb_into_settings`. | Open |
| KB-E09 (P2, stage 2) | After a `settings.db` rollback, deleted documents are not searchable through any KB path, because document tombstones are authoritative at reconcile. The reconcile logs `extra` and `orphaned` at WARNING and raises an ops alert. | The restore drill from the witness: the deleted document is absent from `KnowledgeIndex.search_sync` and KB-wide search, with a WARNING and an alert. Re-point `test_extra_and_orphaned_chunks_are_pruned_only_on_request`, which locks the current behaviour. Negative control: the current reconcile. | Open |
| KB-S05 (P2, stage 3) | Typed acquisition refusals reach the operator as stable API codes (with `transient`) and EN/AR text from `knowledge.err.<code>` catalog entries. Expected refusals are logged once at INFO or WARNING, never as ERROR with a traceback. Validation strings are localized. | A parametrized route test over every `FetchErrorCode` a KB route can raise: stable code, localized text, no ERROR record. An Arabic browser check of a refused crawl. Negative control: the current `safe_error` mapping to `internal_error`. | Open |
| KB-S07 (P2, stage 3) | `/kb` subcommands behave as help, menu and docs say. Either they are handled deterministically in the gateway, admin-gated through `changes_global_config` and reporting the journal's real outcome, or they are removed everywhere and a gated `knowledge_delete_library` tool (danger tier, HITL) is added. The dead `_try_kb_command` branches are deleted. | Gateway tests for add, crawl, refresh and delete (or their documented refusal). `tests/test_slash_help_and_menu.py` extended to subcommands. Negative control: today's slash-turn rewrite. | Open |
| KB-S10 (P2, stage 3) | No Settings control reports "saved" over `{ok:false}`. `window.kazmaSave` rejects `ok === false`, and `PUT /api/settings/memory/merge-kb` answers 4xx or 5xx on failure. | An `ok:false` case in `tests/js/test_kazma_save.js`. A refusing-server run of `tests/js/test_settings_saves_report_truth.js`. Negative control: the current `kazmaSave` check. | Open |
| KB-S13 (P3, stage 3) | Every KB route uses bounded request models behind the Documents bounded reader: `top_k` 1-20 (route and agent tool), `query` up to 2,000 characters, id up to 64 after slugging, name and description up to 500, seed up to 2,048 and http(s). There is one resume path (`resume_job_id` removed from `/ingest`), and page mode's `ok` reflects the outcome. | A walk of every KB route with oversized, malformed and boundary values (no 500, no unclamped `top_k`). A bounded-reader test. Negative control: `Body(...)`. | Open |
| KB-E14 + KB-S15 registry bullet (P3, stage 3) | One crawl journal: the legacy `kb.ingest_jobs` registry is retired. One crawl per library at a time. The observed final URL is the page identity. No double acquisition between discovery and fetch where the profiles allow. | An API state-source test (one source of crawl state). A concurrent-crawl refusal test. A redirect identity test (indexed once). | Open |
| KB-S15 (P3, stage 3) | KB page states are truthful: a partial outcome is never shown as "Done", there are no dead counters ("Pruned"), and mixed-direction content (seed link, current URL) carries `translate="no"` and `<bdi>`. `GET /api/kb/libraries` has no storage side effects (no collection creation, no embedder load). | JS and browser tests in EN and AR (extend `tests/js/test_kb_recovery.js` and the Arabic page tour), each with an old-state control. A side-effect-free list-read test. | Open |
| KB-S14 (P3, stage 3) | The KB docs match the code: pruning, isolation from chat memory, fetch order, job durability, tool location, TUI, archived-library search, `/kb` commands and the `kb_api.py` docstring (16 routes at baseline). Rewrite after the stage 1-3 behaviour settles. | A claims table checked against code facts (for example "pruning: never" ↔ `pruned_urls = 0`), with a negative control per row. The rewritten guide and reference. | Open |
| KB-S06 + KB-E13 loop bullet (P2, stage 4) | No KB SQLite, settings, embedding or mirror work runs on the event loop. That covers supervisor promotion, the agent tools and the gateway `/kb` helpers. Tools that never await become plain `def`s, and the others use `to_thread`. | `_LOOP_STALL_HELPERS` additions: `promote_kb_hits_to_episodes`, `mirror_episode`, the `KnowledgeStore` public methods and the `kb_jobs` functions. The debt-ratchet baseline (`async_tools_never_await`) lowered in the same change. A SQLite tracer test during KB tools, gateway `/kb` and the supervisor merge. Negative control: the current call sites. | Open |
| KB-E11 / KB-S11 (P2, stage 4) | The repair sweep is isolated per library. The sweep report is published as metrics, with a WARNING on the first unavailable pass and on recovery. An ops alert (with cooldown) fires when meaning search stays unavailable or below coverage. `kb_vectors` appears in readiness and the digest. FTS storage errors are distinguished from syntax errors, logged at WARNING and reported in health. FTS coverage is checked and repaired. A fold version triggers a `folded` rebuild. | A poisoned-library test (other libraries repaired). A dead-vector-store test asserting the WARNING and the alert. Dropped-FTS and corrupted-FTS tests asserting a WARNING and a health failure. A fold-version-bump test. A drill record. Negative controls: the current code fails each. | Open |
| KB-E06 (P2, stage 4) | Per-chunk FTS write and delete cost is independent of total FTS size (FTS keyed and deleted by rowid). The store lock is kept out of long work, and libraries are deleted in bounded batches. Search latency stays bounded during ingest. | A VM-step or timing scaling test at 1k, 10k and 100k FTS rows. p95 search latency under a concurrent large ingest. Negative control: the current `UNINDEXED`-column deletes. | Open |
| KB-E07 (P2, stage 4) | Crawl ownership carries install id, host and process start time, with leases. Only owners proven dead on the same install and host are taken over, and only with fencing. The registry is scoped per install. Readiness states that the KB is single-install. | A fake foreign-host running row survives another install's `start()`. A same-host dead PID is recovered. A lapsed lease is taken over with fencing. Negative control: the current `[pid, create_time]` owner. | Open |
| KB-E12 (P3, stage 4) | There is one `KnowledgeIndex` per store behind a guarded singleton, shared by Documents and the KB. On `NotFoundError` the index resets, re-opens by name and retries once. Library collections carry a generation, or dropped names are never reused. | With real Chroma: delete and re-create the same slug, and drop a collection from one instance through a cross-instance `_fit`. The other instance's next write and query succeed. Negative control: the current second instance. | Open |
| KB-E13 (P3, stage 4) | A reopened `KnowledgeStore` connection keeps all pragmas (foreign keys included), and `close()` takes the store lock. | A reopen-then-delete test leaves no orphan rows. A close-during-read race test. Negative control: the current `_get_conn` and `close()`. | Open |
| KB-S04 (P1 multi-user / P3 single-operator, stage 4) | Either every `/api/kb` route and crawl-job read is tenant-scoped, or the KB is refused in multi-user mode. Scoping means: one tenant-scoped library lookup before acting, tenant carried on chunks, unified rows and journal rows, no existence oracle on create, and the credits refresh scoped. | An app-enumerated two-tenant route matrix modelled on `tests/test_memory_routes_tenant_scope.py`: the other tenant's ids read as not found and nothing changes. Or a refusal test in multi-user mode. Negative control: the unscoped store. | Open |
| KB-Q01 (stage 5) | KB scale and recovery are measured on named hardware, with declared ceilings and no unbounded table growth (`unified_items`, the registry, provenance). | At 10k, 100k and 1M chunks, with EN, AR and mixed corpora: ingest throughput, search p50/p95/p99, sweep listing cost and library delete time, and the re-embed RTO after a restore. A multi-day mixed workload (crawls, refreshes, document publications, deletes, a model switch, restarts). Retained evidence, and no NOT RUN step for the claimed profile. | Open |
| KQ01 (stage 5) | KB retrieval quality is measured. | A versioned EN/AR corpus and benchmark modelled on `memory/benchmark.py`, covering explicit search and auto-inject, in which unanswerable questions return empty. Thresholds locked as a ratchet in CI. | Open |
| KQ02 (stage 5) | KB surfaces have one parity matrix. | Web, gateway, agent tools, MCP and TUI × list, create, ingest, search, delete and status, with the expected behaviour, as tests. | Open |

## Start here

### Read first, in this order

1. **The merged audit:** `docs/audits/AUDIT_KNOWLEDGE_2026-10-13.md`. Read its ranked findings, AGENTS.md mismatches, sequencing notes and exit gates. For full evidence, read the two halves in `session-artifacts/audits-2026-10-13/` (`kb-engine.md`, `kb-surfaces.md`).
2. **AGENTS.md sections:**
   - §24F (Knowledge Library meaning search) and §24B (the `crawl_jobs` paragraph);
   - §26G (fenced tool output);
   - §19 B2, E2 and F2 (Documents admission and leases, delete, reconcile);
   - §15B (maintenance runner), §15G S2 (memory off the loop) and §15I (forget tombstones);
   - §18 C (migration import);
   - §28 (negative controls) and §35 (class gates, loop-stall helpers, debt ratchet);
   - §46 (slash commands);
   - the UI conventions on save truth and i18n.
3. **Code entry points** (under `kazma-core/kazma_core/` unless stated):
   - `stores/knowledge.py`, `stores/knowledge_index.py`, `stores/knowledge_ingest.py`, `stores/knowledge_chunker.py`, `stores/kb_jobs.py`;
   - `web_acquire/crawl_jobs.py`;
   - `memory/federated_search.py`, `memory/unified_index.py`, `memory/vector_store_global.py`, `memory/embedder.py`, `memory/worker_bootstrap.py` (`_embed_knowledge_chunks`);
   - `agent/tool_builtins/knowledge.py`, and `agent/graph_supervisor.py` (the KB merge, about lines 1079-1166);
   - `migration/importer.py` (`_merge_kb_into_settings`);
   - `kazma-ui/kazma_ui/kb_api.py`, `templates/knowledge_base.html`, `static/js/kb.js`;
   - `kazma-gateway/.../agent_handler/commands.py` (`_try_kb_command`).
4. **Patterns to copy:**
   - `documents/knowledge.py` and `documents/ingestion.py`: `publish_document_version`, `reconcile_index`, the publication lock;
   - the Documents intake lease and fence (§19 B2);
   - the Documents 2 MiB bounded JSON reader;
   - `tests/test_memory_routes_tenant_scope.py` (route matrix and SQLite tracer);
   - `x_api/reply.py:631-713` (a fenced, tenant-explicit KB consumer).

### How to close a row

1. Reproduce the defect with its witness (`session-artifacts/audits-2026-10-13/kb_engine_probes_2026_10_13.py`, `kb_surfaces_probes.py`, `kb_surfaces_kazmasave_probe.js`).
2. Fix the class, not only the instance: find the sibling paths the audit names.
3. Write the regression under `tests/` asserting the fixed behaviour, with the old behaviour as its negative control. Never copy a witness into `tests/` unchanged, because it asserts the defect.
4. Add or extend the class gate. Lock any lowered debt count into the ratchet baseline in the same change.
5. Update the row's State with the date and the evidence: test names, pass counts and log paths. Use `Implemented <date>` only when the full scope of the row has current evidence, and name what remains open.

### Run rules

- **Priority.**
  - Run every test process at below-normal CPU priority, because the live server shares this machine.
  - `scripts/fast_test.py` lowers its own processes.
  - Do not use low I/O priority or Windows background mode; they were measured far slower.
- **Order.**
  - Run targeted tests per change.
  - Run the full suite once at the end, through `python scripts/fast_test.py` with no `--chunks`, and do not edit files while it runs.
  - `fast_test` skips `tests/e2e`. Template or i18n changes (KB-S05, KB-S09, KB-S15) also need the KB browser tests and the Arabic page tour, run under pytest.
  - Check JavaScript with `node --check` and the `tests/js` suites.
- **Postgres.** Use only disposable, resource-capped containers (for example `postgres:16` with `--memory` and `--cpus`, removed afterwards). Never use the live database container.
- **The live install.** Never touch `C:\Users\balfa\kazma`: no reads, edits, copies or commands there. Never start, stop or reload the Kazma server.
- **Gates.** Fix each class with a gate and a negative control, as AGENTS.md §28 and §35 require.
- **Git.**
  - Make no commits or pushes unless the user asks.
  - The tree holds other uncommitted work, including the active Documents hardening; do not revert, reformat or stage it.
- **Completion.** No requirement is complete without current evidence of its full scope. The stage 5 rows (KB-Q01, KQ01, KQ02) may stay Open while implementation proceeds. Do not remove or shorten them, and do not mark the whole goal complete before they are done.

## Execution log

- 2026-10-13: Tracker opened from the merged audit. No remediation has started.
- 2026-10-13 (from the Memory tracker, shared defects fixed once): Memory M01
  retired `unified_items` -- no writer, no reader, the table dropped -- so the
  unified halves of KB-S02 and KB-E08/KB-S03 are gone, and a deleted library's
  text is no longer served through it (gate in
  `tests/test_memory_forget_witnesses.py`). Memory M04 removed the supervisor's
  promotion of library hits into chat memory and its setting, and a maintenance
  sweep retires the copies an earlier build left: the promotion half of
  KB-E08/KB-S03 and the supervisor bullet of KB-S06 (`mirror_episode` is a
  loop-stall helper; `tests/test_memory_kb_never_in_memory.py`). The rest of
  those rows -- ranking, explain payload, provenance files, Documents record,
  KB tools and `/kb` on the loop -- stays open here.
- 2026-10-13 (Memory M11): `knowledge_search` returns the crawled hits
  inside the untrusted-data fence, its citation directive outside, and the
  per-function fence gate covers `tool_builtins/knowledge.py`
  (`tests/test_memory_tools_fenced.py`): the tool half of KB-S01.
