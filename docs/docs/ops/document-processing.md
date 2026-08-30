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
| `GET /api/documents/ops/readiness` (or Settings → Documents) | PDF parser **ready** when PyMuPDF or pdfplumber is installed |
| pypdfium2 / pypdf only | Parser **degraded** (text only — tables not advertised) |
| Arabic electronic PDF | Logical-order tokens; IR metadata `extractor` / `extraction_score` |
| Arabic scan | Tesseract + `ara` traineddata on PATH; OCR may still be imperfect |
| After `kazma update` | CLI imports (`kazma serve` works); see [Kazma Update](./kazma-update) |

Deploy deps: `pip install -e ".[document-platform]"` (pymupdf + pypdfium2) and
system **Tesseract** with `eng` + `ara` packs when OCR is required.

---

## Day-2 operator surfaces

| Surface | Use |
|---|---|
| Web `/documents` | Upload, library, detail, ops panel (capacity, readiness, audit, GC) |
| Settings `/settings?tab=documents` | Live rollout, intake, workers, OCR, malware, GC |
| `GET /api/documents/ops/*` | Metrics, capacity, readiness, retention, audit |
| `POST /api/documents/ops/maintenance/{dry-run,run}` | Admin GC |
| `python scripts/certify_documents.py` | Cert smoke / `--soak` |

---

## Metrics

Document metrics are emitted through the standard Prometheus exposition at
`/metrics` (no new telemetry dependency; degrades to no-ops when
`prometheus-client` is absent). A numeric, content-free snapshot is also
available at `GET /api/documents/ops/metrics`.

| Area | Metrics |
|---|---|
| Intake | `kazma_documents_intake_files_total{outcome}`, `…_intake_bytes_total`, `…_intake_rejections_total{reason}` |
| Queue | `…_queue_depth`, `…_queue_oldest_age_seconds`, `…_active_leases`, `…_retry_waiting`, `…_dead_letter_current` |
| Stages | `…_stage_total{stage,outcome}`, `…_stage_latency_seconds{stage}` |
| Parser/OCR | `…_parser_total{parser,outcome}`, `…_pages_total{kind}` |
| Sandbox | `…_sandbox_terminations_total{reason}` (timeout/oom/output/degraded) |
| Storage | `…_storage_logical_bytes`, `…_storage_physical_bytes`, `…_storage_dedup_ratio` |
| Indexing | `…_index_latency_seconds`, `…_index_chunks_total` |
| Generation | `…_generation_failures_total{operation}`, `…_redaction_failures_total` |

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
the shared pool. It complements — does not duplicate — the per-job stage event
history. Read a page with `GET /api/documents/ops/audit?limit=&before_id=`.
Details are allowlisted to safe scalars; content never enters the audit.

---

## Backpressure, capacity, and rate limits

Intake is refused with a truthful status + `Retry-After` when a limit is hit:

| Limit | Status | Config key |
|---|---|---|
| Storage free-space floor | **507** | `documents.capacity.storage_free_floor_bytes` |
| Global queue ceiling | **503** | `documents.capacity.max_queued_jobs` |
| Per-tenant queued cap | **429** | `documents.capacity.max_tenant_queued_jobs` |
| Per-tenant active cap | **429** | `documents.capacity.max_tenant_active_jobs` |
| Intake rate / bytes window | **429** | `documents.capacity.intake_rate_per_minute`, `…intake_bytes_per_minute` |

The durable queue capacity is authoritative; the in-memory rate/byte window is
an additional burst guard. `GET /api/documents/ops/capacity` returns an
alert-compatible snapshot with machine-readable `degraded_reasons`.

Default free-floor is **512 MiB**; many production hosts raise it to ≥ 1 GiB
via Settings → Documents or ConfigStore.

---

## Retention and garbage collection

Retention is live ConfigStore-backed (`documents.retention.*`, `documents.gc.*`).
The collector is a crash-safe **mark/sweep** with the database as the sole
authority (SQLite metadata path):

- Reclaims orphan/unreferenced blobs, post-promotion quarantine copies,
  expired-tombstone content, terminally-failed (rejected/dead-letter) version
  content, orphan blob rows, orphan manifests, and aged-out audit rows.
- **Never** deletes referenced blobs, current-version content, or artifacts
  (content-addressed dedup preserved).
- Honors a grace period, bounds deletions per run
  (`documents.gc.max_deletions_per_run`), and refuses to follow or delete
  through a symlink/junction or outside the store root.

A scheduled loop runs every `documents.gc.interval_hours` (cancelled cleanly on
shutdown). Operators can **dry-run then confirm** from the Documents page or via
`POST /api/documents/ops/maintenance/dry-run` and `…/run` (admin-gated).

**Postgres metadata:** CRUD is multi-replica-safe and GC runs on both
backends — `retention._mark` dispatches to `repository.gc_mark`, which
`repository.py` and `repository_pg.py` each implement. (Earlier releases skipped
GC on Postgres with `gc_postgres_metadata_sql_port_pending`; that guard has been
removed and a test asserts it does not come back.)

---

## Malware scanning

Intake runs ClamAV when configured (`documents.security.malware_scan` =
`auto`/`on`/`off`). Probe appears on readiness and Settings → Documents.
See [Document security](../security/document-security.md).

---

## Backup

The nightly native backup snapshots `documents.db` **first**
(`sqlite3.backup()` → point-in-time) when metadata is SQLite, then copies the
content it references and verifies every blob/manifest checksum — a torn
DB/blob snapshot is impossible because blob files are always written before
their rows. Backups land under `kazma-data/backups/document-store-<ts>/` with a
`manifest.json`.

When metadata is Postgres, relational state is part of the platform DB backup
story (`pg_dump` / `kazma migrate`); still backup the content-addressed tree.

---

## Migration

`kazma migrate export|verify|import` carries `documents.db` (when present) + the
content-addressed tree + manifests. `documents.db` has no embedded paths, so it
needs no rewrite; the importer restores it and the tree into the target's
document-store root through the existing atomic stage → verify → backup → swap
flow. See [Migration](./migration.md).

---

## Multi-replica readiness

| Component | Multi-replica | How |
|---|---|---|
| Job queue | Yes (Postgres) | `KAZMA_DATABASE_URL` + `jobs_pg.py`; override with `KAZMA_DOCUMENTS_JOBS_BACKEND=sqlite` |
| Metadata | Yes (Postgres) | `KAZMA_DOCUMENTS_METADATA_BACKEND=postgres` or `auto`; `repository_pg.py` |
| Metadata | No (SQLite default) | Single app replica against a shared store |
| Blobs | Shared volume | All replicas must see the same `documents.storage_root` tree |
| GC | SQLite only today | Skipped on PG metadata with honest error |

Always read `GET /api/documents/ops/readiness`:

```json
{
  "status": "ready|degraded",
  "jobs_backend": "postgres|sqlite",
  "jobs_multi_replica": true,
  "metadata_backend": "postgres|sqlite",
  "metadata_multi_replica": true,
  "degraded_reasons": [],
  "malware": { "available": true, "scanner": "clamdscan", "mode": "auto" }
}
```

---

## Certification & soak

```bash
python scripts/certify_documents.py
python scripts/certify_documents.py --soak --soak-iterations 100
python scripts/certify_documents.py --output report.json
```

Pytest: `tests/test_document_certification_phase10.py` (architecture, crash,
a11y, rollout). Report history: `docs/audits/AUDIT_DOCUMENT_CERTIFICATION.md`.
