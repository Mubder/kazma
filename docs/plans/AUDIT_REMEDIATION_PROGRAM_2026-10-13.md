# Audit remediation program — 2026-10-13

Status: PLANNED — 2026-10-13

This program covers everything left from the 2026-10-13 audits:

- the 129 findings in five areas (Knowledge Library, Swarm, Memory, Scheduled
  and Time Travel);
- the qualification rows those audits add;
- the four open Documents items.

It does not repeat the findings. Each area's tracker is the record of its
rows, their evidence and their state. This document owns four things:

- **Order:** which work comes first, and why.
- **Shared fixes:** the defects that recur across areas, fixed once.
- **File ownership:** which session may edit a file that several areas touch.
- **Owner decisions:** the choices the audits leave to the owner.

Board: the private Artifact "Kazma Audit Board" (every finding, filterable).

| Area | Audit | Tracker | Findings | P1 |
|---|---|---|---|---|
| Knowledge Library | `docs/audits/AUDIT_KNOWLEDGE_2026-10-13.md` | `docs/plans/KNOWLEDGE_INDUSTRIAL_HARDENING_GOAL.md` | 26 (+3 qualification rows) | 8 |
| Swarm | `docs/audits/AUDIT_SWARM_2026-10-13.md` | `docs/plans/SWARM_INDUSTRIAL_HARDENING_GOAL.md` | 32 (+5 qualification rows) | 7 |
| Memory | `docs/audits/AUDIT_MEMORY_2026-10-13.md` | `docs/plans/MEMORY_INDUSTRIAL_HARDENING_GOAL.md` | 36 | 7 |
| Scheduled | `docs/audits/AUDIT_SCHEDULED_2026-10-13.md` | `docs/plans/SCHEDULED_INDUSTRIAL_HARDENING_GOAL.md` | 23 | 8 |
| Time Travel | `docs/audits/AUDIT_TIME_TRAVEL_2026-10-13.md` | `docs/plans/TIME_TRAVEL_INDUSTRIAL_HARDENING_GOAL.md` | 12 (+3 stage gates, 1 qualification) | 2 |
| Documents | `session-artifacts/DOCUMENTS_AUDIT_2026-10-12.md` | `docs/plans/DOCUMENTS_INDUSTRIAL_HARDENING_GOAL.md` | F02, Q02, Q03, Q04 open; Q05 in review | -- |

Several P1s apply to one profile only. They are scheduled last unless the
owner decides otherwise (decision D1):

- KB-S04, SCH-S03 and M07: multi-user and tenant modes.
- M06: Postgres-primary memory.

## Order of work and why

1. **Approval first.** A swarm task started from a chat turn or a fired
   reminder runs danger tools with no approval card (SW-S01 = SCH-S01). This
   is the only class where an unattended action happens today, so it goes
   first.
2. **Then what the model is told.** Untrusted text reaches the model without
   the fence (KB-S01, M11, SW-S10), and Knowledge chunks are stored as the
   user's own words (M04).
3. **Then what is kept.** Delete and Forget must reach every copy (M01,
   KB-E08, TT-05, M05, M10). These are promises the product makes to its user.
4. **Then silent loss.** Failure reported as success, work that does not stop
   or survive a restart, and lost reminders (Scheduled stage 2, Swarm stages 2
   and 3, KB-E02).
5. **Then surfaces, bounds and observability,** each area's later stages.
6. **Then qualification:** soaks, Postgres parity, chaos and published limits.
   Qualification rows stay open until measured and are never shortened.

## Streams (one session each)

