# Kazma Memory Subsystem — Code Map

> **Purpose**: Single reference for all memory-related code locations, entry points, and data paths.
> **Updated**: 2026-07-31
> **Scope**: The V2 cognitive engine. The V1 4-layer RRF stack was removed in the V1→V2 cutover; V2 is the single stack.

---

## Stack overview

Kazma memory is the **V2 Cognitive Engine** — the only stack:

- **V2 Cognitive Engine** — bi-temporal belief graph, 4-tier episodes, Local Ego-Graph PPR recall, procedural DAGs, durable consolidation queue. Active read/write path (`memory.v2.use_new_stack: true`).
- **V1 (4-layer RRF)** — **removed**. `UnifiedMemoryAdapter` / `VectorMemory` / `get_adapter()` / `get_knowledge_graph()` and the `swarm/memory/{adapter,graph,fts5,sqlite_vec}.py` + `memory/{auto_store,vector_store,chroma_client,async_adapter,schema,backfill,fts5}.py` modules are gone.

See `docs/docs/guide/memory-and-rag.md` for the cutover procedure and architecture.

---

## Package Layout

```
kazma-core/kazma_core/memory/           # V2 cognitive engine (single stack)
kazma-core/kazma_core/swarm/memory/     # Swarm memory glue (pipeline_logger only; legacy backends removed)
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
| `recall.py` | Unified V2 recall — beliefs + episodes + PPR + RRF + fence. Accepts `session_id` (thread_id) for session-bias/episode-scoping | `recall()`, `search()`, `format_recall_block()`, `RecallHit`, `RecallResult` |
| `vector_engine.py` | sqlite-vec native + guarded NumPy fallback | `VectorEngine` |
| `ppr.py` | Local Ego-Graph Personalized PageRank | `compute_local_ppr()`, `build_ego_graph()` |
| `task_queue.py` | Durable SQLite-backed consolidation queue | `enqueue_task()`, `register_handler()`, `start_worker()` |
| `worker_bootstrap.py` | Handler registration + worker start at boot + schedulers (6h `macro_sleep`, 24h `native_backup` + `nightly_export`) | `start_memory_worker()`, `register_v2_handlers()`, `register_backup_export_handlers()` |
| `macro_sleep.py` | Decay scoring, tier demotion/promotion, archival | `run_macro_sleep()`, `compute_retention()` |
| `entity_resolution.py` | 3-tier cascade (exact → vector → LLM) + quarantine | `resolve_entity()` |
| `procedural.py` | Parametric DAG skills, Laplace C(d)=(S+1)/(N+2) | `record_procedural_outcome()`, `laplace_confidence()` |
| `consolidator.py` | Post-turn pipeline: turn-text extraction, V2 mirror + belief extraction (LLM/heuristic, fence, dedup) | `schedule_post_turn_memory()`, `extract_turn_texts()` |
| `dual_write.py` | Best-effort mirror of legacy/external writes into V2 | `DualWriteMirror`, `get_mirror()`, `mirror_belief()`, `mirror_episode()` |
| `swarm_bridge.py` | V2-native writes for the swarm subsystem (worker results, SoulEvolution, compaction summaries) | `store_swarm_result()`, `log_evolution_v2()`, `store_compaction_summary()` |
| `backfill_v2.py` | One-shot idempotent migration of the legacy corpus into V2 | `run_backfill()`, `backfill_status()`, `cleanup_polluted_backfill()` |
| `backup.py` | Native `sqlite3.backup()` streaming copies + retention (scheduled 24h via `native_backup` task) | `perform_native_backups()` |
| `export.py` | Nightly JSONL + GraphML long-term dumps (scheduled 24h via `nightly_export` task) | `export_nightly_snapshots()` |
| `health.py` | Health checks for V2 subsystems (embedder, vector engine, beliefs/episodes, consolidator, packages) | `build_memory_health()` |
| `v2_health.py` | V2-specific DB health probes (primary + ops connections) | — |
| `config.py` | **Config SoT** — merges defaults ← `kazma.yaml` ← ConfigStore (incl. `v2.*`) | `read_memory_cfg()`, `DEFAULT_MEMORY_CFG`, `memory_enabled()`, `memory_per_turn_enabled()`, `memory_v2_enabled()`, `set_memory_flag()` |
| `embedder.py` | Shared embedder factory (local MiniLM or remote OpenAI-compatible) + timestamp resolver | `get_embedder()`, `encode_text_to_blob()`, `resolve_unix_timestamp()` |

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
| Post-turn extraction | `memory/consolidator.py:schedule_post_turn_memory()` |
| V2 enabled check | `memory/config.py:memory_v2_enabled()` |
| Backfill migration | `memory/backfill_v2.py:run_backfill()` |
| Worker start (app boot) | `memory/worker_bootstrap.py:start_memory_worker()` |
| Macro sleep sweep | `memory/macro_sleep.py:run_macro_sleep()` |
| Swarm-side V2 writes | `memory/swarm_bridge.py:store_swarm_result()` |

---

## Swarm Memory (`kazma-core/kazma_core/swarm/memory/`)

The V1 backends (`adapter.py`, `graph.py`, `vector.py`, `sqlite_vec.py`, `fts5.py`, `embedder.py`) were removed in the V1→V2 cutover. What remains:

| File | Purpose | Key Exports |
|------|---------|-------------|
| `pipeline_logger.py` | Swarm pipeline logging helper | `get_pipeline_logger()` |
| `__init__.py` | Package exports | — |

Swarm subsystem V2 writes flow through `memory/swarm_bridge.py` (in the core memory package), not this package.

---

## Agent Integration (`kazma-core/kazma_core/agent/`)

| File | Purpose | Key Functions |
|------|---------|---------------|
| `graph_builder.py` | Supervisor graph build + per-turn V2 recall injection + post-turn memory hook | `_format_retrieved_memories()` (line ~329), `respond_node()` calls `schedule_post_turn_memory()` (line ~1319); per-turn recall at line ~551 |
| `tool_registry.py` | `memory_search` tool delegates to `recall()` (line ~766); `get_vector_memory()`/`set_vector_memory()` are retired no-ops (V1 removed) | `memory_search`, `get_vector_memory()` (returns None) |
| `agent_runner.py` | Graph compilation, checkpointer, streaming graph holder | `get_streaming_graph()`, `_ensure_graph()`, `_ensure_streaming_graph()` |

---

## Safety / Prompt Fence (`kazma-core/kazma_core/safety/`)

| File | Purpose | Key Exports |
|------|---------|-------------|
| `prompt_fence.py` | Injection detection + untrusted-data fence | `OVERRIDE_PHRASE_RE`, `is_override_delta()`, `format_untrusted_block()` |

**Used by**: `consolidator` extraction fence, `graph_builder._format_retrieved_memories()`

---

## Singletons & Infrastructure (`kazma-core/kazma_core/`)

| File | Purpose |
|------|---------|
| `config_store.py` | `ConfigStore` singleton (WAL, `busy_timeout=5000`), `get_config_store()`, `batch_set()`, `transaction()` |
| `paths.py` | `primary_memory_db()`, `memory_ops_db()`, `vector_memory_path()`, `data_dir()` |

---

## On-Disk Data Paths (Runtime, `kazma-data/` — gitignored)

| Path | Role | Backend |
|------|------|---------|
| `kazma-data/memory_state.db` | SQLite: beliefs, episodes, entities, entity_merges, procedural_dags, beliefs_archive | V2 cognitive state (hot reads) |
| `kazma-data/memory_ops.db` | SQLite: memory_task_queue, memory_audit_log | V2 operational (queue + audit, cold) |
| `kazma-data/snapshots.db` | Time-travel snapshots (separate subsystem) | `time_travel.py` |

---

## Key Entry Points (Grep Targets)

| What you're looking for | File:Function |
|-------------------------|---------------|
| Config SoT (read effective config) | `memory/config.py:read_memory_cfg()` |
| V2 recall (beliefs + episodes + PPR) | `memory/recall.py:recall()` |
| Belief write (functional/set/state) | `memory/belief_mutation.py:mutate_belief()` |
| Post-turn memory pipeline | `memory/consolidator.py:schedule_post_turn_memory()` |
| Per-turn RAG injection (format hits) | `agent/graph_builder.py:_format_retrieved_memories()` |
| V2 enabled check | `memory/config.py:memory_v2_enabled()` |
| Backfill migration | `memory/backfill_v2.py:run_backfill()` |
| Swarm-side V2 write | `memory/swarm_bridge.py:store_swarm_result()` |
| Embedder factory | `memory/embedder.py:get_embedder()` |
| Prompt fence injection check | `safety/prompt_fence.py:is_override_delta()` |

---

## Data Flow Summary

```
User Turn
    │
    ▼
