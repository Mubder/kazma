---
id: memory-and-rag
title: Memory & RAG
sidebar_label: Memory & RAG
description: Kazma chat memory — V2 cognitive engine (bi-temporal beliefs, PPR recall, tier lifecycle) (2026-07)
---

> **Live SoT (2026-07-31).** V2 is the active memory stack.
>
> - **V2 Cognitive Engine** (production-live, **default `use_new_stack=true`**).
>   Bi-temporal belief graph, 4-tier episodes, Local Ego-Graph PPR
>   retrieval, procedural skill DAGs, durable consolidation queue. This is
>   the **single** read/write path for chat, swarm, self-improvement, and
>   compaction memory.
> - **V1 (the original 4-layer RRF stack)** was **removed** in the
>   V1→V2 cutover. Chroma + SQLite graph + FTS5 + sqlite-vec, RRF-blended,
>   plus `UnifiedMemoryAdapter` / `VectorMemory` / `get_adapter` /
>   `get_knowledge_graph` and the "4-layer RRF" / "L1/L2/L3/L4" concepts,
>   are gone from the codebase. V2 is the only stack.
>
> The V1→V2 cutover extended V2-native writes to the swarm subsystem
> (worker results, SoulEvolution, compaction summaries) via
> `memory/swarm_bridge.py`, so V2 captures everything V1 did.

---

## V2. Cutover procedure (production-safe)

```bash
# 1. Backfill the historical corpus into V2 (idempotent — safe to re-run)
python -c "from kazma_core.memory.backfill_v2 import run_backfill; print(run_backfill())"

# 2. Check what migrated
python -c "from kazma_core.memory.backfill_v2 import backfill_status; print(backfill_status())"

# 3. Flip the flag (ConfigStore — takes effect on next read_memory_cfg)
python -c "from kazma_core.memory.config import set_memory_flag; set_memory_flag('v2.use_new_stack', True)"

# 4. Restart the server — recall() now serves reads from the V2 belief graph
```

**Rollback** is a one-flag flip back to `false`:
```bash
python -c "from kazma_core.memory.config import set_memory_flag; set_memory_flag('v2.use_new_stack', False)"
```

---

## V2.1 Architecture

```
User Turn
    │
    ▼
Supervisor iteration 0
    │
    ├─► Per-turn recall() (session_id=thread_id → biases toward the
    │       current thread's episodes):
    │       1. Belief lookup (currently-valid beliefs matching the query,
    │          bridged by episode entities — "where do I live" → episode
    │          "I moved to Paris" → belief "user lives_in Paris")
    │       2. Episode hybrid search (FTS5 + dense vector, recall tier)
    │       3. Local Ego-Graph PPR boost (2-hop, N≤200, α=0.15)
    │       4. RRF fusion → dedup gate → token-budget truncation
    │       5. format_untrusted_block(source="memory_v2_recall")
    │
    ▼
LLM reply
    │
    ▼
respond_node → schedule_post_turn_memory
    ├─► Legacy path (loop task): auto_store → consolidator
    └─► V2 path (dedicated OS thread, fully decoupled):
            1. mirror_episode (raw turn snapshot)
            2. extract_and_apply_beliefs_sync (heuristic, sync, thread-safe)
            3. enqueue micro_consolidation (LLM deep-pass on worker loop)
```

### Two SQLite databases (split to prevent WAL contention)

| Database | Tables | Role |
|----------|--------|------|
| `memory_state.db` | `beliefs`, `episodes`, `entities`, `entity_merges`, `procedural_dags`, `beliefs_archive` | Cognitive state (hot reads) |
| `memory_ops.db` | `memory_task_queue`, `memory_audit_log` | Operational (queue + audit, cold) |

### Bi-temporal belief graph

The `beliefs` table tracks **two** time axes:
- `valid_from` / `valid_until` — when the fact was true in the real world
- `ingested_at` / `invalidated_at` — when the system learned/forgot it

Functional predicates (`lives_in`, `name_is`, `works_at`, ...) are single-valued: a new value **supersedes** the old (sets `valid_until=now`, links via `supersedes_id`). Set-valued predicates (`uses_tool`, `knows_language`) append. State predicates (`issue_status`) log transitions — recorded in the audit log with `event_type='transition'` (functional supersedes use `event_type='supersede'`), so audit consumers can distinguish the two. Only `valid_until IS NULL` beliefs surface in recall.

### V2 modules

| Module | Purpose |
|--------|---------|
| `memory/schema_v2.py` | Bi-temporal DDL for both databases |
| `memory/belief_mutation.py` | Functional/set/state mutation rules + audit log |
| `memory/belief_extractor.py` | Post-turn LLM + heuristic extraction (gatekeeper, fence) |
| `memory/recall.py` | Unified `recall()` — beliefs + episodes + PPR |
| `memory/vector_engine.py` | sqlite-vec native + guarded NumPy fallback |
| `memory/ppr.py` | Local Ego-Graph Personalized PageRank |
| `memory/task_queue.py` | Durable SQLite-backed consolidation queue |
| `memory/worker_bootstrap.py` | Handler registration + worker start at boot + schedulers (6h macro_sleep, 24h backup/export) |
| `memory/macro_sleep.py` | Decay scoring, tier demotion/promotion, archival |
| `memory/entity_resolution.py` | 3-tier cascade (exact → vector → LLM) + quarantine |
| `memory/procedural.py` | Parametric DAG skills, Laplace C(d)=(S+1)/(N+2) |
| `memory/dual_write.py` | Best-effort mirror of legacy writes into V2 |
| `memory/backfill_v2.py` | One-shot idempotent migration of legacy corpus |
| `memory/backup.py` | Native `sqlite3.backup()` streaming copies (scheduled 24h) |
| `memory/export.py` | Nightly JSONL + GraphML long-term dumps (scheduled 24h) |

### V2 configuration (`memory.v2.*` via ConfigStore)

```yaml
memory:
  v2:
    use_new_stack: true           # V2 is the active stack (flip to false to roll back to V1)
    trust_weight_user: 1.0        # W_trust source weights
    trust_weight_tool: 0.85
    trust_weight_llm: 0.60
    decay_lambda_identity: 0.0001 # λ_type per memory_class
    decay_lambda_general: 0.01
    decay_lambda_ephemeral: 0.10
    recall_ttl_days: 90
    episodic_ttl_days: 30
    archive_after_days: 180
    ppr_alpha: 0.15               # Local Ego-Graph PPR restart factor
    ppr_max_nodes: 200
    procedural_quarantine_threshold: 0.40
```

---

## 9. Honest limits

| Limit | Notes |
|-------|--------|
| Single-node SQLite | `memory_state.db` / `memory_ops.db` are process-local disks |
| Compaction checkpoint_manager | Often not passed; LangGraph checkpointer still persists turns |
| No nightly full-corpus re-consolidation | Nightly native backup + JSONL/GraphML export now run on a 24h scheduler (`worker_bootstrap.py`), but full-corpus *re-consolidation* of the belief graph is still per-turn only (see MEMORY_REMAINING S3) |
| Scale-out | Needs remote vector/graph — see MEMORY_REMAINING S1–S2 |

---

## 10. Related docs

- [Architecture § memory](architecture#7-the-memory-subsystems-overview)
- [Configuration](configuration) · [FAQ](faq) · [Diagnosis map §9](../ops/diagnosis-map)
- [API routes](../reference/api-routes) · [MEMORY_REMAINING plan](https://github.com/Mubder/kazma/blob/main/docs/plans/MEMORY_REMAINING.md)
