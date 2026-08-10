# Kazma documentation (Docusaurus)

## Source of truth

**All product documentation lives under `docs/docs/`.** Retired dual trees (`docs-v2`, loose handovers) were removed from the monorepo; git history retains them if needed.

| Area | Path |
|------|------|
| Docs home | [`docs/docs/intro.md`](docs/intro.md) |
| Guide | [`docs/docs/guide/`](docs/guide/) |
| **Document Intelligence** | [`docs/docs/guide/document-intelligence.md`](docs/guide/document-intelligence.md) · [phases](docs/guide/document-phases.md) · [ops](docs/ops/document-processing.md) · [security](docs/security/document-security.md) |
| Products (Web, IDE, TUI, SaaS) | [`docs/docs/products/`](docs/products/) |
| Reference (tools, env, slash, API) | [`docs/docs/reference/`](docs/reference/) — includes `/api/documents/*`, `document_*` tools, `/documents` slash |
| Ops | [`docs/docs/ops/`](docs/ops/) |
| Consolidation plan | [`DOCS_CONSOLIDATION_PLAN.md`](DOCS_CONSOLIDATION_PLAN.md) |
| Document docs goals | [`plans/DOCUMENT_DOCS_REMEDIATION_GOAL.md`](plans/DOCUMENT_DOCS_REMEDIATION_GOAL.md) · [residuals](plans/DOCUMENT_RESIDUALS_GOAL.md) |
| Engineering audits | [`audits/`](audits/) — includes [`AUDIT_DOCUMENT_CERTIFICATION.md`](audits/AUDIT_DOCUMENT_CERTIFICATION.md) |
| Full system map | [`ARCHITECTURE_AND_SYSTEM_MAP.md`](ARCHITECTURE_AND_SYSTEM_MAP.md) |

**Edit `docs/docs/**` only** for user-facing content.

### Document Intelligence (quick pointer)

Secure durable document pipeline (ingest → sniff → isolated parse/OCR → jobs →
index/convert/redact/ops). Surfaces: Web `/documents`, Settings → Documents,
`document-platform` tools, gateway `/documents`, TUI Documents tab. Install
engines with `pip install -e ".[document-platform]"`; certify with
`python scripts/certify_documents.py`.

## Develop / build

```bash
cd docs
npm install
npm start          # http://localhost:3000/kazma/
npm run build      # production build
npm run serve      # preview build
```

Requirements: Node.js ≥ 18.

## Features

- **Mermaid** diagrams  
- **Local search** (`@easyops-cn/docusaurus-search-local`)  
- Dark mode default  
- Sidebars: Docs · Skills · Security · Contributing  

## Repo pointers

Root `README.md`, `AGENTS.md`, and `architecture.md` must link here — never to `docs-v2/`.
