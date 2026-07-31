# Kazma Memory Subsystem — Code Map

> **Purpose**: Single reference for all memory-related code locations, entry points, and data paths.
> **Updated**: 2026-07-31
> **Scope**: Both the V2 cognitive engine (production-live) and the legacy 4-layer RRF stack.

---

## Stack overview

Kazma memory has TWO stacks running concurrently:

- **V2 Cognitive Engine** — bi-temporal belief graph, 4-tier episodes, PPR recall, procedural DAGs. Default read path when `memory.v2.use_new_stack=true`. Receives dual-writes regardless of the flag.
- **Legacy 4-layer RRF** — Chroma + SQLite graph + FTS5 + sqlite-vec, RRF-blended. Default read path until the flag flips.

See `docs/docs/guide/memory-and-rag.md` for the cutover procedure.

---

## Package Layout

```
kazma-core/kazma_core/memory/           # Core memory package (V2 + legacy)
kazma-core/kazma_core/swarm/memory/     # Swarm adapter + backends (legacy RRF, Graph, Vector, FTS5, sqlite-vec)
kazma-core/kazma_core/agent/            # Agent integration (per-turn recall, post-turn hook)
kazma-core/kazma_core/safety/           # Prompt fence (injection defense)
kazma-core/kazma_core/                  # ConfigStore, Paths (singletons)
kazma-core/tests/                       # Memory test suites
kazma-data/                             # Runtime data (gitignored)
```

---

## V2 Cognitive Engine (`kazma-core/kazma_core/memory/`)

| File | Purpose | Key Exports |
|------|---------|-------------|
| `schema_v2.py` | Bi-temporal DDL for `memory_state.db` + `memory_ops.db` | `ensure_primary_schema()`, `ensure_ops_schema()` |
| `belief_mutation.py` | Functional/set/state mutation rules + audit log + memory_class derivation. State predicates record `event_type='transition'`; functional record `'supersede'` | `mutate_belief()`, `derive_memory_class()` |
| `belief_extractor.py` | Post-turn LLM + heuristic extraction (gatekeeper, fence) | `extract_and_apply_beliefs()`, `extract_and_apply_beliefs_sync()`, `is_filler_turn()` |
| `recall.py` | Unified V2 recall — beliefs + episodes + PPR + RRF + fence. Accepts `session_id` (thread_id) for session-bias/episode-scoping | `recall()`, `format_recall_block()`, `RecallHit`, `RecallResult` |
| `vector_engine.py` | sqlite-vec native + guarded NumPy fallback | `VectorEngine` |
| `ppr.py` | Local Ego-Graph Personalized PageRank | `compute_local_ppr()`, `build_ego_graph()` |
| `task_queue.py` | Durable SQLite-backed consolidation queue | `enqueue_task()`, `register_handler()`, `start_worker()` |
| `worker_bootstrap.py` | Handler registration + worker start at boot + two schedulers (6h `macro_sleep`, 24h `native_backup` + `nightly_export`) | `start_memory_worker()`, `register_v2_handlers()`, `register_backup_export_handlers()` |
| `macro_sleep.py` | Decay scoring, tier demotion/promotion, archival | `run_macro_sleep()`, `compute_retention()` |
| `entity_resolution.py` | 3-tier cascade (exact → vector → LLM) + quarantine | `resolve_entity()` |
| `procedural.py` | Parametric DAG skills, Laplace C(d)=(S+1)/(N+2) | `record_procedural_outcome()`, `laplace_confidence()` |
| `dual_write.py` | Best-effort mirror of legacy writes into V2 | `mirror_belief()`, `mirror_episode()`, `get_mirror()` |
| `backfill_v2.py` | One-shot idempotent migration of legacy corpus | `run_backfill()`, `backfill_status()` |
| `backup.py` | Native `sqlite3.backup()` streaming copies + retention (scheduled 24h via `native_backup` task) | `perform_native_backups()` |
| `export.py` | Nightly JSONL + GraphML long-term dumps (scheduled 24h via `nightly_export` task) | `export_nightly_snapshots()` |

### V2 on-disk data paths

| Path | Role |
|------|------|
| `kazma-data/memory_state.db` | Cognitive state: beliefs, episodes, entities, entity_merges, procedural_dags, beliefs_archive |
| `kazma-data/memory_ops.db` | Operational: memory_task_queue (durable), memory_audit_log (immutable) |

### V2 key entry points

