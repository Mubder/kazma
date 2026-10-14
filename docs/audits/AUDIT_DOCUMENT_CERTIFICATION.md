# Document Intelligence Platform — Release Certification Report

> Historical report. The 2026-10-12 audit found unresolved industrial-readiness
> gaps; the verdict below is not a current industrial qualification. The local
> CLI's `--soak` option performs extended smoke only, and its metadata and
> multi-day qualification gates are NOT RUN. Current remediation is tracked in
> [the hardening ledger](../plans/DOCUMENTS_INDUSTRIAL_HARDENING_GOAL.md).
> The verified state on 2026-10-13 is the next section; the 2026-08-11 report
> follows it, unchanged except that a paragraph which split its gate table now
> follows the table. Its superseded statements are listed below.

## Verified state on 2026-10-13

Sources: the hardening ledger above (requirements F01-F10, Q01-Q05 and their
dated evidence) and the run receipt
`session-artifacts/documents-continuous-run-2026-10-13.json` (logs and source
hashes; a local, git-ignored file, not in the repository). This is verification by the people who made the changes, on one
Windows 11 workstation with disposable PostgreSQL 16 and Docker Desktop
containers. It is not an independent review, not a sustained soak, and not a
deployment qualification. On 2026-10-13 the changes were in the working tree,
not yet committed, so no CI run had seen them.

### What the certifier checks

