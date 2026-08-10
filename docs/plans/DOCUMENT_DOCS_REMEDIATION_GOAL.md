# GOAL: Document Intelligence Documentation Remediation (Phases 0–10)

**Status:** DONE (non-stop execution completed 2026-08-10)  
**Created:** 2026-08-10  
**Source:** Deep audit of document-system docs vs code (findings only → full fix)  
**Scope:** Documentation **only** — no production code/behavior changes  
**Target:** Document-docs composite **~38% → ~88%** (~60% relative improvement / ~80% of listed debt closed)

---

## Mission

Make Document Intelligence **operator-complete and source-honest** from foundation
(phase 0) through certification (phase 10): every durable surface (API, slash,
tools, TUI, Web, migration, config, security) is documented with keys and states
that match code, and overclaims are corrected.

**Non-stop rule:** Execute work packages WP-1 → WP-7 in order without waiting for
intermediate user approval. Stop only on true blockers (missing files, contradictory
code). Report a single completion summary at the end.

---

## Success criteria (must all pass)

| # | Gate | Verification |
|---|------|----------------|
| G1 | Job state machine in guide matches `DocumentJobState` | Grep guide for forbidden `PENDING`/`ACCEPTED` lifecycle story |
| G2 | Config keys in guide use **primary** nested ConfigStore keys (+ aliases noted) | Cross-check against `config.py` `_read(...)` keys |
| G3 | `api-routes.md` lists all `/api/documents/*` routes | Diff vs `documents_api.py` decorators |
| G4 | `slash-commands.md` documents `/documents` (+ `/docs` alias) | Match gateway help strings |
| G5 | `tools-catalog.md` + `native-skills.md` document `document-platform` tools; legacy generators labeled | Match `document_platform/skill_manifest.yaml` |
| G6 | Security docs honest on malware (config-only if unwired), redaction confirm (UI-only), sandbox network (soft), OCR merge (similarity) | No ClamAV “enabled works” claim without code |
| G7 | Voice/media attachment path matches auto-parse + fence + `read_document` | Match `attachments.py` |
| G8 | Migration + production checklist + smoke cover document store | Docs mention `documents.db` + content tree + certify script |
| G9 | Products (web-ui, tui), recent-features, architecture, knowledge-library, Agents.md mention documents | Links to main guide |
| G10 | Phase 0–10 narrative page exists; audit/CHANGELOG counts/dates consistent | One SoT phase map |
| G11 | No dead “SoT” links from guide (API, tools, security, ops) | Click-path in markdown |

---

## Work packages (non-stop DAG)

```
WP-1 Accuracy (guide) ──┐
WP-2 Security honesty ──┼──► WP-3 Reference surfaces (API/tools/slash)
WP-7 Meta (audit/CL)  ──┤
                        ├──► WP-4 Ops/DR/smoke/KB bridge
                        ├──► WP-5 Product + Agents + architecture
                        └──► WP-6 Phase 0–10 narrative + intro links
                                    └──► WP-FINAL consistency pass
```

### WP-1 — Main guide accuracy (Critical C1, C2, architecture wording, auto-index)

**Files:** `docs/docs/guide/document-intelligence.md`

- [ ] Replace fake job state machine with real `DocumentJobState` chain
- [ ] Fix limits table to primary keys (`documents.limits.*`, `documents.ocr.*`,
      `documents.workers.*`, `documents.quotas.*`, `documents.retention.*`,
      `documents.capacity.*`, `documents.intake.*`)
- [ ] Document key prefix table using nested form; note known aliases
- [ ] Clarify dual boundary: durable → `DocumentIngestionService`; execution →
      `DocumentService`; chat transient may call `DocumentService` directly
- [ ] State auto-index is **off by default** (explicit index action)
- [ ] Note optional engines degraded by default; 422/503 when missing
- [ ] Rollout column: `default_authoritative` not bare `authoritative`
- [ ] Link to new phase map page when WP-6 lands

### WP-2 — Security + chat path honesty (Critical C6, C7)

**Files:**  
`docs/docs/security/document-security.md`  
`docs/docs/guide/voice-and-media.md`

