---
id: document-intelligence
title: Document Intelligence
sidebar_label: Document Intelligence
description: End-to-end document processing — ingest, parse, OCR, index, generate, and redact documents through a secure, isolated pipeline.
---

# Document Intelligence

Kazma's document intelligence platform provides production-grade document
ingestion, parsing, OCR, knowledge indexing, generation, conversion, redaction,
and operational controls — all through a single orchestration layer with
hardened isolation, immutable audit, and live configuration.

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
    │
    ├─ Sniff (MIME, OOXML defenses, PDF policy)
    ├─ Parse (isolated subprocess per format)
    ├─ OCR  (per-page quality routing, Tesseract)
    ├─ Index (structural chunking → Knowledge library — explicit action)
    ├─ Generate / Convert (isolated render workers)
    └─ Mutate / Redact (verified redaction pipeline)
```

**Two layers, one path for durable work.**

| Layer | Class | Who calls it |
|---|---|---|
| **Durable coordinator** | `DocumentIngestionService` | Web `/api/documents/*`, native `document-platform` tools, gateway `/documents`, TUI Documents panel |
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
2. **Settings / API:** durable list `workspace.extra_roots` via
   `GET/PUT /api/workspace/extra-roots` (`path`, `mode`: `read`|`write`, `label`).

Session grants TTL ~1 hour; durable roots persist until removed. Read grants
do not allow writes. See `kazma_core.workspace.path_policy`.

### Secure intake

- **Document platform limits (defaults):** 50 MiB per file, 10 files per request
  (`documents.intake.max_bytes`, `documents.intake.max_files`).
  Aliases `documents.intake_max_bytes` / `documents.intake_max_files` are accepted.
- **Chat gateway attachments (separate surface):** 20 MiB per file, 10 files,
  50 MiB aggregate — keeps prompt payloads bounded.
- **Content-addressed immutable storage:** SHA-256 deduplication; quarantine →
  permanent promotion on verified parse.
- **MIME sniffing before parsing:** Extension/content mismatch rejection,
  OOXML structural defense (XXE, macros, external relationships, nested archives,
  compression bombs), PDF policy enforcement (no encryption, active content, or
  embedded files by default).
- **All LLM-visible content is fenced:** Platform reads use
  `<kazma:data source="document" untrusted="true">…</kazma:data>`.
  Chat attachment excerpts may use `source="document_attachment"`.

### Isolated parsing

Every parse/OCR/mutation job runs in a **host subprocess** (`python -I`):

- Scrubbed environment (parent secrets stripped; not a full OS network namespace).
- Per-process memory and CPU limits where the OS supports them.
- Configurable timeout (killed, not leaked).
- On Windows: Job Objects with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (CPU quota
  may report as partially degraded on some Windows hosts).
- Crashes, timed-out jobs, and missing output all fail closed — no
  partial/broken data escapes the sandbox.

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
- **Metadata:** `extractor`, `extraction_score`, `extractors_tried`, per-page
  `layout.method` / `column_count`.
- **Round-trip generate→parse:** RTL titles use fuzzy token coverage (glyph
  noise tolerant), not “any 20 characters”.

Install: `pip install -e ".[document-platform]"` (includes `pymupdf` +
`pypdfium2`). Without PyMuPDF, Arabic electronic PDFs may reverse under
pdfplumber alone.

**Hard-PDF salvage (after the isolated parser returns):** if the native score
is weak, the parent process may try **Docling** (`pip install 'kazma[docling]'`)
then **LlamaParse / Reducto** when `LLAMAPARSE_API_KEY` / `REDUCTO_API_KEY` are
set. API keys never enter the parser sandbox. Kill-switches:
`KAZMA_DOCLING=0`, `KAZMA_REMOTE_PARSE=0`.

### Multilingual OCR

- **Per-page quality routing:** Pages with high text density can skip OCR
  (native text preserved); image-heavy / text-poor pages, high presentation-form
  / CID-mangled layers, and empty text layers route to Tesseract.
- **Language selection:** Configurable language pack list (default includes
  English + Arabic when installed). Auto order is **`ara+eng`** when both are
  configured — `eng+ara` often misreads pure-Arabic scans as Latin gibberish.
- **Deterministic merging:** Native and OCR text are merged with
  **similarity-based** rules (`SequenceMatcher` thresholds + confidence), not
  pure geometric coordinate fusion. Known-bad native layers (presentation forms /
  CID) are **replaced** when OCR confidence is acceptable. OCR blocks may still
  carry TSV pixel coordinates for downstream use.
- **Fallback:** Large pages rasterized at configurable DPI (200 default).
  OCR stays in the isolated worker (`apply_ocr`) — the PDF parser never shells
  out to Tesseract directly.


### Knowledge indexing

- **Structural chunking:** Documents are split at natural boundaries (page,
  section, table) with overlap, preserving citations.
- **Active-version switching:** Index always reflects the current published
  version; tombstone-aware.
- **Tenant isolation:** Chunks are scoped to tenant + actor.
- **Single-fence retrieval:** Retrieved context is wrapped in untrusted-data
  fences for the model.
- **Explicit action:** `POST /api/documents/{id}/index` or tool `document_index`
  with a `library_id` — see [Knowledge Library](./knowledge-library.md).

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
- **Optional engines:** `pymupdf` / WeasyPrint / LibreOffice may be absent.
  Missing engines surface as degraded readiness and truthful HTTP **422/503** —
  core text parsers can still be ready.

**Legacy path:** the `document-generator` skill (`generate_pdf` / `generate_docx`
/ …) writes simple files under `kazma-data/documents/` and does **not** go
through the durable platform. Prefer `document-platform` tools for opaque-ID,
tenant-scoped, restart-safe work.

---

## Operations

### Live configuration

All limits, retention, GC, capacity, and rollout flags are **ConfigStore-backed**
(primary keys are nested). Changes apply **without restart**.

| How to configure | Path |
|---|---|
| **Settings UI** | `/settings?tab=documents` (rollout, intake, workers, OCR, malware, GC) |
| **REST** | `GET/PUT /api/settings/documents` or `PUT /api/settings/single` |
| **YAML** | `kazma.yaml` seeds on first boot; DB overrides win afterward |
| **Env (backends only)** | `KAZMA_DOCUMENTS_JOBS_BACKEND`, `KAZMA_DOCUMENTS_METADATA_BACKEND` |

Document limits are **not** primarily env-driven — use ConfigStore keys.

| Area | Primary key prefix | Notes |
|---|---|---|
| Intake | `documents.intake.*` | aliases: `documents.intake_max_*` |
| Parser / limits | `documents.limits.*` | e.g. `documents.limits.max_pages` |
| Security | `documents.security.*` | malware: `malware_scan` + `malware_fail_closed` |
| OCR | `documents.ocr.*` | |
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
- **Dead-letter** after max attempts for operator review.
- **Recovery at restart:** expired leases on active processing states are reclaimed.

### Backpressure

Intake is refused with truthful HTTP status codes when limits hit:

| Condition | Status | `Retry-After` | Config key |
|---|---|---|---|
| Storage free-space floor | 507 | yes | `documents.capacity.storage_free_floor_bytes` (default 512 MiB) |
| Global queue ceiling | 503 | yes | `documents.capacity.max_queued_jobs` |
| Per-tenant queued/active cap | 429 | yes | `documents.capacity.max_tenant_queued_jobs` / `.max_tenant_active_jobs` |
| Intake rate/byte window | 429 | yes | `documents.capacity.intake_rate_per_minute` / `.intake_bytes_per_minute` |

### Retention & GC

A crash-safe mark/sweep collector runs on a configurable schedule:

- Database is sole authority (no filesystem-only orphans).
- Post-promotion quarantine copies, expired tombstone content,
  terminally-failed version content, orphan blobs, and aged-out audit rows.
- Grace periods, bounded deletions per run, symlink/junction refusal.

### Audit

Every operator/tenant-facing action is appended to an immutable
`document_audit_events` table. Details are allowlisted to safe scalars —
content never enters the audit trail.

### Backup & migration

The nightly native backup snapshots `documents.db` first, then the referenced
content-addressed tree. Every blob checksum is verified. Migration bundles via
`kazma migrate` carry `documents.db` + the content tree (see
[Migration](../ops/migration.md) and [Document processing ops](../ops/document-processing.md)).

---

## Rollout controls

| Mode | `enabled` | `shadow` | `default_authoritative` | Effect |
|---|---|---|---|---|
| Disabled | false | — | — | No durable writes; existing data preserved |
| Shadow | true | true | false | Writes accepted; not the default product path |
| Compatibility | true | false | false | Writes accepted; opt-in routing (default ship posture) |
| Authoritative | true | false | true | Document operations prefer this platform |

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
| Gateway chat | `/documents` or `/docs` — list, status, read, convert, redact, search, health |
| TUI | Documents tab (read/list against shared ingestion service) |
| Certification | `python scripts/certify_documents.py` |

---

## Certification

Release certification runs deterministic hostile-corpus checks, bounded
performance smoke, capability probes, and rollout verification:

```bash
python scripts/certify_documents.py              # CI smoke
python scripts/certify_documents.py --soak        # Opt-in soak (100 iterations)
python scripts/certify_documents.py --output report.json
```

The hostile corpus is **programmatically generated** — no opaque binaries in
the repository. Each case has a reviewed description, expected disposition
(reject/fenced), and expected error codes. The committed manifest under
`tests/fixtures/documents/hostile_manifest.json` is verified at release time.

**Note:** Architecture, a11y, and full crash-matrix gates live in
`tests/test_document_certification_phase10.py`. The CLI certifier reports a
subset of gates plus explicit `NOT RUN` / `CONDITIONAL` items (Postgres
multi-replica jobs, multi-day soak, external review).

See [Phase map](./document-phases.md) and the repo audit report
`docs/audits/AUDIT_DOCUMENT_CERTIFICATION.md` (not part of the Docusaurus tree).

---

## Multi-replica

| Layer | Backend | Multi-replica? |
|---|---|---|
| **Job claiming** | Postgres when pool is up (`jobs_pg.py`) | Yes — `SELECT … FOR UPDATE SKIP LOCKED` |
| **Metadata** (documents/versions/blobs/artifacts/chunks) | SQLite default; Postgres when `KAZMA_DOCUMENTS_METADATA_BACKEND=postgres` or `auto` with PG jobs (`repository_pg.py`) | Yes when Postgres metadata is active |
| **Content blobs** | Content-addressed tree under `documents.storage_root` | Shared filesystem / volume required across replicas |
| **GC mark/sweep** | SQLite SQL today | **Skipped** on Postgres metadata with honest error `gc_postgres_metadata_sql_port_pending` (no silent deletes) |

Env:

| Variable | Values | Effect |
|---|---|---|
| `KAZMA_DOCUMENTS_JOBS_BACKEND` | `auto` (default) / `sqlite` | Force SQLite jobs, or follow platform Postgres |
| `KAZMA_DOCUMENTS_METADATA_BACKEND` | `auto` / `sqlite` / `postgres` | `auto` follows jobs backend |

Check: `GET /api/documents/ops/readiness` → `jobs_multi_replica`, `metadata_multi_replica`, `degraded_reasons`.

---

## Install & enable

### Python extras

```bash
# Simple PDF/DOCX/XLSX generators only (legacy skill)
pip install -e ".[document]"

# Full platform engines (OCR helpers, WeasyPrint, PyMuPDF, pypdfium2 bake-off)
pip install -e ".[document-platform]"

# Everything
pip install -e ".[all]"
```

### System packages (optional)

| Package | Purpose |
|---|---|
| **Tesseract** + language packs (`eng`, `ara`, …) | OCR (scanned / bad text layer; `ara` required for Arabic scans) |
| **ClamAV** (`clamscan` / `clamdscan` on PATH) | Malware scan on intake |
| **LibreOffice** | Some format conversions; high-quality Arabic PDF *generation* route |
| OS fonts | WeasyPrint HTML→PDF |

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
| Text / data | `.txt`, `.csv`, `.md`, JSON-ish | Sniff + text parsers |
| Office OOXML | `.docx`, `.xlsx`, `.pptx` | Structural defenses before parse |
| PDF | `.pdf` | Policy rejects encrypt/JS/polyglot by default |
| Images | PNG/JPEG/… | OCR path when enabled |

Exact readiness is reported per parser/renderer at `GET /api/documents/health`.

---

## Malware scanning

Intake scans quarantined bytes via `documents.security.malware_scan`:

| Mode | Behavior |
|---|---|
| `auto` (default) | Scan if ClamAV on PATH; otherwise skip (unless fail-closed) |
| `on` | Require scanner; missing scanner → reject |
| `off` | Never scan |

Infected files → error code `malware_detected`. See [Document security](../security/document-security.md).

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Upload 413 / 429 / 503 / 507 | Capacity limits; Settings or ops capacity snapshot |
| State stuck / dead_letter | Job events; worker running; `GET /api/documents/health` |
| Empty preview | Not `ready` yet; parser failure code on job |
| Convert/redact 422/503 | Optional engine missing — install `document-platform` + system deps |
| Malware rejections unexpected | ClamAV false positive, or `on` without scanner |
| Multi-replica weirdness | `ops/readiness`; shared blob volume; GC skipped on PG metadata |
| Cross-tenant 404 | Expected — tenant/actor ACL |

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

Full field list: `kazma_core.documents.config.DocumentConfig` (68 fields).
