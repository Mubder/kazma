# Memory — Done vs Remaining

**Status date:** 2026-07-27  
**Shipped commit (main):** `73a2e82b` (+ follow-ups in this doc wave)  
**Primary guide:** [`docs/docs/guide/memory-and-rag.md`](../docs/guide/memory-and-rag.md)

Use this file when picking up memory work later. Do **not** start a greenfield rewrite unless a multi-replica product requirement appears.

---

## 1. What we did (shipped)

### A. Strengthen existing stack (no rewrite)

| Item | Detail |
|------|--------|
| Fail-closed store | `UnifiedMemoryAdapter.store()` / `index()` only return an id when **L1, L3, or L4** confirms a write. L2 alone is not durable. Tools never claim success with an empty id. |
| Config SoT | `kazma_core.memory.config` — ConfigStore overlays `kazma.yaml`. TUI `/config memory` and `memory.enabled` actually gate per-turn + auto-store. |
| FTS unify | `FTS5Memory` uses canonical `memories` / `memories_fts` (same as L3). Legacy `memory_fts` migrated once. |
| Empty-hit filter | RRF drops blank content so chat inject never shows empty rows. |
| L1 chunking on adapter | 2000/200 chunks aligned with VectorMemory. |
| Zero-vector embedder | Health treats zero embeddings as **error**. |
| Docs honesty | FAQ / architecture / memory-and-rag corrected (auto recall + adapter = chat path). |

### B. L2 = real property graph

| Item | Detail |
|------|--------|
| Backend | SQLite `kazma-data/knowledge_graph.db` (nodes, edges, FTS5, multi-hop BFS) |
| Replaces | NetworkX + JSON file |
| API | `add_entity`, `add_relation`, `upsert_triple`, `search`, `query_related`, `to_json`, `clear` |
| Singleton | `get_knowledge_graph()` shared by adapter + HTTP API |
| Migrate | Legacy `knowledge_graph.json` → one-shot import → `.migrated` |

### C. Consolidator (librarian)

| Item | Detail |
|------|--------|
| Module | `kazma_core.memory.consolidator` |
| Flow | Post-turn: `schedule_post_turn_memory` → auto_store then consolidator |
| Extract | LLM JSON facts + SPO triples; heuristic fallback |
| Writes | Clean facts → adapter (L1/L3/L4); triples → L2 graph |
| Fence | `is_override_delta` rejects injection-like facts |
| Dedup | Near-dup skip vs auto_store `texts` |
| Cost | `every_n_turns`, `skip_llm_in_demo`, `skip_adapter_if_auto_stored` |

### D. Dashboard graph UI

| Item | Detail |
|------|--------|
| UI | Memory & Governance → property graph canvas, labels, list |
| Actions | Search, refresh, clear (confirm) |
| APIs | `GET /api/memory/graph[?q=]`, `/search`, `/stats`, `POST /clear` |

### E. Tests

- `kazma-core/tests/test_memory_strengthen.py`
- `kazma-core/tests/test_graph_and_consolidator.py`
- Existing auto_store / health / per_turn tests kept green

---

## 2. Architecture (current truth)

```text
User turn
  → per-turn RAG (RRF: L1 Chroma + L2 graph + L3 FTS + L4 sqlite-vec)
  → LLM reply
  → schedule_post_turn_memory
       → auto_store (heuristic vacuum)
       → consolidator (librarian: fence + dedup + LLM/heuristic)
            → adapter.store facts
            → graph.upsert_triple

Tools memory_store / memory_search → adapter first → VectorMemory fallback
KB (kazma_kb_*) → isolated from chat memory
```

**Config keys** (`memory.*` — ConfigStore ← yaml):

```yaml
memory:
  enabled: true
  per_turn_retrieval: true
  auto_store: true
  auto_store_mode: both   # durable | turns | both
  retrieval_top_k: 5
  consolidation:
    enabled: true
    use_llm: true
    min_user_chars: 24
    every_n_turns: 1
    skip_adapter_if_auto_stored: true
    skip_llm_in_demo: true
  embedding:
    provider: local       # or openai-compatible / nim
    model: all-MiniLM-L6-v2
    dim: 384
```

