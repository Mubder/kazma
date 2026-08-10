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

Phases were implemented primarily as code + tests; only phases **9–10** have
dedicated CHANGELOG product entries. The map below reconstructs **0–8** from
modules and phase-named tests so operators and contributors share one timeline.

---

## At a glance

| Phase | Name | Operator impact | Primary tests |
|------:|------|-----------------|---------------|
| 0–2 | Foundation | Models, config, CAS storage, repository, sandbox, job queue | `test_documents_models`, `config`, `storage`, `repository`, `document_sandbox`, `document_jobs` |
| 3 | Sniff / intake policy | Hostile MIME/OOXML/PDF rejection before parse | Hostile corpus + processor security |
| 4 | Parsers | Format parsers behind isolated worker | `test_document_parsers_phase4` |
| 5 | OCR | Multilingual quality-routed OCR | `test_document_ocr_phase5` |
| 6 | Knowledge | Structural chunking → library index | `test_document_knowledge_phase6` |
| 7 | Generate / convert / redact | Isolated render + verified redaction | `test_document_generation_phase7` |
| 8 | Surfaces | Ingestion coordinator, Web API, tools, gateway, TUI | `test_document_ingestion_phase8`, `*_api_phase8`, `*_actions_phase8`, `*_platform_tools_phase8` |
| 9 | Ops & scale | Audit, capacity, GC, metrics, backup, PG job claims | `test_document_operations_phase9` |
| 10 | Certification | Hostile corpus, cert CLI, a11y, crash matrix | `test_document_certification_phase10` |

Full document suite (when run as the document test files): **217 passed, 2 skipped**
(optional engines) as of the Phase 10 recheck window — re-run to confirm on your tree.

```powershell
& .venv\Scripts\python.exe -m pytest tests/test_document*.py tests/test_documents*.py -q
```

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

**Tests:** `tests/test_document_operations_phase9.py` (**29** cases in file).  
**CHANGELOG:** Unreleased Phase 9 entry.  
**Ops guide:** [Document processing](../ops/document-processing.md).

---

## Phase 10 — Certification, a11y, crash recovery

**Modules:** `certification.py`, `hostile_corpus.py`, cert script
`scripts/certify_documents.py`, UI a11y on Documents page.

**What shipped:** deterministic hostile corpus (19 cases), cert CLI JSON report,
canary readiness, performance smoke, phase10 pytest groups (architecture,
crash, a11y, rollout, backpressure).

**Tests:** `tests/test_document_certification_phase10.py` (**29** cases).  
**Audit:** `docs/audits/AUDIT_DOCUMENT_CERTIFICATION.md` (repo path, outside Docusaurus).  
**CHANGELOG:** Unreleased Phase 10 entry + post-cert recheck notes.

**CLI vs pytest:** the CLI certifier runs hostile corpus, performance smoke,
capabilities, rollout, plus explicit NOT RUN / CONDITIONAL gates. Architecture /
a11y / full crash matrix are enforced in the pytest file.

```bash
python scripts/certify_documents.py
python scripts/certify_documents.py --soak
```

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
| `tests/fixtures/documents/hostile_manifest.json` | 10 corpus SoT |
