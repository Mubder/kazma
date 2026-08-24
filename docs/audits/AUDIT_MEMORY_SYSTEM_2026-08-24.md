# Memory System Audit — V2 Cognitive Engine

**Date:** 2026-08-24
**Scope:** V2 memory stack end-to-end — write paths (extractor / mutation / hygiene / resolution / anchoring), recall + prompt-fencing, state mirror (PG), materialized counts, HTTP surfaces + tenant isolation, schedulers/queue, export/backup, legacy residuals.
**Method:** static verification (every finding cited `file:line`) + live-data forensics against the production stores (`C:\Users\balfa\kazma\kazma-data`, SQLite SoT + PG mirror via `KAZMA_DATABASE_URL`) + three parallel recon passes.
**Trigger:** operator-reported orphan nodes in the memory graph; investigation surfaced one live outage and multiple structural findings.
**Status of fixes:** M-01..M-17 all closed (M-01..M-09 / M-12 / M-13 in the first same-day PRs; M-10, M-11, M-14..M-17 in the P3 hygiene sweep).

---

## Executive summary

| ID | Sev | Status | One-line |
|----|-----|--------|----------|
| M-01 | **P0** | ✅ FIXED `942c026d` | Function-local import shadowing killed every send_prompt on cursor connections |
| M-02 | **P1** | ✅ FIXED `a6d1066d` | Duplicated count/degree SQL drifted between maintainer and read path |
| M-03 | **P1** | ✅ FIXED `98fcaa65` | Ego-graph leaf orphans: payload-object beliefs never anchored to hub |
| M-04 | **P1** | ✅ FIXED `91891140` | PG mirror never receives invalidation/supersede/archive → 35 dead facts live in mirror, 21 ghosts |
| M-05 | **P1** | ✅ FIXED `2e0c70f4`/`002da979` | Tenant isolation is advisory on ~13 memory routes incl. cross-tenant reads + all-tenant wipe |
| M-06 | P2 | ✅ FIXED `38349759` | Entity-merge rewrites beliefs without recomputing materialized counts (3 surfaces) |
| M-07 | P2 | ✅ FIXED `38349759` | UI manual merge rewires beliefs with NO tenant scoping |
| M-08 | P2 | ✅ FIXED `a3acaff3` | Painter top-N slicing amputates edges silently; link-drop delta never surfaced |
| M-09 | P2/P3 | ✅ FIXED `38349759` | Reconsolidation dedupe invalidates without recompute or audit row |
| M-10 | P3 | ✅ FIXED | FTS periodic COUNT reconciliation |
| M-11 | P3 | ✅ FIXED | INSERT OR IGNORE rowcount guard |
| M-12 | P3 | ✅ FIXED `a3acaff3` | ego_anchor predicate normalize |
| M-13 | P3 | ✅ FIXED `a3acaff3` | decide_entity_merge self/cycle guards |
| M-14 | P3 | ✅ FIXED | entity-delete merges-ledger preservation |
| M-15 | P3 | ✅ FIXED | graph-clear audit + Neo4j edge cleanup |
| M-16 | P3 | ✅ FIXED | Export coverage (episodes/archive/merges/audit) |
| M-17 | P3 | ✅ FIXED | Console groups double-fetch + Ungroup consumer |

Verified-healthy list at bottom (what was audited and holds).

---

## M-01 [P0, FIXED] UnboundLocalError killed every send_prompt on V2-cursor connections

A function-local `from kazma_ui.active_turns import get_active_turn` inside the
*legacy* catch-up branch of `chat_websocket`
(`routes/ws_chat.py`, pre-fix line ~741) shadowed the module-level binding for
the entire handler scope. Turn Delivery V2 connections (`?last_seq=`) skip that
branch, so the duplicate-turn guard later read an unbound local → handler died
on every send_prompt → client exhausted its 5-retry ack loop →
"Could not deliver your message after several retries".

Latent since the V2 resume landing: pre-V2 the legacy branch always ran, so the
local import always executed first. Missed by tests because the e2e emits frames
without sending prompts, and WS unit tests connected without a cursor.

