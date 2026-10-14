# Kazma Documents Industrialization — Coding-Agent Handoff

Date: 2026-10-13  
Repository: `G:\GitHubRepos\kazma`  
Primary tracker: [`DOCUMENTS_INDUSTRIAL_HARDENING_GOAL.md`](DOCUMENTS_INDUSTRIAL_HARDENING_GOAL.md)

Last updated: 2026-10-13, F02 Docker profile follow-through (image engine readiness, strict profile, CI job).

## Mission

Continue the Documents-module audit and remediation until the code and its
operational profile are supported by reproducible evidence. Do not describe
the module as industrially ready while material containment, recovery,
fidelity, soak, or independent-review gates remain open. Work in small,
reviewable increments; update the tracker and evidence receipts with each
verified change.

## Important checkout state

This checkout contains extensive **uncommitted** work from the audit. Preserve
it. Do not reset, clean, stage, commit, or push unless the user separately
requests that. Read the repository `AGENTS.md` before editing; its security,
HITL, SQLite, ConfigStore, workspace, and document-specific invariants are
binding. In particular, keep tenant/actor/document/version/job scope intact,
preserve the durable admission and backup semantics, and do not use the local
SQLite fallback as evidence for a PostgreSQL deployment.

The user asked to resume the industrialization work after a PC crash and then
authorized continued work with “Proceed.” The broader work is in progress; the
last broad regression receipt predates the latest Windows worker change.

## Current verified state

- The audit has hardened PDF active-content handling, parser-worker preflight
  and salvage boundaries, remote egress intent and live policy checks,
  document ACL and identity handling, durable admission reservations and byte
  reconciliation, backup/restore behavior, coherent operation policy
  snapshots, and readiness reporting. See the primary tracker for evidence and
  limits; do not infer that every deployment profile is qualified from those
  code changes.
- Windows parser execution now waits behind a trusted stdin gate until the
  worker is assigned to a Job Object configured for memory, hard CPU-rate, and
  active-process-count limits. Job setup failure terminates the gated worker
  without releasing parser code. A subprocess trampoline is used because
  `os.execvpe` crashed on this Windows host.
- Unix still applies memory and CPU-time limits through `setrlimit`. A Unix
  process-count limit is not implemented. In this default `process` profile
  neither platform confines parser filesystem access or establishes a network
  jail; the `container` profile below does, through the Docker broker.
- Latest focused Windows verification: **51 passed** across sandbox,
  preflight, and document operations tests. Ruff F/I and `git diff --check`
  passed. A live smoke exercised no limits, memory, CPU-rate, process-count,
  and combined limits. Receipt:
  [`document-windows-worker-containment.json`](../../session-artifacts/document-windows-worker-containment.json).
- The latest broad regression before the Windows changes was **819 passed,
  14 skipped** across the filtered Documents set. Its receipt is
  [`document-policy-final-verification.json`](../../session-artifacts/document-policy-final-verification.json);
  rerun relevant gates after further source changes. The focused 51-test group
  overlaps and must not be added to the broad total.
- Microsoft documents that Job Object membership propagates to child
  processes by default and defines CPU hard-cap units. This is API behavior
  evidence, not proof of Kazma's full process-tree containment:
  [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects),
  [CPU-rate control](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_cpu_rate_control_information).

### F02 Docker deployment profile (implemented 2026-10-13)

- `KAZMA_DOCUMENT_WORKER_ISOLATION=container` routes both worker call sites
  (`service._run_parser_worker`, `operations._run_worker`) to
  `documents/worker_container.py`, which streams the request and the exact
  files it names as a digest-checked bundle (`documents/worker_bundle.py`) to
  the broker (`documents/worker_broker.py`, stdlib only). The broker alone
  runs the Docker CLI: one fixed image pinned by id, one fixed policy
  (`--network none`, no mounts, read-only root, noexec tmpfs, memory=swap, 1
  CPU, PID/nofile/core limits, `--cap-drop ALL`, `no-new-privileges`, uid
  10001, default seccomp, `--init`), a shared token, bounded concurrency,
  deadline/output-cap/disconnect kills, verified removal with slot quarantine,
  and a startup sweep. It attests the policy from inside a real job container
  (`documents/worker_entry.py probe`) and refuses to serve on any mismatch.
  The client fails closed (no fallback to `process`); readiness/health report
  `worker_isolation`.
