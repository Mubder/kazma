# Memory — Done vs Remaining

**Status date:** 2026-08-24 (memory system audit M-01..M-17 **closed**)  
**Primary guide:** [`docs/docs/guide/memory-and-rag.md`](../docs/guide/memory-and-rag.md)  
**Best-path operator guide:** [`docs/docs/guide/memory-best-path.md`](../docs/guide/memory-best-path.md)  
**Audit:** [`docs/audits/AUDIT_MEMORY_SYSTEM_2026-08-24.md`](../audits/AUDIT_MEMORY_SYSTEM_2026-08-24.md)  
**Priority lock:** [`MEMORY_PRIORITY_NEXT.md`](MEMORY_PRIORITY_NEXT.md)

Use this file when picking up memory work. Do **not** start a greenfield rewrite.

**Trust path:** Single-node V2 + optional scale adapters + KB chat inject is
**complete**. The 2026-08-24 audit (orphans, mirror zombies, tenant gaps, FTS
drift, graph-clear bind bug) is **closed**. Remaining work is **trigger-only
scale** (hosted embed fleet) — not another memory rewrite.

---

## 1. Shipped (do not re-open as greenfield)

| Wave | What |
|------|------|
| V2 engine | Bi-temporal beliefs, episodes, PPR, durable queue, macro_sleep, backup/export |
| Phase A | Access bump, multi-tier dense, session bias, post-turn metrics, remember→recall |
| Phase B | Real FTS5, belief-graph PPR, explain_recall, dense cap |
| Phase C–D + Dash + Eval | Procedural inject, entity merges API/UI, reconsolidation, working tier, backends UI, probe/queue, golden set |
| Priority max batch | VectorBackend factory, failover honesty, tenant tests, working TTL, dashboard alerts, belief drawer, smoke script |
| Admin UI ops (2026-08-04) | Graph entity/virtual id dedupe; entity display rename; list↔graph bridge; belief PATCH edit; self hub (`self_hub.py` User/Mubder → `user`); `/memory` docs |
| System audit 2026-08-24 | M-01..M-17: send_prompt UnboundLocal, count SQL SoT, ego-anchor orphans, mirror tombstones + reconcile CLI, tenant gates, recompute-at-mutation, painter edge delta, FTS docsize drift, INSERT OR IGNORE rollback, merge-ledger archive, graph-clear Neo4j+audit+bind fix, export coverage, Ungroup |

**V1 RRF is gone.** `use_new_stack=false` only disables V2 inject/post-turn.

---

## 2. Architecture (current truth)

```text
User turn
  → recall() via VectorBackend (sqlite-vec on one node; **pgvector** when a
     Postgres DSN is set; Qdrant if you pick it) + FTS5/ILIKE + PPR + session bias
  → procedural hints (optional)
  → format_untrusted_block
  → LLM
  → post-turn: promote working→episodic, mirror working/recall, heuristic beliefs, micro_consolidation queue
```

| Path | Role |
|------|------|
| `memory_state.db` | beliefs, episodes, entities, entity_merges (+ archive), procedural |
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

### P3 — Scale (issues filed)

| Item | Tracking |
|------|----------|
| Full Postgres-primary recall | [#76](https://github.com/Mubder/kazma/issues/76) — **Done** (`state.role=primary` / `KAZMA_MEMORY_STATE_ROLE=primary`; ILIKE sparse **+ pgvector dense RRF**; down = fail-closed, no silent SQLite). Industry stack part 6 (2026-08-25). |
| Multi-region + conflict policy | [#77](https://github.com/Mubder/kazma/issues/77) — **Done** (`state.region` + `state.conflict_policy` = last_write_wins \| origin_wins \| fail_closed) |
| Hosted embed-only fleet defaults | [#78](https://github.com/Mubder/kazma/issues/78) — **Done** (`KAZMA_EMBED_FLEET=1` + OpenAI/Voyage key, or `provider: openai\|voyage`). Local bge-m3 stays the one-node default (no surprise dim switch). |
| Huge-corpus reconsolidation partition | **Done** — subject-hash shards + worker chain |
| Neo4j install default (env/compose) | **Done** — `KAZMA_NEO4J_DEFAULT` / URL env; fail-open SQLite; `deploy/docker-compose.neo4j.yml` |
| Physical KB+beliefs one-table merge | **Index shipped** — `memory/unified_index.py` dual-writes beliefs + KB chunks into one `unified_items` table; source stores stay SoT ([#79](https://github.com/Mubder/kazma/issues/79)) |

### Architecture boundaries (updated)

| Choice | Status |
|--------|--------|
| **KB + chat product merge** | **Done** — inject labeled KB into supervisor; optional promote to episodes (`merge_knowledge_into_chat`, `promote_kb_to_episodes`) |
| **Schema merge** (one table for KB+beliefs) | **Wontfix** (#79) — stores stay separate; chat unifies via inject |
| **Neo4j dual-write** | **Done** — Settings Test/Sync; Docker optional |
| **Neo4j install default** | **Env opt-in** — `KAZMA_NEO4J_DEFAULT=1` / URL env; never hard-require server |
| **Dashboard topology paint** | **SQLite SoT** (entity types, bi-temporal, accent UI). Neo4j online shown in health strip; `?source=neo4j` probe only |
| **Neo4j as only install default** | Still **not** default — SQLite zero-config remains |

Federated search API remains for operator UI: `POST /api/memory/v2/federated-search`.

---

## 3b. Optional polish

| Item | Status |
|------|--------|
| DUI-6 Path-from-query | **Done** — probe/federated → “Show path on graph” |
| DUI-7 Episode overlay | **Done** — Episodes toggle on topology |
| DUI-8 Graph PNG/SVG | **Done** (+ JSON/GraphML) |
| DUI-9 Queue Retry/Clear | **Done** — per-task retry + Clear failed |
| DUI-11 Responsive stack | **Done** — stack ≤900px |
| DUI-13 Empty-state CTA | **Done** — Teach me a fact → chat |
| DUI-14 Graph a11y | **Done** — arrows / ± / Home / Escape |
| Settings consolidation | **Done** — Memory+Embedder one tab; connectors under LLM hub; Refresh Gateway on Platform Connectors |
| Extraction hygiene | **Done** — block `kazma_v2_*` / stack-name subjects at extract + mutate |
| Neo4j edge on invalidate | **Done** — `delete_belief_edge` + supersede path + invalidate API + graph-clear `clear_tenant_edges` |
| beliefs_fts self-heal | **Done** — `beliefs_write` rebuilds FTS on malformed DB errors; 6h `fts_health` COUNT via `*_docsize` |
| Ego-graph leaf orphans | **Done** — write-time + 6h backfill (`ego_anchor.py`) |
| PG mirror tombstones | **Done** — remirror on invalidate/supersede/clear; `scripts/reconcile_memory_mirror.py`; nightly drift warning |
| Tenant graph-clear | **Done** — no all-tenants mode; undo tokens bound to tenant |

---

## 4. Operator smoke

```powershell
pwsh -File scripts/memory_smoke.ps1
```

Manual:
1. Chat “Remember my favorite color is teal.” → “What color?”  
2. Dashboard → **Federated** search same query (memory hits)  
3. After KB ingest: federated query shows **KB** chips with source URL  
4. Health ACTIVE

---

## 5. Tenant checklist (every new write path)

1. Resolve via `resolve_tenant_id(platform, sender_id, session_id)`.  
2. Pass `tenant_id` into dual_write / mutate_belief / recall.  
3. Add isolation test if new table.