**Fix:** shadowing import deleted; regression test drives a real `send_prompt`
over a `?last_seq=` connection through the guard to `prompt_ack` (verified it
hangs on buggy code); source contract bans function-local active_turns
re-imports in the file. Evidence: outage log lines
`.kazma/kazma.log:1337-1357` (`cannot access local variable 'get_active_turn'`).

## M-02 [P1, FIXED] Count/degree SQL duplication drift

`entity_counts.py` carried a second handwritten copy of the degree SQL
("mirrors _entity_degree_sql exactly") and kept pre-fix semantics after the
payload-object fix landed only in `memory_api.py`'s copy — materialized
`graph_degree` counted scalar payloads as neighbors. Surfaced by Kazma's own
verification report ("degree was already 1").

**Fix:** canonical `belief_count_sql()` / `entity_degree_sql()` live once in
`entity_counts.py`; `memory_api` imports them; source-contract test asserts no
inline copy returns.

## M-03 [P1, FIXED] Ego-graph leaf orphans

Beliefs like `sakhrfit → availability_status → fully_clean` minted a subject
concept with a payload object — nothing ever linked either to the hub. Painter
renders them as a disconnected component; entities list masked it by counting
scalars as degree.

**Fix:** `memory/ego_anchor.py` — write-time anchor
(`user → related_to → <subject>`, `extraction_method='system_tool'`) plus an
idempotent backfill wired into the 6h macro-sleep sweep. Live verification:
19 anchored / 6 skipped / isolated=0 / all named targets degree≥1.

---

## M-04 [P1, OPEN] Mirror never hears about death — 35 zombie facts

The dual-write fires only on successful mutations and hardcodes the mirrored
row as alive:

- `belief_mutation.py:392-414` — `mirror_belief_to_state({... "valid_until": None, "invalidated_at": None ...})`
- No mirror call exists in `hygiene.invalidate_belief` (`hygiene.py:169+`),
  the supersede close (`belief_mutation.py:630`), macro_sleep archive deletes
  (`macro_sleep.py:193`), `memory_api` entity-delete/clear, or `/graph/clear`.

**Live data (prod):** mirror 434 rows vs SQLite 413 → 21 exist *only* in the
mirror; of shared ids, **35 are dead in SQLite but live in the mirror**
(zero false-dead in the other direction). Consequences:

- If `KAZMA_MEMORY_STATE_ROLE=primary` is ever enabled (the documented
  multi-replica cutover, `state_backend.py:9-11`), recall resurrects 35 dead
  facts as truth.
- With role=mirror today, ILIKE-assist can still surface stale rows depending
  on merge logic in `recall.py`.

**Fix plan:** tombstone propagation — supersede/invalidate/archive/delete paths
upsert the mirror row carrying `valid_until/invalidated_at` (the upsert at
`state_backend.py:297-301` already supports it); then run a one-time
reconciliation (delete mirror rows absent from SQLite; sync death flags).
Add a drift assertion to the nightly export handler.

## M-05 [P1, OPEN] Tenant isolation is advisory across ~13 routes

`entities.id` is a GLOBAL PK (§16) while most memory routes key on bare ids,
and several reads never consult tenant at all. Enforcement ON does not help
the read-side gaps. Highlights (full inventory in recon notes):

- Unscoped reads: `/api/memory/v2/beliefs`, `/beliefs/{id}`,
  `/beliefs/{id}/recall-trail` (leaks episode previews),
  `/entity-merges`, `/procedural`, `/quality`, `/health`,
  `/graph`, `/graph/export` (`routes_direct.py:439,533,601,679,889,940,273,1011,1311`
  — painter unscoped *by explicit comment*, `:1590-1593`).
- **`POST /api/memory/graph/clear` wipes ALL tenants' active beliefs** in one
  UPDATE, no tenant param (`routes_direct.py:233-240`).
- Id-keyed destructive mutations bypass the tenant predicate even in
  enforcement mode: rename/protect/major/delete/merge-edit/repoint/
  invalidate-batch/hygiene-run/groups-delete|move|tier (`memory_api.py`
  table in recon notes).
- Undo subsystem is global-token + stamps *current* request tenant on restore
  (`memory_api.py:96-133, :371, :794-813`) → resurrection lands in wrong tenant.
