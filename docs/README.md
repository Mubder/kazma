# Kazma documentation (Docusaurus)

## Source of truth

**All product documentation lives under `docs/docs/`.** There is no separate `docs-v2/` tree (archived at `archive/docs-v2/`).

| Area | Path |
|------|------|
| Docs home | [`docs/docs/intro.md`](docs/intro.md) |
| Guide | [`docs/docs/guide/`](docs/guide/) |
| Products (Web, IDE, TUI, SaaS) | [`docs/docs/products/`](docs/products/) |
| Reference (tools, env, slash, API) | [`docs/docs/reference/`](docs/reference/) |
| Ops | [`docs/docs/ops/`](docs/ops/) |
| Consolidation plan | [`DOCS_CONSOLIDATION_PLAN.md`](DOCS_CONSOLIDATION_PLAN.md) |
| Engineering audits | [`audits/`](audits/) (not in site sidebar) |
| Full system map | [`ARCHITECTURE_AND_SYSTEM_MAP.md`](ARCHITECTURE_AND_SYSTEM_MAP.md) |

**Edit `docs/docs/**` only** for user-facing content. Historical material: `archive/`.

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