`scripts/certify_documents.py` runs `kazma_core/documents/certification.py`;
`tests/test_document_certification_phase10.py` (30 tests in 7 groups, part of
CI's test job) runs the same engine.

| Check | What it does | Can report |
|---|---|---|
| `hostile_corpus` | 26 generated cases; the committed manifest is compared field by field (SHA-256 and size excluded: ZIP compression differs between zlib builds). `sniff` cases are sniffed in the certifying process, `parse` cases go through the isolated worker, the `sandbox` case has its result removed | PASS, FAIL |
| `bounded_performance_event_loop_resources` | 3 parses (`--soak`: 100 by default) at concurrency 2: duration, event-loop stall, parent-process peak allocations, thread/handle growth, leftover run directories | PASS, FAIL |
| `runtime_capabilities` | parser, renderer, mutator and OCR readiness of the configured profile | PASS, CONDITIONAL, FAIL |
| `safe_rollout` | the live rollout mode | PASS, CONDITIONAL |
| `worker_isolation` | the configured profile: `container` passes only with a ready broker whose image matches this code; `process` is CONDITIONAL (no filesystem or network confinement); an invalid value fails | PASS, CONDITIONAL, FAIL |
| `extraction_fidelity` | the versioned fidelity corpus against its release targets | PASS, CONDITIONAL |
| `backup_restore_sqlite` | one document ingested, processed, backed up, verified, restored and read back, on a SQLite store | PASS, FAIL |
| `postgres_multi_replica`, `metadata_multi_replica`, `multi_day_soak`, `external_security_review` | not performed by the certifier | NOT RUN, always |

`default_ready` is always false. With the default `process` profile the
overall status is CONDITIONAL.

### Requirements

| ID | State | Evidence and what is open |
|---|---|---|
| F01 PDF active content | Implemented | One bounded policy in the worker on intake, every mutation input and output (redaction included) and every rendered PDF; no user PDF opened outside the worker (class gate). PDF policy tests 52 passed; hostile corpus 19 → 26 cases. Open: a broader real-world PDF corpus, independent review |
| F02 Worker containment | In progress | `container` profile (broker-owned, attested per-job containers) qualified on Docker Engine 29.8.2 (Docker Desktop, Windows 11): real-Docker qualification 22 passed on the rebuilt images, topology run 10/10 checks. Open: the Linux Docker Engine run (new CI job, not yet run), rootless Docker/Podman/Kubernetes, gVisor/Kata, native OS jails for `process`, the Unix process-count cap. The `process` profile stays unqualified for hostile documents |
| F03 Live policy and revocation | Implemented | Remote-parse consent read again before each provider, on SQLite and a real PostgreSQL settings store. Open: deployment qualification |
| F04 Egress identity and audit | Implemented | Immutable job identity on every attempt, audited fallback between providers, audit outage refuses upload; Postgres-marked Documents/settings tests on PostgreSQL 16.15: 68 passed, 1 skipped (an intentionally SQLite-shaped assertion) |
| F05 Authorized idempotency and job reads | Implemented | Every job read names its actor; an unreadable job reads as unknown on Web, agent tools and gateway. `tests/test_document_job_read_matrix.py` 22 passed. Chat surfaces act as one `agent` principal by design |
| F06 Backup and restore | Implemented | Coordinated Postgres backup (one snapshot for dump and file references, GC pins, checksums, restore into an empty store only). PostgreSQL 16 drills: a fixture store restored, started and every job ready in 3.8 s; 1,000 documents (62.5 MiB): restore 1.39 s, first document readable 2.45 s after the restore began; 10,000 documents (625 MiB): backup 9.64 s, verify 5.17 s, restore 10.67 s, first document readable 11.77 s after the restore began. Open: RPO/RTO on deployment hardware and store sizes |
| F07 Error classification | Implemented | Outages (scanner, disk, metadata store) retried then dead-lettered, never rejected as content. `tests/test_document_fault_transitions.py` 11 passed; the old handling fails 7 of them |
| F08 Admission | Implemented | Web upload answered before its body is read (10 passed, body-read counter), fair claims across tenants, tenant quota on both backends, JSON bodies capped at 2 MiB. Open by design: fewest-running fairness, per-process spool slots |
| F09 Order and provenance | Implemented | Versioned EN/AR/bilingual fidelity corpus; Arabic PDF numbers, ligatures and table cells repaired |
| F10 Honest certification | Implemented | The report names the configured profile, worker image and backends; `process` is never reported as confined; certification tests 30 passed |
| Q01 Crash recovery | Implemented for single-host recovery | Real child processes killed at 7 intake and 4 job stages: 24 passed on SQLite and 24 on PostgreSQL 16.15; lease takeover with fencing 8 + 8; a stale owner in a real second container (paused, cut off) taken over and fenced, 4 passed. Open: separate physical machines |
| Q02 Fidelity thresholds | In progress | Corpus v3: 16 of 19 cases meet release targets; not qualified: `ar-ocr`, `en-ocr-degraded`, `ar-ocr-table` (Arabic-Indic digits are not read by Tesseract). One real scan (5 pages of one booklet) measured. Open: more real scans (handwriting, forms, other producers) |
| Q03 Sustained operation | Open | Only a 2-minute pilot of the soak harness: 13 submitted, 13 ready, 3 restarts, 2 injected crashes, p95 15.7 s. No 48-72 h soak |
| Q04 Independent review | Open | No external review |
| Q05 Documentation | In progress | Security, ops and certification documents reviewed against the code on 2026-10-13 |

Whole-repository runs on the final tree (receipt): `scripts/fast_test.py`
14,966 passed, 158 skipped, 3 failed (two fixed and re-run: 28 passed, 5
skipped; the third, the metrics receipt, needs the commit); every
Postgres-marked test on PostgreSQL 16: 390 passed, 9 skipped.

### Superseded in the 2026-08-11 report

- 19 hostile cases: now 26 (XFA, remote GoTo, network-share link,
  SubmitForm, untyped embedded file, rendition, and a navigation-only control
  that must still parse).
- "Document metadata is SQLite only": a Postgres metadata store exists
  (`repository_pg.py`, `KAZMA_DOCUMENTS_METADATA_BACKEND`); readiness reports
  single-replica whenever SQLite serves. Multi-replica operation is still not
  qualified (the certifier's checks are NOT RUN).
- "Postgres job claims not certified in CI": CI's Postgres job runs every
  `@pytest.mark.postgres` test on `postgres:16`; no multi-replica deployment
  has been qualified.
- "Crash recovery states preserved 3/3 (PENDING/ACCEPTED/PROCESSING)": those
  are not the job states (AGENTS.md §19 B); crash recovery is measured by the
  Q01 matrix above.
- "GC still SQLite-SQL only": GC marks and sweeps on Postgres metadata too.
- "Test file: 29 tests": 30 tests in 7 groups.
- Optional engines: in the `container` profile engine readiness is the worker
  image's own report, not the host's packages.

---

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
| Postgres multi-replica (jobs) | **NOT RUN** | Requires live Postgres backend (CI uses SQLite) |
| Metadata multi-replica | **CONDITIONAL** | Document metadata is SQLite only; certified for single replica |
| Multi-day soak | **NOT RUN** | Opt-in only (pass `--soak` to `scripts/certify_documents.py`) |
| External security review | **NOT RUN** | No independent review represented |

**Gate split:** `scripts/certify_documents.py` emits hostile/perf/capabilities/rollout plus explicit NOT RUN / CONDITIONAL items. Architecture, a11y, and the full crash matrix are enforced in `tests/test_document_certification_phase10.py`, not only the CLI.

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
