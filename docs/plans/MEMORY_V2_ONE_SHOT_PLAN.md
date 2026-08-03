# Memory V2 — One-Shot Non-Stop Plan (All Phases)

**Date:** 2026-08-03  
**Audit SoT:** `docs/audits/AUDIT_MEMORY_V2_COGNITIVE_2026-08-03.md`  
**Goal:** Close every load-bearing gap so V2 feels like it never forgets, then raise retrieval intelligence, complete the cognitive loop, and expose **scale-out backends via Web UI** (Phase D).  
**Style:** One continuous program of work (ordered sprints). Ship each phase with tests before the next; do not stop between phases unless a blocker appears.

---

## North star

| Outcome | Meaning |
|---------|---------|
| **Remember works** | “Remember X” → next turn / session reliably recalls X |
| **Honest ops** | Failures show in Dashboard; no silent amnesia |
| **Explainable** | Operator can see *why* something was recalled |
| **Configurable scale** | Single-node SQLite by default; remote vector/graph/embeddings toggled in **Settings Web UI** |

---

## Program map (non-stop sequence)

```text
Sprint 0  Docs & config hygiene (1–2 days)     ─┐
Sprint 1  Phase A — Remember actually works    │  Product truth
Sprint 2  Phase B — Retrieval intelligence     │
Sprint 3  Phase C — Cognitive completeness     │
Sprint 4  Dashboard memory UX overhaul         │  Operator experience
Sprint 5  Phase D — Scale backends + Web UI    ┘  Multi-replica ready
Sprint 6  Eval harness + hardening             forever
```

**Total estimate:** ~6–10 engineering weeks for one focused engineer (Phases A–D + dashboard). Can parallelize UI vs core after Sprint 1.

---

## Sprint 0 — Docs & config hygiene (do first, short)

| ID | Task | Deliverable |
|----|------|-------------|
| S0-1 | Rewrite `MEMORY_REMAINING.md` §2 to V2-only architecture | No V1 RRF “current truth” |
| S0-2 | Guide: remove false `use_new_stack=false → V1` rollback; document kill-switch = “disable injection / post-turn” | Honest ops |
| S0-3 | Dashboard copy: dual-write → “V2 write path”; L1/L2/L3 KPI labels → Beliefs / Episodes / Embeddings / Queue | UI mental model |
| S0-4 | Config: map or hide `memory.auto_store*` as “V2 post-turn pipeline” | Settings honesty |
| S0-5 | Delete dead `graph_builder` legacy branch or make it no-op with log | Code truth |

**Exit:** Operator docs match code.

---

## Sprint 1 — Phase A: “Remember actually works” (CRITICAL)

**Problem this fixes:** episodic writes + recall-tier-only dense + dead access counters → product amnesia.

### A1. Access accounting (M-CRIT-1)

| Task | Files | Detail |
|------|-------|--------|
| A1.1 | `recall.py` | On successful hit, bump `access_count`, set `last_accessed=now` for belief + episode rows (batched UPDATE, same tenant) |
| A1.2 | Config | Optional `memory.v2.access_bump_enabled` (default true) |
| A1.3 | Tests | Recall increments counters; macro_sleep promote path can fire |

### A2. Dense tier coverage (M-CRIT-2)

| Task | Files | Detail |
|------|-------|--------|
| A2.1 | `recall.py` `_episode_dense` | Search `tier IN ('recall','episodic')` (configurable list) |
| A2.2 | `dual_write` / mirror | Option: high-importance or “user asked remember” → write `tier=recall` immediately |
| A2.3 | Tests | Fresh episodic row appears in dense results |

### A3. Session bias (M-CRIT-3)

| Task | Files | Detail |
|------|-------|--------|
| A3.1 | `recall.py` | Pass `session_id` into episode retrieval: boost same-session scores (RRF weight) or soft-filter with floor of global hits |
| A3.2 | `graph_builder` | Already passes `thread_id` — verify wiring |
| A3.3 | Tests | Same-session episodes rank above cross-session for identical content |

