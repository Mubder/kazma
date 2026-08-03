# Audit: V2 Cognitive Memory — Gaps, Bugs, Path to Best-in-Class

**Date:** 2026-08-03  
**Scope:** `kazma-core/kazma_core/memory/*`, graph injection, post-turn pipeline, boot worker, docs  
**Status:** Read-only diagnosis (no code changes in this audit)

---

## Executive verdict

Kazma V2 is a **real cognitive memory stack**, not a toy RAG folder:

- Bi-temporal beliefs (valid-time + system-time)
- Episodes + embeddings
- Durable consolidation queue
- Macro-sleep lifecycle
- Fenced injection
- Split SQLite (state vs ops)
- Scheduled backup/export

It is **not yet “best in universe”** because several **documented load-bearing behaviors are half-wired**, failures **fail soft** (look like amnesia), and **ops/docs still describe a dual-stack world that no longer exists**.

**Highest ROI:** fix access counts, dense tier coverage, session bias, belief-graph PPR, and E2E chat recall tests — before adding new schema.

---

## 1. Architecture (what you actually have)

```text
User turn
  → supervisor iteration 0
      → recall(query, tenant_id, session_id?)   # V2 read path
          beliefs → episodes (LIKE + dense@recall + session-clique PPR)
          → format_untrusted_block → system message
  → LLM …
  → schedule_post_turn_memory
      → OS thread:
          mirror_episode (usually tier=episodic + embedding)
          heuristic extract_and_apply_beliefs_sync
          enqueue micro_consolidation
  → durable worker (asyncio):
      micro_consolidation → LLM deep extract (gated)
      every 6h: macro_sleep (decay / promote / demote / archive)
      every 24h: native backup + JSONL/GraphML export
```

| DB | Role |
|----|------|
| `memory_state.db` | beliefs, episodes, entities, procedural DAGs (hot) |
| `memory_ops.db` | task queue + audit (cold) |

**Correct design choices:** single read path (`recall`), single post-turn entry (`schedule_post_turn_memory`), fence on inject, no V1 dual-write of a living second stack (despite the module name `dual_write`).

---

## 2. What already works well

1. Bi-temporal belief mutations (functional supersede / set / state) + audit  
2. Prompt injection fence (override-like content filtered)  
3. Durable micro-consolidation queue (retry / dead-letter / stuck reclaim)  
4. Heuristic extract off the event loop (avoids httpx loop amnesia)  
5. Split DB for WAL contention  
6. Boot-started worker + 6h / 24h schedulers (Agents.md §15)  
7. Strong **unit** tests on schema/mutations (phase1–5, swarm_bridge, backfill)  
8. Swarm / compaction / self-improvement can write via `swarm_bridge`  

---

## 3. Critical bugs / footguns (user-visible “amnesia”)

| ID | Severity | Bug | Why it hurts |
|----|----------|-----|--------------|
| **M-CRIT-1** | **Critical** | **`access_count` / `last_accessed` never bumped on recall** — only schema + macro_sleep *reads* them | Promotion (`access ≥ 2`) almost never fires; retention scoring is stale |
| **M-CRIT-2** | **Critical** | **Dense episode search only `tier="recall"`** while new turns write **`tier="episodic"`** | Semantic recall ignores most fresh memory until broken promotion path |
| **M-CRIT-3** | **High** | **`session_id` accepted by `recall()` but not used to bias results** | Docs claim thread-local bias; multi-session users get wrong/global mix |
| **M-CRIT-4** | **High** | **PPR is episode–session clique, not belief-graph multi-hop** | Marketing/docs say HippoRAG-style belief ego-graph; product is weaker |
| **M-CRIT-5** | **High** | **Recall/store failures return empty + `debug` logs** | Users see “forgot”; ops sees nothing in health/UI |
| **M-HIGH-6** | **High** | **“FTS5” is `LIKE %term%`** | No real tokenization/rank; poor scale + false misses |
| **M-HIGH-7** | **High** | **False V1 rollback story** | `use_new_stack=false` does **not** restore removed V1 RRF; docs/UI still claim it |
| **M-MED-8** | **Med** | Worker/boot start soft-fails | No micro_consolidation / macro_sleep / backup if boot fails quietly |
| **M-MED-9** | **Med** | O(N) dense belief scan + full-tenant PPR graph load | Latency cliff as corpus grows |
| **M-MED-10** | **Med** | Procedural DAGs recorded, not retrieved into turns | Skills memory is write-only |
| **M-MED-11** | **Med** | Default tenant `shared` / `"default"` | Multi-user cross-bleed risk if SaaS |
| **M-LOW-12** | **Low** | Entity vector merge rarely fed vectors | Merge cascade incomplete |
| **M-LOW-13** | **Low** | `memory_store` → generic `noted` predicate | Structure depends on later LLM re-extract |

### Compound failure mode (the real “bad memory” experience)

```text
User: "Remember my favorite color is teal"
  → episode written as episodic + maybe weak "noted" belief
  → dense search only looks at recall tier → miss
  → access never bumps → never promotes
  → next turn recall often empty or LIKE-only
  → agent looks amnesiac
```

---

## 4. Gaps vs best-in-class cognitive memory

