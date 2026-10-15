# Documents industrial hardening — continuous execution

Status: ACTIVE — 2026-10-13 (continuous run)

Objective: execute the complete Documents audit remediation and qualification
plan continuously, with evidence for each requirement. Industrial readiness is
not achieved by fixing only the reproduced bugs or obtaining a green unit suite.

Baseline: commit `d7e27c194d3f170092a9c405709b9197a2c00161`;
`session-artifacts/DOCUMENTS_AUDIT_2026-10-12.md` contains the original audit.
Baseline selected regression result: 356 passed, 6 skipped. Nine inert audit
witnesses reproduced defects. Baseline witnesses assert the old defects and
must not become permanent tests that require those defects to remain.

## Requirements and completion evidence

| ID | Required result | Evidence required | State |
|---|---|---|---|
| F01 | One bounded, fail-closed PDF active-content policy on intake and mutation | Real inert nested/indirect/chained PDF fixtures, valid navigation controls, traversal budgets | Implemented 2026-10-13 — one policy in the worker on intake, every mutation input/output (redaction bypass closed) and every rendered PDF; XFA, remote/submit/import/rendition/media actions, untyped embedded files and non-web links refused; no PDF opened outside the worker (class gate). Independent review and a broader real-world PDF corpus remain under Q02/Q04 |
| F02 | Structural preflight and local salvage execute inside enforced worker containment; credentialed remote egress is separate | OS-specific CPU/memory/process/output/time, filesystem/network, cancellation and cleanup tests | In progress — Docker profile (`KAZMA_DOCUMENT_WORKER_ISOLATION=container`, broker-owned attested per-job containers) qualified on Docker Desktop 29.8.2 / Windows 11: no network/mounts, memory/PID/CPU/scratch/deadline/output limits, disconnect cancellation, descendant cleanup, stale sweep, compose-shaped topology. Default `process` profile: Windows Job Object caps and Unix rlimits only, no filesystem/network jail (reported unqualified). Engine readiness now comes from the image; `security.execution.container_required` refuses the `process` profile. A Linux run of the CI job (`document-worker-container`, unchanged steps, plain unlabelled `docker build`) passed on 2026-10-15 at `0685ab9f`: Kazma and the broker on Linux (python:3.11-slim, Python 3.11.16, Docker CLI 29.8.2) against Docker Desktop's Linux engine 29.8.2 -- attestation verified with no problems, the qualification suite 139 passed with none skipped, the compose-shaped topology all 10 checks, no job container left; its first run found two defects, fixed in `0685ab9f` (an unlabelled image refused under Docker 29; the OCR test's PyMuPDF). The GitHub job itself waits on the account's billing lock; rootless/Kubernetes, gVisor/Kata, native OS jails and the Unix process cap open. 2026-10-15: on a native install the guard runs the broker (no hand-run process, no `.env` lines), the profile is the admin's protected setting, and the worker image is named by its build inputs and built by one command (see the 2026-10-15 entry) |
| F03 | Effective settings match saved policy; security revocation applies immediately before egress | Existing-service, queued/in-flight revocation, coherent policy revision/readiness tests | Implemented — coherent operation snapshots and readiness (earlier); live revocation rechecked before EACH provider, including after a failed provider, on SQLite and on a real PostgreSQL settings store (2026-10-13). Representative deployment qualification and independent review open (Q03/Q04) |
| F04 | Every remote attempt carries correct immutable tenant/actor/document/version/job identity and durable intent | Concurrent/multiple-service, attachment, failed-upload and audit-outage tests | Implemented — identity, failure phases, transient-read refusal and audit-outage refusal pass on SQLite and on real PostgreSQL 16.15 metadata/jobs/audit/settings (2026-10-13); provider fallback actually runs, each attempt audited; provider transport ignores ambient proxies, refuses redirects and caps responses |
| F05 | Idempotency is authorized and request-bound; all job-read surfaces enforce document ACL | Actor/workspace/payload conflicts, concurrent replay, API/tool/gateway/TUI matrix | Implemented 2026-10-13 — durable request reservation and real PostgreSQL admission races (earlier); every job read (`job_status`, `job_events`, `jobs_for_document`, `cancel_job`, `retry_job`) now requires a named actor (`SYSTEM_ACTOR` only for code that authorized the read; a missing actor is refused — it meant "no check"); an unreadable job answers exactly like an unknown one on the Web routes, the agent tools and the gateway (identical 404, replay by another actor = `idempotency_conflict`); the agent cancel tool checks access (it cancelled any job in the tenant); `/ops/audit` is admin-only. Matrix `tests/test_document_job_read_matrix.py` (signature gate + control). Chat surfaces act as the single `agent` principal by design; per-chat-user identity mapping not built; TUI reads via the Web API |
| F06 | Complete document backup verification and clean restore | SQLite and Postgres restore of DB/blobs/manifests/jobs/ACL/index; measured RPO/RTO | Implemented 2026-10-13 — SQLite drill (earlier) plus a coordinated Postgres backup (`documents/backup_pg.py`): one exported snapshot for `pg_dump --snapshot` and the file references, GC pins, checksummed copy, self-contained verification; restore only into an empty database/storage root with per-table row-count check, lease expiry and restored-intake rollback; the scheduled 6 h job takes this path on Postgres; operator `scripts/document_pg_backup.py`. Drill on real PostgreSQL 16.15 (private databases): restore + start + every job ready **3.8 s** for the fixture, backup 0.19 s; a dump outside the snapshot is caught (negative control). Same day, continued: every worker start re-publishes to the knowledge libraries each version the store records as indexed but no library holds (`reconcile_index`, under the per-document publication lock; an unindex during it is never undone — control without the re-check; operator `index` command, `--prune` for unrecorded chunks), on both backends; a **1,000-document / 62.5 MiB** drill on PostgreSQL 16.15 (1 CPU, 512 MiB): backup 1.14 s, verify 0.48 s, restore 1.39 s, first document readable **2.45 s** after the restore began, backlog drain ~0.9 jobs/s (`tests/test_document_pg_restore_drill_scale.py`, opt-in). Open: RPO/RTO on deployment hardware and store sizes (the drill is the tool) |
| F07 | Typed permanent/transient document errors reach the durable queue unchanged | Real retry/dead-letter/content-rejection transitions and finite attempt counts | Implemented 2026-10-13 — scanner outage retried then dead-lettered (was a permanent content rejection); a Postgres error in intake validation retried (only SQLite errors were); `storage_full` / `metadata_unavailable` codes; a store error while recording a stage outcome no longer ends the worker loop (lease recovery resumes the job). `tests/test_document_fault_transitions.py`: the 7 fixed behaviours fail under an old-handling witness, the 4 controls pass under both |
| F08 | Admission is bounded and truthful under outages and concurrent/unsized uploads | Atomic durable reservations, byte reconciliation, fairness, early Web admission tests | Implemented 2026-10-13 — durable reservations (earlier); Web upload answered before its body is read (declared length, writes disabled, floor counting the incoming file, queue/tenant/rate) with per-process spool slots, spool under the storage root; stages below the floor retried; GC sweeps upload/stage/parser/operation scratch; Postgres metadata enforces the tenant quota (advisory lock, race test + control); fair claims across tenants (fewest running, then readiness) on SQLite and Postgres; bounded SQLite claim window. Same day, continued: the ten JSON routes read through a 2 MiB bounded reader (declared length refused unread, undeclared cut off; malformed/too-deep JSON is a 422; control: `Body(...)` reads everything). Open: fairness is fewest-running, not weighted; spool slots are per process (both by design for one operator per install) |
| F09 | Source reading order and provenance survive extraction | Labeled EN/AR/bilingual structural corpus, no invented physical-page citations | Implemented 2026-10-13 — versioned corpus `documents/fidelity.py` (EN/AR/bilingual × DOCX/PDF/HTML, EN/AR OCR) with declared floors and release targets; fixed Arabic PDF extraction (numbers, ligatures and table cells reversed by PyMuPDF; `parsers/pdf_rtl.py`) and fused HTML table cells. Same day, continued: the "reversed date" was the extraction's error, not the renderer's — after Arabic letters digits are Arabic numbers to the bidi algorithm and "-" does not join them, so `2026-09-30` is DRAWN `30-09-2026`; each number cluster's reading is now the one python-bidi draws as the page shows (round trips through the real algorithm; control without the check), a table row's numbers come back in column order, cells on one baseline are separate words, a misread zero-advance mark is dropped; OCR words get the same number rule. Corpus v2 adds long EN/AR documents, two columns from another producer, wide wrapping tables and a degraded scan; segment recall now requires the words together (control: reading across columns fails). **15/17** cases meet release targets (`ar-pdf` now qualified). Not qualified: `ar-ocr` (Tesseract `ara`: digits of this font misread and two lines dropped by its layout analysis; CER 0.23; 3/4/6/11/12 segmentation, rule removal, grayscale, 2× upscale measured, none qualifies) and `en-ocr-degraded` (3.75 read as 3:75). Corpus v3 (before the commit): OCR pages with a ruled table, read cell by cell -- **16/19** qualified (`en-ocr-table` yes, `ar-ocr-table` no: Arabic-Indic digits) |
| F10 | Certification reports actual performed qualification and effective deployment | Backend/engine/profile/version evidence, worker-tree/native resources, no false soak success | Implemented 2026-10-13 — the report names the configured isolation profile, worker image and metadata/jobs backends; a `process` profile is CONDITIONAL and never reported as confined; `extraction_fidelity` and `backup_restore_sqlite` are real runs; soak and external review stay NOT RUN |
| Q01 | Recovery survives process death at every publication stage | Intake/parse/manifest/index/verify crash matrix, lease loss, DB/disk/scanner faults, shutdown bounds | Implemented 2026-10-13 for single-host recovery — real child processes killed after each of 7 intake stages (keyed/unkeyed) and inside 4 job stages, on SQLite and real PostgreSQL 16.15 (44 cases): reservations released, partial publications rolled back, leases recovered, every job ready once; scanner/disk/DB faults and a bounded shutdown with a stuck stage. Same day, continued: cross-host takeover with fencing — an owner that cannot be proven dead renews a lease (`documents.intake.lease_seconds`, stored on the row); once lapsed it is taken over and rolled back, and the stale owner is fenced (its job is created only while the reservation is still its own, serialized with the takeover; a claim refuses a published intake); it removes its own token rows, and the sweep removes what it left for a day. Drilled with a real child on "another host", paused, taken over and resumed, on SQLite and PostgreSQL 16.15 (8+8 cases; controls: without the fence it publishes over the takeover, without the sweep its rows stay). Legacy rows without a lease are still never taken over. Same day, before the commit: the stale owner in a REAL other container (own hostname, data dir, install id; today's kazma_core mounted) sharing a disposable PostgreSQL with the recoverer here -- frozen with `docker pause` until its lease really lapsed on the database's clock, or cut off the network -- is taken over and fenced when it returns; with its renewals blind the fence alone refuses it; blind and unfenced it publishes (control). `tests/test_document_intake_lease_containers.py` **4 passed**, opt-in, removes everything it starts |
| Q02 | Document fidelity has declared release thresholds | Versioned corpus; OCR CER/WER, tables/numbers, order, omission, citation and rendered-layout measurements | In progress — release thresholds declared for corpus v4 (F09; v4, 2026-10-15: the OCR reference holds the page furniture the renderer drew) with the OCR cases held per engine: **17/19** qualified with the neural OCR engine (`ar-ocr` now qualified: every word and number, CER 0), 16/19 with Tesseract. Arabic and Arabic-Indic digits under OCR: done 2026-10-15 — seven candidates measured (below), the neural engine adopted behind `documents.ocr.engine` (default `auto`), in the document worker on both profiles, Tesseract the fallback. Real scans measured opt-in through the real intake, OCR and search (`tests/test_document_real_scans.py`, manifest outside the repo, floors per engine): the first (a 300 dpi copier scan of an Arabic booklet, 5 pages) found and fixed the OCR TSV quote defect and ruled-table loss. Open: `ar-ocr-table` (one Arabic word) and `en-ocr-degraded` (a decimal point) with either engine; Arabic decimal/date separators; more real scans (handwriting, forms, merged cells, other producers) |
| Q03 | Sustained operation is measured on supported profiles | Actual 48–72 h mixed-load soak with restarts/faults; p95/p99/throughput/resource evidence | Open |
| Q04 | Independent review supports the claimed industrial security profile | External review with scope/results/remediation evidence; never substitute self-review | Done 2026-10-15 -- the review pack was ready (2026-10-15, `docs/audits/Q04_DOCUMENTS_SECURITY_REVIEW_PACK_2026-10-15.md`: scope, claims C1-C14 with their tests, setup, deliverables). The authors' defensive check of those claims (not Q04: no attack attempted, self-review) found gaps in 8 of the 14 and fixed each with a test the old code fails (`docs/audits/Q04_DEFENSIVE_CHECK_2026-10-15.md`: unfenced document text in two prompts, a delete that left a document searchable, a LibreOffice call that bypassed the bounded runner, a broker token served unrestricted, a looser attestation, certification and readiness passing an unconfirmed image, cancel with read access, a remote-parse refusal at DEBUG); the pack states the claims as the code now holds them. An independent reviewer (no authorship, no owner infrastructure) then reviewed the pack's 14 claims (2026-10-15): 13 held, and FIND-01 broke C1 -- a `/GoTo` or `/Thread` with an `/F` file specification is a remote go-to the policy passed as navigation; fixed in `documents/pdf_policy.py` with tests the old policy fails, and OBS-02 (case-insensitive names) and OBS-03 (the C2 gate resolves imports) done, OBS-04 accepted (`docs/audits/Q04_REVIEW_RESULTS_2026-10-15.md`). The reviewer retested the fix commit `7a6a98b9` on 2026-10-15 and confirmed FIND-01 resolved (the three PoCs refuse, controls still parse, 65 tests pass), with OBS-02 and OBS-03 verified and their old-code-fails controls reproduced, no regression. **Done 2026-10-15 (`7a6a98b9`)** -- the first requirement here qualified by an independent reviewer; the review, its finding and the retest are the Q04 evidence. (The Documents goal overall stays open on Q02, Q03 and F02.) |
| Q05 | Product, ops and certification documentation matches verified behavior | Updated architecture/security/ops/gates and regression checks, explicit limitations | Done 2026-10-13 — the full documentation review ran (5 reviewers, every Documents page); its corrections and the 12 code defects it found (DOC-A1..A13, below) landed with gates; the docs gates pass |

## Execution rules

Preserve the shared durable/execution service boundaries, tenant/workspace scope,
HITL paths, source grounding, Arabic text policy and line endings. Fix equivalent
sibling paths together. Add behavioral regressions with the original failure as
a negative control where practical. Use temporary stores and inert fixtures;
never upload customer content or execute PDF actions to prove a rejection.

Keep the supported single-runtime SQLite profile explicit. Qualify shared-state
scale-out separately rather than treating a backend class as HA proof. Do not
start or kill the live server; use the repository's deployment policy only when
deployment is part of authorized verification.

No requirement is complete without current evidence of its full scope. A real
soak or external review may remain outstanding while implementation proceeds;
do not silently remove it, shorten it, or mark the whole goal complete.

## Execution log

- 2026-10-12: Revalidated baseline worktree (clean product tree), established
  this requirement ledger, and began shared PDF policy hardening.
- 2026-10-12: Shared PDF traversal, unavailable/malformed fail-closed policy,
  DOCX paragraph/table order, finite parser retries, backup inventory/manifests
  and verified retention implemented. Structural preflight and local salvage
  now execute inside the bounded child. Durable job identity scopes egress;
  audit outage refuses upload and live denial is rechecked per provider.
  Public job reads enforce the document ACL; sequential replays bind request
  facts and actual SHA. Synthetic salvage segments carry no physical-page
  claim. The CLI reports an extended smoke and unmeasured metadata instead of
  claiming a multi-day soak or backend certification.
- Evidence so far: PDF policy 31 passed; worker/parser/ingestion/Arabic group
  137 passed; access regressions 14 passed; targeted hardening group 25 passed;
  job/access/egress group 34 passed, 3 Postgres tests skipped. These groups
  overlap and are not an aggregate unique-test count. Logs are under
  `session-artifacts/document-*.log`. Later changes require the current
  integration and code-gate rechecks before a final milestone result.
- 2026-10-12: Capacity reads now fail closed on unavailable storage/queue/tenant
  measurements. Unsized streams consume the configured intake upper bound in
  the byte window, rather than zero. This is conservative process-local
  accounting, not the required durable concurrent reservation/reconciliation
  implementation. Declared-length spoofing, early HTTP admission, bounded
  idempotency reservations and fairness remain part of F08/F05.
- Current verification: 98 code/docs gates passed; later policy/Arabic/code
  recheck 179 passed. Broad first Documents run: 355 passed, 6 skipped, 8
  failures. Seven were the SQLite-backed Postgres test adapter's new migration
  syntax; its corrected operations/capacity recheck passed 45. The eighth was
  the corpus's obsolete crash error code; corrected certification recheck
  passed 14. Rejecting a fencing sample no longer counts as proving a fence.
- Native SQLite backup now derives promoted original requirements from durable
  validation events. Quarantine is historical intake provenance and can be
  collected; execution and restore need originals. A real temporary-store
  drill restores queued work, processes it to READY, checks owner/shared/denied
  ACL reads and job history, then restores a second READY snapshot with its
  normalized manifest. The restore/backup/operations group passed 51. Its
  small inert fixture took 3.796 seconds; this is not a deployment RTO claim
  (`session-artifacts/document-restore-drill.xml`).
- Final filtered 35-file Documents verification: **376 passed, 6 skipped**
  across the repository's default eight owned, low-priority pytest chunks
  (`not slow`). Seven chunks passed directly. Two export-fixture failures in
  the eighth were corrected by adding the tenant columns its simplified schema
  omitted; the full five-file chunk recheck passed 23 with 2 skips. Original
  failures remain in their historical logs. Current receipts and hashes are in
  `session-artifacts/document-final-verification.json`; final logs are
  `session-artifacts/document-final-chunk-*.log`. Code/debt/import/SQLite/docs
  gates passed 40; Python compilation and undefined-name checks passed. No
  external review or real multi-day soak has been performed.

- 2026-10-12: Durable intake admission now reserves queue, tenant and rate/byte
  budgets in the job database before reading the stream. Independent-process
  SQLite races prove atomic refusal after the winner exits without cleanup.
  Exact declared lengths are enforced before CAS publication, actual bytes are
  reconciled, partial publication remains held, and matching published jobs
  release reservations at restart. Manual retries use write ACL and admission.
  Canonical tuple encoding removes delimiter collisions in tenant/key UUIDs;
  legacy replays resolve through stored tenant/key identity.
- Current filtered 36-file Documents receipts: **400 passed, 11 skipped**
  across eight owned low-priority chunks, replacing chunk 02 with its full
  final recheck after the UUID change. Code/docs gates: **60 passed**. The
  targeted identity group also passed 53 with 8 skips (overlapping evidence).
  Receipts are in `session-artifacts/document-admission-final-verification.json`.
  Marked live Postgres cases were skipped; the SQL adapter proves vocabulary
  only. No real multi-day soak or independent review has been performed.


- 2026-10-12: Resumed after a PC crash. Source integrity checks found no source
  corruption; one pre-crash test log was NUL-filled and another had no receipt.
  Those runs are excluded from current evidence. Default document services now
  share a coherent, immutable strict ConfigStore snapshot per operation across
  execution, capacity, knowledge and GC, including async/thread boundaries.
  Worker tasks detach startup context; queued jobs recheck malware policy.
  Invalid/unavailable policy refuses new execution (HTTP 503), and remote
  providers recheck live denial even between fallback attempts. Readiness
  exposes configured/effective hashes and restart-required resource settings;
  the panel clears stale green state on failed refresh.
- Backup/migration resolve the installed CAS root instead of a pending proposed
  root, including during a settings outage. Native SQLite document snapshots
  refuse installed PostgreSQL metadata/jobs instead of copying unused fallback
  state. PostgreSQL dumps now include durable admission reservations and the
  mutex table; the schema-parity test covers their DDL.
- Real isolated PostgreSQL **16.14**, 1 CPU / 384 MiB container: **9 passed**
  for job idempotency, five concurrent admission-budget races through independent
  connections, storage SQL and coherent cross-store policy snapshots. The
  container had no live data mounts and was removed. This is targeted backend
  evidence, not process-death recovery, coordinated restore, scale-out or soak
  qualification. Receipts: `session-artifacts/document-real-pg-profile.json`
  and `session-artifacts/document-real-pg-admission-policy.log`.
- Focused post-crash verification: **34 passed** policy/egress/snapshot;
  **75 passed** backup/policy/operations integration; **17 passed** PG backup
  schema/config/handler checks. These groups overlap and are not a unique total.
- 2026-10-13: Final fresh 64-file regression: **819 passed, 14 skipped**, across eight
  owned low-priority chunks with two broad chunks running concurrently. Full
  chunk 02/03 rechecks replace the obsolete fixture-signature and structural
  type-reference failures; original failed logs remain retained. Nine skipped
  PostgreSQL cases passed separately against the real isolated backend above;
  the remaining skips are not represented as qualification. After the final
  operations poller change to the shared JSON reader, JavaScript/docs rechecks
  passed **80** (overlapping the broad suite). Null, HTTP policy refusal, HTML
  proxy responses and network failure all clear stale ready state.
  AST/compilation passed for 53 changed Python files; Ruff F/I has no new
  findings (six preexisting findings retained); `git diff --check` passed.
  Current receipts, log/source hashes and explicit profile limits are in
  `session-artifacts/document-policy-final-verification.json`.

- 2026-10-13 (continued, "proceed without stopping"): the buildable gaps.
  JSON bodies bounded (8 tests + 115 API). Knowledge index reconciled at
  every worker start and by `document_pg_backup.py index` (10 cases on
  SQLite, the 9 backend cases again on PostgreSQL). Arabic number reading verified against
  python-bidi in PDF and OCR; corpus v2 (17 cases, 15 qualified); segment
  contiguity and per-line alignment metrics with negative controls. Intake
  lease + fence + sweep (16 cases on both backends with real child
  processes). 1,000-document restore drill on PostgreSQL 16.15. All
  Postgres-marked document tests: **100 passed**, 2 skipped (the opt-in drill,
  run separately and passed; one existing phase-9 skip). The full suite then
  found four defects in this effort's uncommitted code, all fixed and gated:
  the LibreOffice version probe hung its caller when it timed out under load
  (`subprocess.run` waited without a limit for pipes soffice.bin held; now the
  whole tree is killed and the last wait bounded, `tests/test_soffice_call_bounded.py`
  with the old call as control); the container broker looked docker up on
  PATH alone (now Kazma's one lookup, loaded by path, or the CLI its image
  names); the settings `snapshot` read was not declared as a read to the
  store-announcement gate; `documents/errors.py` (psycopg error classes) had
  no Postgres test (a real server-terminated session now classifies as
  `metadata_unavailable`). The acquisition-alerts browser test (committed
  without a CI job) now has its own step in the Playwright job (passed
  locally, EN and AR). The other HEAD failures are fixed too:
  `acquisition.monitor_settings` is a SETTING and the monitor's alert ledger
  (`acquisition.monitor`, missing from the hand-kept `durable_state.mutate`
  declaration) RUNTIME, with a gate that reads every `mutate` name from its
  callers; the Knowledge browser test renders the page with a request as the
  app does; the report link uses the catalog's `research.open_md`. Only the
  metrics receipt is left, for the commit (it needs the new files staged).
  Receipt:
  `session-artifacts/documents-continuous-run-2026-10-13.json`.

## Next implementation boundary

F02 now has one qualified fail-closed filesystem/network profile: the Docker
container broker (see the 2026-10-13 entries below), whose engine readiness
now comes from the image itself. Still open for F02: the first green run of
the new CI job (a Linux Docker Engine qualification), rootless/Kubernetes profiles, a
stronger kernel boundary (gVisor/Kata) if the threat model needs it, native OS
jails for the `process` profile, and the Unix process-count cap. The
F05/F08 admission protocol reserves request keys and pending job/rate/byte
budgets durably, with bounded length enforcement and actual-byte reconciliation.
Since the 2026-10-13 continuation an owner that cannot be proven dead holds a
lease; a lapsed lease is taken over and the stale owner is fenced at the
publication (the job), with its token-tagged rows removed by itself or the
sweep. Metadata writes are not fenced per statement (they may live in another
database): the job fence plus token cleanup is the design, recorded in
AGENTS §19 B2.
Early HTTP-body admission, aggregate temporary/disk budgets, dispatch fairness
and real Postgres crash/restore and deployment qualification remain open.
Basic independent-connection admission races now pass on real PostgreSQL.
Then complete broader recovery, quality corpus, sustained soak and independent
review qualification.

- 2026-10-13: Windows parser workers now wait behind a trusted launch gate
  until Job Object memory, hard CPU-rate (100% of one logical processor), and
  active-process-count limits are configured and assigned; failed setup kills
  the gated process before it launches the parser. Windows uses a small
  subprocess trampoline because `os.execvpe` crashes on this host. Unix keeps
  `setrlimit` memory/CPU-time limits and reports its missing process-count cap
  honestly. Focused sandbox/preflight tests passed **14**, and the combined
  operations/sandbox/preflight group passed **51**; Ruff F/I and `git diff --check`
  passed. A Windows live smoke exercised no limits, memory, CPU-rate,
  process-count and combined caps. Receipt and source hashes are in
  `session-artifacts/document-windows-worker-containment.json`. This does not
  provide filesystem/network confinement,
  OS-independent process-tree quota proof, or crash/cancellation qualification.
- 2026-10-13 F02 deployment feasibility: Bubblewrap cannot create its
  namespaces inside the stock Docker app container under the default seccomp
  profile; it worked only after disabling seccomp in a throwaway container,
  which is not an acceptable production setting. A separate no-mount,
  no-network, read-only-root Docker worker container with tmpfs, memory/CPU/PID
  limits, dropped capabilities and `no-new-privileges` passed a primitive-level
  smoke. This does not integrate Kazma's worker protocol. The next candidate is
  a restricted broker/orchestrator that alone owns the Docker API; never expose
  `/var/run/docker.sock` to Kazma. See the detailed reproduction and caveats in
  `docs/plans/DOCUMENTS_INDUSTRIAL_HANDOFF_2026-10-13.md`. F02 remains open.
- 2026-10-13 F02 Docker deployment profile (container broker): new
  `KAZMA_DOCUMENT_WORKER_ISOLATION=container` sends every parser, mutation
  and renderer run (and the OCR/LibreOffice/salvage steps inside them)
  through `worker_container.run_container_worker` to a stdlib-only broker
  (`documents/worker_broker.py`), the sole holder of the Docker engine
  socket. Each job is a fresh container of one image pinned by id: no
  network, no mounts, read-only root, size-bounded noexec tmpfs, memory=swap
  limit, 1 CPU, PID/nofile/core limits, all capabilities dropped,
  no-new-privileges, uid 10001, default seccomp, `--init`. Inputs and outputs
  are digest-checked bundles (`worker_bundle.py`); the output stream is
  untrusted (allowlisted names, caps before writes, no overwrite). The broker
  attests the policy from inside a real job container before serving and
  refuses on any mismatch; it kills on deadline, output cap and client
  disconnect, verifies removal, quarantines a slot it cannot clean and sweeps
  stale containers at start. No fallback to `process`; an invalid mode
  refuses every run; a crash with no output maps to the local profile's
  `parser_result_missing`. Worker image: `deploy/document-worker/Dockerfile`
  (hash-locked requirements, no httpx/docling); broker image and compose
  overlay: `deploy/document-broker/Dockerfile`,
  `deploy/docker-compose.document-broker.yml`; `secrets/` is now git- and
  docker-ignored.
- Evidence (Docker Engine 29.8.2, Docker Desktop Linux VM, cgroup v2, Windows
  11): unit protocol/policy/client tests **98 passed**; real-Docker
  qualification **19 passed** (attestation, EN/AR/bilingual parse parity with
  the local worker, hostile corpus parity, mutation/render, OOM kill, PID cap
  EAGAIN, CPU ≤1.15 cores, tmpfs ENOSPC, deadline, output cap, stderr bound,
  orphan descendant, disconnect cancellation, busy admission, stale sweep,
  stress refusal in production mode, crash as no-result, missing image);
  topology smoke (broker container + uid-10001 client with no engine socket,
  hostile corpus, wrong token refused, SIGTERM stop leaves no containers):
  all 10 checks. Negative controls retained: the first attestation caught an
  import-created `/kazma-data` in the image and an unlisted
  `/usr/sbin/docker-init`; the first qualification run caught a broker
  deadlock (synchronous `docker kill` stopped the output pump, so the engine
  could not finish the kill) — both fixed. Gates **passed (repository class gates 183, post-follow-up unit/static/debt/env 167, docs 25; overlapping)**; broad
  Documents regression **921 passed, 14 skipped** (the previous 64-file set plus the new unit file, eight owned low-priority chunks, final code and docs; the same 14 skips as before; a pre-follow-up run gave 919/14). Receipt with hashes and limits:
  `session-artifacts/document-container-worker-profile.json`.
- Not established: bare-metal Linux/rootless Docker/Podman/Kubernetes; a
  gVisor/Kata runtime (containers share the VM kernel); parser/renderer
  readiness probed inside the worker image (still the Kazma process);
  Docling salvage in the image; arm64; representative soak. The `process`
  profile still has no filesystem/network isolation and is reported as
  unqualified for hostile documents; native OS jails remain open.
- 2026-10-13 F02 follow-through (engine readiness, strict profile, CI):
  in the container profile Kazma no longer decides which engines exist from
  its own packages. The broker collects the image's own parser, renderer,
  PDF-mutator and OCR report at start (`worker_entry probe capabilities`) and
  refuses to serve without it; `get_parser_registry`, `get_renderer_registry`,
  `get_mutation_registry` and `get_ocr_health` return live views of that
  report (re-read per lookup, so a service built while the broker was down
  catches up). An engine the image lacks is unavailable with the image's
  reason; a broker that is not ready makes operations answer the retryable
  `worker_isolation_unavailable`, not a permanent engine error. The image
  also reports a fingerprint of its document code, and readiness flags an
  outdated image (`image_code_matches: false`,
  `document_worker_image_outdated`); this caught a real stale build during
  this work. `security.execution.container_required` (the operator's strict
  execution profile) now refuses the `process` profile for documents
  (`worker_isolation_required`; an unreadable policy refuses too), checked
  where Kazma starts a worker. `file_read` asks the parser registry from a
  thread (it probed engine binaries on the event loop); the four factories
  are loop-stall helpers in the static gate, with the old `file_read` shape
  as its negative control. A CI job, `Document worker container profile
  (real Docker)`, builds both images, attests the policy, runs the
  qualification suite with skips failing the job, and runs the compose-shaped
  topology (`scripts/document_broker_topology.py`); the branch-protection
  runbook lists it. Adding it to the GitHub ruleset is the owner's action.
- Two pre-existing renderer defects found because the image is the first
  place WeasyPrint's native libraries were present, both fixed: the renderer
  imported `default_url_fetcher`, which WeasyPrint 70 (the locked floor)
  removed, so every HTML/Markdown->PDF job failed with ImportError while the
  engine read "ready"; and with no approved assets, `iterdir()` on the missing
  asset folder raised and a catch-all reported it as `external_resource_denied`.
  The fetcher is now a WeasyPrint 70 `URLFetcher` subclass that allows only
  `data:` and files inside the approved asset folder and raises a fatal
  fetch error (mapped to `external_resource_denied`) for anything else; the
  plugin's health probe imports exactly what the renderer uses. Pinned by
  `tests/test_weasyprint_renderer.py` (a WeasyPrint 70 stub, the old import as
  negative control) and by real-WeasyPrint container tests (approved asset
  rendered, external image refused, Arabic HTML->PDF).
- Evidence: unit **110 passed** (`tests/test_document_worker_container.py`),
  WeasyPrint **6 passed**, real-Docker qualification **22 passed** on the
  rebuilt images (`session-artifacts/f02-container/docker-qualification-7.log`;
  earlier runs -4..-6 retain the stale-image, HTML-fixture, WeasyPrint import
  and missing-asset-folder failures), topology smoke 10/10 with
  `image_code_matches: true` from the socket-less client, repository/docs/CI
  gates passed (the debt ratchet was lowered by one blind except). Broad
  regression: **1125 passed, 14 skipped** (the same 64-file set plus the new and affected files, eight owned low-priority chunks, all healthy; the same 14 skips), and the full repository/docs/CI gate set **343 passed**. A pre-existing, out-of-scope CI gap was
  reported separately: `tests/e2e/test_acquisition_alerts.py` (commit
  815c83e2) runs in no CI job, which fails
  `test_every_e2e_file_runs_in_a_ci_job`.
- 2026-10-13 F01 (PDF active-content policy coverage): the policy
  (`documents/pdf_policy.py`) now also refuses XFA forms (their XML packets
  carry scripts), a bare `/EF` embedded file without `/Type`, and actions
  that run, open, submit or fetch outside the document (`/GoToR`,
  `/SubmitForm`, `/ImportData`, `/Rendition`, `/RichMediaExecute`, `/Sound`,
  `/Movie`), and link targets that are not absolute http/https/mailto
  (`file:`, UNC and relative targets leak credentials when opened); `/URI`
  values are judged even when the indirect string was first reached under
  another role. Coverage gaps closed: redaction opened its source with
  PyMuPDF before the policy (merge/split/info/fill-form already went through
  `_reader`); rendered PDFs (LibreOffice, WeasyPrint, ReportLab) were never
  checked and now pass the same walk in the worker before they leave; the
  document intent handler parsed the user's source PDF with fitz in the
  server process, and the document pipeline fell back to fitz exactly when
  the worker had refused a file -- both now read only through
  `DocumentService`. A class gate (`tests/test_pdf_openers_stay_in_workers.py`)
  fails on any PDF-library opener outside the worker-side modules or two
  declared checks of Kazma's own just-generated output, with the old reader
  as its negative control. The hostile corpus gained seven structured cases
  (XFA script, remote GoTo to a share, UNC link, SubmitForm, untyped embedded
  file, rendition, and a navigation-only control that must still parse);
  the committed manifest was regenerated (19 -> 26 cases). Evidence: PDF
  policy tests **52 passed** (each new denial, web links and navigation kept,
  shared indirect /URI, XFA, untyped /EF, rendered-output refusal and
  cleanup, the call-site matrix for pdf:info/merge/split/fill-form/redact
  through the real service and worker, intake via inspect and parse),
  opener gate 3, WeasyPrint 6, certification/render suites **71 passed,
  1 skipped**.
- 2026-10-13 F03/F04 (egress qualification): the provider loop now really
  falls back: a failed provider is audited (`failure`) and the next
  configured one is tried, with the live policy read again before it (until
  now the first failure re-raised and the fallback never ran). Both
  providers use one client (`extract_salvage._provider_client`): no ambient
  proxy environment (customer bytes go only to the named provider), no
  redirects (an upload is never re-sent elsewhere), and decoded responses
  capped at 32 MiB (`remote_parse_response_limit`). Tests drive the real
  provider functions through an in-memory transport: fallback phases,
  revocation after a failed provider, proxy/redirect refusal, an oversized
  answer, the LlamaParse job flow. New `tests/test_document_egress_pg.py`
  (Postgres-marked) runs the rules on real PostgreSQL metadata, jobs, audit
  and settings, asserting each store is on Postgres: concurrent services
  keep each job's identity and tenant isolation, a closed Postgres audit pool
  refuses the upload, revocation through the Postgres settings store stops
  the fallback upload, and a reused version keeps the new job's actor.
  Evidence: egress + salvage **21 passed** (SQLite); on a disposable
  PostgreSQL **16.15** container (512 MiB, 1 CPU) every Postgres-marked
  Documents/settings test **68 passed, 1 skipped** (an intentionally
  SQLite-shaped assertion), including the four new egress cases
  (`session-artifacts/f03-pg-egress.log`).
- 2026-10-13 continuous run (F05-F10, Q01, Q02). Each item below names its
  gate; every gate has its old behaviour as a negative control.
  - Q01/F05/F08 recovery: reservation owners (install, host, pid, start time)
    and recorded publication intent; own-failure rollback; start-up recovery of
    provably dead owners only; `stranded` for the rest. Crash matrix
    `tests/test_document_crash_recovery.py`: **22 passed on SQLite, 22 passed on
    PostgreSQL 16.15** (no skips; each case asserts the service is really on
    Postgres). Recovery switched off (old behaviour) leaves the reservation and
    the empty document; an owner rewritten to another host is never taken over.
  - F07 faults: `tests/test_document_fault_transitions.py` **11 passed**; with
    the old classification and worker loop restored by a witness plugin, the 7
    fixed behaviours fail and the 4 controls pass.
  - F05 job reads: `tests/test_document_job_read_matrix.py` **22 passed**
    (signature gate, missing actors, Web routes compared byte for byte with an
    unknown job, admin-only audit, agent cancel, gateway status).
  - F08: `tests/test_document_upload_admission.py` **10 passed** (an ASGI
    body-read counter: refusals read 0 body messages, the old route reads 5);
    `tests/test_document_claim_fairness.py` 4 SQLite + 2 PostgreSQL (private
    database); `tests/test_document_quota_both_backends.py` 2 SQLite + 4
    PostgreSQL incl. a concurrent race and the no-check control.
  - F06: `tests/test_document_pg_backup_restore.py` **3 passed** on PostgreSQL
    16.15 (pg_dump/pg_restore via `docker exec`; private source and target
    databases): restore + start + all jobs ready 3.797 s, first document
    readable 1.047 s after start, database restore 0.235 s, backup 0.188 s;
    torn-dump control; refusal to merge into a non-empty store.
    `tests/test_document_backup_pins.py` 5 passed.
  - F09/Q02: `tests/test_document_fidelity.py` 16 passed (11 cases + controls),
    `tests/test_document_pdf_rtl.py` 8 passed. Before the RTL repair the Arabic
    PDF measured token recall 0.846, numbers 0.50, table cells 0.44; after,
    0.960 / 0.75 / 1.0; bilingual PDF 0.928 -> 1.0. HTML table cells 0.0 -> 1.0.
  - F10: certification `tests/test_document_certification_phase10.py`
    **30 passed** incl. the profile test.
  - Q01 follow-up found by the soak harness pilot: an intake killed in another
    process after the last restart stayed held until the next start. A
    running service now sweeps dead owners every minute
    (`_sweep_stranded_intakes`, cancelled with the workers). Crash matrix
    **24 passed on SQLite and 24 passed on PostgreSQL 16.15** with the live
    sweep and its idled-sweep control (the control's final form drops a fixed
    sleep for a check after the child exits; that edit ran on SQLite after the
    container was removed).
  - Q03 harness (`scripts/document_soak.py`, private SQLite stores unless
    `--use-env`): a 2-minute pilot, not a soak -- 13 submitted / 13 ready,
    3 restarts, 2 injected crashes, nothing pending, no scratch left, p95
    15.7 s, max tree RSS 108.6 MiB. The 48-72 h run remains open.
  - Whole Postgres-marked suite (the CI Postgres job's set) on PostgreSQL
    16.15: **365 passed, 8 skipped, 1 failed** -- the failure is the known
    out-of-scope `tests/e2e/test_acquisition_alerts.py` CI gap; no skip is a
    Documents behaviour except one intentionally SQLite-shaped assertion.
  - Worker image rebuilt for this build's document code
    (`kazma-document-worker:f10`); real-Docker qualification **22 passed**,
    no containers left. The disposable PostgreSQL container and its
    anonymous volume were removed.
  - Broad regression (all Documents tests + repository gates): **931 passed,
    68 skipped** (Postgres/Docker-marked), 1 failure fixed (fidelity's own
    rendered PDFs declared in the PDF-opener gate) and re-run green; the final
    worker-lifecycle/gate re-run **193 passed** plus the sleep-gate fix.
  - Out of scope, flagged as separate tasks: `acquisition.monitor_settings`
    has no settings-restore rule (fails on the committed tree), and the
    acquisition-alert e2e file has no CI job.

## 2026-10-13, continued before the commit: the items that needed more than code

The owner chose three of the remaining items (a container lease drill, a
larger restore drill, a better Arabic OCR model) and supplied a real scan.
Evidence in `session-artifacts/continuous-2026-10-13/` and
`session-artifacts/real-scans/` (the scan's manifest and measurements; the
scan itself stays outside the repository).

- **Better Arabic OCR model.** `tessdata_best` `ara` measured against the
  installed fast model on the corpus pages: CER 0.228 -> 0.224, numbers 1/3
  either way, the same two lines dropped. Arabic-Indic digits are not read in
  either bundled font by either model at any segmentation (European digits
  are). Not adopted: the gain does not justify a 12.6 MB model in every
  install and image. Reading Arabic-Indic digits needs a different OCR engine
  -- a new dependency, the owner's decision.
- **Real scan** (Canon iR-ADV C5560, 300 dpi, 54 pages, no text layer; pages
  1, 2, 3, 30, 48 measured). Found and fixed:
  - the OCR TSV was parsed with CSV quoting, so a word starting with `"`
    swallowed every later row: lines of 14,386 characters of coordinates in
    the text and the index. `quoting=QUOTE_NONE`; test with the old quoting
    as control.
  - ruled tables lost their numbers and rows. New `ocr/tables.py`: grids
    found from continuous strokes (a joined Arabic baseline is not a rule),
    cells >= 3 % of the page wide (a page border is not a table), each cell
    read on its own with a confident digit pass; bounded (600 cells, 120 s a
    page; a failure keeps the page's reading); `documents.ocr.tables`, on by
    default, with a Settings switch. Contents table: page numbers 22 % -> 91 %,
    rows on one line 6 % -> 91 %, complete rows 3 % -> 56 %; chemistry symbols
    2/5 -> 4/5; 6/6 searches on the right page; ~8 s a page instead of ~2.
  - corpus v3 adds `en-ocr-table` (qualified, table cells 1.0; 0.0 with the
    pass off -- the control) and `ar-ocr-table` (cells 0.56: labels read,
    Arabic-Indic digits not); the OCR reference text now includes the table;
    a re-lock keeps the thresholds file's notes. `tests/test_document_ocr_tables.py`
    13 passed (each rule with its old rule as control).
- **Larger restore drill: 10,000 documents** (625 MiB, 6 writers, PostgreSQL
  16 in a 2-CPU / 3 GiB container): backup 9.64 s (664 MB), verify 5.17 s,
  restore 10.67 s, start 1.08 s, first document readable 11.77 s after the
  restore began, every table count equal to the snapshot. Linear in the store
  from 1,000; the start is not.
- **Found by the parallel drill: schema setup deadlocked and waited on
  dumps.** Every Postgres store sent `ALTER TABLE ... ADD COLUMN IF NOT
  EXISTS` / `CREATE INDEX IF NOT EXISTS` at every start, and Postgres locks
  before it checks (measured: the ALTER waits behind `pg_dump`, both behind an
  open writer). New `db/pg_schema.ensure_schema` (one shared advisory lock;
  each statement only when the catalog says it is missing) for every Postgres
  DDL site: core schema, swarm task store, memory mirror and pgvector table,
  document jobs/catalog/intake. `tests/test_pg_schema_setup.py` 7 passed on
  real PostgreSQL (direct statements as controls); the boot-lock test now
  holds the schema lock, and a lock on a core table no longer blocks a boot.
- **Container lease drill**: see Q01 above, 4 passed.

## 2026-10-13, the code defects the documentation review found (DOC-A1..A13)

All done in the working tree, each with a behavioural test, a negative
control and, where a class existed, a gate (AGENTS.md §19 B4b, B6, C, E, F3,
F4). Uncommitted, pending the owner's go-ahead.

| Item | What was wrong | Now | Gate |
|---|---|---|---|
| A1 | Postgres audit had no immutability trigger; its prune called `None` | Trigger `document_audit_guard`; bounded prune; restores create `KAZMA_PG_FUNCTIONS` first | `test_document_audit_pg.py` (real PG) |
| A2 | 3 settings read and never acted on | 8 removed (the gate found 5 more) | `test_document_config_fields_read.py` |
| A3 | Web, chat and agent were different principals | One operator; audited `owner` grants for older documents; multi-user refuses | `test_document_operator_principal.py` |
| A4 | Three status tables; unbounded delete body | One table; bounded reader everywhere | `test_document_api_statuses.py`, `test_document_json_body_cap.py` |
| A5 | `document_import` claimed a final state | Says when it is still processing | `test_document_platform_tools_phase8.py` |
| A6 | Backup path by the jobs backend; empty folders on failure | Path by the catalog; mixed SQLite+PG queue supported; folders renamed when complete | `test_document_backup_shapes.py` (real PG) |
| A7 | Migration carried none of a Postgres store's files | Catalog in the dump, files in the bundle | same |
| A8 | Failed document restore was a warning | Fails the import | same |
| A9 | Unavailable scanner: 400 after the body | 503 before the body | `test_document_api_statuses.py` |
| A10 | No drilled order for both PG backups | Dump first, then `--replace-document-tables` | `test_document_pg_restore_order.py` (real PG) |
| A12 | `CpuRate=10000` capped nothing; **the worker escaped its Job Object on every process's first run** | Suspended launch + `job_cpu_rate`; `code_exec` too | `test_job_objects_hold_suspended_children.py`, `test_document_sandbox.py` |
| A13 | `DOCUMENT_PG_TABLES` unchecked | Checked against DDL and a started store | `test_document_pg_tables.py` (real PG) |

A12's measurement (`session-artifacts/continuous-2026-10-13/doca12-measure.log`):
a 4-thread busy worker under a one-core cap used 3.93-3.99 cores on the first
run of each fresh process and 1.03-1.15 afterwards; after the fix the first
runs used 1.17-1.30. A memory hog allocated 768 MiB under a 256 MiB job with
the old launch and a late assignment; it is refused now.

## 2026-10-15: Arabic and Arabic-Indic digits under OCR (Q02)

The owner approved the downloads and asked for the best engine, replacing the
dependency if needed. Every candidate ran CPU-only through Kazma's own OCR
pipeline (`apply_ocr` and the ruled-table pass), wrapped as an OCR provider,
in a separate virtual environment (`C:\Users\balfa\kazma-soak\ocr-eval`,
outside the repository: scripts and JSON results, no page text). Three
measurements: the corpus v4 OCR cases (`fidelity.measure_case`); a digit set
of generated text (12 fonts -- IBM Plex Sans Arabic, Amiri, Noto Naskh, Kufi
and Sans Arabic, Arial, Tahoma, Times New Roman, Simplified Arabic,
Traditional Arabic, Sakkal Majalla, Arabic Typesetting -- each 10 Arabic lines
with one number: three in four Arabic-Indic, a fifth decimals, a fifth dates;
clean at 300 dpi and as a poor 200 dpi scan, tilted, blurred, speckled and
JPEG-compressed); and the real scan's five measured pages
(`tests/test_document_real_scans.py`'s checks). Four threads; the peak is the
whole process tree's resident memory.

| Engine | `ar-ocr` tokens / numbers / CER | `ar-ocr-table` tokens / numbers / cells / CER | `en-ocr-degraded` numbers | `en-ocr`, `en-ocr-table` | Digit set, numbers exact clean / scan | s per `ar-ocr` page | Peak MiB |
|---|---|---|---|---|---|---|---|
| Tesseract 5 `ara` (installed, fast) | 0.66 / 0.33 / 0.242 | 0.66 / 0.14 / 0.56 / 0.247 | 0.75 | both clean | 0.28 / 0.28 | 0.5 | 216 |
| Tesseract `script/Arabic` (installed, fast) | 0.62 / 0.33 / 0.242 | 0.62 / 0.14 / 0.56 / 0.247 | 0.75 | both clean | 0.39 / 0.32 | 0.5 | 222 |
| tessdata_best `ara` | 0.66 / 0.33 / 0.239 | 0.66 / 0.14 / 0.56 / 0.236 | 0.75 | both clean | 0.27 / 0.27 | 0.6 | 215 |
| tessdata_best `script/Arabic` | 0.66 / 0.33 / 0.212 | 0.66 / 0.14 / 0.56 / 0.210 | 0.75 | both clean | 0.39 / 0.34 | 0.6 | 231 |
| PaddleOCR PP-OCRv5 Arabic (RapidOCR 3.10, ONNX) | 0.84 / 0.00 / 0.106 | 0.85 / 0.14 / 0.67 / 0.125 | 1.00 | 0.99 / CER 0.016; 1.00 / 0.010 | 0.12 / 0.03 | 1.6 | 652 |
| EasyOCR 1.7.2 (`ar`+`en`, CRAFT, PyTorch) | 0.96 / 1.00 / 0.014 | 0.97 / 0.57 / 0.67 / 0.017 | 0.75 | both clean | 0.62 / 0.59 | 16.0 | 4,358 |
| **Kazma neural engine** (adopted) | **1.00 / 1.00 / 0.000** | **0.98 / 1.00 / 1.00 / 0.003** | 0.75 | both clean | **0.65 / 0.63** | 4.8 | 917 |

- **Not adopted, measured:** native PaddleOCR 3.7 (PP-OCRv5 server detection,
  PaddlePaddle): 167-244 s a page and a 43.8 GB peak -- out on cost alone (its
  Arabic numbers in the harness were invalid, a harness defect; the same
  models were measured through RapidOCR's ONNX conversion instead). Tesseract
  in every model and segmentation: no Arabic-Indic digits in any of the 12
  fonts. PaddleOCR alone: the best letters of the candidates, but its Arabic
  recognizer has no Arabic-Indic digits (0.00 on `ar-ocr`'s numbers). EasyOCR
  alone: the digits, but CRAFT detection costs 12 s and 4.3 GB a page.
- **Not measured:** Surya (GPL-3.0 code, model weights under a license that
  restricts commercial use: excluded by license); vision-language OCR models
  (QARI-OCR and the like: billions of parameters, far outside the worker's
  one core and 1 GiB).
- **The adopted engine** combines what measured best, all Apache-2.0 models on
  ONNX Runtime (MIT): PP-OCRv5 mobile detection (RapidAI's ONNX), EasyOCR's
  `arabic_g1` recognizer (the only one that read Arabic-Indic digits) on each
  line, `english_g2` on lines without Arabic, and a vote on letters with
  PaddleOCR's PP-OCRv5 Arabic recognizer and Tesseract's words (two agreeing,
  or PaddleOCR at >= 0.9 alone; a number is always EasyOCR's). EasyOCR's two
  PyTorch recognizers are converted to ONNX at install, checked against
  PyTorch; no PyTorch at run time. Runtime packages: the `ocr` extra
  (onnxruntime, opencv-python-headless, pyclipper, numpy; all permissive).
  Models: about 240 MB (`scripts/ocr_models.py install`, every source pinned
  by URL, size and SHA-256); the worker image grows from 1.27 to 2.16 GB.
- **In the real worker** (one core, 1 GiB, `worker_memory_mb` 1024):
  `ar-ocr` 17.7 s (Tesseract 5.5 s), `ar-ocr-table` 37.4 s (6.2 s), English
  pages 4.7-5.7 s (2.2-3.3 s); peak inside the 1 GiB limit (~800 MB). Near the
  worker's time limit the remaining pages go to Tesseract, said on the
  document.
- **Real scan** (5 measured pages, both engines through the real intake with
  a long worker time limit; every page read by the engine asked for):

| Page | Check | Tesseract | Neural |
|---|---|---|---|
| 1 (cover) | terms | 0.30 | 0.90 |
| 2 (contents table) | terms / page numbers / rows' numbers on one line / complete rows | 0.57 / 0.91 / 0.91 / 0.56 | 0.86 / 1.00 / 1.00 / 0.75 |
| 3 (lesson, tables) | terms / numbers (1) | 0.85 / 1.00 | 0.80 / 0.00 |
| 30 | terms / numbers | 1.00 / 0.00 | 0.94 / 0.50 |
| 48 | terms / numbers / symbols | 0.86 / 0.67 / 0.80 | 0.71 / 0.83 / 0.60 |

  The six searches land on the right page with either engine. The neural
  engine wins on the cover and the contents table and on the numbers of pages
  30 and 48, and loses terms on three text pages (a ligature "الم" read short)
  and page 3's one number. Floors per engine, each 0.02 under its measurement,
  are in the manifest (`floors_by_engine`, outside the repository).
- **Corpus v4 locked per engine** (`tests/fixtures/documents/fidelity_thresholds.json`,
  `ocr_engines`): neural qualified `ar-ocr`, `en-ocr`, `en-ocr-table`;
  Tesseract `en-ocr`, `en-ocr-table`. Not qualified, with reasons in the file:
  neural `ar-ocr-table` ("ريالاً" read with ب for ي; every number and cell
  right) and `en-ocr-degraded` (3.75 read 3,75); Tesseract `ar-ocr`,
  `ar-ocr-table` (no Arabic-Indic digits, lines dropped) and
  `en-ocr-degraded` (3:75).
- **Integration**: `documents.ocr.engine` (`auto` default, `neural`,
  `tesseract`), validated on every write and shown in Settings -> Documents in
  English and Arabic, with whether the neural engine is ready and why not.
  The engine runs only in the document worker; the container image carries it
  and its models (`ocr-models` build stage, hash-locked), and its capability
  report names both engines. Gates: `tests/test_document_ocr_neural.py` (each
  rule with its control), `tests/test_document_fidelity.py` (per engine),
  `tests/test_document_real_scans.py` (per engine, opt-in),
  `tests/test_document_worker_container_docker.py` (the image reads
  Arabic-Indic digits; control: Tesseract in the same image does not).
- **Open:** dates with "/" between Arabic-Indic digits (`arabic_g1` rarely
  reads it); the "الم" ligature on real scans; a second real scan; speed (one
  core) -- the per-document time limit decides how many pages stay on the
  neural engine. (Arabic decimals: closed below.)

## 2026-10-15: the corpus's last OCR misses (Q02, follow-up)

The live install (container profile, neural engine) read `ar-ocr` as "ريالأ"
for "ريالاً", "٣,٧٥" for "٣٫٧٥", "التغليف ." and no final full stop; the table
page "ربالأ"; the degraded English scan "3,75". Each cause was found by
logging every reader's output per line (EasyOCR with its per-character
probabilities, PaddleOCR, Tesseract's words, the vote) on the corpus pages,
in process and through the isolated worker; the readings matched the live
install's exactly.

| Miss | Cause (evidence) | Rule |
|---|---|---|
| "٣,٧٥" | Neither EasyOCR `arabic_g1` (184 classes) nor Tesseract `ara` (85 symbols, one of the ten Arabic-Indic digits) has ٫ or ٬; EasyOCR wrote "," at 0.915. PaddleOCR's dictionary has both but read the number "٣٥". | `arabic.arabic_number_separators`, both engines: a group of other than three digits follows the decimal separator; three digits before another separator are thousands; before exactly three it stays as read (thousands or fils: a wrong guess is a thousandfold) |
| "التغليف ." | EasyOCR emits a space (0.92) between the full stop and a final ف: the font's gap. | A space before a full stop or comma (and ، ؛ ؟) goes; not before ":" ";" "!" "?" (French spacing) |
| last "." lost | The vote replaced EasyOCR's whole word "المراجعة." with PaddleOCR's confident "المراجعة": PaddleOCR drops full stops on every line. | The vote is over letters; punctuation is EasyOCR's |
| "ريالأ" | EasyOCR reads the alef + tanween as أ (0.944; its fathatan class 0.004): EasyOCR's own list for Arabic leaves the harakat out. Tesseract read "ريالاً"; PaddleOCR garbled the word. | Where EasyOCR wrote a hamza or madda and another reader of the same letters saw harakat on that side of that letter, those stand; a mark only another reader saw is never added (Tesseract also wrote "الشماليٍ") |
| "ربالأ" (table page) | EasyOCR ب 0.742 / ي 0.257; Tesseract "ريالاً"; PaddleOCR no aligned word, so no two readers agreed. On both pages every letter EasyOCR misread had the right one second (0.22-0.34). | EasyOCR's second reading (>= 0.1, same bidi class) is a reading: a reader that reads it makes two |
| "3,75" (degraded) | A speck of the scan's salt noise beside the decimal point (EasyOCR "," at 0.985; Tesseract ":" from a speck above). Specks were 1-3 px of area; the smallest real mark, a blurred full stop at stroke 3, 6; clean 300 dpi dots 9 and up. | Before a line is read, ink with area under half its own stroke squared and no side over a stroke is removed from its box, unless it continues past the box |

Tried and not adopted: a crop margin of a tenth of the line's height
(EasyOCR's own pipeline crops with it). It read English line-final full
stops right (english_g2 reads some as ":", 0.82 even on the clean page) but
added full stops to Arabic headings and the footer, which have none, and
misread a word on each Arabic page ("بلفت" on one, "ربالأ" on the table
page). Punctuation is not counted by the fidelity
measure, so the English ":" changes no case; it is left as read.

Measured (corpus v4, both engines, through the isolated worker, then
`scripts/document_fidelity.py --lock`):

| Case | neural before | neural after | Tesseract (unchanged) |
|---|---|---|---|
| `ar-ocr` | all 1.0, CER/WER 0 (marks and separators wrong, not counted) | all 1.0, CER/WER 0; text as written | token 0.658, number 0.333, CER 0.242 |
| `ar-ocr-table` | token 0.984, segment 0.857, CER 0.003, WER 0.018 | all 1.0, CER/WER 0 | token 0.658, number 0.143, cells 0.556 |
| `en-ocr-degraded` | number 0.75 | number 1.0 | number 0.75 (3:75) |
| `en-ocr`, `en-ocr-table` | all 1.0 | all 1.0 | all 1.0 |

Neural qualified: 5 of 5 OCR cases (19 of 19 with the 14 text cases);
Tesseract 2 of 5 (16 of 19). Floors raised by the lock, none lowered. Gate:
`tests/test_document_ocr_reading_rules.py` (each rule with the old rule as
its control; end to end on the table page and the degraded scan, the old
rules re-run on the same model outputs). The worker image must be rebuilt
(`scripts/document_worker_image.py build`) for a container install to read
with these rules.

## 2026-10-15: documents refused on a strict install (no broker)

Found on the live install: `GET /api/documents/ops/readiness` answered
`degraded`, `worker_isolation.reason` "This install requires container
isolation ... set KAZMA_DOCUMENT_WORKER_ISOLATION=container with a document
worker broker". The owner had turned on `security.execution.container_required`,
documents ran in `process`, and no broker ran: the container profile needed
three `.env` lines, a worker image built by hand and a broker process run and
kept alive by hand. Every document was refused.

Done in the working tree (uncommitted), each with a behavioural test and a
negative control (AGENTS.md §19 E1, §39):

| Part | Now | Gate |
|---|---|---|
| The profile | `security.execution.document_worker_isolation` (Settings → Documents, admin; protected from `config_save`; never restored); the environment still wins; an unreadable setting refuses; a worker process never reads settings (`worker_process.mark()`) | `tests/test_document_worker_profile.py` (the env-only resolver as control) |
| Kazma asks the guard | `broker_supervision._publish_broker_request` at boot, on a change (store listener, own thread), every maintenance pass | same (no listener as control) |
| The guard runs the broker | `DocumentBroker` in `kazma_guard.py`: before Kazma, restart with backoff, health probes, quiet retry for a missing image / silent Docker, one page per failure episode and one on recovery, graceful stop at pause/shutdown/`--once`/crash, orphan reaped, scrubbed environment, owner-only token, state in the state file | `tests/test_guard_document_broker.py` (fake clock; real guard with a stand-in broker; the no-broker guard as control) |
| The image | `worker_image.py`: tag from every build input; `scripts/document_worker_image.py build/status/prune`; the broker serves the newest build while the tag is missing and switches by itself (exit 6) | `tests/test_document_worker_image.py` (the walk held to the `.dockerignore`; a dropped-fonts ignore list as control) |
| Readiness and pages | `worker_isolation.selection`, `.supervisor`, `.action` (code, message, command), `image_current`, `image_fallback`; the Documents page and Settings show them in English and Arabic; one ops alert per outdated build | `tests/test_document_worker_profile.py`, `tests/js/test_settings_document_profile.js` |
| Broker exit codes and stop | 2/3/4/5/6; SIGBREAK handled on Windows (the guard's CTRL_BREAK), also while attesting | `tests/test_document_worker_image.py` (the unhandled CTRL_BREAK as control) |

Measured on this machine (Docker Desktop 29.8.2, Windows 11), real guard, fake
Kazma server, real broker and worker image
(`tests/test_document_broker_guard_docker.py`): image build 37 s the first time on this engine (the engines' layers were cached from earlier builds; a build with nothing cached takes minutes), 4.2 s after a code change; the guard's broker ready **5.2 s** after it was started (attestation and engine report included); a real PDF parsed through the container in **1.25 s**; the broker killed, restarted by the guard and ready again in **9.3 s** (5 s of that is the first backoff); the next parse 1.30 s; Kazma not restarted; no container left behind. The broker's own qualification suite passed on the same image (22 tests). The image the guard found by itself for this checkout: `kazma-document-worker:src-2a009799cc0ce433`

Open: the live switch is the owner's (build, Settings, restart the guard once:
`docs/docs/ops/document-processing.md`, "Switch an install to the container
profile"); a Linux/systemd run of the guard's broker supervision (the POSIX
paths are written and unit-tested, not run on Linux here).