| What | File:Function |
|------|---------------|
| V2 recall (read path) | `memory/recall.py:recall()` |
| Belief mutation (write path) | `memory/belief_mutation.py:mutate_belief()` |
| Post-turn extraction | `memory/consolidator.py:schedule_post_turn_memory()` → V2 thread |
| V2 enabled check | `memory/config.py:memory_v2_enabled()` |
| Backfill migration | `memory/backfill_v2.py:run_backfill()` |
| Worker start (app boot) | `memory/worker_bootstrap.py:start_memory_worker()` |
| Macro sleep sweep | `memory/macro_sleep.py:run_macro_sleep()` |

---

---

## Core Memory Package (`kazma-core/kazma_core/memory/`)

| File | Purpose | Key Exports |
|------|---------|-------------|
| `__init__.py` | Package exports | `FTS5Memory`, `VectorMemory`, `read_memory_cfg`, `memory_enabled`, `set_memory_flag` |
| `config.py` | **Config SoT** — merges defaults ← `kazma.yaml` ← ConfigStore | `read_memory_cfg()`, `DEFAULT_MEMORY_CFG`, `memory_enabled()`, `memory_per_turn_enabled()`, `memory_auto_store_enabled()`, `memory_auto_store_mode()`, `memory_retrieval_top_k()`, `set_memory_flag()` |
| `schema.py` | Canonical SQLite DDL for `memories` + `memories_fts` + safe triggers | `ensure_memories_schema_sync()`, `ensure_memories_schema_async()`, `install_memories_fts_triggers_sync/async()` |
| `vector_store.py` | `VectorMemory` — ChromaDB singleton (tools fallback, health count) | `VectorMemory` class, `get_vector_memory()` |
| `fts5.py` | `FTS5Memory` / `FTS5LexicalStore` — SQLite FTS5 lexical store (L3) | `FTS5Memory`, `FTS5LexicalStore`, `lexical_search()`, `get_texts()`, `index()` |
| `auto_store.py` | Heuristic "vacuum" — writes durable facts + turn snapshots post-turn | `auto_store_enabled()`, `auto_store_mode()`, `looks_durable()`, `extract_turn_texts()`, `auto_store_from_messages()`, `schedule_auto_store()` |
| `consolidator.py` | "Librarian" — LLM/heuristic fact + triple extraction, dedup, fence, graph write | `consolidate_from_messages()`, `schedule_consolidation()`, `schedule_post_turn_memory()`, `extract_heuristic()`, `filter_injection()`, `is_near_duplicate()` |
| `async_adapter.py` | `AsyncMemoryAdapter` — wraps sync `VectorMemory` for `CompactionEngine` | `AsyncMemoryAdapter` |
| `health.py` | Health checks for all memory subsystems (embedder, VectorMemory, L1-L4, consolidator) | `build_memory_health()` |
| `backfill.py` | One-time integrity backfill (embeddings, timestamps, graph seeding) | `run_memory_integrity_backfill()` |
| `chroma_client.py` | Shared ChromaDB client factory — **single collection** `agent_memory` | `get_chroma_client()` |
| `paths.py` | (in `kazma_core/paths.py`) `vector_memory_path()`, `memory_db_path()`, `data_dir()` |

---

## Swarm Memory (`kazma-core/kazma_core/swarm/memory/`)

| File | Purpose | Key Exports |
|------|---------|-------------|
| `adapter.py` | **UnifiedMemoryAdapter** — 4-layer RRF co-processor (L1 Chroma, L2 Graph, L3 FTS5, L4 sqlite-vec) | `UnifiedMemoryAdapter`, `MemoryHit`, `get_adapter()`, `set_adapter()`, `query()`, `index()`, `store()`, `search()`, `log_evolution()`, `get_evolution_history()` |
| `graph.py` | `KnowledgeGraph` — SQLite property graph (nodes, edges, FTS5) + `upsert_triple` | `KnowledgeGraph`, `get_knowledge_graph()`, `add_entity()`, `add_relation()`, `upsert_triple()`, `query_related()`, `query_by_type()`, `search()`, `stats()`, `to_json()`, `clear()` |
| `vector.py` | `VectorStore` — ChromaDB wrapper for adapter L1 (same path/collection as tools) | `VectorStore` |
| `sqlite_vec.py` | `SQLiteVectorStore` — adapter L4, per-worker tables + side `_docs` | `SQLiteVectorStore` |
| `embedder.py` | Shared embedder factory (local MiniLM or remote OpenAI-compatible) + timestamp resolver | `get_embedder()`, `encode_text_to_blob()`, `resolve_unix_timestamp()` |
| `fts5.py` | `FTS5LexicalStore` — adapter L3, hard tenant isolation (`tenant_id` exact match) | `FTS5LexicalStore` |
| `__init__.py` | Package exports | — |

---

## Agent Integration (`kazma-core/kazma_core/agent/`)