### A4. Observability (M-CRIT-5)

| Task | Files | Detail |
|------|-------|--------|
| A4.1 | `consolidator` | Module counters: post_turn_ok, mirror_fail, extract_fail, enqueue_fail |
| A4.2 | `v2_health` / `health` | Expose last_error, queue depth, embedder ready, post_turn rates |
| A4.3 | Dashboard | Banner when last post-turn failed or queue dead-letters > 0 |
| A4.4 | Logging | Promote critical failures from `debug` → `warning` |

### A5. E2E proof

| Task | Detail |
|------|--------|
| A5.1 | Integration test: enable V2 → mirror “favorite color teal” → `recall("color")` returns teal belief/episode |
| A5.2 | Optional: graph_builder unit with forced V2 inject non-empty |

### Phase A exit criteria

- [x] Remember → next-turn recall works with defaults  
- [x] Dense search sees new episodes  
- [x] Access counts move  
- [x] Health shows write/queue health (`post_turn`, queue, embedder)  
- [x] All new tests green (`test_memory_v2_phase_a.py` + phase2)  

**Shipped 2026-08-03.** A4.3 UI banner is soft-signaled via health
`status_detail=post_turn_errors` / DEGRADED; richer dashboard banner
lands with the dashboard UX pass.

---

## Sprint 2 — Phase B: Retrieval intelligence

### B1. Real FTS5 (M-HIGH-6)

| Task | Detail |
|------|--------|
| B1.1 | `episodes_fts` virtual table + triggers on insert/update/delete |
| B1.2 | Replace `LIKE` with `MATCH` + BM25 order; keep LIKE as fallback if FTS missing |
| B1.3 | Tenant-safe queries; migration in `schema_v2.ensure_primary_schema` |
| B1.4 | Tests: tokenize, rank, tenant isolation |

### B2. Belief-graph PPR (M-CRIT-4)

| Task | Detail |
|------|--------|
| B2.1 | Build ego graph from belief triples (subject–object–predicate edges) for query entities |
| B2.2 | Seed PPR with top FTS/dense hits + entity seeds |
| B2.3 | Keep session-clique episode PPR as secondary boost |
| B2.4 | Cap nodes (`ppr_max_nodes`); never load full tenant every turn |
| B2.5 | Tests: multi-hop “I moved to Paris” → “where do I live” |

### B3. Scale dense belief search (M-MED-9)

| Task | Detail |
|------|--------|
| B3.1 | Prefer sqlite-vec / prefilter candidates before full cosine |
| B3.2 | Cap candidates; time budget for dense |

### B4. “Why recalled?” debug

| Task | Detail |
|------|--------|
| B4.1 | Optional `recall(..., explain=True)` returns source tags per hit |
| B4.2 | Dashboard / chat debug toggle: show sources (belief_fts / dense / ppr / session_boost) |
| B4.3 | Config: `memory.v2.explain_recall` |

### Phase B exit criteria

- [ ] FTS5 live on episodes  
- [ ] Belief multi-hop improves at least one golden multi-hop case  
- [ ] Explain mode available  

---

## Sprint 3 — Phase C: Cognitive completeness

### C1. Procedural memory loop (M-MED-10)

| Task | Detail |
|------|--------|
| C1.1 | On tool planning / system inject: top-K high-confidence DAGs with matching preconditions |
| C1.2 | Fence as untrusted procedural hints |
| C1.3 | Tests: successful tool sequences raise confidence; quarantine still works |

### C2. Entity resolution productized

| Task | Detail |
|------|--------|
| C2.1 | Micro-consolidation always passes embedding candidates into `resolve_entity` |
| C2.2 | Dashboard: “Pending entity merges” list + approve/deny |
| C2.3 | API: `GET/POST /api/memory/v2/entity-merges` |

### C3. Nightly global re-consolidation (S3)

