# Memory — Done vs Remaining

**Status date:** 2026-08-03  
**Shipped polish commit:** see CHANGELOG *Memory polish P2–P7* + *Memory V2 Phase A*  
**Primary guide:** [`docs/docs/guide/memory-and-rag.md`](../docs/guide/memory-and-rag.md)

Use this file when picking up memory work later. Do **not** start a greenfield rewrite unless a multi-replica product requirement appears.

---

## 1. What we did (shipped)

### A–E. Core stack (prior)

Fail-closed store, config SoT, FTS unify, L2 SQLite graph, consolidator, dashboard graph UI, unit tests. See git history / earlier CHANGELOG sections.

### F. Polish P1–P7 (this wave)

| # | Item | Status |
|---|------|--------|
| **P1** | Consolidator settings UI | **Done** — TUI Settings toggles; Web Packages/health surfaces consolidator |
| **P2** | Stronger consolidator cost | **Done** — `skip_llm_if_auto_stored` (default true) + fixed `skip_adapter_if_auto_stored` |
| **P3** | Graph UI polish | **Done** — hover tooltips, always-on edge labels, **Export** JSON |
| **P4** | Fence at injection time | **Done** — per-turn RAG uses `format_untrusted_block` + drops override-like hits |
| **P5** | Dual Chroma client cleanup | **Done** — `chroma_client.get_chroma_client()` shared by VectorMemory + L1 |
| **P6** | L3 hard tenant filter | **Done** — exact `tenant_id` match only (no NULL sharing when tenant set) |
| **P7** | Integration RAG E2E | **Done** — lightweight L2+L3+fence tests (no Chroma cold-start in CI) |

---

## 2. Architecture (current truth — V2 only)

```text
User turn
  → per-turn recall() (beliefs + episodes hybrid + session bias + PPR)
       → format_untrusted_block → system prompt
  → LLM reply
  → schedule_post_turn_memory (OS thread)
       → mirror_episode (+ optional promote-to-recall on "remember")
       → heuristic belief extract
       → enqueue micro_consolidation (LLM deep-pass on worker)

Tools memory_search → recall(); memory_store → V2 episode/belief
KB (kazma_kb_*) → isolated from chat memory
```

**On-disk (V2):**

| Path | Role |
|------|------|
| `kazma-data/memory_state.db` | beliefs, episodes, entities, procedural DAGs |
| `kazma-data/memory_ops.db` | task queue + audit |

See `docs/docs/guide/memory-and-rag.md` and `docs/plans/MEMORY_V2_ONE_SHOT_PLAN.md`.

---

## 3. What remains (later)

### V2 Cognitive Engine — SHIPPED (2026-07-31) + Phase A fixes (2026-08-03)

Bi-temporal beliefs, episodes, PPR, durable queue, macro_sleep, backup/export.
**V1 RRF stack was removed** — `use_new_stack=false` only disables V2 injection
/post-turn; it does **not** restore L1–L4 RRF.

Phase A (Sprint 1): access bump on recall, dense search over episodic+recall,
session bias, post-turn metrics, explicit-remember → recall tier.

Phase B (Sprint 2): real FTS5 (episodes+beliefs), belief-graph multi-hop PPR,
capped dense belief scan, `explain_recall` source tags.

### Product / scale (only if required)

| # | Item | Trigger |
|---|------|---------|
| S1 | Remote vector (pgvector / Qdrant) | Multi-replica shared recall |
| S2 | Shared graph backend (Postgres graph or Neo4j) | Multi-replica structural memory |
| S3 | Nightly corpus re-consolidation | Large dirty stores need global merge. **Not** the same as the nightly native backup + JSONL/GraphML export — those *recovery artefacts* are already wired (24h scheduler in `worker_bootstrap.py`); S3 is a global re-merge/re-extraction pass over the belief graph itself. |
| S4 | Hosted embedding-only service | No local MiniLM on edge |