- `/api/memory/graph/search` hardcodes `"default"` tenant
  (`routes_direct.py:198`).
- Live proof multi-tenant rows already exist: one episode under tenant
  `web:1e41e64a…` among 345 `default`.

**Fix plan:** scope the ~13 unscoped reads; add tenant param + confirmation to
graph-clear; bind undo tokens to (tenant, principal); pass tenant into
graph-search. Single-user installs unaffected behaviorally (everything is
`default`).

## M-06 [P2, OPEN] Merge rewires skip materialized-count recompute

`decide_entity_merge` rewrites subjects/objects across N beliefs
(`entity_resolution.py:520-542`) and commits without
`recompute_entity_counts` — inherited by worker auto-merge
(`worker_bootstrap.py:126`) and REST decide (`routes_direct.py:704-728`). The
agent tool variant has the same gap (`tool_registry.py:1396-1430`). Stale
values are sticky until each entity is next touched.

**Fix:** one `recompute_entity_counts(conn, [source_id, target_id])` before the
commit heals all three surfaces.

## M-07 [P2, OPEN] UI manual merge is the last unscoped belief-rewriter

`memory_api.py:884-891` runs `UPDATE beliefs SET subject/object=? WHERE …`
with no `tenant_id` predicate — the same class already fixed in
`entity_resolution.py:520-527` and `tool_registry.py:1397-1402`. Cross-tenant
corruption vector; also irreversible per its own receipt.

## M-08 [P2, OPEN] Painter slicing amputates links silently

`routes_direct.py:1612-1619`: nodes sorted by beliefCount, first `limit`
(default 200) kept, then links filtered to kept endpoints. The dropped-edge
delta (`total_links − links`) is computed but never shown; the truncation
banner (memory_console.js:3629-3642) reports node counts only. `groups` is
returned full/unfiltered regardless. Low-risk for correctness, high for
operator trust ("why is this edge missing?").

**Fix:** extend banner with `edges hidden by slicing: N`; optionally keep any
node that shares a kept endpoint's component edge (bounded growth).

## M-09 [P2/P3, OPEN] Nightly dedupe invalidations lack recompute + audit

`global_reconsolidation._dedupe_beliefs` bulk-invalidates duplicate losers
without `recompute_entity_counts` and without a `memory_audit_log` row —
unlike every other invalidation path. Related smaller misses: functional-
supersede old-object not recomputed (`belief_mutation.py:446` covers new
endpoints only); batch-undo/edit-undo raw UPDATEs without recompute/audit
(`memory_api.py:1490-1500, :1346-1361`).

## P3 batch (M-10..M-17)

| ID | Finding | Status |
|----|---------|--------|
| M-10 | FTS partial desync never reconciled: auto-rebuild only when FTS==0 vs base; otherwise reactive to corruption only. Cheap periodic COUNT check would close silent recall-MISSES. | ✅ `fts_health.fts_drift_check` on the 6h macro-sleep sweep |
| M-11 | `_insert_belief` INSERT OR IGNORE + caller reports supersede: PK collision would close old fact with no successor. Add defensive rowcount check. | ✅ rowcount + uuid retry; IntegrityError rolls back the supersede close |
| M-12 | ego_anchor passes `predicate_type="semantic"` — invalid value silently falls back to `'set'` (append-only, safe-by-luck). Normalize + assert anchor stays non-functional (system_tool must not gain supersede rights). | ✅ shipped `a3acaff3` |
| M-13 | decide_entity_merge lacks explicit self/cycle guards (API/tool layers have them); A→B→A cycles resolve to retired entry id. | ✅ shipped `a3acaff3` |
| M-14 | DELETE entity purges its entity_merges ledger rows — loses quarantine audit trail. | ✅ copy into `entity_merges_archive` then drop live rows (FK) |
| M-15 | graph-clear lacks audit row and Neo4j edge cleanup for the mass invalidation. | ✅ audit row + `clear_tenant_edges`; also fixed `(now, now, tenant)` bind (was 2 params for 3 placeholders) |
| M-16 | Export skips episodes/ops-db/beliefs_archive/entity_merges and non-default tenants; acceptable only because native `.db` backups compensate. | ✅ extra JSONL dumps; per-tenant fan-out already in the 24h scheduler |
| M-17 | Console double-fetches groups every 30s (payload already embeds them); 4 server group/export routes have no UI consumer. | ✅ poll uses payload `groups`; Ungroup wires DELETE `/graph/groups/{id}` |