Supervisor Iteration 0
    │
    ├─► Per-turn recall: recall() → beliefs + episodes (FTS5+dense+PPR, RRF-fused)
    │       → format_recall_block() → fence → system prompt
    │
    ▼
LLM Reply
    │
    ▼
respond_node() assembles final messages
    │
    └─► schedule_post_turn_memory(messages)
            ├─► extract_turn_texts()
            ├─► mirror_episode (raw turn snapshot)
            └─► extract_and_apply_beliefs_sync (heuristic, sync, thread-safe)
                    └─► enqueue micro_consolidation (LLM deep-pass on worker loop)
                          → mutate_belief() → memory_state.db + memory_audit_log
```

Swarm-side writes (worker results, SoulEvolution, compaction summaries) reach V2 via `memory/swarm_bridge.py`.

---

## Test Files

| File | Coverage |
|------|----------|
| `tests/test_memory_v2.py` | Memory V2: belief extraction, contradiction, tier migration |
| `tests/test_memory_migration.py` | Backfill + dual-write parity |

---

## Related But Separate Subsystems

| Subsystem | Location | Relationship |
|-----------|----------|--------------|
| Time-Travel / Snapshots | `kazma_core/time_travel.py` | Uses `SupervisorState` snapshots; separate SQLite `snapshots.db` |
| Self-Improvement Soul | `kazma_core/skills/self_improvement.py` | Stores deltas in ConfigStore (`self_improvement.agent_evolution`); evolution logged to V2 via `swarm_bridge.log_evolution_v2()` |
| Knowledge Library (KB) | `kazma_core/knowledge_library/` | Isolated collections `kazma_kb_<id>` + chunks in settings DB; **never mixed** with V2 chat memory |
| Swarm Autoscaler | `kazma_core/swarm/autoscaler.py` | Spawns workers from `swarm_templates.json` |

---

## Configuration Keys (ConfigStore `memory.*`)

```yaml
memory:
  enabled: true                    # Master switch (per-turn recall + auto-store + consolidator)
  per_turn_retrieval: true         # Inject memories every user turn
  retrieval_top_k: 5               # Top-K for per-turn / compaction
  max_context_tokens: 128000       # Compaction window
  v2:
    use_new_stack: true            # V2 is the active stack (single stack post-cutover)
    trust_weight_user: 1.0
    trust_weight_tool: 0.85
    trust_weight_llm: 0.60
    decay_lambda_identity: 0.0001
    decay_lambda_general: 0.01
    decay_lambda_ephemeral: 0.10
    recall_ttl_days: 90
    episodic_ttl_days: 30
    archive_after_days: 180
    ppr_alpha: 0.15                # Local Ego-Graph PPR restart factor
    ppr_max_nodes: 200
    procedural_quarantine_threshold: 0.40
  embedding:
    provider: "local"              # "local" or remote OpenAI-compatible
    model: "all-MiniLM-L6-v2"
    dim: 384
```

**Env overrides**: `KAZMA_VECTOR_COLLECTION`, `KAZMA_EMBED_*`, `KAZMA_DEMO_MODE`

---

## Installation

```bash
# Full vector stack (sqlite-vec + sentence-transformers; chromadb for embedder types / semantic router)
pip install -e ".[rag]"

# Minimal (FTS5-only episode search, no dense vectors)
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
#    (embedder OK, vector engine up, belief/episode counts > 0)

# 4. Chat test
# "Remember my favorite color is teal."
# New session: "What color do I like?" → recalls "teal"
```

---

*Generated from codebase audit 2026-07-31. V1 (4-layer RRF) removed in the V1→V2 cutover; V2 is the single stack. Keep this file updated as memory subsystem evolves.*