### Explicitly **not** planned as rewrites

~~Replacing RRF adapter with a new "memory v2" product name~~ — **Done (V2 cognitive engine, 2026-07-31)**.
- Merging Knowledge Library into chat `agent_memory`
- Dropping SQLite L2 for Neo4j on single-node by default

### Hygiene P1–P3 (optional; shipped 2026-07-27)

These are **not** a greenfield rewrite — strengthen-existing stack only.

| # | Item | Status |
|---|------|--------|
| **H-P1** | `sqlite_query` authorizer allows safe `COUNT`/`LIKE`/… | **Done** — `database_client/tools.py` |
| **H-P2** | Always retire empty legacy `memory_fts` | **Done** — `memory/fts5.py` |
| **H-P3** | Populate named-worker L4 after successful dispatch | **Done** — `swarm/worker_dispatch.py` |

**P0 (agent reliability, not memory):** max-iter synthesis + Settings **Max tool rounds** (`agent.max_iterations`, clamp 5–100) already on main (`6b5aa5f0`, `8fd85ff3`).

### Integrity solid fix (2026-07-27)

| Issue | Fix |
|-------|-----|
| L3 `embedding` always NULL | Adapter + backends encode via shared embedder on write |
| L3 `timestamp` always 0 | `resolve_unix_timestamp()` on every write; never hardcode 0 |
| L3 semantic dead | Hybrid FTS+BLOB cosine search by default |
| Graph empty / underused | Chunk+user edge+heuristic SPO on every store; backfill seeds from L3 |
| Legacy rows | `memory.backfill` one-shot at boot / maintenance |

**Manual full re-embed (all rows):**  
`python -c "from kazma_core.memory.backfill import run_memory_integrity_backfill; print(run_memory_integrity_backfill(force=True))"`

---

## 4. Operator smoke checklist

1. `pip install -e ".[rag]"` and restart server  
2. Dashboard → Memory health = ACTIVE (or clear layer errors)  
3. Chat: “Remember my favorite color is teal.”  
4. New turn / session: “What color do I like?” → recall  
5. Dashboard graph: nodes/edges; hover; **Export**; search “teal”  
6. Toggle `memory.enabled=false` (ConfigStore/TUI) → no auto per-turn/auto-store  

---

## 5. Key code map

| Concern | Path |
|---------|------|
| Config SoT | `kazma-core/kazma_core/memory/config.py` |
| Shared Chroma client | `kazma-core/kazma_core/memory/chroma_client.py` |
| Auto-store | `kazma-core/kazma_core/memory/auto_store.py` |
| Consolidator | `kazma-core/kazma_core/memory/consolidator.py` |
| Per-turn fence | `kazma-core/kazma_core/agent/graph_builder.py` (`_format_retrieved_memories`) |
| FTS + hard tenant | `kazma-core/kazma_core/memory/fts5.py`, `kazma-memory/.../search_backend.py` |
| Adapter RRF | `kazma-core/kazma_core/swarm/memory/adapter.py` |
| L2 graph | `kazma-core/kazma_core/swarm/memory/graph.py` |
| Graph HTTP + export | `kazma-ui/kazma_ui/routes_direct.py` |
| Graph UI | `kazma-ui/kazma_ui/templates/dashboard.html` |
| Polish tests | `kazma-core/tests/test_memory_polish_p2_p7.py` |
| Hygiene P1–P3 tests | `kazma-core/tests/test_memory_p1_p2_p3.py` |

---

## 6. Changelog pointers

- `CHANGELOG.md` → **Make Kazma Memory Great Again**  
- `CHANGELOG.md` → **L2 property graph + LLM consolidator**  
- `CHANGELOG.md` → **Consolidator cost/fence/dedup + graph UI**  
- `CHANGELOG.md` → **Memory polish P2–P7**  
- `CHANGELOG.md` → **Memory hygiene P1–P3 (optional)**  