| Task | Detail |
|------|--------|
| C3.1 | New queue task `global_reconsolidation` (24h or Settings-triggered) |
| C3.2 | Merge near-duplicate beliefs, re-extract from dirty episodes, re-embed |
| C3.3 | Separate from backup/export cadence (slow disk must not block decay) |

### C4. Working-memory tier

| Task | Detail |
|------|--------|
| C4.1 | Current-thread buffer tier `working` (short TTL) |
| C4.2 | Promote working → episodic on turn end; clear on `/new` |
| C4.3 | Recall prefers working for active thread |

### C5. Multi-tenant hard default (optional product flag)

| Task | Detail |
|------|--------|
| C5.1 | Settings: `tenant_mode` UI (shared / per_user / per_platform) |
| C5.2 | Tests: tenant A cannot read B’s beliefs |

### Phase C exit criteria

- [ ] Procedural hints appear when relevant  
- [ ] Entity merge UI works  
- [ ] Nightly reconsolidation runnable  
- [ ] Working tier wired  

---

## Sprint 4 — Dashboard Memory & Graph UX overhaul

### 4.0 Current state (audit of UI)

**Present today (good bones):**

| Area | What exists |
|------|-------------|
| Memory & Governance | Status badge, pipeline chips, refresh |
| KPI strip | Vector / FTS / graph / health (still **V1-labeled**: L1 Chroma, L3 BM25, L2 graph) |
| Component health board | Grouped probes + issues list |
| **V2 Cognitive Engine** | Beliefs / episodes / entities / queue KPIs |
| Known Beliefs list | Search/filter currently-valid beliefs |
| Topology graph | Canvas force layout, filters (entity type / predicate), search, bi-temporal scrub, inspect panel |
| Maintenance deck | Backup / optimize / snapshots, backup table |

**Pain points:**

1. **Two mental models** — legacy KPI strip (Chroma/FTS/L2) stacked above V2 panel → confusion  
2. **V1 labels** — `kpi_l1_chroma`, `kpi_l3_bm25`, `kpi_l2_graph` after V1 removal  
3. **No “last post-turn / last error / embedder model”** on V2 KPIs  
4. **No “why recalled” / live probe** — cannot type a query and see what would inject  
5. **Graph is belief-centric only** — no episode nodes, no session layers, no “path from query seed”  
6. **Belief list is read-only** — no supersede history, invalidate, edit, or provenance  
7. **Queue opaque** — no failed/dead-letter table, no “retry task”  
8. **Time scrub underused** — no play animation, no shareable `?at=` link  
9. **Mobile/dense layout** — two-column 500px min-heights fight small screens  
10. **Maintenance still FTS/vector-centric** — should be V2 backup/export of `memory_state.db` / `memory_ops.db`  
11. **No Settings deep-link** to memory toggles from dashboard  
12. **Graph performance** — full redraw; no virtualization for large graphs  

### 4.1 Information architecture (target)

```text
Dashboard → Memory (single section)
  ├─ Status strip: ACTIVE | DEGRADED | OFF · embedder · last post-turn · queue
  ├─ KPI: Active beliefs · Episodes (by tier) · Entities · Queue (pending/failed)
  ├─ Live recall probe: [query box] → ranked hits + sources (belief/dense/ppr/session)
  ├─ Beliefs browser: list + supersede chain + invalidate + filter
  ├─ Graph: topology + path highlight + bi-temporal scrub + export
  ├─ Queue & workers: depth, last jobs, retry dead-letter
  └─ Maintenance: backup/export V2 DBs, reembed, reconsolidation trigger
```

### 4.2 Concrete UI improvements (implement in Sprint 4)

