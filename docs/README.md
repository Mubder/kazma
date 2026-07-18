# Kazma documentation (Docusaurus)

## Source of truth

The **current, code-audited** reference lives under:

```
docs/docs/guide/     ← merged from docs-v2 (July 2026 / v0.5.0)
```

Navbar **Guide** is the primary entry. Older sidebars (Getting Started, Legacy Concepts, etc.) remain for skills/hub material and historical pages, with banners pointing at Guide when they overlap.

| Guide topic | Path |
|-------------|------|
| Quickstart | [`docs/docs/guide/quickstart.md`](docs/guide/quickstart.md) |
| Architecture | [`docs/docs/guide/architecture.md`](docs/guide/architecture.md) |
| Configuration | [`docs/docs/guide/configuration.md`](docs/guide/configuration.md) |
| Security & HITL | [`docs/docs/guide/security-and-safety.md`](docs/guide/security-and-safety.md) |
| Swarm | [`docs/docs/guide/swarm-orchestration.md`](docs/guide/swarm-orchestration.md) |
| Full list | 18 pages under `docs/docs/guide/` |

Upstream notes that fed the merge still exist at repo root [`docs-v2/`](../docs-v2/) for history; **edit the Guide copies under `docs/docs/guide/`** for the live site.

Repo root [`architecture.md`](../architecture.md) points at the same material.

## Develop / build

```bash
cd docs
npm install
npm start          # http://localhost:3000/kazma/
npm run build      # production build
npm run serve      # preview build
```

Requirements: Node.js ≥ 18.

## Features enabled

- **Mermaid** diagrams (`@docusaurus/theme-mermaid`) — architecture, swarm, HITL flows from the Guide
- **Local search** (`@easyops-cn/docusaurus-search-local`)
- Dark mode default

## Audits (not in the sidebar)

Engineering audit reports live in [`audits/`](audits/) (not part of the published Guide).

## Refreshing Guide from docs-v2

If you edit `docs-v2/docs/*.md` and want to re-merge:

1. Re-run the merge copy (prepend frontmatter, strip H1, fix `.md` links).
2. Or edit `docs/docs/guide/*.md` directly (preferred after this merge).
