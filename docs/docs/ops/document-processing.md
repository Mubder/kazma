---
title: Document Processing Operations
---

# Document Processing — Operations & Scale

Phase 9 operational surface for the [document intelligence platform](../reference/tools-catalog.md).
Everything here is driven by the shared `DocumentIngestionService`; there is no
second parser or parallel write path.

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

## Operational audit

Every operator/tenant-facing action (intake, access, index, generate, convert,
mutate, redact, download, cancel, retry, delete, GC) is appended to an
immutable, tenant-scoped audit trail (`document_audit_events` in
`documents.db`). It complements — does not duplicate — the per-job stage event
history. Read a page with `GET /api/documents/ops/audit?limit=&before_id=`.
Details are allowlisted to safe scalars; content never enters the audit.

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

## Retention and garbage collection

Retention is live ConfigStore-backed (`documents.retention.*`, `documents.gc.*`).
The collector is a crash-safe **mark/sweep** with the database as the sole
authority:

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

## Backup

The nightly native backup snapshots `documents.db` **first**
(`sqlite3.backup()` → point-in-time), then copies the content it references and
verifies every blob/manifest checksum — a torn DB/blob snapshot is impossible
because blob files are always written before their rows. Backups land under
`kazma-data/backups/document-store-<ts>/` with a `manifest.json`.

## Migration

`kazma migrate export|verify|import` bundles now carry `documents.db` + the
content-addressed tree + manifests. documents.db has no embedded paths, so it
needs no rewrite; the importer restores it and the tree into the target's
document-store root through the existing atomic stage → verify → backup → swap
flow.

## Multi-replica readiness

When `KAZMA_DATABASE_URL`/`KAZMA_DB_BACKEND=postgres` is configured, document
**job claiming** uses a real Postgres queue with `SELECT … FOR UPDATE SKIP
LOCKED` + compare-and-swap leases — safe for multiple replicas. SQLite remains
the single-node default (WAL + `BEGIN IMMEDIATE`).

Document **metadata** (documents/versions/blobs/artifacts) is still SQLite, so a
multi-replica deployment is **degraded**: `GET /api/documents/ops/readiness`
reports `metadata_single_replica` honestly. Do not run more than one replica
against a shared document store until metadata is ported to Postgres.