| ID | Improvement | Priority |
|----|-------------|----------|
| **DUI-1** | **Collapse legacy KPI strip** into V2 KPIs (or re-label: Embeddings / FTS index / Beliefs / Health) | P0 |
| **DUI-2** | **Live recall probe** panel: input + top-k results + source chips + token cost estimate | P0 |
| **DUI-3** | **Post-turn health strip**: last success time, last error, failed queue count (red if >0) | P0 |
| **DUI-4** | **Belief detail drawer**: subject/predicate/object, valid_from/until, confidence, supersedes chain, access_count | P1 |
| **DUI-5** | **Invalidate / re-assert** belief actions (HITL for high-stakes) | P1 |
| **DUI-6** | **Graph: “Path from query”** mode — seed entities from probe, highlight PPR path | P1 |
| **DUI-7** | **Graph: episode overlay toggle** (session clusters as faint nodes) | P2 |
| **DUI-8** | **Graph export** PNG/SVG + GraphML download button | P1 |
| **DUI-9** | **Queue table**: pending/processing/failed + Retry + Clear dead-letter | P1 |
| **DUI-10** | **Bi-temporal scrub**: play/pause, step, deep-link `?at=iso` | P2 |
| **DUI-11** | **Responsive**: stack beliefs/graph on narrow screens; reduce min-heights | P1 |
| **DUI-12** | **Settings deep-links**: “Open memory settings” → Settings embedder + memory.v2 + **Phase D backends** | P0 |
| **DUI-13** | **Empty states**: guided “Teach me a fact” CTA → chat with sample prompt | P2 |
| **DUI-14** | **a11y**: keyboard pan/zoom graph, ARIA for filters, reduced-motion option | P2 |

### 4.3 Graph-specific upgrades

| Today | Target |
|-------|--------|
| Force layout canvas | Keep canvas; add **LOD** (cluster when N>200) |
| Entity/predicate filters | + confidence slider, tenant filter, session filter |
| Tooltip on hover | + sticky pin, copy belief id |
| Inspect panel text | + “Open in beliefs list”, “Used in last recall?” |
| No edge weight visual | Edge thickness ∝ confidence / access |
| No search→graph sync | Belief list click → center graph node |

### 4.4 Dashboard APIs to add/extend

| Endpoint | Purpose |
|----------|---------|
| `GET /api/memory/v2/health` | Extend with post_turn_*, embedder, last_error (A4) |
| `POST /api/memory/v2/probe` | Live recall dry-run for probe UI |
| `GET /api/memory/v2/beliefs/{id}` | Detail + supersede chain |
| `POST /api/memory/v2/beliefs/{id}/invalidate` | Soft delete |
| `GET /api/memory/v2/queue` | Queue rows for table |
| `POST /api/memory/v2/queue/{id}/retry` | Retry failed |
| `GET /api/memory/v2/graph` | Already exists — add `seed`, `session_id`, `explain` |

### Phase Dashboard exit criteria

- [ ] No V1-only KPI labels  
- [ ] Probe shows real recall  
- [ ] Queue failures visible  
- [ ] Graph + beliefs list stay in sync  

---

## Sprint 5 — Phase D: Scale backends + **Web UI configuration**

**Requirement:** Phase D is not env-only. Operators configure scale-out from **Settings → Memory → Backends** (and optional Dashboard deep-link).

### D0. Design principles

1. **Default remains local SQLite** (`memory_state.db` / `memory_ops.db` + local/sqlite-vec embeddings) — zero config for single-user.  
2. **Backend profiles are pluggable** via a small interface; UI only sets ConfigStore keys.  
3. **Live re-read** (mirror proxy provider / HITL pattern): changing backend applies on next recall/write without full rearchitecture; some switches need “Apply + reembed” confirm.  
4. **Secrets vaulted** (`is_sensitive_config_key`) for remote API keys.  
5. **Health probes** per backend on Settings save and Dashboard.  
6. **Never break chat** if remote fails: fail-open to local or empty + clear error in UI.

### D1. Backend abstraction (core)

| Interface | Responsibility |
|-----------|----------------|
| `VectorBackend` | `search(vec, filters)`, `upsert(id, vec, meta)`, `delete` |
| `GraphBackend` (optional later) | belief triple store for multi-replica PPR |
| `EmbedderBackend` | already partly in `embedder.py` — unify remote presets |

**Implementations:**