---

## Verified healthy (audited, holds)

- **Single mutation choke point:** repo-wide, exactly one product-code direct
  `INSERT INTO beliefs` (eval-golden seeder → temp DB, verified isolated);
  everything else routes `mutate_belief` / `invalidate_belief`.
- **Source-trust gate:** user_explicit functional beliefs cannot be superseded
  by lower trust; blocked attempts audited (`blocked_supersede`);
  BEGIN IMMEDIATE + lock serialization sound. §20 invariant intact.
- **FTS:** external-content triggers cover supersedes/invalidate/archive;
  soft-invalidated rows cannot ghost-recall (join filters live-only);
  live index delta = 0 on prod.
- **Bi-temporal integrity:** 0 dangling `supersedes_id`; 0 stale sentinels;
  WAL + busy_timeout conventions hold on both DBs.
- **Auth:** default-deny middleware covers all `/api/*` including memory;
  open/demo modes documented and refused under production flag.
- **Backups:** native backups cover BOTH memory DBs (WAL-safe API), retention
  pruned; universal backup sweeps every `*.db`.
- **Queue:** `memory_task_queue` 2878/2878 completed, zero dead letters;
  all four schedulers registered at boot (§15A) — macro-sleep sweep observed
  firing on schedule during this audit.
- **V1→V2 cutover holds:** legacy `memories` table has readers only
  (backfill migration + inert status KPI).
- **Prompt-fence:** recalled memory reaches the LLM through
  `format_recall_block(fence_source="memory_v2_recall")` — fenced, matching §11B policy.

## Live-data appendix (production, 2026-08-24)

```
memory_state.db: beliefs 413 (live) · beliefs_archive 64 · episodes 345+1(web tenant)
                 entities 94 · procedural_dags 193 · entity_merges 4
                 graph_associations 4 (2 members missing from entities)
                 FTS delta 0 · stale sentinels 0 · dangling supersedes 0
                 isolated 0 · orphan shells 1
memory_ops.db:   memory_task_queue 2878 completed / 0 failed · commitments 59
PG mirror:       kazma_beliefs 434 → only-in-mirror 21 · state mismatches 35
                 (all mirror-live-but-SQLite-dead) · sqlite-only 0
```

## Fix plan (priority order)

> **Progress note (2026-08-24, same day):** PR-1..PR-4 shipped — M-04
> (tombstone propagation + `scripts/reconcile_memory_mirror.py` +
> nightly drift assertion; prod mirror reconciled to `MIRROR == SOT`,
> 433/433), M-05 reads/destructive-mutations/undo-binding (PR-2a/2b),
> M-06/M-07/M-09 (PR-3), M-08 banner delta, M-12 predicate normalize,
> M-13 self-merge guard (PR-4/5a). **P3 hygiene (M-10, M-11, M-14..M-17)
> shipped in the follow-up sweep the same day** — FTS COUNT
> reconciliation on macro-sleep, INSERT-OR-IGNORE rowcount+rollback,
> `entity_merges_archive`, graph-clear Neo4j + audit (and the 2-vs-3
> bind bug on the invalidate UPDATE), export JSONL coverage, console
> groups single-fetch + Ungroup. Audit findings are closed.

1. **M-04** mirror tombstones + one-time reconciliation + nightly drift
   assertion. (Blocks safe multi-replica cutover.)
2. **M-05** tenant-hardening pass over the enumerated routes (mechanical:
   thread `_memory_tenant_id()` predicates + params).
3. **M-06/M-07/M-09** recompute-at-mutation completeness (one-line merge fix,
   dedupe-nightly fix, undo fixes).
4. **M-08** painter slicing honesty (banner delta + optional edge-aware keep).
5. **M-10..M-17** hygiene batch as a single sweep.

---

*Audited by ox-alpha (coding agent). Every finding reproducible from cited
code/data at audit time; live numbers from read-only probes preserved in
session tooling.*
