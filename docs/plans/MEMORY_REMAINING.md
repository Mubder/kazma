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

### P2 — Partial wave shipped (2026-08-03)

| # | Item | Status |
|---|------|--------|
| P2-1 | Remote vector write (Qdrant + pgvector) + hybrid | **Done** |
| P2-4 | Graph JSON/GraphML export + LOD cap | **Done** |
| P2-6 | Procedural skills list on Dashboard | **Done** |
| P2-7 | Stuck-queue reclaim chaos test | **Done** |
| P2-8 | Memory quality score API + card | **Done** |
| P2-9 | explain_recall in chat format | **Done** |
| P2-2 | Shared Postgres **state** dual-mirror | **Done** |
| P2-2b | Postgres sparse ILIKE recall assist | **Done** |
| P2-3 | Neo4j graph dual-write + GraphBackend | **Done** |
| P2-5 | LLM tier-3 entity disambiguation (opt-in) | **Done** |
| SaaS | Auth-bound `resolve_tenant_id` (JWT/ContextVar/user) | **Done** |

### P3 — Still deferred (true cutover / ops)

| Trigger | Build |
|---------|--------|
| Full multi-replica recall | Port FTS/dense primary path entirely to Postgres |
| Multi-region | Geo replication + conflict policy |
| No local embeddings | Hosted embed-only fleet defaults |
| Huge corpus | Scaled reconsolidation / partition |
| Graph UX | Animation / a11y polish |

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
