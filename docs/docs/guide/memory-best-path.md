---
title: Memory — best path (operator guide)
sidebar_label: Memory best path
---

# Memory — best path for Kazma

This is the recommended way to run cognitive memory + Knowledge Library
after the V2 program and Horizon A work. **Do not merge stores into one
table.** Use a **unified chat experience** on **separate stores**.

## Architecture (keep this)

```text
Chat turn
  ├─ Personal memory (V2): beliefs + episodes  → memory_state.db
  ├─ Knowledge Library (optional inject)       → knowledge_* stores
  └─ Optional scale adapters
       • Qdrant / pgvector  (vectors)
       • Postgres dual-mirror (state copy)
       • Neo4j (graph topology when online)
```

| Store | Role | Default |
|-------|------|---------|
| V2 cognitive | “Who I am / what we said” | SQLite primary |
| Knowledge Library | “What the docs say” + citations | Separate SQLite/Chroma |
| Neo4j | Graph UI / analytics | **Optional** dual-write |
| Postgres | Multi-process state mirror | **Optional** dual-mirror |

## Operator checklist

1. **Personal memory** — leave defaults; chat “Remember my favorite color is teal.”
2. **Docs** — ingest a Knowledge Library; enable inject in Settings → **Knowledge + chat memory**.
3. **Federated search** — Dashboard → **Search all knowledge** → Federated (labels `MEM` / `KB`).
4. **Smoke** — `pwsh -File scripts/memory_smoke.ps1`
5. **Scale** — only if multi-replica: configure backends in Settings; do **not** drop SQLite until you plan a cutover.

## Settings that matter

**UI location:** Settings → **Memory** tab  
(deep link: `/settings?tab=memory`)

Also: Settings → **Embedder** for model rebuild / vector-space composition.

| Setting | Effect |
|---------|--------|
| `merge_knowledge_into_chat` | Inject labeled KB into supervisor next to V2 memory |
| `promote_kb_to_episodes` | Soft-copy top KB hits into episodic rows (tagged) |
| `memory.backends.*` | Vector / state / graph adapters + failover |
| `tenant_mode` | shared / per_platform / per_user |

## What not to do

- Do **not** dump KB chunks into the beliefs table as raw SPO without provenance.
- Do **not** make Neo4j the only install requirement for single-user setups.
- Do **not** run full Postgres-primary recall until multi-replica is a real product need.

## Related

- [Memory and RAG](./memory-and-rag.md)
- [Knowledge Library](./knowledge-library.md)
- Plan backlog: `docs/plans/MEMORY_REMAINING.md`