| Backend | Type | Use |
|---------|------|-----|
| `local_sqlite` | vector+state | Default |
| `sqlite_vec` | vector | Default dense path |
| `pgvector` | vector | Multi-replica Postgres |
| `qdrant` | vector | External vector DB |
| `chroma_remote` | vector | Optional (not chat SoT) |
| `local_minilm` / `bge_m3` | embedder | Default local |
| `openai_compat` | embedder | Hosted OpenAI-compatible |
| `cohere` / etc. | embedder | Future presets |

**Config keys (ConfigStore):**

```yaml
memory:
  backends:
    mode: local                    # local | hybrid | remote
    vector:
      provider: sqlite_vec         # sqlite_vec | pgvector | qdrant
      url: ""                      # DSN or HTTP endpoint
      api_key: ""                  # vaulted
      collection: kazma_memory
      dimension: 1024
    embedder:
      provider: local              # local | openai_compat | custom
      model: BAAI/bge-m3
      base_url: ""
      api_key: ""                  # vaulted
      dim: 1024
    graph:
      provider: sqlite             # sqlite | postgres_graph (future)
      url: ""
    failover:
      on_remote_error: local       # local | empty | raise
  v2:
    # existing keys...
```

### D2. Web UI — Settings → Memory → Backends

**Location:** Web Settings page, new tab/section **Memory** (or sub-panel under Embedder expanded to “Memory & embeddings”).

**UI layout (easy for non-experts):**

```text
┌─ Memory backends ─────────────────────────────────────┐
│ Mode:  (•) Local only   ( ) Hybrid   ( ) Remote-first │
│                                                        │
│ Embeddings                                             │
│   Provider [ Local MiniLM/BGE ▼ ]  Model [........]    │
│   Base URL [..............]  API key [••••] (if remote)│
│   [ Test embed ]  latency · dim                        │
│                                                        │
│ Vector store                                           │
│   Provider [ SQLite-vec ▼ ]                            │
│   Connection [ DSN / URL .............. ]              │
│   Collection [ kazma_memory ]                          │
│   [ Test connection ]  [ Rebuild embeddings… ]         │
│                                                        │
│ Graph store (advanced)                                 │
│   Provider [ Local SQLite ▼ ]                          │
│   Connection [ ................ ]                      │
│                                                        │
│ Failover if remote down: [ Fall back to local ▼ ]      │
│                                                        │
│ [ Save ]  [ Reset to local defaults ]                  │
│ Status: ● local healthy · last probe 2s ago            │
└────────────────────────────────────────────────────────┘
```

**UX rules:**

| Rule | Behavior |
|------|----------|
| Guided mode | Radio **Local / Hybrid / Remote** expands only relevant fields |
| Test buttons | Call probe APIs; toast success/fail with latency |
| Rebuild | Modal confirm: “Re-embed all episodes/beliefs? May take minutes.” Progress bar from existing reembed status |
| Save | `batch_set` ConfigStore + invalidate embedder singleton + vector backend factory |
| Validation | SSRF on remote URLs (`allow_private` for local docker if needed); never store masked `***` as key |
| i18n | All labels in `i18n.py` en/ar |

### D3. APIs for Phase D UI

| Method | Path | Role |
|--------|------|------|
| `GET` | `/api/settings/memory/backends` | Current config (keys masked) |
| `PUT` | `/api/settings/memory/backends` | Save profile |
| `POST` | `/api/settings/memory/backends/test-embed` | Probe embedder |
| `POST` | `/api/settings/memory/backends/test-vector` | Probe vector DB |
| `POST` | `/api/settings/memory/backends/rebuild` | Start reembed job |
| `GET` | `/api/settings/memory/backends/rebuild/status` | Progress |
| `POST` | `/api/settings/memory/backends/reset-local` | Wipe remote config → local |

### D4. Runtime wiring