- [ ] Malware: document as **config knobs** (`documents.security.malware_scan`);
      do not claim live ClamAV unless code exists
- [ ] Redaction: interactive confirm is **Web UI** (`kazmaConfirm`); API/tools can
      redact without UI confirm (ACL still applies)
- [ ] Sandbox: scrubbed env + resource limits; **not** full network namespace isolation
- [ ] OCR: merge is **similarity-based** (SequenceMatcher thresholds), not pure
      coordinate geometry
- [ ] Fence: platform uses `source="document"`; chat attachments may use
      `document_attachment`
- [ ] Voice/media: document auto-parse + fenced excerpt + full path / `read_document`

### WP-3 — Reference surfaces (Critical C3–C5, High dual-stack)

**Files:**  
`docs/docs/reference/api-routes.md`  
`docs/docs/reference/slash-commands.md`  
`docs/docs/reference/tools-catalog.md`  
`docs/docs/guide/native-skills.md`  
`docs/docs/reference/environment-variables.md` (note ConfigStore-primary)

- [ ] Full Documents API matrix from `documents_api.py`
- [ ] `/documents` slash section (alias `/docs`); fix or qualify `/research` note
- [ ] `document-platform` tools table; label `document-generator` as simple/legacy path
- [ ] native-skills section for `document_platform` (+ pointer to dual stack)
- [ ] Env-vars: state document config is **ConfigStore live keys**, not env-primary

### WP-4 — Ops / DR / production / knowledge bridge (High H3–H4, Medium M1)

**Files:**  
`docs/docs/ops/document-processing.md`  
`docs/docs/ops/migration.md`  
`docs/docs/ops/production-checklist.md`  
`docs/docs/ops/smoke-matrix.md` (if present)  
`docs/docs/guide/knowledge-library.md`

- [ ] Fix ops intro link → document-intelligence guide (not tools-catalog)
- [ ] Migration: `documents.db` + content-addressed tree / `document-store` bundle path
- [ ] Production checklist: enable/rollout, capacity, certify smoke, backup, readiness
- [ ] Smoke matrix: upload → ready → read; optional certify_documents.py
- [ ] Knowledge library: document_index / library_id bridge + fence on search

### WP-5 — Product pages + system map + Agents.md (High H2, H5)

**Files:**  
`docs/docs/products/web-ui.md`  
`docs/docs/products/tui.md`  
`docs/docs/guide/recent-features.md`  
`docs/docs/guide/architecture.md`  
`docs/ARCHITECTURE_AND_SYSTEM_MAP.md`  
`Agents.md` (or `AGENTS.md` if that is the live name)

- [ ] Web UI: `/documents` page + ops panel summary
- [ ] TUI: Documents tab (`DocumentsPanel`, key binding if any)
- [ ] Recent features: Document Intelligence row + links
- [ ] Architecture: short subsystem box + link to guide
- [ ] System map: documents package + transports
- [ ] Agents.md: critical subsystem § — boundary, no parser imports in gateway/UI,
      ConfigStore keys, PG jobs vs SQLite metadata, fence, HITL redaction note

### WP-6 — Phase 0–10 narrative (Critical “docs from phase 0”)

**Files:**  
`docs/docs/guide/document-phases.md` (new)  
`docs/docs/intro.md` (link)  
`docs/sidebars.js` (register)

Phase map (code/test-backed):

