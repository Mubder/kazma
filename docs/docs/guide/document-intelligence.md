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
- [Tools catalog](../reference/tools-catalog.md) — agent-facing document tools
- [Document processing ops](../ops/document-processing.md) — metrics, backpressure, GC, backup
- [Document security](../security/document-security.md) — threat model and security architecture
- [API routes](../reference/api-routes.md) — `/api/documents/*` endpoints

---

## Architecture

```
User Input (file upload / URL / paste / tool call)
    │
    ▼
DocumentIngestionService  ◄── tenant + actor ACL, capacity gate, audit
    │
    ▼
Durable Job Queue  ◄── CAS transitions, leases, retries, dead-letter
    │
    ▼
DocumentService  ◄── single execution boundary
    │
    ├─ Sniff (MIME, OOXML defenses, PDF policy)
    ├─ Parse (isolated subprocess per format)
    ├─ OCR  (per-page quality routing, Tesseract)
    ├─ Index (structural chunking → KnowledgeStore)
    ├─ Generate / Convert (isolated render workers)
    └─ Mutate / Redact (conservative raster redaction)
```

**One boundary, one path.** Every document operation routes through
`DocumentService`. Gateway adapters, the Web UI API, native tools, and the TUI
all talk to `DocumentIngestionService` — none import parser internals.

---

## Core features

### Secure intake

- **Streamed attachment limits:** 20 MiB per file, 10 files per request, 50 MiB aggregate.
- **Content-addressed immutable storage:** SHA-256 deduplication; quarantine →
  permanent promotion on verified parse.
- **MIME sniffing before parsing:** Extension/content mismatch rejection,
  OOXML structural defense (XXE, macros, external relationships, nested archives,
  compression bombs), PDF policy enforcement (no encryption, active content, or
  embedded files by default).
- **All content is fenced:** Every byte reaching the LLM passes through
  `<kazma:data untrusted>` prompt fencing (§7).

### Isolated parsing

Every parse/OCR/mutation job runs in a **host subprocess** (`python -I`):

- Scrubbed environment (no parent env vars, no user site packages).
- Per-process memory and CPU limits.
- Configurable timeout (killed, not leaked).
- On Windows: Job Objects with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
- Crashes, timed-out jobs, and missing output all fail closed — no
  partial/broken data escapes the sandbox.

### Multilingual OCR

- **Per-page quality routing:** Pages with high text density skip OCR (native
  text preserved); image-heavy / text-poor pages route to Tesseract.
- **Language probing:** Auto-detection of Arabic vs English (plus configurable
  additional languages).
- **Deterministic merging:** OCR text merged with native text at
  coordinate-level precision; confidence scores preserved.
- **Fallback:** Large pages rasterized at configurable DPI (200 default).

### Knowledge indexing

- **Structural chunking:** Documents are split at natural boundaries (page,
  section, table) with overlap, preserving citations.
- **Active-version switching:** Index always reflects the current published
  version; tombstone-aware.
- **Tenant isolation:** Chunks are scoped to tenant + actor.
- **Single-fence retrieval:** Retrieved context always wrapped in
  `<kazma:data untrusted>` fences.

### Generation & Conversion

- **Document generation:** JSON/markdown → PDF/DOCX/HTML via isolated
  render workers. Payload bounded to 1 MiB. Results re-ingested as
  tenant-owned documents.
- **Format conversion:** Transform any supported input into a target format.
- **PDF operations:** Info extraction, split, merge, form field fill.
- **Redaction:** Conservative raster-based redaction with pre/post byte-count
  verification. Interactive confirmation via `kazmaConfirm`.

---

## Operations

### Live configuration

All limits, retention, GC, capacity, and rollout flags are ConfigStore-backed.
Toggle via `PUT /api/settings/single` or `kazma.yaml` without restart.

| Area | Key prefix |
|---|---|
| Intake limits | `documents.intake_*` |
| Parser limits | `documents.max_*`, `documents.security_*` |
| OCR | `documents.ocr_*` |
| Retention / GC | `documents.retention_*`, `documents.gc_*` |
| Backpressure | `documents.capacity_*` |
| Rollout | `documents.*` (`enabled`, `shadow`, `default_authoritative`) |

### Queue health

Jobs flow through a durable state machine: PENDING → ACCEPTED → PROCESSING →
COMPLETED / FAILED / DEAD_LETTER / REJECTED / CANCELLED.

- **Leases** with heartbeat prevent stuck jobs.
- **Retry** with exponential backoff (configurable).
- **Dead-letter** after max retries for operator review.
- **Recovery at restart:** Orphaned ACCEPTED/PROCESSING jobs are reclaimed.

### Backpressure

Intake is refused with truthful HTTP status codes when limits hit:

| Condition | Status | `Retry-After` |
|---|---|---|
| Storage free-space floor | 507 | yes |
| Global queue ceiling | 503 | yes |
| Per-tenant queued/active cap | 429 | yes |
| Intake rate/byte window | 429 | yes |

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

### Backup & Migration

The nightly native backup snapshots `documents.db` first, then the referenced
content-addressed tree. Every blob checksum is verified. Migration bundles via
`kazma migrate` carry the full document store.

---

## Rollout controls

The platform supports **canary deployment** with live toggles:

| Mode | `enabled` | `shadow` | `authoritative` | Effect |
|---|---|---|---|---|
| Disabled | false | — | — | No durable writes; existing data preserved |
| Shadow | true | true | false | Writes accepted, not default path |
| Compatibility | true | false | false | Writes accepted, opt-in routing |
| Authoritative | true | false | true | All document operations route here |

**Safe rollback:** Disabling stops new writes but preserves all blobs, jobs,
manifests, and metadata. Data is never deleted on rollback.

---

## Certification

Release certification runs deterministic hostile-corpus checks, bounded
performance smoke, capability probes, and rollout verification. Run:

```bash
python scripts/certify_documents.py              # CI smoke
python scripts/certify_documents.py --soak        # Opt-in soak (100 iterations)
python scripts/certify_documents.py --output report.json
```

The hostile corpus is **programmatically generated** — no opaque binaries in
the repository. Each case has a reviewed description, expected disposition
(reject/fenced), and expected error code. The committed manifest under
`tests/fixtures/documents/hostile_manifest.json` is verified at release time.

---

## Multi-replica

When Postgres is configured, job claiming uses `SELECT … FOR UPDATE SKIP
LOCKED` — safe for multiple replicas. Document metadata remains SQLite, so
`GET /api/documents/ops/readiness` honestly reports `metadata_single_replica`.

Do not run more than one replica against a shared document store until
metadata is ported to Postgres.

---

## Limits at a glance

| Resource | Default | Configurable |
|---|---|---|
| Max file size | 50 MiB | `documents.intake_max_bytes` |
| Max files per request | 10 | `documents.intake_max_files` |
| Max pages | 500 | `documents.max_pages` |
| Max rows per sheet | 100,000 | `documents.max_rows_per_sheet` |
| Max cells | 2,000,000 | `documents.max_cells` |
| Max expanded bytes | 256 MiB | `documents.max_expanded_bytes` |
| Max archive members | 10,000 | `documents.max_archive_members` |
| OCR DPI | 200 | `documents.ocr_dpi` |
| Worker timeout | 300 s | `documents.worker_timeout_seconds` |
| Worker memory | 1 GiB | `documents.worker_memory_mb` |
| Tenant quota | 10 GiB | `documents.quota_tenant_bytes` |
| Audit retention | 365 days | `documents.retention_audit_days` |
