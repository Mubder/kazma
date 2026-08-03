---
id: diagnosis-map
title: Diagnosis map (multi-path systems)
sidebar_label: Diagnosis map
description: X-relates-to-Y map of dual paths, shared elements, and first places to look when debugging Kazma
---

# Diagnosis map — multi-path systems

**Purpose:** When a bug “touches many files,” this doc answers: **what is the same element**, **how many paths serve it**, and **where to look first**.

**Audience:** operators, contributors, and coding agents.  
**Companion:** narrative [Architecture](../guide/architecture) · full module catalog [`docs/ARCHITECTURE_AND_SYSTEM_MAP.md`](https://github.com/Mubder/kazma/blob/main/docs/ARCHITECTURE_AND_SYSTEM_MAP.md) · agent rules [`AGENTS.md`](https://github.com/Mubder/kazma/blob/main/AGENTS.md).

> **How to use:** find the **symptom** in §1 → open the **element** in §2–10 → apply the **invariant** and **verify** step.  
> If you change a multi-path element, update **this page** in the same PR.

---

## 0. Mental model (one brain, many mouths)

```
User surfaces          Identity                 Brain                    Execution              Safety
─────────────          ────────                 ─────                    ─────────              ──────
Web SSE                session_id               build_supervisor_graph   UnifiedToolExecutor    HITL A graph interrupt
Web WS    ──►          ≠ thread_id   ──►        (LangGraph)       ──►    LocalToolRegistry ──►  HITL B swarm bus
Telegram/Discord/Slack gw-{platform}-…          checkpointer             IdeService._call_tool  HITL C pipeline
TUI / CLI              active_thread.*          agent_runner             MCP + native skills    YOLO / grants
```

**Golden rules**

1. Platform IDs never enter LangGraph state (gateway restores them on reply).  
2. Model **and** provider switch together (`model_registry`).  
3. Danger tools always pass **one of** the three HITL mechanisms.  
4. Workspace root has **one** resolver: `file_write._get_workspace()`.  
5. Runtime settings SoT is **ConfigStore** (`get_config_store()`), not ad-hoc files.

---

## 1. Symptom → first place to look

| Symptom | Check first | Then | Related element |
|---------|-------------|------|-----------------|
| Web UI stuck on **Stop** / Enter dead | `chat.js` turn machine + `agentStore` `idle`/`stream_end` | Prefer WS vs SSE path; missing end event | §2 Chat transports |
| YOLO “on” but still asks approval | `thread_id` ContextVar vs session_id | WS resume uses `ainvoke` + `enable_yolo(thread)` | §2, §4 HITL A |
| Danger tools run with **no** prompt | Which **graph** is live (`_graph_holder` recompiled?) | `hitl_config` omitted at a build site | §3 Graph build |
| Approve button does nothing | Resume **thread_id** matches interrupt | Checkpointer present; `Command(resume=…)` | §4 HITL A |
| Double approval / hang after Approve | `_graph_hitl_gate_ctx` / `_hitl_approved_ctx` | Bus should skip when graph already approved | §4 double-gate |
| IDE write **denied by HITL** | IDE uses **bus** (HITL B), not graph interrupt | NullBus fail-closed without platform bus | §4 B, §7 Tools |
| Settings toggle has **no effect** | Key names: YAML nested vs ConfigStore flat | Consumer function actually reads the key | §6 Config layers |
| Wrong model / 401 after switch | All **three** `model_registry` entry points | Provider class branch (not generic Bearer) | §5 Providers |
| File tools “outside workspace” | `_get_workspace()` precedence only | Do not reimplement in IdeService | §8 Workspace |
| Memory forgets / search ≠ chat recall | UnifiedMemoryAdapter vs VectorMemory fallback | per-turn RAG flag | §9 Memory |
| Empty `web_search` | SearXNG URL + JSON format | Backend chain notes in tool output | §10 Research |
| Thin `read_url` / bot wall | Recovery cascade Firecrawl→Jina→Playwright | Keys / `KAZMA_JINA_READER=0` | §10 Research |
| Swarm task stuck “paused” | HITL C checkpoint manager | Not A or B | §4 C |
| `/replay` empty on one channel | `snapshot_recorder` at **all** graph build sites | Capture in supervisor node | §3 Time travel |
| Session in sidebar only after F5 | WS must refresh/upsert sessions like SSE | SessionManager shared? | §2 Sessions |

---

## 2. Chat transports (SSE ↔ WebSocket)

| Matter | Related to |
|--------|------------|
| Browser chat | **Both** `sse_chat.py` and `routes/ws_chat.py` + `static/js/chat.js` |
| Preferred transport | Client prefers **WS** when `connectionStatus === 'connected'` |
| Session store | **One** `SessionManager` / `chat_sessions.db` for both |
| LangGraph thread | `ChatSession.thread_id` (may **≠** `session_id` for plain web UUIDs) |
| Platform-linked web sessions | `session_id == thread_id` when `gw-*` |

### Must stay in sync

| Concern | SSE | WebSocket |
|---------|-----|-----------|
| Endpoint | `POST /api/chat/stream` | `/ws/chat/{session_id}` |
| Graph source | `_graph_holder` (post-recompile) | same |
| `recursion_limit` | **100** | **100** (`_GRAPH_RECURSION_LIMIT`) |
| Turn end | SSE `event: done` | `idle` + `stream_end` |
| HITL emit | SSE `hitl_approval` frame | telemetry `hitl_approval` |
| HITL resume | `POST /api/approve/{thread_id}` | WS `approve_tool` |
| YOLO | `/yolo` slash in stream | enable on approve + ContextVar |
| Env context | per-turn `build_env_context()` | same |
| Soul inject | fenced self-improvement block | (see gateway for TG path) |

### Invariants

- Adding a **new server event** only on SSE or only on WS re-breaks the UI.  
- `session_id` is UI/storage; **`thread_id` is LangGraph + YOLO + HITL**.  
- HITL resume for custom LLMs: use **`ainvoke(Command)`**, not hanging `astream_events`.

### Key files

- `kazma-ui/kazma_ui/sse_chat.py`  
- `kazma-ui/kazma_ui/routes/ws_chat.py`  
- `kazma-ui/kazma_ui/static/js/chat.js`  
- `kazma-ui/kazma_ui/static/js/stores/agentStore.js`  
- `kazma-ui/kazma_ui/session_manager.py`  
- `kazma-ui/kazma_ui/app.py` (mounts both routers + `_graph_holder`)

---

## 3. Graph build sites (silent failure if omitted)

`build_supervisor_graph(...)` is called from **multiple** sites. Omitting kwargs fails **only that path**.

| Site | hitl_config | checkpointer | snapshot_recorder | Used by |
|------|-------------|--------------|-------------------|---------|
| `agent_runner.get_streaming_graph` | yes | **no** (bootstrap) | yes | Early holder before recompile |
| `agent_runner._ensure_graph` | yes | yes | yes | CLI / agent `run()` |
| `app.py` startup recompile | yes (or None if disabled) | yes (`checkpoints.db`) | reuse agent’s | Live Web SSE/WS |
| `build_child_graph` / sub-agent | auto-deny danger | no | yes | Child agents |
| Gateway handler | injected prebuilt graph | via graph | slash recorder | TG/Discord/Slack |

### Invariants

- After startup, Web traffic **must** use the recompiled graph in `_graph_holder`.  
- Time travel empty ⇒ recorder missing at a build site **or** supervisor not calling `capture`.  
- Pre-startup / failed recompile ⇒ HITL/state may not persist.

### Related

- `kazma-core/kazma_core/agent/graph_builder.py`  
- `kazma-core/kazma_core/agent_runner.py`  
- `kazma-ui/kazma_ui/app.py`  
- `kazma-core/kazma_core/time_travel.py`

---

## 4. HITL — three mechanisms (do not conflate)

| Mechanism | When | Gate location | Resume |
|-----------|------|---------------|--------|
| **A. Graph interrupt** | Single-agent chat danger tools | `graph_builder` tool worker | HTTP approve / WS approve / gateway slash |
| **B. Swarm bus** | Swarm tools + **IDE** `LocalToolRegistry.execute` | `tool_registry.execute` → `SafetyMiddleware` | Platform buttons / bus callbacks |
| **C. Pipeline checkpoint** | Swarm PIPELINE tasks | `checkpoint_manager` | `approve_checkpoint` |

### Double-gate (A then B)

After graph Approve, ContextVars prevent a second bus prompt. Breaking this = hang or double UI.

### Danger list SoT

| Source | Role |
|--------|------|
| `CANONICAL_DANGER_TOOLS` | Code SoT |
| `kazma.yaml` `safety.hitl.require_approval_for` | File defaults (parity-tested) |
| ConfigStore `safety.require_approval_for` | Settings UI override → **`get_hitl_config()`** |
| `_EXTENDED_DANGER` | **Alias** of CANONICAL (not a longer list) |
| MCP `classify_mcp_tool` | Name heuristics → UnifiedToolExecutor |

### Bus topology

- 0 platforms → `NullBusAdapter` (danger **fail-closed** unless headless).  
- 1 platform → that adapter.  
- 2+ platforms → **`FanOutBusAdapter`** (first approval wins).

### Related files

- `safety/hitl.py`, `safety/yolo.py`, `safety/hitl_grants.py`  
- `agent/tool_registry.py`, `agent/graph_builder.py`  
- `swarm/safety.py`, `swarm/bus.py`, `swarm/checkpoint_manager.py`  
- Gateway `*_bus.py` adapters  

---

## 5. Provider / model resolution

| Matter | Related to |
|--------|------------|
| Active model | **Must** update provider via `find_provider_for_model` / `set_active_model` |
| Dispatch | **Four** special classes: Google, Anthropic, Azure, Bedrock |
| Generic path | `LLMProvider` → Bearer `/chat/completions` only |
| Entry points | `get_client`, `get_model`, `get_client_by_provider` — **all three** need the same branches |

**Symptom of desync:** model name points at OpenAI-compatible API but provider is Anthropic (or reverse).

**File:** `kazma-core/kazma_core/model_registry.py` (+ provider modules).

---

## 6. Config layers (YAML vs ConfigStore vs env)

| Layer | Examples | Loaded by |
|-------|----------|-----------|
| Product YAML | `kazma.yaml` / `kazma.local.yaml` | `config_loader` at boot |
| ConfigStore | `settings.db` flat keys (`safety.hitl_enabled`) | Runtime UI + `get_config_store()` |
| Environment | `KAZMA_*` | Process env (often overrides / features) |

### Key naming traps

| Concept | YAML | ConfigStore / Settings |
|---------|------|-------------------------|
| HITL on/off | `safety.hitl.enabled` | `safety.hitl_enabled` |
| Approval list | `safety.hitl.require_approval_for` | `safety.require_approval_for` |
| Timeout | `safety.hitl.approval_timeout_seconds` | `safety.approval_timeout` |
| SearXNG | (env `KAZMA_SEARXNG_URL`) | `search.searxng_url` |

**Consumer:** `get_hitl_config()` merges YAML **then** ConfigStore (and YOLO can force off).

Never construct `ConfigStore()` in app code — only `get_config_store()`.

---

## 7. Tool execution stack

| Path | Executor | MCP? | HITL |
|------|----------|------|------|
| Chat / graph | `UnifiedToolExecutor` (agent.tools) | yes | A (interrupt) + double-gate |
| IDE mutating ops | `get_tool_registry()` → `LocalToolRegistry` | **no** | B (bus) |
| Swarm worker helpers | often LocalToolRegistry | depends | B |
| Native skills | registered onto LocalToolRegistry | n/a | as tools |

**Invariants**

- IDE **must not** call raw `file_write` / shell functions — always `_call_tool` → registry.  
- Tool “exists in chat but not IDE” is often **MCP-only** on UnifiedToolExecutor.  
- Swarm package has a separate `tools/registry.py` name — do not confuse with agent `tool_registry.py`.

---

## 8. Workspace resolution (single ladder)

**One function:** `kazma_core.workspace.binding.resolve_active_root()`  
(also re-exported as `file_write._get_workspace` / `configure_workspace`).

Precedence (high → low):

1. Per-task `workspace_scope` ContextVar (`SwarmTask.workspace_id`)  
2. Active **WorkspaceStore** row (Switch Repo / clone — user intent)  
3. `configure_workspace()` process pin  
4. `KAZMA_WORKSPACE` env  
5. Default `{data_dir}/workspace` (project sandbox, not monorepo cwd)

**Binding bus:** `WorkspaceStore.set_active_workspace` → `notify_root_changed`  
→ process pin + MCP rebind for `workspace_bound` servers  
(`@modelcontextprotocol/server-filesystem` uses `${KAZMA_ACTIVE_WORKSPACE}`).

| Related module | Must |
|----------------|------|
| `IdeService._resolve_workspace_root` | **Delegate** same rules |
| `workspace_api` | **Delegate** `resolve_active_root` (no second ladder) |
| `env_context.build_env_context` | Same root awareness |
| `worker_dispatch` | Wrap with `workspace_scope` when task has id |
| All `file_*` tools | Use `_get_workspace` / `resolve_active_root` only |
| MCP filesystem | `workspace_bound: true` + rebind on switch |

---

## 9. Memory / RAG

| Layer | Role |
|-------|------|
| Default | `UnifiedMemoryAdapter` RRF (L1 Chroma + L2 SQLite graph + L3 FTS5 + L4 sqlite-vec) |
| Fallback | `VectorMemory` singleton if adapter path fails |
| Per-turn | Supervisor inject when `memory.per_turn_retrieval` (ConfigStore ← yaml) |
| Post-turn | `schedule_post_turn_memory` → auto_store + consolidator |
| Compaction | `compaction.py` / ContextAuthority → same adapter |
| Health | `build_memory_health()` on Dashboard |
| Graph UI | `GET/POST /api/memory/graph*` |

If chat recall is empty: install `.[rag]`, check Dashboard health (embedder / L1 / L3), confirm `memory.enabled`.  
If chat ≠ tool results: both should hit the adapter; look for fail-closed empty store or DEMO mode.  
Backlog: [`docs/plans/MEMORY_REMAINING.md`](https://github.com/Mubder/kazma/blob/main/docs/plans/MEMORY_REMAINING.md).

---

## 10. Research / fetch / search

| Element | Cascade / order | Config |
|---------|-----------------|--------|
| `web_search` | SearXNG → DDG → Bing → Wikipedia | `KAZMA_SEARXNG_URL`, ConfigStore `search.searxng_url`, compose profile `search` :8088 |
| `read_url` / KB ingest | optional pre-backends → httpx → **recovery** Firecrawl → Jina → Playwright | `KAZMA_FETCH_BACKEND`, `KAZMA_FIRECRAWL_*`, `KAZMA_JINA_READER` (`0` = never) |

KB ingest and research use **`kazma_core.web_acquire`** (`fetch_text` / `search` / `crawl` profiles) over the shared recovery ladder in `read_url` — one I/O stack, product sinks stay separate.

Live smoke: `scripts/smoke_research_stack.py`.

---

## 11. Identity & databases

| ID / DB | Purpose | Related |
|---------|---------|---------|
| `session_id` | Web UI chat session key | SessionManager |
| `thread_id` | LangGraph checkpoint + YOLO + HITL | Must match resume |
| `gw-{platform}-{sender}` | Gateway mint; often `session_id == thread_id` on web mirrors | SessionStore |
| `chat_sessions.db` | Web messages / sessions | SSE + WS |
| `sessions.db` | Gateway platform sessions | adapters |
| `checkpoints.db` | LangGraph durable state | HITL pause/resume |
| `snapshots.db` | Time travel | recorder |
| `settings.db` | ConfigStore | Settings UI |
| `swarm_tasks.db` | Swarm TaskStore | engine |

Deleting “a session” may need **both** SessionManager **and** checkpoint thread cleanup.

---

## 12. Injection sites (keep in lockstep)

| Injected content | Sites | Fence / guard |
|------------------|-------|---------------|
| Env / workspace block | agent init, SSE/WS per-turn, swarm worker, IDE swarm send | honesty about host |
| Soul / self-improvement | agent_runner, sse_chat, gateway graph | `format_untrusted_block` + `is_override_delta`; kill-switch `KAZMA_SELF_IMPROVEMENT=0` |
| Knowledge context | gateway (+ other chat paths as added) | untrusted fence |

Adding a **fourth** site without the fence is a security regression.

---

## 13. Packages at a glance

| Package | Owns | Does **not** own |
|---------|------|------------------|
| `kazma-core` | Graph, tools, swarm, ConfigStore, IDE service, safety | HTTP UI, platform APIs |
| `kazma-ui` | FastAPI, SSE/WS chat, settings, IDE page, static JS | Platform adapters |
| `kazma-gateway` | TG/Discord/Slack adapters, slash, session isolation | Web SessionManager |
| `kazma-tui` | Textual UI over core APIs | Separate agent brain |
| `kazma-cli` | `kazma` commands | Long-running gateway |
| `kazma-skills` | Native skill packages | Runtime HITL policy |
| `kazma-memory` | Tokenizer / search helpers | Full RAG policy |

---

## 14. Verify / drift tests

| Concern | How to verify |
|---------|----------------|
| Danger list parity | `tests/test_agent_skills.py` / `scripts/check_docs_sync.py` |
| HITL wired at build sites | `kazma_core_tests/test_hitl_gates_wired.py` |
| Research stack | `python scripts/smoke_research_stack.py` |
| SearXNG / recovery unit | `tests/test_searxng_discovery.py`, `tests/test_hard_page_recovery.py` |
| Settings → HITL list | ConfigStore `safety.require_approval_for` → `get_hitl_config` |
| Compile | `py_compile` on touched modules |

When you **merge** two paths into one, add a test that would have failed under the old dual-path bug.

---

## 15. Changelog of multi-path merges (living)

| Date | Merge / fix | What not to re-split |
|------|-------------|----------------------|
| 2026-07 | SessionManager shared SSE+WS | Do not add per-transport session dicts |
| 2026-07 | FanOut bus multi-platform | Docs must not claim exclusive TG>Discord>Slack only |
| 2026-07 | Workspace SoT + scope | Do not fork IdeService precedence |
| 2026-07 | WS recursion_limit=100 | Keep aligned with SSE/gateway |
| 2026-07 | Settings `require_approval_for` → `get_hitl_config` | Do not re-dead the Settings control plane |
| 2026-07 | Hard-page recovery shared by KB | Keep fetch in `read_url._fetch_full_text` |

---

## 16. Quick “X is related to Y” index

| If you touch… | Also check… |
|---------------|-------------|
| `sse_chat.py` | `ws_chat.py`, `chat.js`, turn end events |
| `ws_chat.py` | same + YOLO ContextVar thread bind |
| `graph_builder.py` HITL | all build sites, double-gate ContextVars |
| `tool_registry.execute` | IDE service, swarm safety, MCP executor |
| `model_registry` one method | the other two methods + provider classes |
| `file_write._get_workspace` | IdeService, env_context, workspace_scope |
| `CANONICAL_DANGER_TOOLS` | kazma.yaml, Settings default, swarm alias, MCP patterns |
| `ConfigStore` safety keys | `get_hitl_config`, SettingsManager flat keys |
| `build_env_context` | agent_runner, SSE, WS, worker dispatch |
| Soul / self_improvement | all inject sites + prompt_fence |
| Swarm bus adapter | FanOut vs Null, IDE fail-closed UX |
| `read_url` recovery | knowledge_ingest, crawl, web_research |

---

*Keep this document shorter than a full module dump — point at files, state invariants, and relationships. Full catalogs stay in `ARCHITECTURE_AND_SYSTEM_MAP.md`.*
