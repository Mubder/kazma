# Memory — Done vs Remaining

**Status date:** 2026-08-03  
**Primary guide:** [`docs/docs/guide/memory-and-rag.md`](../docs/guide/memory-and-rag.md)  
**Priority lock:** [`MEMORY_PRIORITY_NEXT.md`](MEMORY_PRIORITY_NEXT.md)

Use this file when picking up memory work. Do **not** start a greenfield rewrite.

---

## 1. Shipped (do not re-open as greenfield)

| Wave | What |
|------|------|
| V2 engine | Bi-temporal beliefs, episodes, PPR, durable queue, macro_sleep, backup/export |
| Phase A | Access bump, multi-tier dense, session bias, post-turn metrics, remember→recall |
| Phase B | Real FTS5, belief-graph PPR, explain_recall, dense cap |
| Phase C–D + Dash + Eval | Procedural inject, entity merges API/UI, reconsolidation, working tier, backends UI, probe/queue, golden set |
| Priority max batch | VectorBackend factory, failover honesty, tenant tests, working TTL, dashboard alerts, belief drawer, smoke script |

**V1 RRF is gone.** `use_new_stack=false` only disables V2 inject/post-turn.

---

## 2. Architecture (current truth)

```text
User turn
  → recall() via VectorBackend (local sqlite-vec default) + FTS5 + PPR + session bias
  → procedural hints (optional)
  → format_untrusted_block
  → LLM
  → post-turn: promote working→episodic, mirror working/recall, heuristic beliefs, micro_consolidation queue
```

| Path | Role |
|------|------|
| `memory_state.db` | beliefs, episodes, entities, procedural |
| `memory_ops.db` | task queue + audit |

---

## 3. Remaining — by priority

### P2 — Next time (frozen until re-opened)

| # | Item |
|---|------|
| P2-1 | Full remote vector **write** (Qdrant/pgvector upsert on every store) |
| P2-2 | Shared Postgres state for multi-process |
| P2-3 | Neo4j / external graph |
| P2-4 | Graph LOD, PNG/SVG export, bi-temporal play |
| P2-5 | LLM tier-3 entity disambiguation |
| P2-6 | Procedural skills browser UI |
| P2-7 | Chaos: kill worker mid-queue |
| P2-8 | Dashboard memory quality score |
| P2-9 | Chat inline why-recalled every turn |

### P3 — Trigger-only scale

| Trigger | Build |
|---------|--------|
| 2+ app replicas | P2-1 then consider P2-2 |
| SaaS isolation SLAs | Auth-bound tenant + audit reviews |
| No local embeddings | Hosted embed-only service |
| Huge corpus | Scaled reconsolidation / partition |

### Explicitly not planned

- Merging Knowledge Library into chat agent memory  
- Dropping SQLite for Neo4j on single-node by default  

---

## 4. Operator smoke

```powershell
pwsh -File scripts/memory_smoke.ps1
```

Manual: Chat “Remember my favorite color is teal.” → new turn “What color?” → Dashboard probe same query → health ACTIVE.

---

## 5. Tenant checklist (every new write path)

1. Resolve via `resolve_tenant_id(platform, sender_id, session_id)`.  
2. Pass `tenant_id` into dual_write / mutate_belief / recall.  
3. Add isolation test if new table.
