# Archived documentation

This folder holds documentation that was **removed from the live product docs tree**
during the 2026-07-21 consolidation. Live source of truth: **`docs/docs/`** (Docusaurus).

| Path | What it was | Why archived | Later decision |
|------|-------------|--------------|----------------|
| `docs-v2/` | Code-audited rewrite (authoring tree) | Fully merged into `docs/docs/guide/`; dual tree confused readers | **Retention:** keep until next minor release after 0.6.x docs freeze, then delete if no unique content remains (diff vs `docs/docs/guide/`). **Do not resurrect as a live tree.** |
| `docs-legacy/getting-started/` | Old Docusaurus install/quickstart | Superseded by Guide quickstart + `start/` | Merge any unique install notes, then delete |
| `docs-legacy/core-concepts/` | Old architecture/agent-loop pages | Superseded by `guide/architecture.md` | Cherry-pick diagrams if needed |
| `docs-legacy/api-reference/` | Old/partial API pages (incl. unwired delegation) | Superseded by `reference/api-routes.md` | Do not republish fabricated Hub APIs |
| `docs-loose/` | HANDOVER, TROUBLESHOOTING, MEMORY, compaction, ROADMAP, … | Orphan files outside sidebar; content partly folded into Guide | Review for unique issues before delete |

**Do not** edit these for product documentation. Edit `docs/docs/**` only.

See `docs/DOCS_CONSOLIDATION_PLAN.md` for the full plan and coverage matrix.