| Stream | Scope | Starts with | Waits on |
|---|---|---|---|
| **S0 Shared** | Shared gates (W1.2, W1.3, W1.5); short | W1.3, then W1.2 and W1.5 | Wave 0 |
| **S1 Swarm** | `SWARM_INDUSTRIAL_HARDENING_GOAL.md`, plus W1.1 | W1.1 (SW-S01 = SCH-S01), then Swarm stage 1 | Wave 0 |
| **S2 Scheduled** | `SCHEDULED_INDUSTRIAL_HARDENING_GOAL.md` | Stage 2 (durable fire record, outbox, typed outcomes): needs nothing from the others | W1.1 before it closes SCH-S01 and SCH-S05 |
| **S3 Memory** | `MEMORY_INDUSTRIAL_HARDENING_GOAL.md`, plus W1.4 | W1.4 (the copies registry, M01), then the rest of stage 1 | Wave 0 |
| **S4 Knowledge** | `KNOWLEDGE_INDUSTRIAL_HARDENING_GOAL.md` | KB-S01 on the W1.2 gate; KB-S02, KB-S08, KB-S09 | W1.2 for KB-S01; W1.4 for KB-E08 / KB-S03 |
| **S5 Time Travel** | `TIME_TRAVEL_INDUSTRIAL_HARDENING_GOAL.md` | TT-12 (a real-graph fixture across turns) | W1.1's gate-registry changes for TT-02; W1.4 for TT-05 |
| **S6 Documents** | The Documents leftovers below | Q05 (running now) | Wave 0 for F02 |

At most four sessions should run at once on this machine. The live install
runs here too, and test runs starve it (see the running rules below). A good
start is S0, S1 and S3, which unblock the others. S2 can join at once on its
stage 2. S4 and S5 join when their first prerequisite lands.

## Wave 0 — one committed baseline (before any stream starts)

