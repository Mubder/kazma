---
id: document-phases
title: Document Intelligence — Phase map (0–10)
sidebar_label: Document phases
description: Code-backed map of Document Intelligence build phases 0–10, tests, and operator entry points.
---

# Document Intelligence — Phase map (0–10)

This page is the **source of truth for phase narrative**. Product usage lives in
[Document Intelligence](./document-intelligence.md); security in
[Document security](../security/document-security.md); ops in
[Document processing](../ops/document-processing.md).

**Start here if you only want to use the product:** [Document Intelligence](./document-intelligence.md)
(operator walkthrough + install). Use this phase map for engineering history
and test ownership.

Phases were implemented primarily as code + tests; only phases **9–10** have
dedicated CHANGELOG product entries. The map below reconstructs **0–8** from
modules and phase-named tests so operators and contributors share one timeline.

**Post-phase residuals (malware, Settings UI, Postgres metadata, soak):**
`docs/plans/DOCUMENT_RESIDUALS_GOAL.md` + CHANGELOG “Document residuals” entry.

**Post-phase hardening (2026-10-13):** isolation profiles, PDF policy,
admission and recovery, backup/restore, fidelity — see
[Post-phase hardening](#post-phase-hardening) below and the tracker
`docs/plans/DOCUMENTS_INDUSTRIAL_HARDENING_GOAL.md`.

---

## At a glance

| Phase | Name | Operator impact | Primary tests |
|------:|------|-----------------|---------------|
| 0–2 | Foundation | Models, config, CAS storage, repository, sandbox, job queue | `test_documents_models`, `config`, `storage`, `repository`, `document_sandbox`, `document_jobs` |
| 3 | Sniff / intake policy | Hostile MIME/OOXML/PDF rejection before parse | Hostile corpus + processor security |
| 4 | Parsers | Format parsers behind isolated worker | `test_document_parsers_phase4` |
| 4+ | PDF Arabic / multi-engine | PyMuPDF primary + pypdfium2 bake-off + layout reading order + fuzzy RTL verifier (post-phase product hardening) | `test_document_parsers_phase4` (Arabic/layout/multi-engine), `test_unified_document_layer` (RTL verifier) |
| 5 | OCR | Multilingual quality-routed OCR | `test_document_ocr_phase5` |
| 5+ | OCR Arabic scans | `ara+eng` language order; replace presentation-form/CID native layers | `test_document_ocr_phase5`, scanned PDF tests in parsers phase4 |
| 6 | Knowledge | Structural chunking → library index | `test_document_knowledge_phase6` |
| 7 | Generate / convert / redact | Isolated render + verified redaction | `test_document_generation_phase7` |
| 8 | Surfaces | Ingestion coordinator, Web API, tools, gateway, TUI | `test_document_ingestion_phase8`, `*_api_phase8`, `*_actions_phase8`, `*_platform_tools_phase8` |
| 9 | Ops & scale | Audit, capacity, GC, metrics, backup, PG job claims | `test_document_operations_phase9` |
| 10 | Certification | Hostile corpus, cert CLI, a11y, crash matrix | `test_document_certification_phase10` |
| post | Hardening (2026-10-13) | Isolation profiles, one PDF active-content policy, durable admission + intake leases, outage retries, job-read ACL, Postgres backup/restore + index reconcile, fidelity corpus, OCR tables | [Post-phase hardening](#post-phase-hardening) |

Full document suite (when run as the document test files): **217 passed, 2 skipped**
(optional engines) as of the Phase 10 recheck window — history; the suite has
grown since (see [Post-phase hardening](#post-phase-hardening)). Re-run to
confirm on your tree. Postgres-marked tests skip without a Postgres server, and
the real-Docker, container-lease, large-restore and real-scan drills run only
when their opt-in variables are set.

```powershell
& .venv\Scripts\python.exe -m pytest tests/test_document*.py tests/test_documents*.py -q
```

The glob leaves out a few document gates with other names:
`tests/test_pdf_openers_stay_in_workers.py`, `tests/test_unified_document_layer.py`,
`tests/test_docx_rtl_visual.py` and `tests/test_weasyprint_renderer.py`.

---

## Phase 0–2 — Foundation

**Modules:** `models.py` (DocumentIR, `DocumentJobState`), `config.py`,
`storage.py` (content-addressed blobs), `repository.py`, `sandbox.py`,
`jobs.py` (SQLite durable queue).

**What shipped:** immutable document/version/blob model; live ConfigStore config;
CAS paths; tenant-scoped repository; isolated subprocess runner; job CAS +
leases.

**Operator entry:** none standalone — used by all later phases.

---

## Phase 3 — Sniff / intake policy

**Modules:** `sniff.py` (+ security policy flags on config).

**What shipped:** extension/content mismatch, OOXML XXE/macro/external-rel,
compression bombs, PDF encrypt/active/polyglot policy, UTF-8 validity.

**Operator entry:** automatic on every ingest/parse; see hostile corpus.

---

## Phase 4 — Parsers

**Modules:** `parsers/*`, `parser_worker.py`, `registry.py`, `service.py`.

**What shipped:** consolidated format parsers running **only** inside the
isolated worker; capability readiness reporting.

**Tests:** `tests/test_document_parsers_phase4.py`.

---

## Phase 5 — OCR

**Modules:** `ocr/*` (Tesseract, raster, quality routing).

**What shipped:** per-page quality routing; language packs; similarity-based
native/OCR merge; DPI raster fallback.

**Tests:** `tests/test_document_ocr_phase5.py`.

---

## Phase 6 — Knowledge indexing

**Modules:** `knowledge.py`, `indexer.py`.

**What shipped:** structural chunking into Knowledge libraries; explicit index /
unindex; fenced search.

**Tests:** `tests/test_document_knowledge_phase6.py`.  
**Bridge:** [Knowledge Library](./knowledge-library.md#document-intelligence--library-bridge).

---

## Phase 7 — Generation, conversion, redaction

**Modules:** `operations.py`, `mutation.py`, `mutation_worker.py`,
`renderer_worker.py`, `renderers/*`, `artifacts.py`.

**What shipped:** generate + re-ingest; convert; PDF split/merge/info/fill;
verified redaction (optional engines may skip some tests).

**Tests:** `tests/test_document_generation_phase7.py` (some cases skip without
engines).

---

## Phase 8 — Surfaces (product paths)

**Modules:** `ingestion.py` (coordinator), `kazma_ui/documents_api.py`,
`documents.html` / `documents.js`, gateway `/documents`,
`kazma_skills/.../document_platform`, `kazma_tui/documents.py`.

**What shipped:** one durable public boundary for Web, API, tools, chat, TUI.

**Tests:**

- `test_document_ingestion_phase8.py`
- `test_documents_api_phase8.py`
- `test_document_actions_phase8.py`
- `test_document_platform_tools_phase8.py`

**Operator entry:** `/documents` UI · `/api/documents/*` · `/documents` slash ·
`document_*` tools · TUI Documents tab.

---

## Phase 9 — Operations & scale

**Modules:** `telemetry.py`, `audit.py`, `retention.py`, `capacity.py`,
`backup.py`, `jobs_pg.py`.

**What shipped:** Prometheus-safe metrics, append-only audit, mark/sweep GC,
backpressure (429/503/507), document backup, Postgres **job** multi-replica
claims, readiness honesty for SQLite metadata.

**Tests:** `tests/test_document_operations_phase9.py` (**29** cases at the
phase; **31** test functions on 2026-10-13).  
**CHANGELOG:** Unreleased Phase 9 entry.  
**Ops guide:** [Document processing](../ops/document-processing.md).

---

## Phase 10 — Certification, a11y, crash recovery

**Modules:** `certification.py`, `hostile_corpus.py`, cert script
`scripts/certify_documents.py`, UI a11y on Documents page.

**What shipped:** deterministic hostile corpus (19 cases; 26 since the
2026-10-13 PDF policy work), cert CLI JSON report, canary readiness,
performance smoke, phase10 pytest groups (architecture, crash, a11y, rollout,
backpressure).

**Tests:** `tests/test_document_certification_phase10.py` (**29** cases at the
phase; **30** test functions on 2026-10-13, including one that the report
names the profile it actually ran).  
**Audit:** `docs/audits/AUDIT_DOCUMENT_CERTIFICATION.md` (repo path, outside Docusaurus).  
**CHANGELOG:** Unreleased Phase 10 entry + post-cert recheck notes.

**CLI vs pytest:** the CLI certifier runs the hostile corpus, performance
smoke, capabilities, rollout, the configured isolation profile
(`worker_isolation`), the fidelity corpus (`extraction_fidelity`) and a real
SQLite backup/restore (`backup_restore_sqlite`), and reports Postgres
multi-replica, metadata multi-replica, multi-day soak and external review as
NOT RUN. `--soak` is an extended smoke (100 iterations), not a multi-day soak.
Architecture / a11y / full crash matrix are enforced in the pytest file.

```bash
python scripts/certify_documents.py
python scripts/certify_documents.py --soak
```

---

## Post-phase hardening (2026-10-13) {#post-phase-hardening}

An audit of the platform on 2026-10-12 was followed by a hardening pass
tracked requirement by requirement, with its evidence, in
`docs/plans/DOCUMENTS_INDUSTRIAL_HARDENING_GOAL.md`. Each row is current
behaviour, with the modules that hold it and the tests that gate it (most with
the old behaviour as a negative control).

| ID | What holds now | Modules | Tests |
|---|---|---|---|
| F01 | One bounded PDF active-content policy on intake, on every mutation input and output and on every rendered PDF, always inside the worker; outside the worker modules a PDF library opens only PDFs Kazma itself just rendered (declared checks) | `pdf_policy.py`, `sniff.py`, `mutation_worker.py`, `renderer_worker.py` | `test_document_pdf_policy`, `test_pdf_openers_stay_in_workers` |
| F02 | Two isolation profiles: `process` (default; Windows Job Object or Unix rlimits, no filesystem/network isolation) and `container` (broker-owned, attested, one container per job; engine readiness from the image); `security.execution.container_required` refuses `process`; structural preflight and local salvage run in the worker | `sandbox.py`, `worker_container.py`, `worker_broker.py`, `worker_bundle.py`, `worker_entry.py`; `deploy/document-worker/Dockerfile`, `deploy/docker-compose.document-broker.yml` | `test_document_sandbox`, `test_document_worker_preflight`, `test_document_worker_container`, `test_document_worker_container_docker` (opt-in) |
| F03 | One immutable policy snapshot per operation; restart-bound settings reported as pending; unreadable policy refuses; remote-parse consent read again before each provider | `policy.py`, `config.py`, `extract_salvage.py` | `test_document_live_policy` |
| F04 | Every remote-parse attempt durably audited with the job's identity before upload; fallback to the next provider; no ambient proxy, no redirects, capped answers | `extract_salvage.py`, `ingestion.py` | `test_document_egress_hardening`, `test_document_egress_pg` (Postgres) |
| F05 | Every job read names its actor; an unreadable job answers like an unknown one; request-bound idempotency; audit read admin-only | `ingestion.py`, `documents_api.py` | `test_document_job_read_matrix`, `test_document_access_hardening`, `test_document_jobs_pg_idempotency` (Postgres) |
| F06 | Verified SQLite backup; coordinated Postgres backup at one snapshot with GC pins; restore only into empty targets; index reconciled at every worker start | `backup.py`, `backup_pg.py`, `retention.py`, `knowledge.py`, `scripts/document_pg_backup.py` | `test_document_backup_restore_hardening`, `test_document_pg_backup_restore`, `test_document_backup_pins`, `test_document_reindex_after_restore`, `test_document_pg_restore_drill_scale` (opt-in) |
| F07 | Outages (scanner, disk, metadata store, broker) retried then dead-lettered; only content is rejected; a failed outcome write never ends the worker loop | `errors.py`, `worker.py`, `ingestion.py` | `test_document_fault_transitions` |
| F08 | Durable admission reservations; Web upload answered before its body; bounded JSON bodies; spools under the storage root; tenant quota enforced on Postgres metadata; claims fair across tenants | `admission.py`, `capacity.py`, `jobs.py`, `jobs_pg.py`, `repository_pg.py`, `documents_api.py` | `test_document_admission_hardening`, `test_document_capacity_hardening`, `test_document_upload_admission`, `test_document_json_body_cap`, `test_document_claim_fairness`, `test_document_quota_both_backends` |
| F09 / Q02 | Versioned fidelity corpus (version 3: 19 cases, 16 meet the release targets); Arabic PDF lines re-read glyph by glyph with bidi-checked numbers; OCR numbers in logical order; ruled scanned tables read cell by cell | `fidelity.py`, `parsers/pdf_rtl.py`, `arabic.py`, `ocr/tables.py`, `ocr/tesseract.py` | `test_document_fidelity`, `test_document_pdf_rtl`, `test_document_number_order`, `test_document_ocr_tables`, `test_document_real_scans` (opt-in) |
| F10 | The certification report names the isolation profile, image and backends it ran with; `process` is CONDITIONAL; soak and external review NOT RUN | `certification.py` | `test_document_certification_phase10`, `test_document_certification_honesty` |
| Q01 | Process death at every intake and job stage recovered; owners proven dead rolled back at start and every minute; cross-host intakes hold a lease and are fenced after a takeover | `admission.py`, `ingestion.py`, `jobs.py` | `test_document_crash_recovery`, `test_document_intake_lease`, `test_document_intake_lease_containers` (opt-in) |

**Open:** a 48–72 hour soak (`scripts/document_soak.py` has run a two-minute
pilot only), an independent security review, multi-replica qualification,
rootless/Kubernetes and stronger-kernel (gVisor/Kata) container profiles, a
filesystem/network jail for the `process` profile, Arabic OCR, and deployment
RPO/RTO. [Document Intelligence](./document-intelligence.md#not-qualified)
lists the limits for operators.

---

## Test file index

| File | Phase focus |
|------|-------------|
| `tests/test_documents_models.py` | 0–2 |
| `tests/test_documents_config.py` | 0–2 |
| `tests/test_documents_storage.py` | 0–2 |
| `tests/test_documents_repository.py` | 0–2 |
| `tests/test_document_sandbox.py` | 0–2 |
| `tests/test_document_jobs.py` | 0–2 / queue |
| `tests/test_document_worker.py` | worker |
| `tests/test_document_processor_security.py` | 3 / security |
| `tests/test_document_parsers_phase4.py` | 4 |
| `tests/test_document_ocr_phase5.py` | 5 |
| `tests/test_document_knowledge_phase6.py` | 6 |
| `tests/test_document_generation_phase7.py` | 7 |
| `tests/test_document_ingestion_phase8.py` | 8 |
| `tests/test_document_actions_phase8.py` | 8 |
| `tests/test_documents_api_phase8.py` | 8 |
| `tests/test_document_platform_tools_phase8.py` | 8 |
| `tests/test_document_operations_phase9.py` | 9 |
| `tests/test_document_certification_phase10.py` | 10 |
| `tests/fixtures/documents/hostile_manifest.json` | 10 corpus SoT (26 cases) |
| `tests/test_document_pdf_policy.py`, `tests/test_pdf_openers_stay_in_workers.py` | post / F01 |
| `tests/test_document_worker_preflight.py`, `tests/test_document_worker_container.py`, `tests/test_document_worker_container_docker.py` | post / F02 |
| `tests/test_document_live_policy.py` | post / F03 |
| `tests/test_document_egress_hardening.py`, `tests/test_document_egress_pg.py` | post / F04 |
| `tests/test_document_job_read_matrix.py`, `tests/test_document_access_hardening.py`, `tests/test_document_jobs_pg_idempotency.py` | post / F05 |
| `tests/test_document_backup_restore_hardening.py`, `tests/test_document_pg_backup_restore.py`, `tests/test_document_backup_pins.py`, `tests/test_document_reindex_after_restore.py`, `tests/test_document_pg_restore_drill_scale.py` | post / F06 |
| `tests/test_document_fault_transitions.py` | post / F07 |
| `tests/test_document_admission_hardening.py`, `tests/test_document_capacity_hardening.py`, `tests/test_document_upload_admission.py`, `tests/test_document_json_body_cap.py`, `tests/test_document_claim_fairness.py`, `tests/test_document_quota_both_backends.py` | post / F08 |
| `tests/test_document_fidelity.py`, `tests/test_document_pdf_rtl.py`, `tests/test_document_number_order.py`, `tests/test_document_ocr_tables.py`, `tests/test_document_real_scans.py` | post / F09, Q02 |
| `tests/fixtures/documents/fidelity_thresholds.json` | fidelity corpus floors, release targets, qualified list |
| `tests/test_document_certification_honesty.py` | post / F10 |
| `tests/test_document_crash_recovery.py`, `tests/test_document_intake_lease.py`, `tests/test_document_intake_lease_containers.py` | post / Q01 |
