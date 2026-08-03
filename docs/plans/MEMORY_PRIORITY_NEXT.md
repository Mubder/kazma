# Memory priority — next pass (locked 2026-08-03)

**Product posture:** Best single-node experience + multi-replica/SaaS **base** (contracts, not full remote write).  
**This wave:** P0 + P1 max batch (implement).  
**Frozen:** P2 next time · P3 trigger-only scale.

Full execution plan: session plan / this file. Backlog SoT: `MEMORY_REMAINING.md`.

## P0 Must (this wave)

| ID | Item |
|----|------|
| P0-1 | Smoke / golden automation (`scripts/memory_smoke.ps1`) |
| P0-2 | Dashboard sticky alert (post-turn / queue failed) |
| P0-3 | Probe empty hints |
| P0-4 | Working-tier TTL (`working_ttl_hours` + macro_sleep) |
| P0-5 | Entity merge on primary + test |
| P0-6 | `VectorBackend` protocol + `LocalSqliteVectorBackend` + factory |
| P0-7 | Failover policy (local / empty / raise) for non-write-ready providers |
| P0-8 | Tenant isolation tests (beliefs + episodes + resolve_tenant_id) |
| P0-9 | Sensitive `api_key` vault parity |
| P0-10 | Settings capability matrix (full vs probe-only) |
| P0-11/12 | Docs tiers |

## P1 Should (this wave)

| ID | Item |
|----|------|
| P1-1 | Belief detail drawer + invalidate |
| P1-2 | Probe explain sources (already) |
| P1-3 | Last reconsolidation stats on health / Dashboard |
| P1-4 | Smoke script (CI-ready) |
| P1-5 | List click → graph + drawer |
| P1-6 | Capability strings in Settings (i18n can follow) |

## P2 — Shipped partial wave (2026-08-03 follow-on)

| ID | Status |
|----|--------|
| P2-1 Remote vector write (Qdrant REST + pgvector optional) | **Done** — hybrid dual-write; failover intact |
| P2-4 Graph export JSON/GraphML + LOD node cap 200 | **Done** |
| P2-6 Procedural skills list Dashboard | **Done** |
| P2-7 Chaos stuck-queue reclaim test | **Done** |
| P2-8 Memory quality score API + card | **Done** |
| P2-9 explain_recall in chat inject + format chips | **Done** |

## Scale foundation shipped (follow-on)

| ID | Status |
|----|--------|
| P2-2 Postgres state dual-mirror | **Done** — episodes/beliefs mirror; recall remains SQLite |
| P2-3 Neo4j GraphBackend | **Done** — dual-write when driver+URL available |
| P2-5 LLM entity tier-3 | **Done** — opt-in `entity_llm_disambiguate` |

## SaaS / multi-replica assist (follow-on)

| Item | Status |
|------|--------|
| Postgres sparse ILIKE recall assist | **Done** — merges when local thin |
| Auth-bound `resolve_tenant_id` (JWT ContextVar + auth_user_id) | **Done** — SSE/WS wired |

## Horizon A — product excellence (best options)

| ID | Status |
|----|--------|
| A1 Federated search (memory + KB, labeled) | **Done** |
| A2 Source footer on injected memory block | **Done** |
| A4 CI memory smoke step | **Done** |
| A3 Docs / boundary wording | **Done** |
| A5 i18n (en/ar) memory backends + federated UI | **Done** |
| Graph play/pause scrub + reduced-motion | **Done** |
| Operator best-path guide | **Done** (`memory-best-path.md`) |

## Trust path complete

Single-node + optional adapters + product merge of KB inject. **No further
must-have memory work** unless a scale trigger fires.

## Still deferred (scale — trigger only)

Full FTS/dense cutover of recall to Postgres · multi-region · hosted embed fleet

## P3 Trigger-only

| Trigger | Build |
|---------|--------|
| 2+ replicas sharing memory | Remote vector write + reindex; then shared state DB |
| Multi-user SaaS SLAs | Auth-bound tenant + audit |
| No local embed compute | Hosted embed-only defaults |
| Huge corpus | Scaled reconsolidation / partition |