- **W0.1 Commit and push the working tree.** It holds the whole Documents
  hardening, today's OCR, schema and drill work, and these audits.
  - Stage the files, then regenerate the metrics receipt with
    `scripts/generate_metrics.py --write --require-collected`. The full
    suite's one known failure is this receipt; it needs the files staged.
  - Commit straight to `main` (the owner's rule), push, and read CI once.
  - Needs the owner's go-ahead.
- **W0.2 Read the first CI run of `Document worker container profile`** on
  Linux Docker Engine. This closes or reopens Documents F02.
- **W0.3 One worktree per stream,** created from the new `main`. Streams
  never share a checkout.

## Wave 1 — shared fixes (fix the class once)

| ID | The class | Fix once | Rows it closes or unblocks | Owner | Gate (with negative control) |
|---|---|---|---|---|---|
| **W1.1** | Detached work inherits the graph's approval authority | Approval authority is per gated tool call, never a context flag that `asyncio.create_task` copies. Detached work goes through the swarm bus or the gate registry. Approval ids are minted by the registry, never taken from tool arguments. | SW-S01, SCH-S01; unblocks SW-S02, SW-S11, TT-02 | S1 | A detached danger call from a chat turn and from a fired reminder stops at a card; the old flag runs it (control). Re-run every HITL and gate-registry suite, not only Swarm's. |
| **W1.2** | Tool output that reaches the model is not fenced | Extend `test_no_unfenced_web_tool_output` from web tools to every tool that returns stored or third-party text, found from the tool registry. Fence at the return site. | KB-S01, M11, SW-S10 (each area fixes its own sites against the gate) | S0 | A tool returning library, memory or worker text without `fence_untrusted` fails; an unfenced tool is planted as the control. |
| **W1.3** | Blocking work on the loop through public or imported wrappers | Make `_module_stall_wrappers` follow public functions and imported helpers. Start each area at a ratchet baseline that only goes down. | KB-S06, SW-E07, SW-S12, SCH-E10 / SCH-S11, M04 (loop half), M34 | S0 | A planted wrapper chain that reaches a listed helper is found; the current, narrower rule misses it (control). |
| **W1.4** | Delete and Forget do not reach every copy | One declared registry of every store that holds turn, fact or library text (`unified_items`, `beliefs_archive`, audit log, local and remote vectors, `snapshots.db`, weekly summaries, KB chunks, queue payloads), with a forget/purge hook per store. Each store in `store_registry` declares its hook or why it holds none. | M01, M05, M08, M10; unblocks KB-E08 / KB-S03 and TT-05 | S3 | Forget a fact, a turn and a library, then search every store and get nothing back; the store that is never cleaned today (`unified_items`) is the control. |
| **W1.5** | A page reports "saved" when the server refused | `window.kazmaSave` (in `static/js/auth-guard.js`) treats `{ok:false}` and non-2xx as failure. Routes return non-2xx on refusal. | KB-S10; the same class in MEM-S08 (M32) and SW-S08 | S0 | `tests/js` runs every Settings save against a refusing server; the old helper reports success (control). |

The remaining classes (failure reported as success; work that does not stop;
tenant isolation) are fixed area by area. Their designs differ too much to
share code. The area audits name the sibling findings so each fix checks the
others.

## Wave 2 — each area's first stages, in parallel

| Stream | Order (tracker rows) | Exit |
|---|---|---|
| S1 Swarm | W1.1 → stage 1 (SW-S02, SW-S03, SW-S11, SW-S04, SW-S05) → stage 2 (SW-E02, SW-E03, SW-E06 / SW-S06, SW-E05, SW-E09) → stage 3 (SW-E01, SW-E04, SW-E08) | The tracker's stage gates: a two-platform approval drill, transition property tests on SQLite and Postgres, and a real kill matrix with no duplicate effects |
| S2 Scheduled | stage 2 (SCH-E04, SCH-E01, SCH-E02 + SCH-S05 delivery, SCH-E06, SCH-E07, SCH-E12 + SCH-S07, SCH-S02) → stage 1 after W1.1 (SCH-S01 regression, SCH-S05 authority) → stage 3 (SCH-E03, SCH-E05 + SCH-S09 + SCH-E09, SCH-E09 + SCH-S06) | No silent loss across a restart (a real kill per phase); one time model for EN and AR on every path |
| S3 Memory | W1.4 → stage 1 (M02, M03, M04, M05, M08, M09, M10, M22) → stage 2 (M11, plus M07, M21 and M06 per decision D1) | Forget converges on every store; the benchmark ratchet holds; Arabic entities keep their identity |
| S4 Knowledge | stage 1 (KB-S01, KB-S08, KB-S02, KB-E08 / KB-S03 after W1.4, KB-S09) → stage 2 (KB-E05, KB-E02 / KB-S12, KB-E03, KB-E04, KB-E10, KB-E01, KB-E09) | No unfenced KB text reaches the model; deletion is real; publication is atomic; migration carries the KB |
| S5 Time Travel | TT-12 → S1 (TT-02 after W1.1, TT-04, gate TT-G1) → S2 (TT-01, TT-03, TT-05 after W1.4, gate TT-G2) | A restore under a pending approval is refused or settled; a rewind is a rewind on every surface |

## Wave 3 — surfaces, bounds, observability, docs

Each stream continues with its later stages:

- **Swarm:** stages 4 and 5.
- **Scheduled:** stages 4 and 5.
- **Memory:** stages 3 and 4.
- **Knowledge:** stages 3 and 4.
- **Time Travel:** S3.

AGENTS.md and the docs are corrected in the same change as the behaviour they
describe. Each audit lists the claims that do not match the code.

## Wave 4 — qualification, and what needs more than code

| Item | What | Needs |
|---|---|---|
| Documents Q03, Swarm SQ01, TT-G3, KB-Q01, Memory stage 4 | Soaks and scale runs (Documents: 48 to 72 h mixed load with restarts and faults) | A machine that is not the live install, or an agreed overnight slot (decision D3) |
| Swarm SQ02, Documents and Memory Postgres rows | Postgres parity | Disposable PostgreSQL containers here, plus the CI Postgres job |
| Swarm SQ03 | Temporal durable path, on a time-skipping test server | The owner approves the test-server download (D9) |
| Swarm SQ04 | Chaos: database locked or down, Temporal unreachable, shutdown mid-pattern | Fault injection here |
| Swarm SQ05, every area | Published limits; AGENTS.md revised to the measured behaviour | The measurements above |
| Documents Q04 | Independent security review | Someone other than the author (D4) |
| Documents Q02 | Arabic-Indic digits under OCR | An OCR engine choice (D2); more real scans from the owner |
| TT-Q1, KB-Q01 / KQ01 / KQ02 | Surfaces not exercised; retrieval quality; one parity matrix | Test harnesses only, never the live install |

## Documents, remaining

- **F02:** read the first Linux Docker Engine CI run after W0.1. Rootless,
  Kubernetes, gVisor and Kata matter only for documents from untrusted
  people (D5).
- **Q02:** an OCR engine for Arabic-Indic digits (D2); more real scans go
  into the manifest outside the repository and run through
  `tests/test_document_real_scans.py`.
- **Q03:** the 48 to 72 hour soak (`scripts/document_soak.py`) on the D3
  machine.
- **Q04:** an independent review (D4).
- **Q05 (done 2026-10-13):** the full documentation review; its changes and
  the DOC items' page updates pass the docs gates (32 tests).
- **Code defects the Q05 review found (all done 2026-10-13 but Q-level
  qualification noted under each; gates named in AGENTS.md §19):**
  - **DOC-A1 (done):** trigger `document_audit_guard` + bounded Postgres
    prune; restores create `KAZMA_PG_FUNCTIONS` first. `tests/test_document_audit_pg.py`.
    On Postgres metadata, `document_audit_events` has no
    immutability triggers (they are SQLite-only, `documents/audit.py`), and
    `DocumentAuditStore.prune_older_than` uses the SQLite connection, which is
    `None` there. Retention records `audit_prune:AttributeError` and the table
    grows without bound. Fix both, gated on real Postgres with the old prune
    as the control.
  - **DOC-A2 (done):** removed, with five more found by the new gate (eight
    in all); none was shipped in kazma.yaml, so nothing stored to retire.
    `tests/test_document_config_fields_read.py`. `documents.security.fence_llm_content`,
    `allow_encrypted_documents` and `allow_external_render_resources` are
    read and validated, but nothing acts on them. Remove them (and retire
    their stored values via `config_defaults`), or wire them. A setting that
    does nothing must not exist.
  - **DOC-A3 (done):** every surface acts as `principal.OPERATOR` on a
    single-operator install; older documents get an audited `owner` ACL row;
    multi-user refuses. `tests/test_document_operator_principal.py`.
    Chat `/documents` and the agent's
    document tools act as the principal `agent`
    (`agent_handler/commands.py`, `native/document_platform/tools.py`), while a
    document is owned by whoever ingested it (`ingestion.py` `owner_id=actor`)
    and only the owner or an ACL row may read it. A document uploaded on the
    Web page is therefore invisible to chat and the agent, and theirs to the
    Web page; nothing grants access across. The gateway help ("every platform
    sees the same opaque document_id") says otherwise. On a single-operator
    install the chat and agent surfaces act for the install's operator
    (decision D1 refuses multi-user mode until it is built). The gate: a
    document uploaded on the Web is read by `/documents read` and by the
    agent's tool, and the reverse; the `agent` principal is the control.
  - **DOC-A4 (done):** one `_STATUS_BY_CODE`; delete reads through the
    bounded reader. `tests/test_document_api_statuses.py`.
    `POST /api/documents/{id}/delete` read an unbounded JSON
    body (not the 2 MiB `_json_body` reader), and writes-disabled answers 422
    on action routes but 503 on upload, import, index, unindex, delete and
    retry. Use one reader and one status.
  - **DOC-A5 (done):** manifest, docstring and reply say what the wait does.
    The `document_import` manifest said the tool returns the
    final state; it waits 30 s. Say what it does.
  - **DOC-A6 (done, a deliberate deviation):** the path follows the catalog;
    SQLite catalog + Postgres queue is SUPPORTED (promotions read from the
    queue, kept in the manifest), not refused -- it is a documented choice and
    refusing would leave it with no backup; Postgres catalog + SQLite queue is
    refused by name; folders renamed into place when complete.
    `tests/test_document_backup_shapes.py`. With SQLite metadata and Postgres jobs,
    `documents/backup.py` (`_installed_on_postgres`) takes the Postgres path
    on the jobs backend alone. `backup_pg.py` then reads a catalog that is not
    there (it fails, or copies stale leftover tables), and it creates the
    `document-pg-*` folder before connecting, so a failed run leaves an empty
    folder. Choose the path by the metadata backend, and refuse an unsupported
    mixed shape by name. Create the folder only on success.
  - **DOC-A7 (done):** a Postgres catalog's files travel (catalog in
    `postgres.dump`); the CLI reads configured backends.
    `kazma migrate export` did not carry a Postgres document
    store's content. It copies only what the local, empty `documents.db`
    references, and the "unsupported backend" refusal exists only inside the
    server. The CLI must refuse, or carry the store through the coordinated
    backup. The test fakes the service, so it never covered the CLI.
  - **DOC-A8 (done):** fails the import. A failed document restore during
    `kazma migrate import` was a
    warning (`migration/importer.py`), and the import still reports `ok`. A
    failed restore fails the import.
  - **DOC-A9 (done):** 503 before the body. An upload refused because a
    required malware scanner cannot
    run answers 400 after the body is read. `malware_scanner_unavailable` is
    missing from `documents_api._ingestion_error`'s status map; it should be
    503 and should be answered in `precheck_upload`, before the body.
  - **DOC-A10 (done, drilled):** whole-database dump first, then
    `restore --replace-document-tables`; both wrong orders are the controls.
    `tests/test_document_pg_restore_order.py`. There was no drilled order for restoring both
    Postgres backups (the whole-database `pg_shared_*` dump and the
    coordinated document backup) into one database.
  - **DOC-A11 (done 2026-10-13):** `docs/docs/ops/disaster-recovery.md` held
    a form feed and a split line where `D:\fresh-kazma` and
    `\neo4j_graph.jsonl` had been mangled. Restored, and
    `tests/test_docs_no_control_characters.py` now holds every Markdown file
    to it (the mangled shapes as control).
  - **DOC-A12 (done, and a bigger finding):** CpuRate sized by
    `job_cpu_rate`; measuring it found the worker escaped its Job Object on
    the first run of every process (venv launcher), with no memory/CPU/
    process caps -- fixed by a suspended launch, `code_exec` too, gated.
    Measured: 3.93-3.99 cores before, 1.03-1.30 after (`doca12-measure.log`).
    The Windows Job Object CPU cap of the `process` profile set
    `CpuRate = 100*100`, commented as "at most one core". Windows reads
    `CpuRate` as a share of the whole machine in hundredths of a percent, so
    10,000 is 100 % (no cap). Size it from the processor count, measured with
    a real busy worker.
  - **DOC-A13 (done):** `tests/test_document_pg_tables.py` (DDL + a started
    store on real Postgres). Nothing checked `backup_pg.DOCUMENT_PG_TABLES` against the
    document DDL (only `KAZMA_PG_TABLES` has a gate).

  Owner: S6, right after Q05.

## Shared files: who edits them

A stream that needs a change in a file it does not own asks the owner stream,
or waits until the owner's change has landed on `main` and rebases.

| File | Owner | Others |
|---|---|---|
| `kazma-core/kazma_core/agent/graph_tool_worker.py`, `agent/tool_registry.py` | S1 (W1.1) | S2 and S5 rebase after W1.1 |
| `kazma-core/kazma_core/safety/hitl_gates.py`, `kazma-ui/kazma_ui/hitl_decision.py` | S1 (stage 1) | S5 (TT-02) after S1's stage 1 |
| `kazma-core/kazma_core/safety/prompt_fence.py`, `tests/test_static_gates.py` | S0 | Area streams add entries, never rules |
| `kazma-core/kazma_core/memory/forget.py`, `memory/belief_mutation.py` (`unified_items`) | S3 (W1.4) | S4 and S5 register their stores through W1.4's registry |
| `kazma-core/kazma_core/memory/federated_search.py` | S4 (KB-S02) | S3 reviews; M04 lands after KB-S02 |
| `kazma-core/kazma_core/migration/importer.py`, `migration/path_rewrite.py` | S4 (KB-E01) first | S5 (TT-09, the JSON-escaped paths class), then S3 (M10) |
| `kazma-ui/kazma_ui/turn_runtime.py`, `kazma-ui/kazma_ui/sse_chat/turn_input.py` | S5 (TT-03, TT-04) | S3's M09 uses S5's rewind service |
| `kazma-core/kazma_core/safety/commitment/authorize.py` | S2 (SCH-E03, SCH-E05) | S1 (scope) by coordination |
| `kazma-ui/kazma_ui/static/js/auth-guard.js` (`kazmaSave`) | S0 (W1.5) | -- |
| `AGENTS.md` | Each stream edits only its own sections: S1 §4, §5, §7, §9, §14, §20 scope, §30; S2 §16; S3 §15; S4 §24F and §19 E2; S5 §12; S0 §26G and §35 | Conflicts are resolved by rebasing, never by overwriting |

## Owner decisions

Each decision has a default. The default applies unless the owner says
otherwise; this follows the standing rule to decide and record the reason. A
decision that removes a feature the owner may want (D6, D8) is asked first.

| # | Decision | Default |
|---|---|---|
| D1 | Multi-user and Postgres-primary memory profile (KB-S04, SCH-S03, M07, M21, SW-S16, M06) | Refuse those surfaces in multi-user mode, and refuse Postgres-primary memory, with a clear message, until each is built. Kazma is one operator per install. |
| D2 | OCR engine for Arabic-Indic digits | Measure one candidate engine on the real-scan manifest before choosing. No new dependency until the owner approves it. |
| D3 | Where soaks run | A separate machine or VM; never alongside the live install during the day |
| D4 | Independent security review | The owner names a reviewer; until then Q04 stays open and nothing claims it |
| D5 | Hardened container runtimes (gVisor, Kata, rootless) | Not needed for one operator's own documents |
| D6 | Swarm handoff (SW-E13): product feature or extension point | Ask first: it removes or adds a feature |
| D7 | Swarm: is an explicit Deny final (SW-S05)? | A human Deny is final on every platform, and the card says so |
| D8 | Semantic routing (SW-E10), the Refiner call (SW-E15) | Ask first: fix or remove |
| D9 | Temporal test-server download (SQ03) | Ask before downloading |
| D10 | Time Travel: rewind the transcript, or rename the feature (TT-03); memory on rewind (M09) | Rewind the transcript; forget the rewound turns' memories by provenance |
| D11 | Time Travel: restore with a pending approval (TT-02) | Refuse with 409 `approval_pending`, naming the card |
| D12 | Fired reminders' capability set (SCH-S05) | Read tools plus notify; anything more is declared at booking and shown |

## Running rules for every stream

- **Truth and scope.** The working tree of the stream's own worktree is the
  truth. Never touch the live install (`C:\Users\balfa\kazma`), and never
  start or stop the Kazma server.
- **Test priority.** Tests run at below-normal priority. Run targeted tests
  while working, then one full suite (`python scripts/fast_test.py`, no
  `--chunks`) before each push.
- **One full suite at a time on this machine.** Write
  `G:\GitHubRepos\kazma\session-artifacts\.full-suite.lock` (stream name and
  start time) before starting a full run, and delete it after. A lock older
  than 90 minutes is stale.
- **Postgres.** Use disposable, resource-capped containers only, removed
  after use. Never use `kazma-db-win`.
- **Each fix.** A behavioural test, a class gate and a negative control
  (AGENTS §28, §35). The audit's witnesses are inverted, never kept as tests
  that require a defect.
- **Push.** Push straight to `main` after the full suite passes. Rebase first
  (`sync_with_base_branch` in an app worktree), regenerate the metrics
  receipt, then read CI once.
- **Tracker.** Each stream updates its own tracker's state column and
  execution log in the same commit as the change.
- **Owner approval.** Downloads, new dependencies, and anything that leaves
  the machine (posting, messaging) need the owner's approval.

## Done

- **An area is done when** every row of its tracker is closed with evidence:
  stage exit gates green, the full suite and the area's Postgres tests green,
  a receipt of the runs, and AGENTS.md and docs matching the code.
  Qualification rows close only when measured.
- **The program is done when** all six trackers are done, or a remaining row
  is explicitly accepted by the owner with the reason recorded here.

## Session prompts (paste one per new session)

> **S0 Shared.** Read `docs/plans/AUDIT_REMEDIATION_PROGRAM_2026-10-13.md`
> and AGENTS.md §26G, §28 and §35. Execute W1.3, W1.2 and W1.5 non-stop,
> each with its gate and negative control. Record each area's starting
> baseline in that area's tracker. Follow the program's running rules, then
> report once.

> **S1 Swarm.** Read the program document, then
> `docs/plans/SWARM_INDUSTRIAL_HARDENING_GOAL.md` and its audit. Execute W1.1,
> then the Swarm stages in order, non-stop, under the program's running rules
> and file ownership. Decide open choices by the program's defaults and record
> them, except D6 and D8, which you ask about. Report once at the end of each
> stage.

> **S2 Scheduled.** Read the program document, then
> `docs/plans/SCHEDULED_INDUSTRIAL_HARDENING_GOAL.md` and its audit. Start
> with stage 2, which does not depend on W1.1; take stage 1 after W1.1 is on
> `main`. Work non-stop under the running rules, and report once per stage.

> **S3 Memory.** Read the program document, then
> `docs/plans/MEMORY_INDUSTRIAL_HARDENING_GOAL.md` and its audit. Execute W1.4
> (the copies registry) first, since Knowledge and Time Travel wait on it.
> Then do the Memory stages in order, non-stop, under the running rules, and
> report once per stage.

> **S4 Knowledge.** Read the program document, then
> `docs/plans/KNOWLEDGE_INDUSTRIAL_HARDENING_GOAL.md` and its audit. Do
> stage 1 against the W1.2 gate, and KB-E08 / KB-S03 once W1.4 is on `main`.
> You own `memory/federated_search.py` and the migration importer first.
> Work non-stop under the running rules, and report once per stage.

> **S5 Time Travel.** Read the program document, then
> `docs/plans/TIME_TRAVEL_INDUSTRIAL_HARDENING_GOAL.md` and its audit. Build
> TT-12's real-graph fixture first. Do TT-02 after W1.1's gate-registry
> changes are on `main`, and TT-05 after W1.4. Work non-stop under the running
> rules, and report once per stage.

## Progress log

- 2026-10-13: Program planned from the five merged audits and the Documents
  tracker. Q05 (Documents docs review) running. Nothing committed yet (W0.1
  waits for the owner).
- 2026-10-14: S3 Memory M01-M36 implemented in the working tree (M21, M24
  partial; Stage 4 soak and Postgres restore drill open; owner items in the
  Memory tracker). Not committed.
- 2026-10-14: S1 Swarm Stages 1-5 implemented in the working tree, with
  SCH-S01 (one fix with SW-S01). Swarm witnesses: 35 of 39 red on
  2026-10-14. Green: the SW-E10 and SW-E13 witnesses (owner decisions D8,
  D6), and SW-S07 and SW-S13, whose audit witnesses bypass the enforcement
  point (a bare APIRouter; a replaced `_post_message`); their regressions
  test the mounted router and the HTTP client below the chokepoint. 116
  postgres-marked swarm tests green on a disposable container. Open for the
  owner: D6 (SW-E13 handoff), D8 (SW-E10 semantic routing, SW-E15 Refiner),
  D9 (SQ03 Temporal download), D3 (SQ01 soak); still open: SW-S03's dashboard
  link, SQ04. Not committed.
- 2026-10-14: Memory and Swarm committed (ad9057e3). Shared pieces committed
  (c575d4e4): one single-operator rule (`kazma_core.security.single_operator`)
  and the per-area cross-module loop-stall gate (`_CROSS_MODULE_AREAS`). Then
  Scheduled (S2), Time Travel (S5) and Knowledge (S4) ran as one agent each
  in their own worktree from c575d4e4 and were merged one at a time:
  Scheduled 110b106c, Time Travel 205a5f77, Knowledge next. Each merge ran
  its area's tests and the global gates on the merged tree; one full suite
  and the Postgres job follow the last merge. Open: the soaks (D3), the
  owner decisions (D6, D8, D9), and an independent review per area.