| File | Purpose | Key Functions |
|------|---------|---------------|
| `graph_builder.py` | Supervisor graph build + per-turn RAG injection + post-turn memory hook | `_format_retrieved_memories()` (line ~329), `respond_node()` calls `schedule_post_turn_memory()` (line ~1319) |
| `agent_runner.py` | Graph compilation, checkpointer, streaming graph holder | `get_streaming_graph()`, `_ensure_graph()`, `_ensure_streaming_graph()` |

---

## Safety / Prompt Fence (`kazma-core/kazma_core/safety/`)

| File | Purpose | Key Exports |
|------|---------|-------------|
| `prompt_fence.py` | Injection detection + untrusted-data fence | `OVERRIDE_PHRASE_RE`, `is_override_delta()`, `format_untrusted_block()` |

**Used by**: `consolidator.filter_injection()`, `graph_builder._format_retrieved_memories()`

---

## Singletons & Infrastructure (`kazma-core/kazma_core/`)

| File | Purpose |
|------|---------|
| `config_store.py` | `ConfigStore` singleton (WAL, `busy_timeout=5000`), `get_config_store()`, `batch_set()`, `transaction()` |
| `paths.py` | `vector_memory_path()`, `memory_db_path()`, `knowledge_graph_path()`, `vector_db_path()`, `data_dir()` |

---

## On-Disk Data Paths (Runtime, `kazma-data/` — gitignored)

| Path | Role | Backend |
|------|------|---------|
| `kazma-data/vector_memory/` | ChromaDB persistent dir, collection `agent_memory` | L1 (Chroma) + `VectorMemory` tools |
| `kazma-data/memory.db` | SQLite: `memories` + `memories_fts` (FTS5) | L3 (FTS5Memory, FTS5LexicalStore) |
| `kazma-data/knowledge_graph.db` | SQLite: `kg_nodes`, `kg_edges`, `kg_nodes_fts` | L2 (KnowledgeGraph) |
| `kazma-data/vector.db` | SQLite-vec: per-worker tables + `_docs` side tables | L4 (SQLiteVectorStore) |
| `kazma-data/snapshots.db` | Time-travel snapshots (separate subsystem) | `time_travel.py` |

---

## Key Entry Points (Grep Targets)

| What you're looking for | File:Function |
|-------------------------|---------------|
| Config SoT (read effective config) | `memory/config.py:read_memory_cfg()` |
| Per-turn RAG injection (format hits) | `agent/graph_builder.py:_format_retrieved_memories()` |
| Post-turn memory pipeline (auto_store → consolidator) | `memory/consolidator.py:schedule_post_turn_memory()` |
| Heuristic vacuum (durable facts + turn snapshots) | `memory/auto_store.py:auto_store_from_messages()` |
| LLM librarian (fact + triple extraction) | `memory/consolidator.py:consolidate_from_messages()` |
| 4-layer RRF query (retrieve) | `swarm/memory/adapter.py:UnifiedMemoryAdapter.query()` |
| 4-layer RRF store (write, fail-closed) | `swarm/memory/adapter.py:UnifiedMemoryAdapter.store()` |
| Property graph triple upsert | `swarm/memory/graph.py:KnowledgeGraph.upsert_triple()` |
| FTS5 lexical search | `swarm/memory/fts5.py:FTS5LexicalStore.lexical_search()` |
| SQLite-vec local worker query | `swarm/memory/sqlite_vec.py:SQLiteVectorStore.query()` |
| Embedder factory | `swarm/memory/embedder.py:get_embedder()` |
| Prompt fence injection check | `safety/prompt_fence.py:is_override_delta()` |
| Shared Chroma client (fixes write/read split) | `memory/chroma_client.py:get_chroma_client()` |
| Adapter singleton (lazy init all 4 layers) | `swarm/memory/adapter.py:get_adapter()` |
| KnowledgeGraph singleton | `swarm/memory/graph.py:get_knowledge_graph()` |

---

## Data Flow Summary

```
User Turn
    │
    ▼
Supervisor Iteration 0
    │
    ├─► Per-turn RAG: retrieve_memories() → adapter.query() → RRF blend → _format_retrieved_memories() → fence → system prompt
    │
    ▼
LLM Reply
    │
    ▼
respond_node() assembles final messages
    │
    ├─► schedule_post_turn_memory(messages) ──► async task
    │       │
    │       ├─► auto_store_from_messages()
    │       │       ├─► looks_durable(user_text) → store durable_fact
    │       │       └─► mode=turns/both → store turn snapshot
    │       │       └─► returns {durable, turn, ids, texts}
    │       │
    │       └─► consolidate_from_messages(auto_store_stats)
    │               ├─► skip LLM if auto_store wrote durable + skip_llm_if_auto_stored
    │               ├─► _extract_with_llm() OR extract_heuristic()
    │               ├─► _sanitize_extracted() → prompt_fence.is_override_delta()
    │               ├─► is_near_duplicate() vs auto_store texts
    │               └─► _apply_to_memory()
    │                       ├─► adapter.store(facts)  [skipped if skip_adapter_if_auto_stored]
    │                       └─► graph.upsert_triple(triples)  [always]
    │
    ▼
Next Turn
```