Compare to leading research systems (HippoRAG / GraphRAG / MemGPT-style OS memory / production agent memory):

| Capability | Kazma V2 today | Best-in-class bar |
|------------|----------------|-------------------|
| Structured long-term facts | Bi-temporal beliefs ✓ | Same + confidence + provenance UI |
| Episodic log | Episodes ✓ | Working / episodic / recall tiers **consistent** with retrieval |
| Multi-hop association | Session-clique PPR ⚠️ | Entity/belief graph PPR |
| Session continuity | Param present, unused ✗ | Strong thread + user scopes |
| Write→read latency | Async queue ✓ | Guaranteed eventual + metrics |
| Failure visibility | Silent empty ✗ | Health + per-turn memory debug |
| Scale-out | Single-node SQLite | Optional remote vector/graph |
| Procedural memory | Write-only ⚠️ | Retrieve into planning |
| Evaluation | Unit mutations ✓ | Nightly recall quality eval set |
| Operator UX | Partial dashboard | Belief browser, merge queue, “why recalled” |

---

## 5. Docs / config debt (confuses operators)

| Artifact | Issue |
|----------|--------|
| `MEMORY_REMAINING.md` §2 | Still documents V1 4-layer RRF as “current truth” |
| Guide / dashboard | Dual-write / rollback language after V1 removal |
| `memory.auto_store*` | Config defaults for deleted V1 path |
| Health L1/L2/L3 labels | Mapped onto V2 beliefs/episodes/queue — wrong mental model |
| `dual_write` module name | Is the **primary** write path, not transitional |

---

## 6. Test coverage

**Strong:** schema, mutate_belief, macro_sleep unit, swarm_bridge shapes, backfill idempotency, fence formatting.

**Weak / missing:**

1. Full chat E2E: remember → next turn recall with V2 **default** on  
2. Fresh episodic rows appear in dense search  
3. `access_count` increments on recall  
4. `session_id` bias behavior  
5. micro_consolidation with fake LLM end-to-end  
6. Multi-tenant isolation on beliefs  
7. Worker dead-letter / boot failure surfacing  

---

## 7. Roadmap: “best memory in the universe” (phased)

### Phase A — Make existing design *true* (1–2 weeks) — **do first**

1. Bump `access_count` / `last_accessed` on recall hits  
2. Dense search include `episodic` (+ optional promote-on-write for high importance)  
3. Implement `session_id` bias (boost same-thread episodes)  
4. Surface memory metrics: post-turn ok/fail, queue depth, last error, embedder ready  
5. E2E test: teal → new turn recall  

### Phase B — Retrieval intelligence (2–3 weeks)

6. Real FTS5 on episode text (tenant-safe)  
7. Belief-graph PPR (entity edges), episode clique secondary  
8. Cap PPR construction (session-local / query-local subgraph)  
9. ANN / sqlite-vec for beliefs (kill full scan)  
10. “Why recalled” debug panel (sources: belief_fts / dense / ppr)

### Phase C — Cognitive completeness (ongoing)

11. Procedural DAGs into tool planning context  
12. Entity merge UI + vector-fed resolve by default  
13. Nightly global re-consolidation (MEMORY_REMAINING S3)  
14. Working-memory buffer for current task (true 3-tier lifecycle)  
15. Recall quality eval suite (golden questions, nightly)

### Phase D — Scale (only if product needs multi-replica)

16. Remote vector (pgvector/Qdrant)  
17. Shared graph backend  
18. Hosted embeddings  

### Phase E — Docs hygiene (cheap, do with A)

19. Kill false V1 rollback; rewrite MEMORY_REMAINING §2  
20. Rename dual_write mental model / dashboard copy  
21. Clean auto_store / health package noise  

---

## 8. Suggested first implementation sprint (after chat reliability)

**Memory Sprint 1 — “Remember actually works”**

| Task | Closes |
|------|--------|
| Access bump on recall | M-CRIT-1 |
| Dense search episodic+recall | M-CRIT-2 |
| Session bias | M-CRIT-3 |
| Health counters for post-turn / queue | M-CRIT-5 |
| E2E remember/recall test | test hole #1 |
| Doc fix: no V1 rollback | M-HIGH-7 |

Expected user impact: “Remember X / What is X?” becomes reliable without new models or DBs.

---

## 9. Key files

| Concern | Path |
|---------|------|
| Config | `memory/config.py` |
| Read | `memory/recall.py` |
| Write post-turn | `memory/consolidator.py`, `dual_write.py` |
| Beliefs | `belief_mutation.py`, `belief_extractor.py` |
| Lifecycle | `macro_sleep.py`, `task_queue.py`, `worker_bootstrap.py` |
| Inject | `agent/graph_builder.py` |
| Boot | `kazma_ui/app.py` (`start_memory_worker`) |
| Guide | `docs/docs/guide/memory-and-rag.md` |
| Stale plan | `docs/plans/MEMORY_REMAINING.md` |

---

## 10. Bottom line

You upgraded to a **serious architecture**. The gap to “best” is not another rewrite — it is **closing the loop** between write tiers, access-driven lifecycle, retrieval paths, and honest observability.

**Do Phase A first.** That is the difference between “has a cognitive engine” and “feels like it never forgets.”