**On-disk paths:**

| Path | Role |
|------|------|
| `kazma-data/vector_memory/` | Chroma `agent_memory` |
| `kazma-data/memory.db` | FTS `memories` / `memories_fts` |
| `kazma-data/knowledge_graph.db` | L2 property graph |
| `kazma-data/vector.db` | L4 sqlite-vec (per-worker tables) |

Install for full vector: `pip install -e ".[rag]"`.

---

## 3. What remains (later)

### Nice polish (optional)

| # | Item | Why |
|---|------|-----|
| P1 | TUI/Settings UI for `memory.consolidation.*` | Keys work; no dedicated panel toggles |
| P2 | Stronger consolidator cost (skip LLM if auto_store durable-only turn) | Save tokens further |
| P3 | Graph UI polish (hover, edge labels always, export JSON) | Dashboard canvas is v1 |
| P4 | Fence consolidator text at **injection** time too | Defense-in-depth if facts re-enter system prompt |
| P5 | Dual Chroma client cleanup | VectorMemory + L1 still two clients; works, not elegant |
| P6 | L3 hard tenant filter | Soft today; fine single-tenant |
| P7 | Integration RAG E2E in CI | Unit tests green; full Chroma first-load is slow |

### Product / scale (only if required)

| # | Item | Trigger |
|---|------|---------|
| S1 | Remote vector (pgvector / Qdrant) | Multi-replica shared recall |
| S2 | Shared graph backend (Postgres graph or Neo4j) | Multi-replica structural memory |
| S3 | Nightly corpus re-consolidation | Large dirty stores need global merge |
| S4 | Hosted embedding-only service | No local MiniLM on edge |

### Explicitly **not** planned as rewrites

- Replacing RRF adapter with a new “memory v2” product name  
- Merging Knowledge Library into chat `agent_memory`  
- Dropping SQLite L2 for Neo4j on single-node by default  

---

## 4. Operator smoke checklist

1. `pip install -e ".[rag]"` and restart server  
2. Dashboard → Memory health = ACTIVE (or clear layer errors)  
3. Chat: “Remember my favorite color is teal.”  
4. New turn / session: “What color do I like?” → recall  
5. Dashboard graph: nodes/edges increase; search “teal”  
6. Toggle `memory.enabled=false` (ConfigStore/TUI) → no auto per-turn/auto-store  

---

## 5. Key code map

| Concern | Path |
|---------|------|
| Config SoT | `kazma-core/kazma_core/memory/config.py` |
| Auto-store | `kazma-core/kazma_core/memory/auto_store.py` |
| Consolidator | `kazma-core/kazma_core/memory/consolidator.py` |
| Health | `kazma-core/kazma_core/memory/health.py` |
| FTS (degrade path) | `kazma-core/kazma_core/memory/fts5.py` |
| VectorMemory | `kazma-core/kazma_core/memory/vector_store.py` |
| Adapter RRF | `kazma-core/kazma_core/swarm/memory/adapter.py` |
| L2 graph | `kazma-core/kazma_core/swarm/memory/graph.py` |
| Per-turn + post-turn hook | `kazma-core/kazma_core/agent/graph_builder.py` |
| Tools | `kazma-core/kazma_core/agent/tool_registry.py` |
| Graph HTTP | `kazma-ui/kazma_ui/routes_direct.py` |
| Graph UI | `kazma-ui/kazma_ui/templates/dashboard.html` |

---

## 6. Changelog pointers

- `CHANGELOG.md` → **Make Kazma Memory Great Again**  
- `CHANGELOG.md` → **L2 property graph + LLM consolidator**  
- `CHANGELOG.md` → **Consolidator cost/fence/dedup + graph UI**  