| Task | Detail |
|------|--------|
| D4.1 | `get_vector_backend()` factory re-reads ConfigStore live (like `get_proxy_provider`) |
| D4.2 | `get_embedder()` re-reads or invalidate-on-save |
| D4.3 | `recall` / `dual_write` / `mutate_belief` use factory — no hard-coded sqlite paths for vectors when remote |
| D4.4 | SQLite **state** remains local by default; optional later “state on Postgres” is out of Phase D unless product needs it |
| D4.5 | Failover policy applied on every remote call |

### D5. Tests (Phase D)

| Test | Assert |
|------|--------|
| Local mode | Default factory returns sqlite paths |
| Save backends | ConfigStore keys set; api_key not `***` |
| Test-embed mock | 200 + latency |
| Remote fail → local | failover policy |
| UI static | Settings HTML/JS has Memory backends section |

### Phase D exit criteria

- [ ] User can switch Local → OpenAI-compatible embeddings from UI without editing yaml  
- [ ] User can point vectors at pgvector/Qdrant from UI  
- [ ] Test connection works  
- [ ] Rebuild embeddings progress visible  
- [ ] Chat still works if remote down (failover)  

---

## Sprint 6 — Eval harness & continuous quality

| Task | Detail |
|------|--------|
| E1 | Golden set JSON: (setup turns, query, expected contains) |
| E2 | Nightly CI job: run golden set against V2 |
| E3 | Dashboard “Memory quality” score (last run pass rate) |
| E4 | Chaos: kill worker mid-queue; assert dead-letter + recovery |

---

## Dependency graph (one-shot order)

```text
S0 hygiene ──► S1 Phase A ──┬──► S2 Phase B ──► S3 Phase C
                            │
                            └──► S4 Dashboard (can start after A4 health APIs)
                                       │
S1 A2/A4 ──────────────────────────────┼──► S5 Phase D UI (needs stable config SoT)
                                       │
S2/S3 ─────────────────────────────────┴──► S6 Eval (uses probe + A/B behavior)
```

**Non-stop rule:** After S1 merges, open S2 and S4 in parallel. S5 after A4 + Settings patterns exist. S3 can trail S2. S6 continuous after A5 E2E exists.

---

## Risk register

| Risk | Mitigation |
|------|------------|
| Reembed blocks chat | Background job; never on request thread |
| Remote vector latency | Timeouts + failover + cache query embeddings |
| Breaking existing local users | Default local; migration flags opt-in |
| Graph UI OOM | LOD / node cap / server-side filter |
| Scope creep “best in universe” | Exit criteria per phase; no new schema in Phase A |

---

## Success metrics (product)

| Metric | Target after full program |
|--------|---------------------------|
| Remember→recall E2E pass | ≥ 95% on golden set |
| Silent empty post-turn rate | Visible in health; alert if spike |
| p95 recall latency local | < 50ms for small corpora; budgeted for large |
| Operator time to switch to remote embeddings | < 2 minutes in Settings UI |
| Dashboard “why recalled” | Available for any probe query |

---

## File touch map (summary)

| Phase | Primary paths |
|-------|----------------|
| A | `recall.py`, `dual_write.py`, `consolidator.py`, `v2_health.py`, `graph_builder.py`, tests |
| B | `recall.py`, `ppr.py`, `schema_v2.py`, `vector_engine.py` |
| C | `procedural.py`, `entity_resolution.py`, `worker_bootstrap.py`, new queue handlers |
| Dashboard | `dashboard.html`, `routes_direct.py`, CSS, i18n |
| D | `memory/backends/*` (new), `embedder.py`, Settings UI/JS, `config.py`, health |

---

## Immediate next action

When you say **go**:

1. Execute **Sprint 0 + Sprint 1 (Phase A)** first (non-stop until exit criteria).  
2. Then **Sprint 4 Dashboard DUI-1..3 + probe** in parallel with **Sprint 2**.  
3. **Sprint 5 Phase D Web UI** after Settings backend APIs land.

This is the complete non-stop plan from “remember works” through “scale from the UI” and a memory dashboard that matches V2.