- Images: `deploy/document-worker/Dockerfile` (hash-locked
  `requirements.txt`, no httpx/docling), `deploy/document-broker/Dockerfile`;
  topology: `deploy/docker-compose.document-broker.yml` (only the broker mounts
  the engine socket). `secrets/` is git- and docker-ignored.
- Evidence on Docker Desktop 29.8.2 / Windows 11: 98 unit tests, 19 real-Docker
  qualification tests (`KAZMA_TEST_DOCUMENT_WORKER_IMAGE`), a broker-container +
  socket-less client-container topology smoke (10/10 checks), repository and
  docs gates, and the broad Documents regression. Receipt:
  [`document-container-worker-profile.json`](../../session-artifacts/document-container-worker-profile.json);
  detailed logs under `session-artifacts/f02-container/`. Ops runbook:
  `docs/docs/ops/document-processing.md` (Isolated document workers); threat
  model: `docs/docs/security/document-security.md` (Layer 3).
- Two real defects were found by the new checks and fixed: import-time
  `data_dir()` created `/kazma-data` inside the image (the attestation refused
  it; the image now points `KAZMA_DATA_DIR` at its tmpfs), and the broker
  deadlocked when it ran `docker kill` on the thread that drains the job's
  stdout (an attached container whose stream backs up cannot be killed).

- Follow-through (same day): in the container profile the four readiness
  factories (`get_parser_registry`, `get_renderer_registry`,
  `get_mutation_registry`, `get_ocr_health`) answer from the image's own
  capability report (live views); readiness flags an image built from other
  document code (`image_code_matches`). `security.execution.container_required`
  now refuses the `process` profile for documents. CI job `Document worker
  container profile (real Docker)` builds, attests and qualifies on Linux
  Docker Engine (pending its first run; the owner adds it to the ruleset).
  Two pre-existing WeasyPrint renderer defects (removed `default_url_fetcher`
  import; missing asset folder reported as an external resource) were found
  by the container tests and fixed. Details and evidence: tracker, F02.

### F02 isolation-boundary findings (2026-10-13, before the implementation)

- `run_isolated_subprocess()` is the common launch choke point for the parser,
  renderer/mutation workers, and several OCR helpers. Put enforcement there or
  in an explicitly selected backend used by all those call sites; do not add a
  parser-only path that bypasses timeout/output/Job Object handling.
- The current parser request names the original source in the shared content
  store, passes the configured `storage_root` to `parser_worker`, and writes
  request/result files below `storage_root/parser-runs`. Therefore mounting
  only the run directory read/write would break parsing, while mounting the
  whole content root would expose other tenants/documents. A jailed profile
  needs per-run source staging, a per-run scratch `storage_root`, bounded disk
  accounting, and only the exact Python/runtime/package paths needed read-only.
  Verify extension/parser plugins, Tesseract, OCR model/cache use, and local
  salvage before finalizing that allowlist.
