# Document Intelligence Platform — Release Certification Report

**Version:** v0.7.0  
**Date:** 2026-08-11 (cert suite + Phase 10 recheck; earlier draft stamped 2026-07-28)  
**Certifier:** Architectural audit + automated certification suite  
**Scope:** Full document intelligence subsystem (Phases 0–9 implementation, Phase 10 certification)

---

## Executive Summary

The Kazma Document Intelligence Platform has completed its 10-phase buildout and
certification. The platform provides secure, isolated, durable document
processing with content-addressed immutable storage, hostile-input hardening,
prompt-fence enforcement, and live operational controls. All 192 existing tests
pass; the certification suite adds 29 new tests covering hostile corpus
verification, crash/recovery, architecture compliance, rollout safety, a11y,
and backpressure.

**Certification verdict: CANARY-READY.** The platform is ready for opt-in
deployment with the `documents.enabled=true` flag. Default-authoritative is not
recommended until optional engines (fitz, WeasyPrint, LibreOffice) are
provisioned and a multi-day soak is complete.

---

## Gate Results

| Gate | Status | Detail |
|---|---|---|
| Hostile corpus determinism | **PASS** | 19/19 cases matched committed manifest; all SHA-256s unique |
| Hostile corpus rejection | **PASS** | Every hostile sample rejected or retained only inside prompt fence |
| Bounded performance / event loop | **PASS** | Bounded parser work stayed off event loop; peak memory within limit; no resource leak |
| Runtime capabilities | **CONDITIONAL** | Core parsers ready; fitz, WeasyPrint, LibreOffice not installed (render/conversion degraded) |
| Safe rollout | **PASS** | Live mode reports compatibility; rollout safely disables writes without data loss |
| Architecture compliance | **PASS** *(pytest Phase 10)* | Gateway and UI must not import parser/OCR/renderer workers; durable path uses DocumentIngestionService; execution uses DocumentService (chat may call DocumentService transiently) |
| A11y / label compliance | **PASS** *(pytest Phase 10)* | Document UI carries ARIA labels, x-cloak, `dir="auto"` on content preview |
| Backpressure | **PASS** *(pytest Phase 10)* | Capacity guard returns degraded_reasons; all limits have positive defaults |
| Crash / recovery matrix | **PASS** *(pytest Phase 10 + jobs tests)* | Lease expiry reclaims exactly-once, idempotent enqueue, cancel/retry/dead-letter transitions |

**Gate split:** `scripts/certify_documents.py` emits hostile/perf/capabilities/rollout plus explicit NOT RUN / CONDITIONAL items. Architecture, a11y, and the full crash matrix are enforced in `tests/test_document_certification_phase10.py`, not only the CLI.
| Postgres multi-replica (jobs) | **NOT RUN** | Requires live Postgres backend (CI uses SQLite) |
| Metadata multi-replica | **CONDITIONAL** | Document metadata is SQLite only; certified for single replica |
| Multi-day soak | **NOT RUN** | Opt-in only (pass `--soak` to `scripts/certify_documents.py`) |
| External security review | **NOT RUN** | No independent review represented |

---

## Key Performance Indicators

| Metric | Value | Threshold | Pass |
|---|---|---|---|
| Core parsers ready | ≥2 | ≥1 | ✅ |
| Hostile corpus cases | 19 | ≥15 | ✅ |
| Unique SHA-256 in corpus | 19/19 | 100% | ✅ |
| Crash recovery states preserved | 3/3 (PENDING/ACCEPTED/PROCESSING) | 3/3 | ✅ |
| Architecture compliance (gateway) | 0 violations | 0 | ✅ |
| Architecture compliance (UI) | 0 violations | 0 | ✅ |
| Rollout rollback safety | preserves data | preserves data | ✅ |
| A11y (ARIA labels) | present | present | ✅ |
| A11y (x-cloak) | present on all x-show | present | ✅ |
| Capacity degraded_reasons | machine-readable | machine-readable | ✅ |

---

## Known Limitations

1. **Optional engines missing (fitz, WeasyPrint, LibreOffice).** PDF
   redaction, HTML-to-PDF generation, and format conversion via LibreOffice
   are degraded. The API returns actionable HTTP 422/503 with engine
   availability details. Fix: install `pymupdf`, `weasyprint`, and
   `libreoffice` on the deployment host.

2. **Document metadata is SQLite only.** Multi-replica deployments are
   degraded — `GET /api/documents/ops/readiness` reports
   `metadata_single_replica` honestly. Do not run >1 replica against a
   shared document store. Fix: port metadata tables to Postgres (tracked in
   roadmap).

3. **Postgres job claims not certified in CI.** The `SELECT ... FOR UPDATE
   SKIP LOCKED` path requires a live Postgres backend. CI certifies the
   SQLite WAL path. Fix: add a Postgres service to CI.

4. **No multi-day soak.** The opt-in `--soak` flag exercises 100+ iterations
   but has not been run for hours/days. Fix: schedule a soak run before
   default-authoritative promotion.

5. **No external security review.** The hostile corpus is reviewed but not
   independently audited. Fix: commission a penetration test.

---

## Residual execution note (2026-08-10)

Post-docs residual sprint shipped:

- Live ClamAV malware scan path (`documents/malware.py`)
- Settings → Documents UI
- Postgres metadata repository (`repository_pg.py`; GC still SQLite-SQL only)
- `document-platform` pip extra
- Soak sample: 20 iterations PASS (performance gate); report under
  `session-artifacts/document-cert-soak-residual.json`
- Docusaurus production build

**External security review** remains NOT RUN — requires an independent firm;
this report is not a substitute.

---

## Rollout Recommendation

| Phase | Config | Duration | Action |
|---|---|---|---|
| **1. Canary** | `enabled=true`, `shadow=true` | 1–2 weeks | Test on non-production workloads |
| **2. Opt-in** | `enabled=true`, `shadow=false` | 2–4 weeks | Direct traffic with feature flag |
| **3. Default** | `enabled=true`, `default_authoritative=true` | After soak + engine provisioning | All document paths route through platform |

**Safe rollback at any phase:** Set `enabled=false` — stops new durable writes
but preserves all existing blobs, jobs, manifests, and metadata.

---

## Test Artifacts

- **Test file:** `tests/test_document_certification_phase10.py` (29 tests, 7 groups)
- **Hostile corpus manifest:** `tests/fixtures/documents/hostile_manifest.json`
- **Certification script:** `scripts/certify_documents.py`
- **Hostile corpus generator:** `kazma_core/documents/hostile_corpus.py`
- **Certification engine:** `kazma_core/documents/certification.py`

---

## Changes Since Last Audit

| Change | Type | Description |
|---|---|---|
| Phase 9 review fixes | Bug | GC data-loss race (dedup mtime + live-reference recheck); backup false-success (referenced-blob verification); migration export missing-blob raise; telemetry span exception masking; shutdown race (GC thread vs repo close) |
| Certification suite | New | 29 tests in 7 groups: hostile corpus determinism, runner, crash matrix, architecture compliance, rollout, a11y, backpressure |
| Committed hostile manifest | New | `tests/fixtures/documents/hostile_manifest.json` — deterministic verification |
| Document intelligence guide | New | `docs/docs/guide/document-intelligence.md` |
| Document security architecture | New | `docs/docs/security/document-security.md` |
| Certification report | New | `docs/audits/AUDIT_DOCUMENT_CERTIFICATION.md` (this file) |
| Sidebar + nav updates | New | All new docs wired into sidebar, intro.md, and system map |