---

## Test Files

| File | Coverage |
|------|----------|
| `tests/test_memory_polish_p2_p7.py` | Consolidator cost (P2), fence at injection (P4), dual Chroma cleanup (P5), L3 tenant filter (P6), RAG E2E (P7) |
| `tests/test_memory_p1_p2_p3.py` | Hygiene: sqlite_query authorizer (H-P1), FTS legacy retire (H-P2), L4 worker populate (H-P3) |
| `tests/test_memory_v2.py` | *(planned)* Memory V2: belief extraction, contradiction, tier migration |
| `tests/test_memory_migration.py` | *(planned)* Backfill + dual-write parity |

---

## Related But Separate Subsystems

| Subsystem | Location | Relationship |
|-----------|----------|--------------|
| Time-Travel / Snapshots | `kazma_core/time_travel.py` | Uses `SupervisorState` snapshots; separate SQLite `snapshots.db` |
| Self-Improvement Soul | `kazma_core/skills/self_improvement.py` | Stores deltas in ConfigStore (`self_improvement.agent_evolution`); uses `prompt_fence` |
| Knowledge Library (KB) | `kazma_core/knowledge_library/` | Isolated collections `kazma_kb_<id>` + chunks in settings DB; **never mixed** with `agent_memory` |
| Swarm Autoscaler | `kazma_core/swarm/autoscaler.py` | Spawns workers from `swarm_templates.json`; populates L4 on dispatch |

---

## Configuration Keys (ConfigStore `memory.*`)

```yaml
memory:
  enabled: true                    # Master switch (per-turn + auto-store + consolidator)
  per_turn_retrieval: true         # Inject memories every user turn
  auto_store: true                 # Heuristic writes after reply
  auto_store_mode: "both"          # "durable" | "turns" | "both"
  retrieval_top_k: 5               # Top-K for per-turn / compaction
  max_context_tokens: 128000       # Compaction window
  consolidation:
    enabled: true
    use_llm: true                  # LLM extract (heuristic fallback)
    min_user_chars: 24
    every_n_turns: 1
    skip_adapter_if_auto_stored: true   # Dedup bias vs auto_store
    skip_llm_if_auto_stored: true       # Cost control (P2)
    skip_llm_in_demo: true
  embedding:
    provider: "local"              # "local" or remote OpenAI-compatible
    model: "all-MiniLM-L6-v2"
    dim: 384
```

**Env overrides**: `KAZMA_VECTOR_COLLECTION`, `KAZMA_EMBED_*`, `KAZMA_DEMO_MODE`

---

## Installation

```bash
# Full vector stack (Chroma, sentence-transformers, sqlite-vec)
pip install -e ".[rag]"

# Minimal (FTS5 only, no vector)
pip install -e .
```

---

## Quick Health Check

```bash
# 1. Install deps
pip install -e ".[rag]"

# 2. Restart server
# (see AGENTS.md for PowerShell commands)

# 3. Dashboard → Memory Health = ACTIVE
#    (embedder OK, VectorMemory count > 0, L1-L4 green)

# 4. Chat test
# "Remember my favorite color is teal."
# New session: "What color do I like?" → recalls "teal"

# 5. Dashboard → Memory Graph: nodes/edges visible, Export JSON works
```

---

## Migration Notes (for Memory V2)

When implementing the bi-temporal belief architecture:

1. **New tables**: `beliefs`, `episodes`, `entities`, `entity_merges`, `procedural_dags` (see `schema_v2.py`)
2. **Backfill script**: `memory/backfill_v2.py` — reads `memories` + `kg_nodes` + Chroma → writes `episodes` + `beliefs` + `entities`
3. **Dual-write wrapper**: `memory/dual_write.py` — `MemoryAgent` writes both old and new schemas (used during the transition; V2-native paths now bypass it)
4. **Config flag**: `memory.use_new_stack: true` (V2 is the active stack after the cutover; flip to `false` to roll back to V1)
5. **Rollback**: Set `false` → V1 adapter path reactivates instantly (V1 code + stores retained)

---

*Generated from codebase audit 2026-07-31. Keep this file updated as memory subsystem evolves.*