---
id: document-intelligence
title: Document Intelligence
sidebar_label: Document Intelligence
description: End-to-end document processing — ingest, parse, OCR, index, generate, and redact documents through isolated workers, with what is and is not qualified stated.
---

# Document Intelligence

Kazma's document platform ingests, parses, OCRs, indexes, generates, converts
and redacts documents through one orchestration layer, with isolated workers,
an append-only audit trail and live configuration. What has been measured and
what has not is listed under [What is not qualified](#not-qualified).

**Quick links:**

- [Phase map (0–10)](./document-phases.md) — what each build phase shipped
- [Tools catalog](../reference/tools-catalog.md) — agent-facing `document_*` tools
- [Document processing ops](../ops/document-processing.md) — metrics, backpressure, GC, backup
- [Document security](../security/document-security.md) — threat model and security architecture
- [API routes](../reference/api-routes.md#documents--document-intelligence) — `/api/documents/*`
- [Slash commands](../reference/slash-commands.md#documents) — `/documents` / `/docs`

---

## Architecture

```
User Input (file upload / workspace import / tool call / slash / TUI)
    │
    ▼
DocumentIngestionService  ◄── tenant + actor ACL, capacity gate, audit
    │                         (durable public boundary for Web/API/tools/gateway/TUI)
    ▼
Durable Job Queue  ◄── CAS transitions, leases, retries, dead-letter
    │
    ▼
DocumentService  ◄── single *execution* boundary (sniff / parse / OCR / render)
    │                 every step below runs in an isolated worker
    │                 (process or container profile)
    ├─ Sniff (MIME, OOXML defenses, PDF active-content policy)
    ├─ Parse (one parser per format)
    ├─ OCR  (per-page quality routing, the neural engine or Tesseract, ruled tables cell by cell)
    ├─ Index (structural chunking → Knowledge library — explicit action)
    ├─ Generate / Convert (render workers)
    └─ Mutate / Redact (verified redaction pipeline)
```

**Two layers, one path for durable work.**

| Layer | Class | Who calls it |
|---|---|---|
| **Durable coordinator** | `DocumentIngestionService` | Web `/api/documents/*`, native `document-platform` tools, gateway `/documents`, TUI Documents panel (through the Web API) |
| **Execution engine** | `DocumentService` | Ingestion workers only for durable jobs; **also** chat attachment transient parse via gateway `attachments.py` |

Neither the gateway nor the UI import parser/OCR/renderer **internals**
(`documents.parsers`, `documents.ocr`, workers). Chat may call `DocumentService`
for a best-effort fenced excerpt of an attached file — that is intentional and
does **not** create a second durable store.

**Auto-index is off by default.** A successful parse lands the document at
`ready`. Publishing into a Knowledge library is an **explicit** index action
(API / tool / UI).

---

## Core features

### Extra folders (path grants)

Outside-workspace paths are **denied by default**. To open a folder with permission:

1. **Chat (smooth):** when a file tool fails, the agent calls `request_path_access`
   (HITL approval card). On approve, a **session grant** is created for that
   folder and the agent retries the tool.
2. **Workspace page / API:** Workspace → **Folders outside the workspace**
   lists, adds (read only, or read and write) and removes the durable list
   `workspace.extra_roots` (`GET/PUT /api/workspace/extra-roots`: `path`,
   `mode`: `read`|`write`, `label`). A new folder must be a full path to an
   existing folder, not a whole drive, and not one holding Kazma's own
   install or data (its keys, its stores); the page says which rule a
   refused path broke.

Session grants TTL ~1 hour; durable roots persist until removed. Read grants
do not allow writes. See `kazma_core.workspace.path_policy`.

### Secure intake

- **Document platform limits (defaults):** 50 MiB per file, 10 files per request
  (`documents.intake.max_bytes`, `documents.intake.max_files`).
  Aliases `documents.intake_max_bytes` / `documents.intake_max_files` are accepted.
- **Chat gateway attachments (separate surface):** 20 MiB per file, 10 files,
  50 MiB aggregate — keeps prompt payloads bounded.
- **Admission before the body:** the Web upload is answered before a byte of
  its body is read (declared length, writes disabled, free-space floor counting
  the incoming file, queue, tenant caps, rate window); see
  [Backpressure](#backpressure). The ten JSON routes (generate, convert, index,
  search, …) read their body through a 2 MiB bounded reader: a larger body is a
  413, malformed JSON a 422.
- **Content-addressed immutable storage:** SHA-256 deduplication; the upload
  lands in quarantine and is promoted to the immutable originals store once
  validation (the sniff, run in the worker) passes.
- **MIME sniffing before parsing (inside the worker):** extension/content
  mismatch rejection, OOXML structural defense (XXE, macros, external
  relationships, nested archives, compression bombs), and one PDF
  active-content policy (`documents/pdf_policy.py`). Refused PDFs: encrypted
  ones, script (JavaScript, XFA forms), actions that run, open, submit or fetch
  outside the document (Launch, remote GoTo, SubmitForm, ImportData,
  renditions, rich media, sound, movie), embedded files, and links whose target
  is not an absolute `http`, `https` or `mailto` URI. Navigation inside the
  document and web links stay. No setting turns these refusals off. The same
  policy runs on every mutation input and output and on every rendered PDF.
- **All LLM-visible content is fenced:** Platform reads use
  `<kazma:data source="document" untrusted="true">…</kazma:data>`.
  Chat attachment excerpts may use `source="document_attachment"`.

### Isolated parsing

Every parse, OCR, mutation and render runs in a worker outside the Kazma
process. `KAZMA_DOCUMENT_WORKER_ISOLATION` selects the profile
(`documents/worker_container.py`):

- **`process` (default):** a host subprocess (`python -I`) with a scrubbed
  environment (parent secrets stripped), the worker timeout
  (`documents.workers.timeout_seconds`, the process tree is killed) and output
  caps. On Windows a Job Object holds the memory limit
  (`documents.workers.memory_mb`), a CPU-rate limit and an active-process cap
  (32); the worker waits behind a
  launch gate until they are applied, and a failed setup stops it before the
  parser starts. On Unix the limits are memory and CPU time (`setrlimit`); there
  is no process-count cap. This profile has **no filesystem or network
  isolation**: readiness reports `hostile_document_profile: unqualified`.
- **`container`:** every run goes to the document worker broker
  (`documents/worker_broker.py`), the only holder of the Docker socket, which
  runs it in a fresh container of one pinned image: no network, no mounts,
  read-only root, a size-bounded tmpfs, memory/CPU/PID limits, all capabilities
  dropped, `no-new-privileges`, a non-root user. The broker checks this policy
  from inside a job container before it serves. Inputs and outputs travel as
  digest-checked bundles. A broker that is unconfigured, down, unattested or
  busy refuses the run (`worker_isolation_unavailable` /
  `worker_isolation_busy`, retried by the queue); there is no fallback to
  `process`. Which engines exist is the image's own report, and readiness
  flags an image built from other document code (`document_worker_image_outdated`).
  The image does not include Docling, so local salvage is not part of this
  profile.
- Any other value refuses every run (`worker_isolation_invalid`). The setting
  `security.execution.container_required` refuses the `process` profile
  (`worker_isolation_required`).

Crashes, timeouts and missing output fail closed: no partial data leaves the
worker. Resource failures (timeout, out of memory, a killed worker, no result)
are retried within the queue's finite budget and then dead-lettered.
Containers share the host kernel; what the container profile has and has not
been qualified on is in
[Document security](../security/document-security.md#layer-3-isolated-execution).

### PDF text extraction (Arabic + multi-engine)

Electronic (text-layer) PDFs are **not** OCR’d by default. The parser runs a
**scored multi-engine bake-off** and keeps the best IR:

| Order | Engine | Role |
|---|---|---|
| 1 | **PyMuPDF** | Primary — logical-order Arabic, tables, multi-column layout |
| 2 | **pypdfium2** (optional) | PDFium peer in the score bake-off (text-only; capability is DEGRADED without PyMuPDF/pdfplumber so tables are not advertised) |
| 3 | **pdfplumber** | Strong tables; Arabic may reverse if used alone |
| 4 | **pypdf** | Text-only last resort |

- **Scoring:** `score_document_extraction` prefers clean Unicode, penalises
  Arabic presentation forms and `(cid:N)` dumps, and applies a small rank
  bonus (PyMuPDF > pypdfium2 > pdfplumber). Strong scores short-circuit.
- **Layout:** multi-column / legal pages use PyMuPDF `dict` geometry
  (`parsers/pdf_layout.py`) — columns top→bottom, LTR or RTL when
  Arabic-dominant. Single-column keeps plain text (better continuous Arabic).
- **Right-to-left lines are re-read glyph by glyph** (`parsers/pdf_rtl.py`).
  PyMuPDF reverses such a line character by character, which turned numbers
  and ligatures around ("١٢٣٤٥٦٧" read "٧٦٥٤٣٢١", "في" read "يف") and reversed
  Arabic table cells. Glyphs are read in drawing order and only right-to-left
  runs are reversed, as whole glyphs. Each number cluster gets the reading that
  the bidi algorithm (`arabic.visual_order`) draws as the page shows it, so a
  date written "في 2026-09-30" comes back as written although the page draws
  it "30-09-2026". An RTL table's columns are read right to left. A gap wider
  than half a font size is a word break.
- **Metadata:** `extractor`, `extraction_score`, `extractors_tried`, per-page
  `layout.method` / `column_count`.
- **Round-trip generate→parse:** RTL titles use fuzzy token coverage (glyph
  noise tolerant), not “any 20 characters”.

Install: `pip install -e ".[document-platform]"` (includes `pymupdf` +
`pypdfium2`). Without PyMuPDF, Arabic electronic PDFs may reverse under
pdfplumber alone.

**Hard-PDF salvage:** if the native score is weak, **Docling**
(`pip install -e ".[docling]"`) runs inside the bounded parser worker when
`documents.security.local_salvage` permits it (default on). **LlamaParse /
Reducto** run from the Kazma process, never the worker, and only with explicit
`documents.security.remote_parse` consent (default off) and a provider key. Keys alone never authorize upload. Each attempt
requires a durable audit with the job's tenant, actor, workspace, document and
version; consent is checked again before each provider attempt. Transient
attachment excerpts have no durable egress scope and cannot upload remotely.
API keys never enter the worker. Providers are tried in order: a failed one is
audited as a failure and the next configured one is tried, after the live
policy is read again. The provider client ignores ambient proxy variables,
follows no redirects and refuses an answer over 32 MiB
(`remote_parse_response_limit`). Additional vetoes: `KAZMA_DOCLING=0`,
`KAZMA_REMOTE_PARSE=0` (they can turn a tier off, never on). Salvage Markdown
has synthetic segments, explicitly labelled without physical source-page
citations. In the `container` profile there is no Docling tier.

### Multilingual OCR

- **Per-page quality routing:** Pages with high text density can skip OCR
  (native text preserved); image-heavy / text-poor pages, high presentation-form
  / CID-mangled layers, and empty text layers route to OCR.
- **Two engines** (`documents.ocr.engine`, Settings → Documents):
  - `auto` (default) reads with the **neural engine** when its packages and
    models are installed, else with Tesseract; `neural` does the same and says
    on the document when Tesseract had to read it; `tesseract` uses Tesseract
    only.
  - The **neural engine** (`documents/ocr/neural.py`) is the one that measured
    best on Arabic and Arabic-Indic digits (2026-10-15): PaddleOCR PP-OCRv5
    text detection, EasyOCR's Arabic recognizer (`arabic_g1`: Arabic and Latin
    letters, both digit sets) with `english_g2` for lines without Arabic, and a
    vote on letters with PaddleOCR's PP-OCRv5 Arabic recognizer and
    Tesseract's words (two readers agreeing, or a confident PaddleOCR word,
    replace EasyOCR's; EasyOCR's own second reading of a letter it doubted
    counts as a reader; a number and a word's punctuation are always
    EasyOCR's). EasyOCR writes no harakat: where it wrote a hamza and another
    reader of the same letters saw a tanween there ("ريالاً"), the tanween is
    kept. A line's last mark is decided by two readers: EasyOCR says that a
    mark is there, Tesseract's reading of the same word says which (EasyOCR's
    English model often reads a final "." as ":"); a mark only Tesseract
    reads is never added. Specks smaller than the line's own pen are removed before a line is
    read, and a "," or "." between Arabic-Indic digits is put back as the
    Arabic separator ٫ or ٬ when a number's grammar decides it (before
    exactly three digits it stays as read: thousands, or three decimal
    places). All four models run on ONNX Runtime inside the document worker;
    a drawn line is put in logical order with the PDF reader's rule, never a
    recognizer's own. All the models are Apache-2.0.
  - **Cost:** about 15 s for an Arabic text page and 30–45 s for a page with
    ruled tables on one core (the document worker's limit), against 2–8 s with
    Tesseract; about 800 MB of memory in the worker (inside its 1 GiB). Near
    the worker's time limit (`documents.workers.timeout_seconds`, 300 s) the
    remaining pages are read by Tesseract and the document says from which
    page; raise the limit to keep long scans on the neural engine.
- **Language selection:** Configurable language pack list (default includes
  English + Arabic when installed). Auto order is **`ara+eng`** when both are
  configured — `eng+ara` often misreads pure-Arabic scans as Latin gibberish.
- **Deterministic merging:** Native and OCR text are merged with
  **similarity-based** rules (`SequenceMatcher` thresholds + confidence), not
  pure geometric coordinate fusion. Known-bad native layers (presentation forms /
  CID) are **replaced** when OCR confidence is acceptable. OCR blocks may still
  carry TSV pixel coordinates for downstream use.
- **Fallback:** Large pages rasterized at configurable DPI (200 default,
  72–600). OCR stays in the isolated worker (`apply_ocr`) — the PDF parser
  never shells out to Tesseract directly.
- **Ruled tables read cell by cell** (`documents/ocr/tables.py`,
  `documents.ocr.tables`, on by default, switch in Settings → Documents).
  Tesseract's whole-page reading keeps neither the rows nor most numbers of a
  ruled table. The pass finds the grid from the ruling lines and reads each
  cell on its own; a cell with no letter or a doubtful reading is read again as
  digits only (when `eng` is installed), and that reading wins only when it is
  confident. Only ruled grids are found: a table without ruling lines, a narrow
  table and merged cells that span rules keep the page's own reading. The pass
  is bounded to 600 cells and 120 s per page; past either, or when a cell cannot
  be read, the page keeps its reading and a warning names the page. It costs
  time: on the 300 dpi scan measured on 2026-10-13 a page took about 8 s
  instead of 2.
- **Arabic word order:** a number OCR reads from an Arabic line is put back in
  logical order with the same rule as PDF text (`arabic.number_in_logical_order`
  for Tesseract's words, `parsers.pdf_rtl.logical_line` for the neural engine's
  lines).
- **Qualified with the neural engine:** Arabic OCR of a clean page (`ar-ocr`:
  every word and number, CER 0). With Tesseract Arabic OCR is not qualified:
  `ara` does not read Arabic-Indic digits in any font or model measured and
  skips some lines. Not qualified with either: the Arabic page with a ruled
  table (one word misread by the neural engine), numbers read from degraded
  scans, Arabic decimals and dates written with the Arabic separators (the
  recognizer has no `٫` and rarely reads `/` between Arabic-Indic digits). See
  [What is not qualified](#not-qualified).


### Knowledge indexing

- **Structural chunking:** Documents are split at natural boundaries (page,
  section, table) with overlap, preserving citations.
- **Active-version switching:** Index always reflects the current published
  version; tombstone-aware.
- **Tenant isolation:** Chunks are scoped to tenant + actor.
- **Single-fence retrieval:** Retrieved context is wrapped in untrusted-data
  fences for the model.
- **Arabic search:** the library's keyword index folds Arabic text
  (`arabic.fold_for_search`) at index time and at query time.
- **Explicit action:** `POST /api/documents/{id}/index` or tool `document_index`
  with a `library_id` — see [Knowledge Library](./knowledge-library.md).
- **Deleting a document leaves every library first.** A library it cannot
  leave stops the delete (`document_unindex_failed`, naming the library), so a
  retry can finish it; a deleted document is never still searchable.
- **The index is reconciled at every worker start**
  (`DocumentIngestionService.reconcile_index`): each version the store records
  as indexed but no library holds is re-published from its own manifest, under
  the document's publication lock, so a concurrent unindex is never undone. A
  missing library, a superseded version or a missing manifest is reported,
  not invented. This is what brings the libraries back after a restore of the
  document store alone.

### Generation & conversion

- **Document generation:** Structured payload → PDF/DOCX/HTML/markdown via
  isolated render workers. Generate payload bounded to **1 MiB**. Results are
  re-ingested as tenant-owned documents.
- **Format conversion:** Transform a processed document into a target format
  (opaque `document_id` only — no raw server paths from clients).
- **PDF operations:** Info extraction, split, merge, form field fill.
- **Redaction:** Verified physical/raster redaction pipeline with post-checks.
  **Web UI** confirms via `kazmaConfirm` before calling the API. **API and agent
  tools** can call redact without that UI dialog (tenant/ACL still apply).
- **Rendering never fetches.** Only `data:` URIs in the source and the assets
  copied into the job are read; an HTML or Markdown source naming an external
  URL or another local file is refused (`external_resource_denied`). Every
  rendered PDF passes the PDF active-content policy before it leaves the worker.
- **Optional engines:** `pymupdf` / WeasyPrint / LibreOffice may be absent.
  Missing engines surface as degraded readiness and truthful HTTP **422/503** —
  core text parsers can still be ready. In the `container` profile the engines
  are the worker image's, as the image reports them.

**Legacy path:** the `document-generator` skill (`generate_pdf` / `generate_docx`
/ …) renders through the same isolated workers (`DocumentService.generate`)
but writes plain files under `kazma-data/documents/` and does **not** go
through the durable platform. Prefer `document-platform` tools for opaque-ID,
tenant-scoped, restart-safe work.

---

## Operations

### Live configuration

All limits, retention, GC, capacity, and rollout flags are **ConfigStore-backed**
(primary keys are nested). Each operation reads one immutable snapshot of the
`documents` settings when it starts and keeps it to the end, so a change
applies from the next operation **without restart** — except the settings bound
to constructed stores and workers: `documents.storage_root`,
`documents.quotas.tenant_bytes`, `documents.workers.concurrency`,
`documents.workers.lease_seconds`, `documents.workers.heartbeat_seconds`,
`documents.workers.retry_base_seconds` and `documents.workers.retry_max_seconds`
take effect at the next restart; until then readiness lists them under
`policy.pending_restart`. Settings that cannot be read, or a saved value that
is invalid, refuse new work (HTTP 503 `document_policy_unavailable`) instead of
falling back to defaults.

| How to configure | Path |
|---|---|
| **Settings UI** | `/settings?tab=documents` (rollout, intake, workers, OCR and table reading, malware, GC, indexing) |
| **REST** | `GET/PUT /api/settings/documents` or `PUT /api/settings/single` |
| **YAML** | `kazma.yaml` seeds on first boot; DB overrides win afterward |
| **Env (backends and isolation)** | `KAZMA_DOCUMENTS_JOBS_BACKEND`, `KAZMA_DOCUMENTS_METADATA_BACKEND`, `KAZMA_DOCUMENT_WORKER_ISOLATION` (+ `KAZMA_DOCUMENT_BROKER_URL`, `KAZMA_DOCUMENT_BROKER_TOKEN_FILE`) |

Document limits are **not** primarily env-driven — use ConfigStore keys.

| Area | Primary key prefix | Notes |
|---|---|---|
| Intake | `documents.intake.*` | aliases: `documents.intake_max_*`; `documents.intake.lease_seconds` |
| Parser / limits | `documents.limits.*` | e.g. `documents.limits.max_pages` |
| Security | `documents.security.*` | malware: `malware_scan` + `malware_fail_closed` |
| OCR | `documents.ocr.*` | `documents.ocr.tables` (cell-by-cell table reading) |
| Rendering | `documents.render.*` | `documents.render.embed_html_fonts` |
| Workers | `documents.workers.*` | timeout, memory, concurrency, leases |
| Indexing | `documents.indexing.*` | |
| Retention / GC | `documents.retention.*`, `documents.gc.*` | |
| Capacity | `documents.capacity.*` | |
| Quotas | `documents.quotas.*` | |
| Rollout | `documents.enabled`, `documents.shadow`, `documents.default_authoritative` | |

### Queue health (real state machine)

Jobs use the durable states in `DocumentJobState` (not a generic
PENDING/ACCEPTED pipeline):

```
received
  → quarantined
  → validating
  → ready_to_parse | ocr_required | rejected
  → parsing | ocr_running
  → normalizing → indexing → verifying → ready
```

Side paths: `retry_wait` (transient / lease recovery), `cancelled`, `dead_letter`.

- **Leases** with heartbeat prevent stuck active work.
- **Retry** with exponential backoff (configurable).
- **Dead-letter** after max attempts (`documents.workers.max_attempts`, default
  3) for operator review.
- **Outages are retried; only content is rejected.** A required malware
  scanner that cannot run, a full disk (`storage_full`), a locked or unreachable
  metadata store (`metadata_unavailable`, SQLite or Postgres) and a worker
  broker that is down go through the retry budget, then dead-letter — never
  `rejected`. A store error while recording a stage's outcome leaves the job
  leased; lease recovery resumes it.
- **Recovery at restart:** expired leases on active processing states are
  reclaimed, and intakes cut short by a dead process are rolled back (see
  [Backpressure](#backpressure)).
- **Fair claims:** workers take jobs from the tenant with the fewest running
  first, then by when the job became ready (both backends).
- **Every job read names its caller.** Job status, events, the jobs of a
  document, cancel and retry check that the actor can read the job's document.
  A job the caller cannot read answers exactly like an unknown one (404 on the
  Web routes) on every surface; a replay of another actor's upload key is an
  `idempotency_conflict`. On a single-operator install the Web page, chat and
  the agent's tools act as one principal, the install's operator
  (`documents.principal.OPERATOR`): a document ingested on one surface is
  read on the others. Documents ingested under an older name (`agent`, a
  signed-in name) get an `owner` ACL row for the operator, granted by their
  owner and audited; ownership never changes. With platform users the page
  acts as each user and chat and the agent refuse (one operator per install;
  per-user chat identity is not built).

### Backpressure

Intake is refused with truthful HTTP status codes when limits hit:

| Condition | Status | `Retry-After` | Config key |
|---|---|---|---|
| Storage free-space floor | 507 | yes | `documents.capacity.storage_free_floor_bytes` (default 512 MiB) |
| Global queue ceiling | 503 | yes | `documents.capacity.max_queued_jobs` |
| Per-tenant queued/active cap | 429 | yes | `documents.capacity.max_tenant_queued_jobs` / `.max_tenant_active_jobs` |
| Intake rate/byte window | 429 | yes | `documents.capacity.intake_rate_per_minute` / `.intake_bytes_per_minute` |
| Too many uploads being received (per process) | 503 `uploads_busy` | yes | `documents.capacity.max_concurrent_uploads` (default 4) |
| Upload outlived its lease and was taken over | 409 `intake_taken_over` | no (retry the upload) | `documents.intake.lease_seconds` (default 300) |

The Web upload is answered before its body is read: a refusal never waits for
the file to arrive. The budgets are durable reservations in the job database,
so they hold across processes; a body with no declared length counts as the
intake limit until its real size is known. A measurement that cannot be read
(storage, queue, tenant counts, the admission store) refuses with 503 rather
than admitting.

An intake whose process died on this host is rolled back at the next start
and by a sweep every minute. An intake whose owner cannot be proven dead
(another host) renews a lease while it runs; once the lease lapses it is taken
over and rolled back, and if the old owner resumes it cannot publish: its job
is created only while the reservation is still its own, and the rows it wrote
are removed. A reservation with no owner or no lease (written by an older
build) is never taken over: readiness counts it as
`stranded_intake_reservations` (`document_intake_reservations_stranded`) for
the operator. Upload spool slots are counted per process, and claim fairness
is by fewest running jobs, not weighted.

### Retention & GC

A crash-safe mark/sweep collector runs on a configurable schedule:

- Database is sole authority (no filesystem-only orphans); the mark/sweep is
  the same on SQLite and Postgres metadata.
- Post-promotion quarantine copies, expired tombstone content,
  terminally-failed version content, orphan blobs, and aged-out audit rows.
- Scratch a crash left behind under the storage root: upload spools, staged
  copies, parser and operation run directories.
- Blobs pinned by a running Postgres backup are kept until it has copied them.
- Grace periods, bounded deletions per run, symlink/junction refusal.
- The scheduled pass (`documents.gc.auto_maintain`, every
  `documents.gc.interval_hours`) runs only while durable writes are enabled.

### Audit

Every operator/tenant-facing action is appended to the
`document_audit_events` table. On SQLite, database triggers refuse updates and
any delete but the retention prune; the Postgres table has no such triggers,
and Kazma only appends to it and prunes it. Details are allowlisted to safe
scalars — content never enters the audit trail. Each remote-parse upload has
an `egress` row written before the bytes move. Reading the trail
(`GET /api/documents/ops/audit`) is admin-only.

### Backup & migration

The document store is backed up every six hours, with the native memory
backup (the `native_backup` task).

- **SQLite store:** `documents.db` is snapshotted first, then the referenced
  content-addressed tree and normalized manifests are copied. Verification
  checks the database, blobs and copied manifests, and rejects an absent or
  invalid checksum inventory. Retention counts verified recovery points.
- **Postgres store** (`documents/backup_pg.py`, taken automatically when the
  installed metadata or jobs are on Postgres): one exported `REPEATABLE READ`
  snapshot serves both the `pg_dump --snapshot` of the document tables and the
  list of files they reference; those files are pinned against garbage
  collection and copied with checksums. A restore goes only into an **empty**
  database and storage root, re-checks every table's row count against the
  snapshot, expires the dead source's leases and rolls back intakes it had not
  finished. Operator commands: `scripts/document_pg_backup.py`
  (`backup`, `verify`, `restore`, `index`).
- **Knowledge index:** library chunks and vectors are a separate store and are
  not in either backup. After a restore the service re-publishes every
  document the store records as indexed, at the next worker start (see
  [Knowledge indexing](#knowledge-indexing)).

Migration bundles via `kazma migrate` carry `documents.db` + the content tree
(see [Migration](../ops/migration.md) and
[Document processing ops](../ops/document-processing.md)). Recovery-point and
recovery-time figures for a given deployment come from running the restore
drills on its own hardware; none is asserted here.

---

## Rollout controls

| Mode | `enabled` | `shadow` | `default_authoritative` | Effect |
|---|---|---|---|---|
| Disabled | false | — | — | No durable writes; existing data preserved |
| Shadow | true | true | false | Writes accepted; reported as `shadow` |
| Compatibility | true | false | false | Writes accepted (default ship posture) |
| Authoritative | true | false | true | Writes accepted; reported as `authoritative` |

Only `enabled` changes behaviour today: it gates durable writes and the
scheduled garbage collection. `shadow` and `default_authoritative` are
recorded and reported (readiness `rollout.mode`, the certifier's
`safe_rollout` check); no code path routes by them. With both set,
authoritative wins. Settings that cannot be read count as disabled for writes.

**Safe rollback:** Setting `documents.enabled=false` stops new durable writes but
preserves all blobs, jobs, manifests, and metadata. Data is never deleted on
rollout disable.

---

## Surfaces

| Surface | Entry |
|---|---|
| Web UI | `/documents` — upload, library, detail, ops panel (GC dry-run + confirm) |
| Settings | `/settings?tab=documents` — live rollout, intake, OCR, malware, GC, workers |
| REST | `/api/documents/*` — see [API routes](../reference/api-routes.md#documents--document-intelligence) |
| Agent tools | `document_import`, `document_read`, `document_index`, … (`document-platform` skill) |
| Gateway chat | `/documents` or `/docs` — list, status, read, convert, pdf-info, redact, search, health |
| TUI | Documents tab (list and preview through the Web API) |
| Certification | `python scripts/certify_documents.py` |

---

## Certification

The certifier reports what it actually ran on this machine, with the
configured profile:

```bash
python scripts/certify_documents.py              # CI smoke
python scripts/certify_documents.py --soak        # Extended smoke (100 iterations), not a multi-day soak
python scripts/certify_documents.py --output report.json
```

| Check | What it is | Status it can reach |
|---|---|---|
| `hostile_corpus` | the 26 committed hostile cases, each refused or fenced as declared | PASS / FAIL |
| `bounded_performance_event_loop_resources` | repeated parses with duration, event-loop stall and memory limits | PASS / FAIL |
| `runtime_capabilities` | parser, OCR, renderer and mutator readiness | CONDITIONAL when an optional engine is missing, FAIL when no parser is ready |
| `safe_rollout` | the live rollout mode | CONDITIONAL in authoritative or disabled mode |
| `worker_isolation` | the configured isolation profile | `process` is always CONDITIONAL; `container` is PASS when the broker is ready and its image was not built from other document code, else FAIL; an invalid profile is FAIL |
| `extraction_fidelity` | the fidelity corpus against its release targets | CONDITIONAL while any case is below target or cannot be measured (today: three cases below) |
| `backup_restore_sqlite` | one ready document backed up, verified, restored and read back | PASS / FAIL |
| `postgres_multi_replica`, `metadata_multi_replica`, `multi_day_soak`, `external_security_review` | not done by a local run | always NOT RUN |

The report also names the isolation profile, worker image and configured
metadata/jobs backends, and its overall status is never better than
CONDITIONAL while a check is CONDITIONAL or NOT RUN.

The hostile corpus is **programmatically generated** — no opaque binaries in
the repository. Each case has a reviewed description, expected disposition
(reject/fenced), and expected error codes. The committed manifest under
`tests/fixtures/documents/hostile_manifest.json` is verified at release time.

**Extraction fidelity** (`documents/fidelity.py`, thresholds in
`tests/fixtures/documents/fidelity_thresholds.json`): a versioned corpus built
with Kazma's own engines — English, Arabic and bilingual DOCX, PDF and HTML,
long documents, two columns from another producer, wide tables, OCR pages
(plain, ruled table, degraded scan) — measured on the real parser for word,
segment, number and table-cell recall, reading order, page honesty, alignment
and OCR CER/WER. Corpus version 4 has 19 cases (the OCR reference now holds
all the text a page shows, its footer included). The five OCR cases are
measured with each engine against its own floors: with the neural engine
**17** of 19 meet the release targets (`ar-ocr` among them); with Tesseract
16. Not qualified: `ar-ocr-table` with the neural engine (one word,
`ريالاً`, misread; every number and cell right) and with Tesseract (Arabic-Indic
digits not read), `ar-ocr` with Tesseract, and `en-ocr-degraded` with both (a
decimal point read as a comma or a colon). `python scripts/document_fidelity.py`
measures it again with every engine installed here.

**Note:** Architecture, a11y, and full crash-matrix gates live in
`tests/test_document_certification_phase10.py`. Process-death recovery,
intake takeover, restore drills and the container profile have their own
suites (see the [Phase map](./document-phases.md#post-phase-hardening)).

See [Phase map](./document-phases.md) and the repo audit report
`docs/audits/AUDIT_DOCUMENT_CERTIFICATION.md` (not part of the Docusaurus tree).

---

## Multi-replica

| Layer | Backend | Multi-replica? |
|---|---|---|
| **Job claiming** | Postgres when pool is up (`jobs_pg.py`) | Yes — `SELECT … FOR UPDATE SKIP LOCKED` |
| **Metadata** (documents/versions/blobs/artifacts/chunks) | Follows the jobs backend by default (`auto`); `KAZMA_DOCUMENTS_METADATA_BACKEND=sqlite` or `postgres` forces it (`repository_pg.py`) | Yes when Postgres metadata is active |
| **Intake admission** | Reservations in the jobs database; a lapsed lease is taken over and its owner fenced | Yes on Postgres jobs |
| **Content blobs** | Content-addressed tree under `documents.storage_root` | Shared filesystem / volume required across replicas |
| **GC mark/sweep** | Backend-agnostic — `retention._mark` dispatches to `repository.gc_mark`, implemented by both backends | Yes |

These are what each backend is built for. Running several replicas is not
qualified: the certifier reports `postgres_multi_replica` and
`metadata_multi_replica` as NOT RUN, and upload spool slots are counted per
process. Readiness reports SQLite metadata as single-replica whenever it is
the backend serving.

Env:

| Variable | Values | Effect |
|---|---|---|
| `KAZMA_DOCUMENTS_JOBS_BACKEND` | `auto` (default) / `sqlite` | Force SQLite jobs, or follow platform Postgres |
| `KAZMA_DOCUMENTS_METADATA_BACKEND` | `auto` / `sqlite` / `postgres` | `auto` follows jobs backend |

Check: `GET /api/documents/ops/readiness` → `jobs_multi_replica`, `metadata_multi_replica`, `degraded_reasons`
(also `worker_isolation`, `stranded_intake_reservations`, `policy`, `malware`,
`accepting_durable_writes`).

---

## Install & enable

### Python extras

```bash
# Simple PDF/DOCX/XLSX generators only (legacy skill)
pip install -e ".[document]"

# Full platform engines (OCR helpers, WeasyPrint, PyMuPDF, pypdfium2 bake-off)
pip install -e ".[document-platform]"

# The neural OCR engine: its packages (part of the ocr extra, which
# document-platform includes), then its models, once
pip install -e ".[ocr]"
python scripts/ocr_models.py install

# Everything
pip install -e ".[all]"
```

### System packages (optional)

| Package | Purpose |
|---|---|
| **Tesseract** + language packs (`eng`, `ara`, …) | OCR (scanned / bad text layer; `ara` required for Arabic scans; `eng` also serves the digits-only pass of table cells); also the third reader of the neural engine's letter vote |
| **ClamAV** (`clamscan` / `clamdscan` on PATH) | Malware scan on intake |
| **LibreOffice** | Legacy `.doc` / `.xls` / `.ppt` parsing, some format conversions, and the PDF *generation* route for documents containing Arabic |
| OS fonts | WeasyPrint HTML→PDF (IBM Plex Sans Arabic and Amiri ship with Kazma for generated documents) |

In the `container` profile these come from the worker image
(`deploy/document-worker/Dockerfile`), not from the host -- the neural OCR
engine and its models included (an `ocr-models` build stage downloads and
converts them; the worker has no network).

### The neural OCR engine's models

`python scripts/ocr_models.py install` downloads four files pinned by URL,
size and SHA-256 in `documents/ocr/neural_models.py` (PaddleOCR's PP-OCRv5
detection and Arabic recognition as RapidAI's ONNX conversions, EasyOCR's
`arabic_g1` and `english_g2`), converts the two EasyOCR models from PyTorch to
ONNX and checks the conversion against PyTorch on fixed inputs, and writes
them with a `manifest.json` into `KAZMA_OCR_MODEL_DIR` (default
`<data dir>/models/ocr`, about 240 MB, not backed up or carried in a bundle:
it is re-installed). The conversion needs PyTorch once: when the interpreter
has none, the script runs it in a throwaway environment through `uv`, and
nothing is added to Kazma's own environment. `python scripts/ocr_models.py
verify` checks an installed set. The document worker uses the models only when
every file matches the manifest, and never downloads anything.

### First operator walkthrough

1. Start Kazma and open **`/documents`**.
2. (Optional) **Settings → Documents** — leave `enabled`, tune malware to `auto` or `on` if ClamAV is installed.
3. Upload a small PDF or `.txt` → wait for state **`ready`**.
4. Preview content (BiDi-safe with `dir="auto"`).
5. Set a `library_id` → **Index** → search via UI/API/`document_search`.
6. Ops panel: capacity, readiness, audit; GC = dry-run then confirm.
7. Cert smoke: `python scripts/certify_documents.py`

### Chat & agent

- Attach a PDF in chat → auto-parse excerpt (fenced) when a parser is available.
- Agent tools: `document_import` (workspace path), `document_read`, `document_index`, …
- Gateway: `/documents list`, `/documents read <id>`, …

---

## Supported formats (typical)

| Kind | Examples | Notes |
|---|---|---|
| Text / data | `.txt`, `.md`, `.markdown`, `.log`, `.csv`, `.tsv`, `.json`, `.html`, `.htm`, `.rtf` | Sniff + text parsers (strict UTF-8) |
| Office OOXML | `.docx`, `.xlsx`, `.pptx` | Structural defenses before parse; macro-enabled extensions are refused |
| Legacy Office | `.doc`, `.xls`, `.ppt` | Converted by LibreOffice inside the worker, then parsed |
| PDF | `.pdf` | The PDF active-content policy refuses encrypted files, script, external actions and embedded files |
| Images | `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.bmp`, `.webp` | OCR path when enabled |

Exact readiness is reported per parser/renderer at `GET /api/documents/health`.

---

## Malware scanning

Intake scans quarantined bytes via `documents.security.malware_scan`:

| Mode | Behavior |
|---|---|
| `auto` (default) | Scan if ClamAV on PATH; otherwise skip (unless fail-closed). A scanner error is treated as a skip unless fail-closed |
| `on` | Require a working scanner |
| `off` | Never scan |

`documents.security.malware_fail_closed` makes `auto` require a scanner too;
it defaults to on when `KAZMA_PRODUCTION=1`. When a required scanner cannot
run, a new upload is refused with `malware_scanner_unavailable` and readiness
reports `accepting_durable_writes: false`; a job already queued (it is scanned
again before parsing, under today's policy) is held and retried, then
dead-lettered — never rejected as if its content were unsafe. Infected files →
error code `malware_detected`. See [Document security](../security/document-security.md).

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Upload 413 / 429 / 503 / 507 | Capacity limits; Settings or ops capacity snapshot |
| Upload 409 `intake_taken_over` | The upload outlived its lease and was rolled back; send it again |
| Every operation 503 `document_policy_unavailable` | The saved document settings cannot be read or one is invalid; fix the setting |
| Jobs retry or fail with `worker_isolation_*` | `_unavailable` / `_busy` (retried): the `container` profile's broker is down, unattested or full. `_required`: the install requires the container profile. `_invalid`: a mistyped profile. Readiness `worker_isolation` says which |
| State stuck / dead_letter | Job events; worker running; `GET /api/documents/health`; readiness `stranded_intake_reservations` |
| Empty preview | Not `ready` yet; parser failure code on job |
| Convert/redact 422/503 | Optional engine missing — install `document-platform` + system deps (or the worker image, in the `container` profile) |
| Malware rejections unexpected | ClamAV false positive, or a required scanner (`on`, or fail-closed in production) that cannot run |
| Multi-replica weirdness | `ops/readiness`; shared blob volume; multi-replica operation is not qualified |
| Cross-tenant 404 | Expected — tenant/actor ACL; an unreadable job answers like an unknown one |

More ops detail: [Document processing](../ops/document-processing.md).

---

## Limits at a glance

| Resource | Default | Primary ConfigStore key |
|---|---|---|
| Max file size | 50 MiB | `documents.intake.max_bytes` |
| Max files per request | 10 | `documents.intake.max_files` |
| Max pages | 500 | `documents.limits.max_pages` |
| Max rows per sheet | 100,000 | `documents.limits.max_rows_per_sheet` |
| Max cells | 2,000,000 | `documents.limits.max_cells` |
| Max expanded bytes | 256 MiB | `documents.limits.max_expanded_bytes` |
| Max archive members | 10,000 | `documents.limits.max_archive_members` |
| OCR DPI | 200 | `documents.ocr.dpi` |
| Worker timeout | 300 s | `documents.workers.timeout_seconds` |
| Worker memory | 1 GiB | `documents.workers.memory_mb` |
| Tenant quota | 10 GiB | `documents.quotas.tenant_bytes` |
| Audit retention | 365 days | `documents.retention.audit_days` |
| Storage free floor | 512 MiB | `documents.capacity.storage_free_floor_bytes` |
| Concurrent Web uploads | 4 per process | `documents.capacity.max_concurrent_uploads` |
| Intake lease | 300 s | `documents.intake.lease_seconds` |
| Table cells read per scanned page | 600 cells / 120 s | fixed (`documents/ocr/pipeline.py`) |
| OCR engine | `auto` | `documents.ocr.engine` (`auto`, `neural`, `tesseract`) |
| JSON request body | 2 MiB | fixed (`documents_api.py`) |
| Generation payload | 1 MiB | fixed (`ingestion.py`) |
| Remote-parse answer | 32 MiB | fixed (`extract_salvage.py`) |

Full field list: `kazma_core.documents.config.DocumentConfig` (75 fields).

## What is not qualified {#not-qualified}

These are stated limits of the current build, from the code, the certifier and
the fidelity corpus — not plans:

- **Hostile documents on the default `process` profile.** It bounds memory, CPU
  and time but does not confine the filesystem or the network. Only the
  `container` profile does, and its containers share the host kernel.
- **Arabic OCR with Tesseract** (`ar-ocr`, `ar-ocr-table` below the release
  targets: Arabic-Indic digits are not read). With the neural engine, the
  Arabic page with a ruled table (`ar-ocr-table`, one word misread), Arabic
  decimals and dates written with the Arabic separators, and real scans at the
  release targets (measured on one real scan only, where it beat Tesseract on
  most pages and lost on some words). Numbers read from degraded scans
  (`en-ocr-degraded`) with either engine. Tables without ruling lines and
  merged cells are not read cell by cell.
- **Multi-replica operation** (the certifier's `postgres_multi_replica` and
  `metadata_multi_replica` are NOT RUN).
- **Sustained operation:** no 48–72 hour mixed-load soak has been run; `--soak`
  is an extended smoke.
- **Independent security review:** none (`external_security_review` is NOT RUN).
- **Recovery-point and recovery-time objectives** for a deployment: the restore
  drills measure them; no figure is promised.
- **Docling salvage in the `container` profile:** not in the worker image.