- **Windows:** Microsoft documents an `Experimental_CreateProcessInSandbox`
  API with AppContainer filesystem/network policy, but the API is explicitly
  experimental, Windows 11 only, dynamically resolved from
  `processmodel.dll`, and uses a FlatBuffer specification without a public
  header. Treat it as a compatibility spike, not a production foundation, until
  the supported Windows versions and API stability are established. If using
  AppContainer, test file ACL/package-SID access for the Python runtime and
  per-run files; default deny network and fail closed on launch failure. See
  [Microsoft API reference](https://learn.microsoft.com/en-us/windows/win32/secauthz/createprocessinsandbox)
  and [AppContainer isolation](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation).
- **Linux:** namespace-based containment can separate filesystem and network
  views, but availability depends on kernel/user-namespace policy and deployment
  capabilities. A helper such as bubblewrap must be explicitly detected and
  configured; test the actual locked-down Docker/host profile. Missing helper,
  namespace, mount, or network isolation must refuse the strict profile rather
  than silently run the existing subprocess. References:
  [network namespaces](https://man7.org/linux/man-pages/man7/network_namespaces.7.html),
  [mount namespaces](https://man7.org/linux/man-pages/man7/mount_namespaces.7.html),
  [user namespaces](https://man7.org/linux/man-pages/man7/user_namespaces.7.html).
- **Docker feasibility check:** In a disposable `python:3.11-slim` container,
  installing Bubblewrap and using Docker's default seccomp profile failed at
  namespace creation. With `seccomp=unconfined`, Bubblewrap could run after
  `/lib64` was included, but broad seccomp disablement is not an acceptable
  Kazma-container setting. Docker's own docs say the default profile blocks
  namespace creation and advise against unconfined seccomp; preserve that
  protection. Separately, a direct per-job container smoke succeeded with
  `--network none --read-only --tmpfs /tmp --memory=256m --cpus=1
  --pids-limit=16 --cap-drop ALL --security-opt no-new-privileges
  --user 10001:10001`: no Kazma data/home path was visible, an external socket
  connect returned Linux errno 101 (network unreachable), and `/tmp` remained
  writable. A separate root-UID run verified writes to the image root fail
  with errno 30 (`EROFS`) while `/tmp` is writable; this only validates
  Docker's primitive, not a Kazma parser job. Receipt:
  [`document-container-isolation-poc.json`](../../session-artifacts/document-container-isolation-poc.json).
  See [Docker seccomp](https://docs.docker.com/engine/security/seccomp/) and
  [container runtime options](https://docs.docker.com/engine/containers/run/).
- Do **not** mount `/var/run/docker.sock` into the main Kazma app container:
  Docker's daemon API is host-root authority. To productionize the Docker
  profile, use a dedicated, narrow worker broker/orchestrator that alone owns
  the engine connection. Kazma sends a bounded input/request stream; the
  broker starts a fixed worker image/entrypoint with no mounts, no network,
  read-only root, bounded tmpfs/resources, dropped capabilities, and bounded
  stdout/stderr, then returns only a bounded result stream. The broker must not
  accept arbitrary commands, image names, environment variables, mounts, or
  resource overrides from the app. Do not share the document volume with the
  job container; stage each job's source and scratch inside its ephemeral
  filesystem. Broker authentication, request identity, concurrency/disk
  quotas, cancellation/container deletion, and engine/API hardening still need
  design and tests.
- **macOS:** Apple's App Sandbox is entitlement-based, and embedded command-line
  helpers inherit their containing app's sandbox. Do not assume a plain
  `subprocess.Popen` call from Kazma's Python CLI gains that entitlement. A
  supported macOS profile needs a signed/entitled launcher or a separately
  qualified sandbox backend. See [Apple App Sandbox](https://developer.apple.com/documentation/security/app-sandbox)
  and [sandboxed command-line tools](https://developer.apple.com/documentation/security/protecting-user-data-with-app-sandbox).
- Repo README documents Linux, macOS, Windows, and Docker deployment. The
  industrial claim must name the exact profiles actually tested; it may be
  necessary to mark some native profiles unsupported for hostile-document
  parsing until their OS jail is available.

## Continuous run 2026-10-13 (F01-F10, Q01, Q02)

Items 1-7 below were executed in one run; the tracker holds the exact evidence
and every gate's negative control. Summary of what changed and what is still
open:

- **Recovery (Q01/F05/F08):** reservations record their owner and publication
  intent; failed intakes roll back their own rows; a start rolls back intakes
  of provably dead owners (or restored from a backup) and reports the rest as
  `stranded`. Crash matrix on SQLite and real PostgreSQL (44 cases). Open:
  cross-host takeover (needs fencing).
- **Faults (F07):** scanner outages, full disks and metadata outages (SQLite or
  Postgres) are retried, never rejected; a store error after a stage no longer
  ends the worker loop.
- **Job reads (F05):** required actor, uniform not-found, agent cancel checked,
  admin-only audit. Open by design: chat surfaces act as one `agent` principal.
- **Admission and fairness (F08):** answered before the Web body, spool slots,
  scratch under the storage root and swept, stage floor, Postgres quota, fair
  claims. Open: JSON-body routes in memory; per-process spool slots.
- **Backup (F06):** coordinated Postgres backup/restore with a measured drill
  (3.8 s fixture RTO). Open: knowledge index re-index after restore;
  representative-size RPO/RTO on deployment hardware.
- **Fidelity (F09/Q02):** versioned corpus, declared floors and release
  targets; Arabic PDF extraction repaired (numbers, ligatures, tables), HTML
  tables kept. 9/11 cases qualified; Arabic PDF dates and Arabic OCR are not.
  Open: a real-world corpus.
- **Certification (F10):** reports the configured profile and backends; real
  fidelity and backup runs.
- **Still open, not runnable here:** Q03 (48-72 h soak), Q04 (independent
  review), F02's Linux Docker Engine run (first CI run of the new job).

The worker image must be rebuilt for this build's document code (readiness
reports `document_worker_image_outdated` until it is); CI builds it fresh.

### Same day, continued: the buildable gaps

- **Cross-host takeover (Q01):** built. An owner that cannot be proven dead
  renews a lease; a lapsed lease is taken over and the stale owner is fenced
  at its job (the publication), cleans its own token rows, and the sweep
  removes what it left. Real-child drills on SQLite and PostgreSQL with
  negative controls (`tests/test_document_intake_lease.py`). Legacy rows
  without a lease stay operator-only.
- **Re-index after restore (F06):** every worker start reconciles the
  knowledge libraries with what the store records as indexed, under the
  per-document publication lock; `scripts/document_pg_backup.py index`.
- **Representative restore (F06):** 1,000 documents / 62.5 MiB on PostgreSQL
  16.15: backup 1.14 s, restore 1.39 s, first document readable 2.45 s after
  the restore began, drain about 0.9 jobs/s on this machine.
- **JSON bodies (F08):** bounded at 2 MiB before parsing.
- **Fidelity (F09/Q02):** Arabic PDF qualified (the "reversed date" was the
  extraction's reading of a correct bidi layout; numbers are now read as the
  bidi algorithm draws them); OCR numbers get the same rule; corpus v2
  with long, two-column, wide-table and degraded cases: 15/17 qualified.
  Arabic OCR and degraded-scan numbers are not, with the measurements of what
  was tried.
- **Left by decision, with reasons:** per-chat-user identity (one operator
  per install); weighted fairness and cross-replica spool slots (one operator
  per install, per-process slots bound the disk they protect); native OS jails
  and the Unix process cap for the `process` profile (platform work needing
  review: the container profile is the qualified boundary).

## Remaining work, in priority order

1. **F02 — real parser containment.** Design and implement supported,
   fail-closed filesystem and network isolation for each claimed OS profile.
   Also prove CPU/memory/process-tree/output/time limits, cancellation, and
   cleanup using descendant-spawning, timeout, setup-failure, and crash tests.
   Document the exact deployment requirements and explicit degraded behavior.
   Do not call a scrubbed environment or subprocess boundary a filesystem or
   network sandbox. Keep remote document egress separate and consent/audit
   guarded.
2. **F01 — PDF policy coverage.** Verify one bounded, fail-closed policy is
   applied on intake and every mutation/export path. Add or complete inert
   nested, indirect, chained-action, malformed, and valid-navigation fixtures;
   assert traversal budgets and no bypass across call sites.
3. **F03/F04 — policy and egress qualification.** Finish the external-facing
   transport matrix and real PostgreSQL coverage for live revocation and
   immutable tenant/actor/document/version/job intent. Include concurrent
   services, audit-store outage, failed uploads, attachments, and provider
   fallback attempts.
4. **F05/F08/Q01 — recovery and admission.** Done on 2026-10-13 (process
   death at every stage, early HTTP admission, scratch budgets, fairness,
   lease takeover with fencing, on SQLite and PostgreSQL; the stale owner in
   a real other container, frozen or partitioned, opt-in
   `tests/test_document_intake_lease_containers.py`). Remaining: a run on
   real separate machines.
5. **F06 — coordinated backup and restore.** Qualify PostgreSQL metadata,
   jobs, admission reservations, content blobs, manifests, ACLs, and indexes as
   one recoverable state. Measure representative restore RPO/RTO; the small
   SQLite drill is not a deployment target. Measured since on PostgreSQL:
   1,000 and 10,000 documents (`tests/test_document_pg_restore_drill_scale.py`,
   see the tracker); deployment hardware not yet.
6. **F07 — durable error transitions.** Prove typed permanent/transient
   failures survive queue transitions, retry finite times, and dead-letter
   correctly under real fault conditions.
7. **F09/Q02 — fidelity thresholds.** Build a versioned English, Arabic, and
   bilingual corpus. Measure OCR CER/WER, tables and numbers, reading order,
   omissions, citation provenance, and rendered-layout fidelity. Declare
   release thresholds before claiming qualification.
8. **F10/Q03/Q04/Q05 — operations and evidence.** Report actual configured
   backend/engine/profile/version; run a representative 48–72 hour mixed-load
   soak with restarts and faults; record throughput, p95/p99, resource use,
   RPO/RTO, and cleanup. Obtain an independent security review, remediate its
   findings, and align user, security, ops, and certification documentation.

## Working and verification rules

- First inspect `git status`, the tracker, `AGENTS.md`, and the relevant code
  before changing files. Existing user changes are intentional unless proven
  otherwise; do not discard them.
- Keep tests under the repository's low-priority process policy. The project
  runner is `scripts/fast_test.py`; for narrow tests, use a lowered-priority
  pytest process as in the latest verification receipt. Do not use xdist or
  start/stop the live Kazma server. Use disposable, resource-capped containers
  for real PostgreSQL checks and remove them after use.
- Run focused tests for the changed boundary, applicable broader integration
  tests, compilation/lint as appropriate, and `git diff --check`. Preserve
  failure logs and receipts; never count a crashed, incomplete, stale, or
  skipped backend run as passing evidence.
- Update `DOCUMENTS_INDUSTRIAL_HARDENING_GOAL.md` with current state and exact
  evidence. Refresh source hashes in receipts after edits. Keep open gates open
  until their stated evidence exists; no numeric readiness score should
  replace the qualification record.
- Do not deploy, alter the live install, create commits, or publish changes
  without a separate user request.

## Immediate next action

1. **Owner:** add `Document worker container profile (real Docker)` to the
   `KazmaLatestRule` required checks once its first CI run is green. Two
   out-of-scope gaps were raised as separate tasks: the acquisition-alert e2e
   file runs in no CI job, and `acquisition.monitor_settings` has no
   settings-restore rule (the committed tree fails that gate).
2. **Q03:** run a 48-72 hour mixed-load soak on a supported profile (container
   workers, Postgres metadata) with restarts and the fault injections of
   `tests/test_document_crash_recovery.py` /
   `tests/test_document_fault_transitions.py`; record throughput, p95/p99,
   resources, RPO/RTO and cleanup.
3. **Q02:** corpus v3 covers long, two-column, wide-table, degraded-scan and
   OCR ruled-table cases; one real scan is measured opt-in
   (`tests/test_document_real_scans.py`, manifest outside the repo). Add more
   real documents (handwriting, forms, merged-cell tables, other producers).
   Arabic-Indic digits under OCR need another engine: tessdata_best `ara`
   was measured and gives no real gain (the owner decides on an engine).
4. **Q04:** independent security review of the hostile-document profile.
5. Keep native Windows/macOS/bare-Linux `process` profiles unqualified for
   hostile parsing until their own OS jail exists (Unix process-count cap
   still open).
