---
title: Memory — best path (operator guide)
sidebar_label: Memory best path
---

# Memory — best path for Kazma

Recommended way to run **V2 cognitive memory** + **Knowledge Library** after the memory program. **Do not merge stores into one table.** Use a **unified chat experience** on **separate stores**.

Deep architecture: [Memory & RAG](./memory-and-rag.md).

## Architecture (keep this)

```text
Chat turn
  ├─ Personal memory (V2): beliefs + episodes  → memory_state.db
  ├─ Knowledge Library (optional inject)       → knowledge_* stores
  └─ Optional scale adapters
       • Qdrant / pgvector  (vectors)
       • Postgres dual-mirror (state copy)
       • Neo4j (dual-write; Dashboard still paints SQLite)
```

| Store | Role | Default |
|-------|------|---------|
| V2 cognitive | “Who I am / what we said” | SQLite primary (SoT) |
| Knowledge Library | “What the docs say” + citations | Separate store |
| Neo4j | Dual-write of belief triples | **Optional** |
| Postgres | Multi-process state mirror | **Optional** |

## Operator checklist

1. **Personal memory** — defaults; chat “Remember my favorite color is teal.” then “What color?”  
2. **Docs** — ingest Knowledge Library; keep **Inject Knowledge into chat** on (Settings → **Memory**).  
3. **Federated search** — Dashboard → Search all knowledge → **Federated** (`MEM` / `KB` chips).  
4. **Explain recall** (optional) — Settings → Memory → **Explain recall**; chat workbench shows **Memory context** with channel chips.  
5. **Smart Knowledge search** (optional) — expand inject to all active libs on technical questions.  
6. **Memory admin (`/memory`)** — graph + entities/beliefs (below).  
7. **Smoke** — `pwsh -File scripts/memory_smoke.ps1` · [Smoke matrix](../ops/smoke-matrix) · [Recent features](./recent-features)  
8. **Optional Neo4j** — only if you want graph dual-write (below).  
9. **Scale** — multi-replica only: configure backends; do **not** drop SQLite until a real cutover plan ([#76](https://github.com/Mubder/kazma/issues/76)).

## Memory admin page (`/memory`)

Use this page to clean topology without raw SQL.

| Goal | How |
|------|-----|
| See the belief graph | Top **Graph & health** canvas; Refresh if labels look stale |
| Rename hub “You” → brand name | Entities: find person **User** / `ent_…` or hub → **Rename** → e.g. `Mubder`. Canvas hub should show that name. |
| Rename a project node | Click node or entity row → **Rename** (id stays `shipx`; label becomes `ShipX`) |
| Focus list ↔ graph | Click a row to zoom the canvas; click a node to highlight the list |
| Fix a wrong belief | Beliefs tab → **Edit** (object / predicate / subject) |
| Merge duplicate shells | Set Src + Tgt → **Merge** (beliefs rewired to target) |
| Link two entities | Src + Tgt + predicate → **Link** |
| Drop junk beliefs | Select → **Invalidate** |

### Hub identity note

The canvas center node is always **`id=user`**. A separate person shell
(`ent_…` named User) is treated as the **same self**: rename updates the hub
display name, and list click focuses the hub (`graph_id: user`). Do not expect
a second “You” node for that shell.

Deep dive: [Memory & RAG — Memory admin UI](./memory-and-rag.md#memory-admin-ui-memory).

## Settings that matter

**UI:** Settings → **Memory** (`/settings?tab=memory`)  
Includes isolation, KB toggles, backends, Neo4j Test/Sync, **and embedder** (no separate Embedder tab).

| Setting | Effect |
|---------|--------|
| `merge_knowledge_into_chat` | Inject labeled KB into supervisor next to V2 memory |
| `promote_kb_to_episodes` | Soft-copy top KB hits into episodic rows (tagged) |
| `memory.backends.*` | Vector / state / graph adapters + failover |
| Graph provider `neo4j` | Dual-write triples; topology **paint** stays SQLite |
| `tenant_mode` | shared / per_platform / per_user |

## Optional Neo4j

```bash
docker compose -f deploy/docker-compose.neo4j.yml up -d

# Env install default (fail-open if server down):
export KAZMA_NEO4J_DEFAULT=1
export KAZMA_NEO4J_PASSWORD=YOUR_PASSWORD_HERE
```

Or UI: Graph store **Neo4j** → Save → **Test Neo4j** → **Sync beliefs → Neo4j**.

- Masked password `***` in the form does **not** wipe the vault secret on Test.  
- After bulk invalidate, Sync again if Neo4j should drop orphans.  
- Do **not** make Neo4j a required install for single-user setups.

## Maintenance

| Job | Cadence |
|-----|---------|
| macro_sleep (decay / tiers) | ~6h |
| backup + export | ~24h |
| global reconsolidation (dedupe + re-embed) | ~24h; **auto-partitions** large corpora |

Dashboard: **Run reconsolidation**, queue **retry** / **Clear failed**, component health board.

## What not to do

- Do **not** dump KB chunks into the `beliefs` table as raw SPO without provenance.  
- Do **not** require Neo4j for a normal install.  
- Do **not** run full Postgres-primary recall until multi-replica is a real product need.  
- Do **not** treat “memory V2” as a product version entity (hygiene blocks subjects like `kazma_v2_4_0`).  
- Do **not** rewrite belief **subject** ids by hand to “fix” labels — use **Rename** (display) or **Merge** (identity).  
- Do **not** expect empty person shells with zero beliefs to appear as extra graph nodes — self shells focus the hub.

## Related

- [Memory and RAG](./memory-and-rag.md)  
- [Knowledge Library](./knowledge-library.md)  
- Plan: [`docs/plans/MEMORY_REMAINING.md`](https://github.com/Mubder/kazma/blob/main/docs/plans/MEMORY_REMAINING.md)  
- Scale: [#76](https://github.com/Mubder/kazma/issues/76) · [#77](https://github.com/Mubder/kazma/issues/77) · [#78](https://github.com/Mubder/kazma/issues/78)