| Phase | Name | Primary modules / tests |
|------:|------|-------------------------|
| 0–2 | Foundation | models, config, storage, repository, sandbox, jobs |
| 3 | Sniff / intake policy | sniff, hostile corpus reject paths |
| 4 | Parsers | parsers/*, test_document_parsers_phase4 |
| 5 | OCR | ocr/*, test_document_ocr_phase5 |
| 6 | Knowledge index | knowledge, indexer, phase6 tests |
| 7 | Generate / convert / redact | operations, mutation, phase7 |
| 8 | Surfaces | ingestion, API, gateway, document_platform, TUI |
| 9 | Ops & scale | audit, capacity, retention, telemetry, backup, jobs_pg |
| 10 | Certification | certification, hostile_corpus, phase10 tests |

- [ ] Write phase page with status + test file pointers + operator entry points
- [ ] Wire sidebar + intro

### WP-7 — Meta consistency (Medium/Low C8)

**Files:**  
`docs/audits/AUDIT_DOCUMENT_CERTIFICATION.md`  
`CHANGELOG.md` (doc note only if needed)

- [ ] Align test counts (phase9 = 29 tests in file; full suite 217/2 skip when true)
- [ ] Clarify cert CLI gates vs pytest phase10 groups
- [ ] Fix audit date/timeline note (cert report vs phase changelog dates)
- [ ] Prefer newest unreleased document section ordering if editing CHANGELOG

### WP-FINAL — Consistency pass

- [ ] Grep docs for wrong keys: `documents.max_pages`, bare PENDING lifecycle
- [ ] Grep for “ClamAV” claims that imply live scan without config-only caveat
- [ ] Ensure all guide quick-links resolve
- [ ] Update this goal file status → DONE with completion table

---

## Finding → work package map

| ID | Finding | WP |
|----|---------|-----|
| C1 | Wrong job states | WP-1 |
| C2 | Wrong config keys | WP-1 |
| C3 | Empty API SoT | WP-3 |
| C4 | Missing platform tools | WP-3 |
| C5 | Missing slash `/documents` | WP-3 |
| C6 | Stale attachment docs | WP-2 |
| C7 | Security overclaims | WP-2 |
| C8 | Audit/CHANGELOG drift | WP-7 |
| H1 | No phase 0–8 narrative | WP-6 |
| H2 | Surfaces undocumented | WP-3, WP-5 |
| H3 | Migration/DR incomplete | WP-4 |
| H4 | KB bridge missing | WP-4 |
| H5 | Agents.md silent | WP-5 |
| M1 | Ops wrong intro link | WP-4 |
| M2 | Rollout column name | WP-1 |
| M3 | Capacity default honesty | WP-1 / WP-4 |
| M4 | Optional engines | WP-1 |
| M5 | Test map | WP-6 |
| M6 | Fence source identity | WP-2 |
| M7 | Auto-index off default | WP-1 |
| M8 | Dual skill stack | WP-3 |
| L* | Polish, smoke, dates | WP-4, WP-7 |

---

## Out of scope (explicit)

- Implementing ClamAV / malware scanning code  
- Porting document metadata to Postgres  
- Installing fitz/WeasyPrint/LibreOffice  
- Running multi-day soak  
- Changing runtime behavior of ingestion/API/tools  
- Unrelated docs (memory V2, swarm, email) except cross-links required by G9  

---

## Execution log

| WP | Status | Notes |
|----|--------|-------|
| WP-1 | **DONE** | `document-intelligence.md` rewritten (states, nested keys, dual boundary, auto-index, engines) |
| WP-2 | **DONE** | `document-security.md` honesty; `voice-and-media.md` attachment auto-parse |
| WP-3 | **DONE** | API matrix, slash `/documents`, tools + native-skills dual stack, env-vars note |
| WP-4 | **DONE** | Ops link fix; migration document-store; production + smoke document rows; KB bridge |
| WP-5 | **DONE** | web-ui, tui, recent-features, architecture, system map, Agents.md §19 |
| WP-6 | **DONE** | New `document-phases.md`; sidebar + intro links |
| WP-7 | **DONE** | Audit date/gate split; CHANGELOG phase9 count + docs remediation entry |
| WP-FINAL | **DONE** | Grep consistency; goal marked complete |

---

## Known residuals (accepted)

- Malware scanner still **not implemented** — docs now honest; product work separate.
- Metadata multi-replica / multi-day soak / external review remain CONDITIONAL / NOT RUN.
- Optional engines still often missing on developer hosts.
- Docusaurus build not re-run in this pass (markdown + sidebars updated).
- Live Settings UI still has no dedicated Documents tab (ConfigStore keys only) — documented.

---

## Completion definition

Goal is **DONE** when G1–G11 pass and this file’s execution log shows all WPs complete.
No partial “good enough” without listing residual gaps under **Known residuals**.

**Gates G1–G11:** satisfied by WP-1…WP-FINAL file updates above (2026-08-10).
