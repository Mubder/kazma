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

## P2 Next time (do not start)

Remote vector **write** path · shared Postgres state · Neo4j · graph LOD/export polish · LLM entity tier-3 · procedural skills browser · chaos kill-worker · quality score · chat-inline why-recalled every turn.

## P3 Trigger-only

| Trigger | Build |
|---------|--------|
| 2+ replicas sharing memory | Remote vector write + reindex; then shared state DB |
| Multi-user SaaS SLAs | Auth-bound tenant + audit |
| No local embed compute | Hosted embed-only defaults |
| Huge corpus | Scaled reconsolidation / partition |
