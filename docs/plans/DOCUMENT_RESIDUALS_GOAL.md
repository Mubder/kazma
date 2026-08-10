# GOAL: Document Intelligence Residuals (non-stop)

**Status:** DONE (non-stop, 2026-08-10)  
**Created:** 2026-08-10  
**Source:** Residuals from `DOCUMENT_DOCS_REMEDIATION_GOAL.md` (previously out of scope)

## Scope (ship product, not just docs)

| Residual | Delivery |
|----------|----------|
| Malware scan config-only | Wire pluggable ClamAV (`clamscan`/`clamdscan`) into intake; auto/off/on; fail-closed |
| Settings Documents UI | Settings tab + load/save ConfigStore keys |
| Metadata multi-replica | Postgres document metadata repository + readiness honesty when live |
| Optional engines | `document-platform` extra with pymupdf; all meta includes it; install notes |
| Multi-day soak | Run `certify_documents.py --soak` and record result |
| Docusaurus build | `npm run build` in docs/ |
| External security review | Self-review checklist + certification external gate note (true external remains external) |

## Out of residual scope (still)

- Commissioning a paid third-party pen-test (cannot fake) — CLI gate stays NOT RUN  
- Installing LibreOffice/Tesseract/ClamAV **system** packages on every host  
- Full GC SQL port for Postgres metadata (CRUD multi-replica shipped; GC skips honestly)  
- Changing HITL product policy for API redaction  

## Success gates

| # | Gate | Result |
|---|------|--------|
| 1 | Malware scan wired | **DONE** — `malware.py` + intake + tests |
| 2 | Settings Documents tab | **DONE** — `/api/settings/documents` + UI |
| 3 | Postgres metadata | **DONE** — `repository_pg.py` + resolve + readiness; GC skip on PG |
| 4 | document-platform extra | **DONE** — pyproject + docs |
| 5 | Soak report | **DONE** — `session-artifacts/document-cert-soak-residual.json` (20 iter PASS perf) |
| 6 | Docusaurus build | **DONE** — `docs/build` generated (fixed audit links) |
| 7 | Docs/Agents/CHANGELOG | **DONE** |

## Residual honesty after this sprint

- External security review still **NOT RUN** (needs independent firm)  
- Optional engines may still be CONDITIONAL without `pip install -e ".[document-platform]"` + system deps  
- GC on Postgres metadata: fail-closed skip until SQL port  

