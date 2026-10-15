---
title: Document Processing Operations
---

# Document Processing — Operations & Scale

Operational surface for the
[document intelligence platform](../guide/document-intelligence.md)
(Phase 9+ residuals). Everything is driven by the shared
`DocumentIngestionService`; there is no second parser or parallel write path.

**Related:** [API routes](../reference/api-routes.md#documents--document-intelligence) ·
[Document security](../security/document-security.md) ·
[Phase map](../guide/document-phases.md) ·
[Production checklist](./production-checklist.md) ·
[Smoke matrix](./smoke-matrix.md) ·
[Document Intelligence product guide](../guide/document-intelligence.md)

---

## PDF parse / OCR readiness (operators)

Electronic PDFs use a **scored multi-engine bake-off** (PyMuPDF primary →
optional pypdfium2 → pdfplumber → pypdf). Scanned / empty / presentation-form
layers route through isolated **Tesseract** OCR (`ara+eng` auto order). Full
product detail: [Document Intelligence → PDF text extraction](../guide/document-intelligence.md#pdf-text-extraction-arabic--multi-engine).

| Check | Healthy signal |
|---|---|
| `GET /api/documents/health` (the Documents page's capability list) | PDF parser **ready** when PyMuPDF or pdfplumber is installed |
| pypdfium2 / pypdf only | Parser **degraded** (text only — tables not advertised) |
| Arabic electronic PDF | Logical-order tokens; IR metadata `extractor` / `extraction_score` |
| Arabic scan | The neural OCR engine (`documents.ocr.engine`: `auto` uses it once installed, below), else Tesseract + `ara` traineddata on PATH (Arabic-Indic digits not read) |
| After `kazma update` | CLI imports (`kazma serve` works); see [Kazma Update](./kazma-update) |

Deploy deps: `pip install -e ".[document-platform]"` (pymupdf + pypdfium2) and
system **Tesseract** with `eng` + `ara` packs when OCR is required. In the
[container profile](#isolated-document-workers-container-profile) these are
the worker image's engines, not the host's: `/api/documents/health` then
reports what the image has.

**Tables on scanned pages** are read cell by cell (`documents.ocr.tables`,
default on). A ruled grid on the page is found from its lines, each cell is
read on its own, and a number cell Tesseract reads as junk is read again as
digits. The table becomes one table block, cells right to left for an Arabic
table. It costs about one Tesseract call per cell (a 33-row contents page
took ~20 s); at most 600 cells and 120 s a page, after which the page keeps
its own reading. Tables without ruling lines, tables narrower than about a
fifth of the page and cells merged across rules keep the page's reading.
Measured on a real 300 dpi scan of an Arabic booklet: a contents table's page
numbers went from 22 % to 91 % read, and from 3 % to 56 % of its rows came
back complete on one line. **Arabic-Indic digits** (١٢٣) are not read by
Tesseract's Arabic model, fast or best, at any page segmentation and in
either bundled font; European digits are. The neural engine reads them.

### The neural OCR engine (Arabic and Arabic-Indic digits)

`documents.ocr.engine` (Settings → Documents, validated on every write):
`auto` (default) reads scanned pages with the neural engine when it is
installed here (or in the worker image, in the container profile) and with
Tesseract otherwise; `neural` is the same and says on the document when
Tesseract had to read it; `tesseract` never uses the neural engine.
`/api/settings/documents` and `/api/documents/health` say which engine reads
pages (`selected_engine`) and why the neural one cannot.

Install it on a host (the `process` profile):

```bash
uv pip install -e ".[ocr]"            # onnxruntime, opencv-python-headless, pyclipper, numpy
python scripts/ocr_models.py install  # about 240 MB into <data dir>/models/ocr
python scripts/ocr_models.py verify
```

`install` downloads the four pinned models (each checked against its size
and SHA-256), converts EasyOCR's two PyTorch recognizers to ONNX (checked
against PyTorch on fixed inputs) and writes a `manifest.json`. The conversion
needs PyTorch once; without it in this interpreter the script runs it through
`uv run --isolated` (CPU wheels on Linux), so Kazma's own environment gains
nothing. `KAZMA_OCR_MODEL_DIR` moves the model directory. The document worker
uses the models only when they match the manifest, and downloads nothing.
The directory is not backed up or carried by `kazma migrate` (it is
re-installed).

In the **container profile** the worker image carries the engine: its
`ocr-models` build stage (`deploy/document-worker/ocr-models.txt`, hash-locked,
CPU PyTorch) runs the same installer, and only the models reach the final
image (`/opt/kazma/ocr-models`, read-only). The image's capability report
names both engines; rebuild the image to update the models.

Measured on 2026-10-15 (CPU only; numbers in
`docs/plans/DOCUMENTS_INDUSTRIAL_HARDENING_GOAL.md`, Q02): on the fidelity
corpus the clean Arabic page went from token recall 0.66 / CER 0.24 / numbers
1 of 3 (Tesseract) to every word and number (CER 0); on a 12-font set of Arabic
lines with numbers, numbers read 27 % -> 74 % clean and 28 % -> 69 % as a poor
scan; on the real scan (five pages of an Arabic booklet), cover-page terms
30 % -> 90 %, the contents table's page numbers 91 % -> 100 % and its rows
56 % -> 75 % complete, while three text pages lost terms (85 -> 80 %,
100 -> 94 %, 86 -> 71 %) and one page the single number Tesseract read. It
costs time and memory: on one core (the worker's limit) about 15 s for
an Arabic text page and 30–45 s for a page with ruled tables, against 2–8 s,
and about 800 MB of the worker's 1 GiB. Near the worker's time limit
(`documents.workers.timeout_seconds`, 300 s) the remaining pages are read by
Tesseract and the document's warnings say from which page: raise the limit
(about 45 s a scanned page) to keep a long scan on the neural engine.

---

## Day-2 operator surfaces

| Surface | Use |
|---|---|
| Web `/documents` | Upload, library, detail, ops panel (capacity, readiness, audit, GC) |
| Settings `/settings?tab=documents` | Where untrusted documents run (process or container), rollout, intake, execution limits, storage floor, OCR (and its table pass), malware, GC, indexing |
| `GET /api/documents/health` | Parser, OCR, renderer and PDF-mutator readiness, isolation profile, policy revision |
| `GET /api/documents/ops/*` | Metrics, capacity, readiness, retention, audit (audit is admin-only) |
| `POST /api/documents/ops/maintenance/{dry-run,run}` | Admin GC |
| `python scripts/certify_documents.py` | Cert smoke / `--soak` |
| `python scripts/document_pg_backup.py` | Postgres store: backup, verify, restore; knowledge-index comparison (`index`) |
| `python scripts/document_fidelity.py` | Extraction fidelity corpus against its floors |
| `python scripts/document_worker_image.py` | The container profile's worker image for the code on disk: `build`, `status`, `prune` |

---

## Saved and effective policy

A default running coordinator reads a coherent ConfigStore snapshot at each
operation boundary. Intake limits, parser/OCR/render budgets, capacity limits,
index chunking and retention/GC settings apply to the next operation. Concurrent
and in-flight operations retain their admitted immutable policy; nested calls
and worker threads share that snapshot. A worker stage reads policy when it
starts, and waiting jobs are scanned again under that stage's malware policy.
Remote parse denial is additionally checked immediately before each provider
upload, including during an operation already in progress.

These constructed-resource settings require coordinator reconstruction (normally
a service restart): `storage_root`, `quotas.tenant_bytes`, worker `concurrency`,
`lease_seconds`, `heartbeat_seconds`, `retry_base_seconds` and
`retry_max_seconds` (all under `documents`; worker fields under `workers`). They
keep their installed effective values until then. Changing the root does not
move or migrate existing data. Backup and migration use the installed CAS root,
including during a settings outage, so a pending root change cannot redirect
them. Offline calls validate saved policy without creating a coordinator. The
health/readiness `policy` object reports
`restart_required`, per-field `pending_restart` configured/effective values, and
SHA-256 `configured_revision` / `effective_revision` fingerprints. An intake
version stores `intake_policy_revision`; a normalized manifest stores its parse
`policy_revision`. Retry-attempt limits are persisted when a job is created;
changing `documents.workers.max_attempts` (default 3; the older key
`documents.worker.max_retries` is still read) applies to new jobs. A fingerprint identifies
the validated values, not a signed
policy or a deployment certification.

An unavailable or invalid live policy refuses new execution with
`document_policy_unavailable`; HTTP surfaces return **503** and `Retry-After`.
The rollout guard refuses writes on policy failure. The operations panel clears
previously green status when a refresh fails. Explicit `DocumentConfig` injection
is a fixed override for tests and callers; it does not become a live binding.
Default-only configuration reads remain available to compatibility/diagnostic
callers, while execution requires the strict snapshot. This is not yet a full
multi-replica settings or deployment qualification.

---

## Metrics

Document metrics are emitted through the standard Prometheus exposition at
`/metrics` (no new telemetry dependency; degrades to no-ops when
`prometheus-client` is absent). A numeric, content-free snapshot is also
available at `GET /api/documents/ops/metrics`.

| Area | Metrics |
|---|---|
| Intake | `kazma_documents_intake_files_total{outcome}`, `…_intake_bytes_total`, `…_intake_rejections_total{reason}` |
| Queue | `…_queue_depth`, `…_queue_oldest_age_seconds`, `…_active_leases`, `…_retry_waiting`, `…_dead_letter_current`, `…_dead_letter_total` |
| Stages | `…_stage_total{stage,outcome}`, `…_stage_latency_seconds{stage}` |
| Parser/OCR | `…_parser_total{parser,outcome}`, `…_pages_total{kind}` |
| Sandbox | `…_sandbox_terminations_total{reason}` (timeout/oom/output/degraded) |
| Storage | `…_storage_logical_bytes`, `…_storage_physical_bytes`, `…_storage_dedup_ratio` |
| Indexing | `…_index_latency_seconds`, `…_index_chunks_total` |
| Generation | `…_generation_failures_total{operation}`, `…_redaction_failures_total` |

The **gauges** are set only when the ops routes are read: the queue gauges by
`GET /api/documents/ops/metrics` or `GET /api/documents/ops/capacity`, the
storage gauges by `GET /api/documents/ops/metrics` (the Documents page reads
both when it opens and after its actions). Nothing else refreshes them, so a
scrape of `/metrics` alone shows their last values; a monitoring setup that
alerts on them should also poll `GET /api/documents/ops/metrics`. The counters
and histograms are updated as work happens.

**Per-tenant quota is a query API**, not a Prometheus label (unbounded tenant
label cardinality is unsafe). Read it from the metrics snapshot
(`tenant_quota`) or `GET /api/documents/ops/capacity`.

**Logs/traces** carry only correlation ids (tenant/workspace/document/version/
job/attempt/parser) — never document content, filenames, redaction terms, or
secrets. Optional OpenTelemetry spans are emitted when `opentelemetry` is
installed; otherwise they no-op.

---

## Operational audit

Every operator/tenant-facing action (intake, access, index, generate, convert,
mutate, redact, download, cancel, retry, delete, GC) is appended to an
immutable, tenant-scoped audit trail (`document_audit_events`). On SQLite it
lives in `documents.db`; on Postgres metadata it uses the same table name in
the shared pool. Both refuse an update and any delete but the retention
sweep's (triggers on SQLite; `document_audit_guard` on Postgres, since
2026-10-13; see [Document security](../security/document-security.md#layer-6-audit-immutability)).
It complements — does not duplicate — the per-job stage event
history. Read a page with
`GET /api/documents/ops/audit?limit=&before_id=&document_id=&event_type=`. The
route is **admin-only** (401 without a session, 403 for other roles): the
trail names every document, job and actor of the tenant, including documents
the caller cannot read. Details are allowlisted to safe scalars; content never
enters the audit.

---

## Backpressure, capacity, and rate limits

Intake is refused with a truthful status when a limit is hit; the capacity
refusals (429/503/507 and `intake_in_progress`) also carry `Retry-After`:

| Limit | Status | Config key |
|---|---|---|
| Storage free-space floor | **507** | `documents.capacity.storage_free_floor_bytes` |
| Global queue ceiling | **503** | `documents.capacity.max_queued_jobs` |
| Per-tenant queued cap | **429** | `documents.capacity.max_tenant_queued_jobs` |
| Per-tenant active cap | **429** | `documents.capacity.max_tenant_active_jobs` |
| Intake rate / bytes window | **429** | `documents.capacity.intake_rate_per_minute`, `…intake_bytes_per_minute` |
| Capacity authority unavailable | **503** | Queue/tenant reads or required free-space measurement failed |
| Uploads being received at once (per process) | **503** `uploads_busy` | `documents.capacity.max_concurrent_uploads` (default 4) |
| Declared length past the intake limit | **413** `intake_too_large` | `documents.intake.max_bytes` (default 50 MiB) |
| Tenant storage quota | **413** `quota_exceeded` | `documents.quotas.tenant_bytes` (default 10 GiB) |
| Durable writes switched off | **503** `document_platform_disabled` | `documents.enabled` |
| Required malware scan cannot run | **503** `malware_scanner_unavailable`, before the body is read | `documents.security.malware_scan` = `on`, or `documents.security.malware_fail_closed` |
| Intake outlived its lease and was taken over | **409** `intake_taken_over` | `documents.intake.lease_seconds` (default 300) |

The Web upload (`POST /api/documents`) asks the budget limits -- the storage
floor, the queue and tenant caps, the rate window, the declared length, the
platform switch and whether a required malware scanner can run -- **before it
reads the request body**: a refusal costs the client one request, not the
upload. The storage floor counts the incoming file (free space minus the
declared size). The body is then spooled under the storage root
(`.upload-staging/`), where the floor measures it and garbage collection
removes what a crash leaves; the service re-checks everything at intake, and
the malware scan and the tenant storage quota are applied there.

Every document route answers an error code with the same status
(`documents_api._STATUS_BY_CODE`): a refusal the server's state causes --
writes off, no scanner, no permitted worker profile -- is a 503 with
`Retry-After` on every route. Until 2026-10-13 writes switched off answered
422 on the action routes and a missing required scanner 400, after the body.

The JSON-body routes (`/import`, `/generate`, `/merge`, `/convert`, `/split`,
`/fill-form`, `/redact`, `/index`, `/unindex`, `/search`, and `/{id}/delete`
whose body is optional) read their body
through a 2 MiB bounded reader: a declared length past it is refused unread and
an undeclared one is cut off as soon as it passes (**413**
`request_too_large`); malformed JSON, a body that is not an object, or nesting
too deep for the parser is a **422** `invalid_json`.

Durable ingestion reserves queue slots and the tenant's file/byte window in
the job database before reading its source. SQLite serializes short reservation
transactions with `BEGIN IMMEDIATE`; PostgreSQL locks a shared admission row.
Pending streams count against queue and tenant admission caps. Unsized streams
reserve the configured maximum intake size; declared lengths must match before
blob publication. Successful reads reconcile the reservation to actual bytes.
The window survives service restarts. Verified idempotent replays consume no new
admission credit, including when the queue is full. Manual job retries also
reserve capacity and require write access to a live document.

`GET /api/documents/ops/capacity` reports pending reservation counts and bytes,
with machine-readable `degraded_reasons`. An unavailable reservation database
refuses intake with **503**. An already reserved key returns **409** with
`intake_in_progress`; a different request using it returns
`idempotency_conflict`. Request keys are limited to 256 characters.
Replay looks up the stored tenant/key pair, preserving older job IDs. New IDs
encode tenant, key and object kind as distinct values so delimiter-containing
tenant names and keys cannot alias each other.

Reservations never expire by age alone. Each one records its owner (install,
host, process id and start time) and, before any metadata write, the document
and version ids it publishes; every row it creates carries its token. An
intake that fails rolls back its own rows. When the service starts, and every
minute while its workers run, it rolls back the unfinished intakes of owners
that are **provably dead** -- same install and host, the process gone or its
id reused -- and of reservations restored from a backup.

An owner that cannot be proven dead -- an intake started on **another host**,
or a process this one cannot inspect -- holds a **lease** while it runs: it
renews it every quarter of `documents.intake.lease_seconds` (default 300 s,
recorded on the reservation, so every replica judges it by the owner's
setting). A reservation whose lease has lapsed is taken over within a minute
and rolled back, and its capacity freed. The old owner may only be partitioned
or paused, so it is **fenced**: the job that publishes an intake is created
only while the reservation is still its own, so a taken-over intake can never
publish. If it wakes, its upload fails with `intake_taken_over` (409) and
nothing is saved (retry it); it removes the rows it wrote, and the sweep
removes any it left, for a day after the takeover.

A reservation written by a build without the lease (no heartbeat) is never
taken over: it stays held and counts as `admission.stranded` in
`GET /api/documents/ops/capacity`, and readiness reports
`stranded_intake_reservations` with the reason
`document_intake_reservations_stranded` (a lapsed lease is counted there too
until the sweep takes it over; a remote intake renewing its lease is not).
Clear a legacy one only after confirming its publisher is gone.

Crash recovery is qualified by a matrix that kills a real process after each
intake stage (reservation, stored bytes, publication intent, blob, document,
version, job) and inside each job stage (parse, normalize, index, verify), on
SQLite and on real PostgreSQL: the next start releases the reservation, removes
what the dead intake published, takes over expired leases and finishes every
job once (`tests/test_document_crash_recovery.py`). The lease is qualified by
a real intake process given another host's identity and no working renewal,
paused, taken over and resumed, on both backends: it is fenced, removes what it
wrote, and the sweep removes what it wrote and could not remove
(`tests/test_document_intake_lease.py`).

Workers claim fairly across tenants: the next job comes from the tenant with
the fewest jobs running, then by when the job became ready (a retry waits its
turn). PostgreSQL metadata enforces the tenant blob quota inside the
publishing transaction. PostgreSQL race tests require an explicitly configured
test database; a SQLite adapter does not qualify PostgreSQL concurrency.

Default free-floor is **512 MiB**; on a production host consider ≥ 1 GiB
(Settings → Documents or ConfigStore). Parse stages check the floor too: a
stage that would start below it is retried later (`storage_full`), never run
into a full disk.

---

## Retention and garbage collection

Retention is live ConfigStore-backed (`documents.retention.*`, `documents.gc.*`).
The collector is a crash-safe **mark/sweep** with the metadata database
(SQLite or Postgres) as the sole authority:

- Reclaims orphan/unreferenced blobs, post-promotion quarantine copies,
  expired-tombstone content, terminally-failed (rejected/dead-letter) version
  content, orphan blob rows, orphan manifests, and aged-out audit rows.
- **Never** deletes referenced blobs, current-version content, or artifacts
  (content-addressed dedup preserved).
- Honors a grace period, bounds deletions per run
  (`documents.gc.max_deletions_per_run`), and refuses to follow or delete
  through a symlink/junction or outside the store root.

GC also removes scratch a crash left behind -- upload spools
(`.upload-staging/`), staged copies (`.stage/`), and `parser-runs/` /
`operation-runs/` directories -- once past the grace period, and never deletes a
blob or manifest pinned by a running backup (`.backup-pins/`).

A scheduled loop runs three minutes after start and then every
`documents.gc.interval_hours` (default 6) while `documents.gc.enabled`,
`documents.gc.auto_maintain` and durable writes are on (cancelled cleanly on
shutdown). Operators can **dry-run then confirm** from the Documents page or via
`POST /api/documents/ops/maintenance/dry-run` and `…/run` (admin-gated).

**Postgres metadata:** CRUD is built for several replicas (not qualified as a
deployment: see [Multi-replica readiness](#multi-replica-readiness)) and GC
runs on both backends — `retention._mark` dispatches to `repository.gc_mark`, which
`repository.py` and `repository_pg.py` each implement. (Earlier releases skipped
GC on Postgres with `gc_postgres_metadata_sql_port_pending`; that guard has been
removed and a test asserts it does not come back.)

---

## Isolated document workers (container profile)

The default `process` profile runs parsers in a local child process with
resource limits but **no filesystem or network isolation**. For documents you
do not trust, run the container profile: every parse, OCR, render and
mutation job then runs in a fresh, attested container with no network and no
mounts. The security model is in
[Document security — Layer 3](../security/document-security.md#layer-3-isolated-execution).

### Choose the profile

**Settings → Documents → Where untrusted documents run** (admin only):
*Local process* or *A container per job*. The setting is
`security.execution.document_worker_isolation`. It is under `security.`, so
the agent's config tools cannot change it, and a settings restore never
changes it. `KAZMA_DOCUMENT_WORKER_ISOLATION` in the environment still wins
when it is set; the page then shows the switch disabled and says why.

When the install requires containers (`security.execution.container_required`,
the strict execution profile), the local process profile refuses every
document (`worker_isolation_required`). The Settings page says so above the
switch and offers **Use the container profile**.

A setting that cannot be read refuses documents (`document_policy_unavailable`,
retried): it never falls back to the less isolated profile.

### Native install: the guard runs the broker

On an install the guard supervises (`scripts/service/kazma_guard.py`, the
`KazmaAgent` task), nothing has to be run by hand and `.env` needs nothing:

- **Kazma asks, the guard runs it.** When the container profile is chosen,
  Kazma writes a request into `.kazma/document_broker.request.json` (at boot,
  within a second of the Settings change, and on the 15-minute maintenance
  pass). The guard starts the broker within a second, and stops it when the
  profile goes back to *process*. The request outlives restarts, so after one
  the guard starts the broker **before** Kazma, and it attests while Kazma
  boots.
- **The guard hands Kazma the address.** It starts every server with
  `KAZMA_DOCUMENT_BROKER_URL` (a loopback port it keeps for the install),
  `KAZMA_DOCUMENT_BROKER_TOKEN_FILE` (`.kazma/document_broker.token`, made once
  by the guard, readable by its account only, never logged) and
  `KAZMA_GUARD_BROKER_REQUEST_FILE`. A `.env` that names another broker wins;
  the guard then runs none.
- **The image for this code.** The broker asks for
  `kazma-document-worker:src-<16 hex>`, a tag derived from everything the
  image is built from (the Dockerfile, its requirements, all of `kazma_core`).
  While that tag is not built it serves the newest image the build script
  made, and stops to switch (exit 6, restarted at once) as soon as it is.
- **Kept running.** A broker that exits is restarted (5 s, 15 s ... 5 min); one
  that stops answering is restarted after three missed probes. A missing image
  or a Docker engine that does not answer is checked again every 30 s, quietly.
  The guard pages once when the broker keeps failing (three failed starts in a
  row) and once when Docker has not answered for 10 minutes, and once more
  when it is back. A missing or outdated image is Kazma's ops alert instead
  (`documents.worker_image_outdated`, once per build, with the command).
- **Maintenance.** A pause (`--pause`, `kazma update`) stops the broker with
  Kazma; the next start starts it again. A broker whose code or image changed
  is restarted when Kazma is.
- **What it records.** `guard.log` has `broker.*` events like the guard's own;
  the broker's output is `.kazma/document_broker.log`; the guard's state file
  holds `document_broker` (state, pid, image, restarts, last exit), which
  readiness reads; `kazma_guard.py --status` shows it.

The broker runs as the account that runs the guard, with an environment
reduced to what Python, Windows and the docker CLI need (the guard's own
variables, its Telegram token included, stay out). It listens on loopback
only; the token is the only authentication there.

#### Switch an install to the container profile

In the install folder, with the install's Python (`.venv\Scripts\python.exe`
on Windows):

1. **Build the worker image** for the code on disk. The first build takes a
   few minutes (the engines), later ones seconds:

   ```powershell
   .venv\Scripts\python.exe scripts\document_worker_image.py build
   ```

2. **Choose the container profile**: Settings → Documents → *Use the container
   profile* (or the switch, then **Save**).

3. **Restart the guard once**, as below -- only because the guard that runs
   today started before it could run the broker. The Documents page and
   `kazma_guard.py --status` say when this is needed.

#### Restart the guard

A reload (`kazma_guard.py --reload`) restarts Kazma, never the guard's own
code: the guard is the `KazmaAgent` task's process and lives from boot to
boot. To run a newer guard, when no chat turn runs:

```powershell
.venv\Scripts\python.exe scripts\service\kazma_guard.py --pause --stop --when-idle --reason "guard update"
schtasks /End /TN KazmaAgent
schtasks /Run /TN KazmaAgent
.venv\Scripts\python.exe scripts\service\kazma_guard.py --resume
```

The pause has the old guard stop Kazma gracefully and hold; the new guard
starts holding the same pause, and the resume starts Kazma, which asks for
the broker. Run the two `schtasks` lines from an elevated terminal if a
normal one is refused. A reboot does what the two `schtasks` lines do.

From then on: switching the profile is the Settings page alone, a code update
is the usual `kazma_guard.py --reload --when-idle`, and after an update the
Documents page (and the ops alert) asks for step 1 again when the image is
older than the code -- the broker keeps serving the older image until it is
built, then switches by itself.

The guard's own settings, in its environment (the `KazmaAgent` task's, not
`.env`): `KAZMA_GUARD_DOCUMENT_BROKER` (`auto` follows Kazma's request, `on`
always runs one, `off` never does), `KAZMA_GUARD_BROKER_PORT` (pin the port),
`KAZMA_GUARD_BROKER_IMAGE` (pin an image; no fallback then).

`python scripts/document_worker_image.py status` says which tag this code
needs, which are built, and what the guard's broker serves;
`... prune` removes older builds (never this code's or the one serving).

### Deploy (Docker Compose)

```bash
docker build -f deploy/document-worker/Dockerfile -t kazma-document-worker:local .
```

```bash
mkdir -p secrets
python -c "import secrets; print(secrets.token_urlsafe(48))" > secrets/document_broker_token
```

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.document-broker.yml up -d
```

`secrets/` is ignored by git and by every image build. The overlay builds and
adds the `document-broker` service (`deploy/document-broker/Dockerfile`) — the
only container that mounts `/var/run/docker.sock`, itself with no network, a
read-only root and no capabilities — and sets
`KAZMA_DOCUMENT_WORKER_ISOLATION=container`, `KAZMA_DOCUMENT_BROKER_URL`
(a unix socket on a volume the two share) and
`KAZMA_DOCUMENT_BROKER_TOKEN_FILE` on `kazma`. The token must be at least 32
characters. Never mount the engine socket into `kazma` itself.

### A broker run by hand

Without the guard (a developer's server, another supervisor), run
`python -I kazma-core/kazma_core/documents/worker_broker.py --image <tag>
--token-file <file> --listen tcp:127.0.0.1:8765` under an account that may
use Docker (add `--fallback-to-newest-built` to serve the newest build while
`<tag>` is missing), and set `KAZMA_DOCUMENT_BROKER_URL=tcp://127.0.0.1:8765`
and `KAZMA_DOCUMENT_BROKER_TOKEN_FILE=<file>` for Kazma. The token is the
only authentication on TCP: the broker listens on loopback unless started
with `--allow-non-loopback`. Its exit codes: 0 asked to stop, 2 could not
start, 3 the policy did not hold or the image reported no engines, 4 no
worker image, 5 Docker does not answer, 6 the image asked for appeared.

### Check it

- `python -I kazma-core/kazma_core/documents/worker_broker.py --image <tag>
  --token-file <file> --attest-only` prints the attestation and the image's
  engine report (exit 0 = the policy holds and the image reported its
  engines, 3 = it does not, with each problem named, 2 = the broker could not
  start).
- `GET /api/documents/ops/readiness` → `worker_isolation`: `mode`, `ready`,
  `filesystem_isolation`, `network_isolation`, `hostile_document_profile`
  (`container_broker` when ready; `unqualified` for the `process` profile),
  `image_code_matches`, `image_current` (the image serving was built from
  this checkout), `image_fallback` (an older build serves), `broker` (image,
  image id, limits, `attested`, `attested_at`, `problems`, `active`,
  `quarantined`), `selection` (which profile and what chose it: the
  environment, the setting or the default), `supervisor` (who runs the
  broker -- the guard, an older guard, another broker, nobody -- and the
  guard's own record of it) and `action`: the one step that fixes what is not
  right (`code`, an English `message`, and the `command` to run), or null. A
  container profile that is not ready -- or the `process` profile while
  `security.execution.container_required` is on -- adds
  `document_worker_isolation_unavailable` to `degraded_reasons`.
- The Documents page shows the same: the profile, the broker's state, an
  older image serving, and the step with its command (both languages).
- The broker logs one line per job: worker, status, exit code, output bytes,
  `cleanup=verified|failed` and duration.
- **Engine readiness comes from the image.** At start the broker also asks the
  image which parsers, renderers, PDF mutators and OCR languages it has, and
  refuses to serve without that report. In the container profile Kazma's
  `GET /api/documents/health` and every operation's engine check read that
  report, never the host's own packages: a host without LibreOffice or
  WeasyPrint does not refuse what the image can do, and an engine the image
  lacks reads unavailable with the image's reason.
- **A stale image is visible.** The image reports a fingerprint of its
  document code, and the build script labels it with the fingerprint of
  everything it was built from. `image_code_matches: false` or
  `image_current: false` (and the readiness reason
  `document_worker_image_outdated`) means the image was built from other code
  than this Kazma runs: `python scripts/document_worker_image.py build`.

### Failures and what they mean

| Code | Meaning | Action |
|---|---|---|
| `worker_isolation_unavailable` | Broker unconfigured, unreachable, unattested, token refused, or the run was lost | Readiness names the step (`worker_isolation.action`); the job is retried within the queue's finite budget |
| `worker_isolation_busy` | Every broker slot was busy for the admission wait | Raise `--concurrency` (and the host's capacity) or let the retries absorb the burst |
| `worker_isolation_invalid` | `KAZMA_DOCUMENT_WORKER_ISOLATION` is neither `process` nor `container` | Fix the setting; every run is refused until then |
| `worker_isolation_required` | `security.execution.container_required` is on and documents run in the `process` profile | Settings → Documents → *Use the container profile* (or remove a `KAZMA_DOCUMENT_WORKER_ISOLATION=process` from the environment) |
| `document_policy_unavailable` | The profile setting (or the execution policy) could not be read | Retried; the settings store's own outage is reported elsewhere |
| `document_engine_unavailable` | The worker image itself lacks the engine for this operation (its own report says why) | Rebuild the worker image with the engine; the host's packages do not count |
| `worker_output_invalid` | The worker returned a stream that breaks the protocol (names, sizes, digests) | Permanent; treat the document as hostile |
| `parser_oom` | The job container was killed by its memory limit | Retried within budget; raise `--memory-mb` only if legitimate documents hit it |
| `parser_timeout` / `parser_output_limit` | Deadline or output cap hit; the container was killed and removed | As in the process profile |

`cleanup=failed` in the broker log means a job container could not be
removed; its slot stays out of service until a later attempt removes it, and
readiness reports `quarantined`.

### Sizing

Broker limits are fixed when it starts (`--memory-mb` 2048, `--cpus` 1,
`--pids` 256, `--tmpfs-mb` 768, `--concurrency` 2, `--max-timeout` 600 s,
`--max-input-mb` 640, `--max-output-mb` 320); Kazma cannot raise them.
`/tmp` holds a job's inputs, scratch files and output, and its pages count
against the memory limit, so `--tmpfs-mb` must cover the largest merge
(`documents.intake.max_files` × `documents.intake.max_bytes`) plus its
output, and `--memory-mb` must exceed `--tmpfs-mb` by the parser's working
memory. Each job pays a container start and removal (0.35–0.8 s on the
qualification host).

### Qualification

CI's `Document worker container profile (real Docker)` job builds both
images, attests the policy, runs the qualification suite
(`tests/test_document_worker_container_docker.py`, with
`KAZMA_TEST_DOCUMENT_WORKER_IMAGE` naming the image; a skip fails the job)
and the compose-shaped topology
(`python scripts/document_broker_topology.py <worker image> <broker image>`).
Run the same two commands on a deployment host to qualify it. On a native
install, `tests/test_document_broker_guard_docker.py` (same variable) runs
the real guard and broker: a real PDF parsed through the container, the
broker killed and brought back by the guard, and nothing left behind.

### Rebuild and rotate

Native install: `python scripts/document_worker_image.py build` after an
update; the guard's broker switches to the new image within a minute, no
restart. To rotate the token, delete `.kazma/document_broker.token` and reload
Kazma: the guard makes a new one and restarts the broker. Compose: the broker
pins the worker image by id at start, so after rebuilding the worker image,
restart `document-broker`; the token file is read per run on the Kazma side
and at start on the broker side: write the new token, then restart
`document-broker`.

## Malware scanning

Intake runs ClamAV when configured (`documents.security.malware_scan` =
`auto`/`on`/`off`, default `auto`), and every parse stage scans the original
again under the policy of the moment. The probe appears on readiness
(`malware`) and Settings → Documents.

A scan is **required** when the mode is `on` or
`documents.security.malware_fail_closed` is on -- its default is on with
`KAZMA_PRODUCTION=1`, so a production host without `clamscan`/`clamdscan`
refuses uploads. When a required scan cannot run, readiness reports
`malware_scanner_unavailable` with `accepting_durable_writes: false`, a new
upload is refused (`malware_scanner_unavailable`), and a job already queued is
held and retried within its finite budget (`documents.workers.max_attempts`)
and then dead-lettered -- an outage is never recorded as unsafe content. Under
`auto` without fail-closed, a missing or failing scanner is logged and the
document is accepted unscanned. See
[Document security](../security/document-security.md).

---

## Backup

The native backup (every 6 hours) snapshots `documents.db` **first**
(`sqlite3.backup()` → point-in-time) when metadata and jobs are both on SQLite,
then copies the content it references and verifies database, blob and
copied-manifest checksums. Verification derives required references from the
snapshot independently of the checksum inventory; missing required bytes or an
invalid inventory fail the backup. Retention counts verified snapshots (the
scheduled job keeps the five newest), so a failed attempt does not displace a
good recovery point. Backups land under
`kazma-data/backups/document-store-<ts>-<unique-id>/` with a `manifest.json`.
For SQLite jobs, durable validation events identify promoted originals as the
required execution source. Redundant quarantine copies may already have been
collected. A restore revalidates an available immutable original when intake
needs validation again.

There is no restore command for a SQLite store: with Kazma stopped, copy the
backup directory's contents into an **empty** storage root (the configured
`documents.storage_root`, by default `kazma-data/document-store`) and start.
Queued work resumes, and ACLs and job history come back with the documents;
this is the drill `tests/test_document_backup_restore_hardening.py` runs, and
`scripts/certify_documents.py` repeats it for one document
(`backup_restore_sqlite`).

**The backup follows the catalog** (`documents/backup.py`,
`installed_document_backends`, since 2026-10-13): where the document METADATA
is decides the path, read from the running service or, in a CLI process with
none, from the configured backends.

- **SQLite metadata beside Postgres jobs** (`KAZMA_DOCUMENTS_METADATA_BACKEND=sqlite`
  on a Postgres install) is snapshotted like a SQLite store, from
  `documents.db`. Whether a version's source is needed from `quarantine` or
  `originals` is decided by the job events, which are in Postgres then: they
  are read from the queue after the snapshot (promotions only accumulate, so
  that is a safe superset) and kept in the backup's `manifest.json`
  (`promoted_versions`, `jobs_backend: "postgres"`) so a later verify reads
  the same. The queue itself is not in this backup; it is in the shared
  Postgres dump (`pg_shared_*`). Before, the scheduled job took this shape to
  the coordinated backup below because the jobs were on Postgres, which read a
  catalog that was not there (or copied tables an earlier configuration left).
- **Postgres metadata beside SQLite jobs** (only after a fallback: the
  queue's Postgres repository could not start) cannot be held by any backup
  at one point in time and is refused by name
  (`document_backup_backend_unsupported`).
- A backup is written under a hidden `.…partial` name and renamed into
  place only when it is complete; a failed run leaves nothing that looks like
  a backup (it used to leave an empty `document-pg-*` folder).

A fallback SQLite database never stands in for a Postgres catalog: the
migration tools take the same path by the catalog, see [Migration](#migration).

**A PostgreSQL store** (metadata and jobs on Postgres) is backed up by the same 6-hour job through the
coordinated backup (`kazma_core.documents.backup_pg`), as one point in time:
a `REPEATABLE READ` transaction exports its snapshot, reads which blobs and
manifests the document tables reference inside it, pins them so garbage
collection keeps them, and `pg_dump --snapshot` dumps the document tables as
that snapshot saw them; the referenced files are then copied and checksummed.
Backups land under `kazma-data/backups/document-pg-<ts>-<id>/` with a
`manifest.json` (snapshot time and WAL position, per-table row counts,
checksums). The snapshot time is the recovery point: anything written after it
is not in that backup.

```bash
python scripts/document_pg_backup.py backup --dest <dir> [--storage-root <dir>]
python scripts/document_pg_backup.py verify <backup dir>
python scripts/document_pg_backup.py restore <backup dir> --target-url <database URL> --storage-root <empty dir>
```

`pg_dump`/`pg_restore` are found as for the other Postgres backups: on PATH,
or through `docker exec` into the database container (`KAZMA_DB_CONTAINER`).
The scheduled job keeps the five newest verified `document-pg-*` backups; a
manual `backup --dest` prunes nothing.

A restore verifies the backup first, and goes only into a database that has
**none of the document tables** yet (an empty one, or one holding only Kazma's
other tables) and an **empty** storage root; it refuses anything else. It
restores in one transaction that stops at the first error, re-checks every
table's row count against the snapshot, expires the leases of the dead source
and marks its unfinished intakes as restored so the next start rolls them
back. Then point the install at the restored store and restart: queued and
interrupted jobs resume.

**Restoring both Postgres backups into one database** (drilled:
`tests/test_document_pg_restore_order.py`). The shared Postgres dump
(`pg_shared_*.dump`, see [Disaster recovery](./disaster-recovery.md)) also
contains the document tables, but not as of the moment the content files were
copied. Restore in this order:

1. The shared dump (`python scripts/pg_backup.py restore ...`) into the new
   database: Kazma's other tables, and document tables that are too old.
2. The coordinated backup with `--replace-document-tables`
   (`python scripts/document_pg_backup.py restore <backup dir> --target-url <url> --storage-root <empty dir> --replace-document-tables`):
   after the backup verifies and the storage root is found empty, it drops the
   dump's document tables in one transaction and restores its own.

The other orders fail, as the drill's negative controls show: without the
option the coordinated restore refuses a database that has document tables;
and the coordinated restore first, then the shared dump, puts the dump's older
document rows back over it (`--clean`), so a document ingested between the
two backups keeps its files and loses its catalog row.

The knowledge index (library chunks in the settings store, vectors in Chroma)
is a separate store and not in this backup; the restore lists the indexed
documents under `reindex_required`. Every start of the document workers
compares the two and **re-publishes** each version the document store records
as indexed but its library does not hold, from the version's own manifest, as
the document's owner. A library that does not exist on this install, a
version that is no longer the document's current one or a missing manifest is
reported in the log, never invented. This happens on either metadata backend,
whenever the knowledge stack is available. To see the comparison, or to remove
chunks of documents the store does not record as indexed (deleted, unknown or
unindexed since), run with Kazma stopped (`--tenant <id>` limits it to one
tenant):

```bash
python scripts/document_pg_backup.py index                    # report only
python scripts/document_pg_backup.py index --apply             # re-publish what is missing
python scripts/document_pg_backup.py index --apply --prune     # ... and remove what is not recorded
```

Measured on real PostgreSQL 16.15 in a 1-CPU, 512 MiB container on Windows 11:

| Store | Backup | Verify | Restore | Start | First document readable | Backlog drain |
|---|---|---|---|---|---|---|
| 3 documents (`tests/test_document_pg_backup_restore.py`) | 0.19 s | -- | 0.24 s (database) | -- | 1.05 s after start | every job ready 3.8 s after the restore began |
| 1,000 documents, 62.5 MiB (`tests/test_document_pg_restore_drill_scale.py`) | 1.14 s (66.8 MB) | 0.48 s | 1.39 s | 1.05 s | 2.45 s after the restore began | 20 jobs in 22.5 s (about 0.9 a second) |
| 10,000 documents, 625 MiB, filled by 6 writers (2-CPU, 3 GiB container) | 9.64 s (664 MB) | 5.17 s | 10.67 s | 1.08 s | 11.77 s after the restore began | 20 jobs in 23.1 s |

Backup, verify and restore grow with the store (about linearly from 1,000 to
10,000 documents); starting the restored service does not. Filling the store
with several writers at once also showed that parallel service starts on a
fresh database deadlocked on schema setup; schema setup now takes no table
lock it does not need (`kazma_core/db/pg_schema.py`).

These are this machine's numbers, not a deployment RTO: the recovery point
is the last successful backup's snapshot (6-hourly by default), and draining
a restored backlog takes as long as processing it did. Run the drill on your
own store's size and hardware: set `KAZMA_TEST_RESTORE_DRILL_DOCUMENTS`
(and `KAZMA_TEST_RESTORE_DRILL_KIB`, `KAZMA_TEST_RESTORE_DRILL_WRITERS`,
`KAZMA_DRILL_REPORT`) with a real PostgreSQL test database.

The intake lease is drilled with the stale owner in a real other container
(`tests/test_document_intake_lease_containers.py`, set
`KAZMA_TEST_CONTAINER_LEASE_DRILL_IMAGE` to an image holding Kazma's
dependencies): frozen with `docker pause` or cut off the network, it is taken
over once its lease lapses on the database's clock and is refused when it
returns; nothing it wrote is published. It starts and removes its own
network, PostgreSQL and containers.

---

## Migration

`kazma migrate export|verify|import` carries the document store as its catalog
requires (since 2026-10-13, the same rule as the backup; the CLI reads the
configured backends, as it has no running service):

- **SQLite catalog:** `documents.db` (when present) + the content-addressed
  tree + manifests. `documents.db` has no embedded paths, so it needs no
  rewrite. Beside a Postgres queue the promotions are read from the queue, and
  the queue travels in `data/postgres.dump`.
- **Postgres catalog:** the catalog travels in `data/postgres.dump`, taken
  first; the exporter then copies every content file and manifest that
  catalog references (a superset of the dump's: blobs are immutable and
  written before their rows). The bundle used to carry none of them: the
  exporter read the empty local `documents.db`.
- **Postgres catalog beside a SQLite queue** (a fallback): refused by name.

The importer puts the files into the target's document-store root after the
other stores, and installs `documents.db` only when the target's catalog is on
SQLite. A referenced file missing at export marks the bundle incomplete and
`verify` refuses it. A document store that fails to restore on import **fails
the import** (`ok: false`, the error in `errors`, the staged files kept); it
used to be a warning beside a successful import. See [Migration](./migration.md).

---

## Multi-replica readiness

| Component | Multi-replica | How |
|---|---|---|
| Job queue | Yes (Postgres) | Any Postgres install (`KAZMA_DB_BACKEND=postgres` or a `postgresql://` `KAZMA_DATABASE_URL`) + `jobs_pg.py`; override with `KAZMA_DOCUMENTS_JOBS_BACKEND=sqlite` |
| Intake admission | With the job queue | Reservations, leases and the takeover fence live in the job database (`documents/admission.py`) |
| Metadata | Yes (Postgres) | `KAZMA_DOCUMENTS_METADATA_BACKEND=postgres`, or `auto`/unset on a Postgres install (auto follows the job queue); `repository_pg.py` |
| Metadata | No (SQLite) | A SQLite install, or `KAZMA_DOCUMENTS_METADATA_BACKEND=sqlite`: one app replica |
| Blobs | Shared volume | All replicas must see the same `documents.storage_root` tree |
| Upload spool slots | Per process | `documents.capacity.max_concurrent_uploads` counts one process's uploads |
| GC | Backend-aware | Metadata repository supplies authoritative live references on SQLite and Postgres |

These are what the backends support. No multi-replica document deployment has
been qualified: `scripts/certify_documents.py` reports `postgres_multi_replica`
and `metadata_multi_replica` as **NOT RUN**, and the rest of Kazma runs as one
runtime (see [Multi-replica & SaaS](./multi-replica-and-saas.md)).

Always read `GET /api/documents/ops/readiness`:

```json
{
  "status": "ready|degraded|disabled",
  "jobs_backend": "postgres|sqlite",
  "jobs_multi_replica": true,
  "metadata_backend": "postgres|sqlite",
  "metadata_multi_replica": true,
  "multi_replica": true,
  "degraded_reasons": [],
  "policy": { "mode": "live", "restart_required": false, "pending_restart": {} },
  "malware": { "available": true, "scanner": "clamdscan", "mode": "auto", "fail_closed": false },
  "worker_isolation": { "mode": "process", "ready": true, "hostile_document_profile": "unqualified" },
  "stranded_intake_reservations": 0,
  "accepting_durable_writes": true
}
```

`status` is `degraded` when a configured backend is not the one serving
(`jobs_postgres_unavailable_fell_back_to_sqlite`,
`metadata_postgres_unavailable_fell_back_to_sqlite`) or when the service adds a
reason of its own: `document_worker_isolation_unavailable`,
`document_worker_image_outdated`, `document_intake_reservations_stranded`,
`malware_scanner_unavailable`. It is `disabled` (with `underlying_status`) when
durable writes are switched off (`durable_writes_disabled`). A backend chosen in `.env` — SQLite
metadata beside Postgres jobs, the common single-server shape — is `ready`
with `metadata_multi_replica: false`; `multi_replica` is true only when both
jobs and metadata are. (That shape has no coordinated document backup: see
[Backup](#backup).)

---

## Certification & soak

```bash
python scripts/certify_documents.py
python scripts/certify_documents.py --soak --soak-iterations 100
python scripts/certify_documents.py --output report.json
```

The legacy `--soak` option selects an extended bounded smoke. It does not
establish multi-day endurance: that gate stays **NOT RUN**. The report names
what is configured now -- the worker isolation profile (`process` is
`CONDITIONAL` and never reported as confined; `container` passes only when the
broker is ready, attested and runs this build's code), the worker image and the
metadata/jobs backends -- and its checks are real runs: the hostile corpus, a
bounded performance smoke, the extraction-fidelity corpus against the release
targets (`extraction_fidelity`), and a SQLite backup, verification and restore
of one ready document (`backup_restore_sqlite`). Parent Python allocations and
sampled native RSS of the process tree are reported; sampling is not a hard
peak measurement. The CLI does not qualify multi-replica metadata, a
48–72 hour mixed-load recovery soak, or independent security review.

The soak itself has a harness, `scripts/document_soak.py`: a real ingestion
service under a steady upload mix (the fidelity corpus and small texts), a
service restart every `--restart-every` seconds and an intake killed
mid-publication every `--crash-every` seconds, with throughput, intake-to-ready
p50/p95/p99, process-tree RSS and what is left pending or in scratch in its
`--report`. It runs on private SQLite stores unless `--use-env` is given, and
fails when a job does not finish, a reservation stays pending or scratch
outlives the run. Only a 2-minute pilot has been run (13 of 13 ready, 3
restarts, 2 injected crashes); the 48–72 hour run is still open.

```bash
python scripts/document_soak.py --duration 172800 --report soak.json
```

### Extraction fidelity

```bash
python scripts/document_fidelity.py          # measure and compare to the floors
python scripts/document_fidelity.py --lock   # write today's numbers as the floors
```

A versioned corpus (EN, AR and bilingual documents rendered by Kazma's own
DOCX, PDF and HTML engines, plus OCR of a rasterized page) is parsed by the
real parser and measured: word and segment recall (a segment's words must come
back together), reading order, exact numbers, table cells, honest page claims,
paragraph alignment, OCR CER/WER. Version 2 adds long documents (English and
Arabic, many pages), two columns laid out by another producer (MuPDF), wide
tables of wrapping cells, and a degraded scan (rotated, blurred, speckled,
JPEG-compressed); version 3 adds an OCR page with a ruled table, in English
and Arabic. `tests/fixtures/documents/fidelity_thresholds.json` declares
the floors and the release targets; the cases that meet the targets are listed
as `qualified`. On 2026-10-13, 16 of 19 cases met them. Not qualified: Arabic
OCR, with or without a table (Tesseract `ara` misreads Arabic-Indic digits and
skips two lines its layout analysis drops; CER 0.23) and numbers OCR'd from
the degraded scan (3.75 read as 3:75). Since 2026-10-15 the OCR cases are
measured per engine: with the neural engine all five OCR cases meet the
targets (19 of 19); with Tesseract the three above still do not. The corpus is generated: handwriting,
forms and merged-cell tables are not represented.

**Real scans** are measured opt-in (`tests/test_document_real_scans.py`):
`KAZMA_TEST_REAL_SCANS_MANIFEST` names a manifest kept outside the repository
-- each scan's path and SHA-256, the pages to measure, the numbers, symbols and
single terms each page holds (never its text), searches that must find their
page, and the floors this build reached. The scan goes through the real
intake, OCR, indexing and search. Without the manifest the test skips.

Pytest: `tests/test_document_certification_phase10.py` (architecture, crash,
a11y, rollout). Report history: `docs/audits/AUDIT_DOCUMENT_CERTIFICATION.md`.
