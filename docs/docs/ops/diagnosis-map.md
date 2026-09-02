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
3. Danger tools pass **one of** three HITL **execution** paths; **decision** truth is `hitl_gates.db`. Swarm FanOut is **tri-state**, not first-wins.  
4. Workspace root has **one** resolver: `workspace.binding.resolve_active_root()` (also `file_write._get_workspace`).  
5. Runtime settings SoT is **ConfigStore** (`get_config_store()`), not ad-hoc files.  
6. Turn Delivery: `close_turn` is the only closer; the client **projects**. No second painter.

---

## 1. Symptom → first place to look

| Symptom | Check first | Then | Related element |
|---------|-------------|------|-----------------|
| Web UI stuck on **Stop** / Enter dead | `chat.js` turn machine + `agentStore` `idle`/`stream_end` | Prefer WS vs SSE path; missing end event | §2 Chat transports |
| YOLO “on” but still asks approval | `thread_id` ContextVar vs session_id | WS resume uses `ainvoke` + `enable_yolo(thread)` | §2, §4 HITL A |
| Danger tools run with **no** prompt | Which **graph** is live (`_graph_holder` recompiled?) | `hitl_config` omitted at a build site | §3 Graph build |
| Approve button does nothing | Resume **thread_id** matches interrupt; registry **claim** 409? | Checkpointer present; `Command(resume=…)`; `hitl_gates.db` row state | §4 HITL A, gate registry |
| Ghost / pre-stamped **Approved** card / second question only on dashboard | `hitl_gates.py` — `pending` is the only live-button state | `close_turn` + `_serverGates`; never infer Approved from a missing row | Gate registry (bottom) |
| Double approval / hang after Approve | `_graph_hitl_gate_ctx` / `_hitl_approved_ctx` | Bus should skip when graph already approved; **H-8** no second gate from `execute()` | §4 double-gate |
| IDE write **denied by HITL** | IDE uses **bus** (HITL B), not graph interrupt | NullBus fail-closed without platform bus | §4 B, §7 Tools |
| Settings toggle has **no effect** | Key names: YAML nested vs ConfigStore flat | Consumer function actually reads the key | §6 Config layers |
| Wrong model / 401 after switch | All **three** `model_registry` entry points | Provider class branch (not generic Bearer) | §5 Providers |
| File tools “outside workspace” | `_get_workspace()` precedence only | Do not reimplement in IdeService | §8 Workspace |
| Memory forgets / search ≠ chat recall | `recall()` empty / FTS drift / embedder down | Dashboard V2 health; `fts_health`; not a V1 adapter | §9 Memory |
| Empty `web_search` | SearXNG URL + JSON format | Backend chain notes in tool output | §10 Research |
| Thin `read_url` / bot wall | Recovery cascade Firecrawl→Jina→Playwright | Keys / `KAZMA_JINA_READER=0`; pin-IP when no proxy | §10 Research |
| Stale KB hits after docs shrink | Smart re-index: page hash skip vs purge-on-change; gone-URL prune | `KnowledgeIndex.index` / `purge_source` / site prune | §10 Knowledge |
| KB inject empty / wrong tenant | `list_auto_inject_libraries` + `kb_mode=inject` federated RRF | `KAZMA_KB_AUTO_INJECT`, smart search, archive flag | §10 Knowledge |
| Swarm task stuck “paused” | HITL C checkpoint manager | Not A or B | §4 C |
| Agent **resumes old task** after subject change | `shift_explicit` vs `shift_inferred`; `task_status` superseded? | Only **explicit** pivot disarms recall; inferred re-ranks | §9 turn focus, AGENTS.md §29 |
| Agent **abandons** legit multi-step task mid-flow | `shift_inferred` on an interrogative check-in | Check-ins (EN + AR شنو/وش/…) must never classify as drift | §9 turn focus |
| `/replay` empty on one channel | `snapshot_recorder` at **all** graph build sites | Capture in supervisor node | §3 Time travel |
| Session in sidebar only after F5 | WS must refresh/upsert sessions like SSE | SessionManager shared? | §2 Sessions |
| Telegram: **approval expired after final answer** | Duplicate/late `approve_task` callback after resume | Soft message / debounce in `hitl.py` — work usually already ran | §4 HITL A |
| **Tool loop / recursion limit** (bilingual stop card) | `recursion_limit` vs `agent.max_iterations` misaligned | `/long on` or Settings long-task; `resolve_turn_budgets()` | §11 Long-task |
| **Proceed** redoes same work after budget hit | Continue context not stored/consumed | `long_task.continue.{thread}` + inject on next turn | §11 Long-task |
| Long audit dies with YOLO on | YOLO only skips HITL; still needs capacity | Enable **both** `/long on` and `/yolo` | §4, §11 |
| Bot acks **"Saved. Ready…"** instead of executing commands | Stale mission after a **Partial**: continue-context injected ahead of a NEW command + mission left active | Since 2026-08-19: injection gated by `is_continuation_reply`, Partial pauses the long task. Older builds: `/long off` clears it | §11 Long-task |
| Reminder / HITL card **never delivers** after ~5 min | SessionStore TTL is **300s** — do **not** look up `chat_id` at fire time | Cron uses `delivery_target` captured at schedule. Helper: `kazma_core.sessions.ttl.refuse_session_lookup_for_durable_job` | §2 Sessions, AGENTS.md §16 |
| Prompt with **"barcode"** routes to the coding model | `ModelRouter.classify` used to substring-match `code` | Word-boundary classify; `models.defaults.<kind>` wins over YAML keywords | §5 Providers |
| MCP resource text **obeyed as instructions** | Resource body must be fenced | `mcp_read_resource` → `format_untrusted_block(source=mcp_resource:…)` | §12 Injection |
| MCP server asked Kazma to **sample** (call our LLM) | `sampling/createMessage` must not auto-run | Denied without HITL; `KAZMA_MCP_SAMPLING` default off | §4 HITL |
| Plan drawn, **no reply** (memory save / tools) | `plan_fence.py` split/normalize; supervisor plan-only continue | SSE/WS `done.content` SoT; chat.js strip + always applyFinal | §2 Chat transports |

---

## 2. Chat transports (SSE ↔ WebSocket)

| Matter | Related to |
|--------|------------|
| Browser chat | **SSE graph** (`sse_chat/` package + `static/js/chat.js` projector) + **WS telemetry** (`routes/ws_chat.py`) |
| Preferred transport | **SSE** for turns and HITL. WS is cursor resume / live frames. |
| WS graph escape hatch | `KAZMA_WS_GRAPH=1` restores `send_prompt` / `approve_tool` (debug) |
| Session store | **One** `SessionManager` / `chat_sessions.db` for both |
| LangGraph thread | `ChatSession.thread_id` (may **≠** `session_id` for plain web UUIDs) |
| Platform-linked web sessions | `session_id == thread_id` when `gw-*` |

### Must stay in sync

| Concern | SSE (graph SoT) | WebSocket (telemetry) |
|---------|-----|-----------|
| Endpoint | `POST /api/chat/stream` | `/ws/chat/{session_id}` |
| Graph source | `_graph_holder` (post-recompile) | same holder; **idle unless** `KAZMA_WS_GRAPH=1` |
| `recursion_limit` | long-task budgets | same helper when graph is enabled |
| Turn end | SSE `event: done` | `idle` + `stream_end` (journaled) |
| HITL emit | SSE `hitl_approval` frame | telemetry `hitl_approval` (scan) |
| HITL resume | `POST /api/approve/{thread_id}` | WS `approve_tool` **off** unless `KAZMA_WS_GRAPH=1` |
| YOLO | `/yolo` slash in stream | same, only if WS graph is on |
| Env context | per-turn `build_env_context()` | same when graph is on |
| Soul inject | fenced self-improvement block | (see gateway for TG path) |
| Plan fence / final text | `plan_fence.pick_user_facing_text` on `done.content` + session persist | `_persist_final_assistant_message` same pick (checkpoint vs stream) |

### Invariants

- Adding a **new telemetry event** only on SSE or only on WS re-breaks the UI.  
- Graph turns belong on SSE. Do not add a third graph client.  
- `session_id` is UI/storage; **`thread_id` is LangGraph + YOLO + HITL**.  
- User-facing assistant text is never a glued ```plan closer (` ```Saved.`). `plan_fence.py` is the SoT.  
- HITL resume for custom LLMs: use **`ainvoke(Command)`**, not hanging `astream_events`.

### Key files

- `kazma-ui/kazma_ui/sse_chat/` (package)  
- `kazma-ui/kazma_ui/turn_runtime.py` (`close_turn`)  
- `kazma-ui/kazma_ui/routes/ws_chat.py`  
- `kazma-ui/kazma_ui/static/js/chat.js` (`renderTurn`, `_paintHitlFromDoc`, `_serverGates`)  
- `kazma-ui/kazma_ui/static/js/modules/turn_document.js`  
- `kazma-ui/kazma_ui/static/js/stores/agentStore.js`  
- `kazma-ui/kazma_ui/session_manager.py`  
- `kazma-ui/kazma_ui/app.py` (mounts both routers + `_graph_holder`)

---

## 3. Graph build sites (silent failure if omitted)

`build_supervisor_graph(...)` is called from **multiple** sites. Omitting kwargs fails **only that path**.

| Site | hitl_config | checkpointer | snapshot_recorder | Used by |
|------|-------------|--------------|-------------------|---------|
| `agent_runner.get_streaming_graph` | yes + **`auto_deny`** | **no** (sync, checkpointer-less) | yes | Voice / boot-window only — cannot resume `interrupt()` |
| `agent_runner._ensure_graph` | yes | yes | yes | CLI / agent `run()` |
| `app.py` startup recompile | yes (or None if disabled) | yes (`checkpoints.db`) | reuse agent’s | Live Web SSE/WS |
| `build_child_graph` / sub-agent | auto-deny danger | no | yes | Child agents |
| Gateway handler | injected prebuilt graph | via graph | slash recorder | TG/Discord/Slack |

### Invariants

- After startup, Web traffic **must** use the recompiled graph in `_graph_holder`.  
- Time travel empty ⇒ recorder missing at a build site **or** supervisor not calling `capture`.  
- Pre-startup / failed recompile ⇒ HITL/state may not persist.

### Related

- `kazma-core/kazma_core/agent/graph_builder.py` (wires)  
- `kazma-core/kazma_core/agent/graph_supervisor.py` / `graph_tool_worker.py` / `graph_respond.py`  
- `kazma-core/kazma_core/agent_runner.py`  
- `kazma-ui/kazma_ui/app.py`  
- `kazma-core/kazma_core/time_travel.py`

---

## 4. HITL — three execution paths + one registry (do not conflate)

| Mechanism | When | Gate location | Resume |
|-----------|------|---------------|--------|
| **A. Graph interrupt** | Single-agent chat danger tools | `graph_tool_worker.tool_worker_node` | HTTP approve / gateway slash (WS off unless `KAZMA_WS_GRAPH=1`) |
| **B. Swarm bus** | Swarm tools + **IDE** `LocalToolRegistry.execute` | `tool_registry.execute` → `SafetyMiddleware` | Platform buttons / bus callbacks |
| **C. Pipeline checkpoint** | Swarm PIPELINE tasks | `checkpoint_manager` (+ `_gate_register_pipeline`) | `approve_checkpoint` + `settle_gate` |
| **Registry** | All of the above | `hitl_gates.db` CAS | Decision truth; checkpoint is execution truth |

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
- 2+ platforms → **`FanOutBusAdapter`** (**tri-state**: `True` settles; `False` is a vote until `expected_voters` or deadline — not first-boolean-wins; not web `claim_gate`).

### Related files

- `safety/hitl.py`, `safety/yolo.py`, `safety/hitl_grants.py`  
- `agent/tool_registry.py`, `agent/graph_tool_worker.py`  
- `swarm/safety.py`, `swarm/bus.py`, `swarm/checkpoint_manager.py`  
- `safety/hitl_gates.py`, `kazma_ui/turn_runtime.py`, `chat.js` `_serverGates`  
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
| Timeout | `safety.hitl.approval_timeout_seconds` (default 300) | `safety.approval_timeout` |
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
| `python_exec` runtime | E2B (if keyed) → Docker jail → local blocklist | n/a | A or B before run |
| Codebase search | `code_index` SQLite symbols + live `rg` (Python grep fallback) | n/a | read |

**Invariants**

- IDE **must not** call raw `file_write` / `file_apply_patch` / shell functions — always `_call_tool` → registry.  
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

V2 is the **only** chat memory stack (`recall()` in `memory/recall.py`). The V1
4-layer RRF adapter (`UnifiedMemoryAdapter` / `VectorMemory`) was **removed**.

| Layer | Role |
|-------|------|
| Recall | FTS5 + dense (sqlite-vec, or **pgvector** when a Postgres DSN is set) + belief/episode PPR + session bias |
| Write | `mutate_belief` (single INSERT choke) + optional PG mirror + Neo4j dual-write |
| Per-turn | Supervisor inject when `memory.per_turn_retrieval` (ConfigStore ← yaml) |
| Post-turn | `schedule_post_turn_memory` → extractor → `mutate_belief` + ego-anchor |
| Health | `build_memory_health()` / `build_v2_health()` on Dashboard |
| Graph UI | `GET /api/memory/v2/graph` · `/memory` · `memory_console.js` |
| Entity rename / hub | `POST …/entities/{id}/rename` · `memory/self_hub.py` (User shells → hub `user`) |
| Belief edit | `PATCH /api/memory/v2/beliefs/{id}` |

**Disconnected concept on the canvas (subject with a literal object, no hub edge):**
payload-object beliefs used to mint a leaf with no `user → related_to → subject`.
Write-time ego-anchor + 6h backfill attach them (`memory/ego_anchor.py`). Restart
after the 2026-08-24 audit if you still see orphans from before the backfill.

**Orphaned duplicate node (same label twice):** belief object text equaled an
entity id → dual entity + virtual node. Fixed by server dedupe; unique ids required.

**Hub still says “You” after renaming person User:** rename must sync
`entities.user` (self_hub); list `graph_id` focuses hub.

**Truncation banner shows missing edges:** painter keeps the top-N nodes then
drops links whose endpoints were sliced. The banner reports **connections hidden
by slicing** — filter or raise `limit`, this is not lost data.

**Chat recall misses a fact that exists in SQLite:** FTS `content=` tables do
not expose partial desync via `COUNT(*)` on the virtual table. The 6h sweep
compares `*_docsize` vs base and rebuilds (`fts_health.fts_drift_check`).

**Postgres mirror would resurrect dead facts on `role=primary`:** invalidate /
supersede / archive / graph-clear now tombstone the mirror. Nightly export logs
drift; reconcile with `python scripts/reconcile_memory_mirror.py`.

**“Could not deliver your message after several retries” (Web, V2 cursor):**
not a memory bug — a function-local `get_active_turn` import shadowed the
module binding on `?last_seq=` sockets (`ws_chat.py`, audit M-01). Fixed; restart.

If chat recall is empty: install `.[rag]`, check Dashboard health (embedder / V2),
confirm `memory.enabled`.  
If chat ≠ tool results: both should hit `recall()`; look for fail-closed empty
store or DEMO mode.  
Audit: [`AUDIT_MEMORY_SYSTEM_2026-08-24.md`](https://github.com/Mubder/kazma/blob/main/docs/audits/AUDIT_MEMORY_SYSTEM_2026-08-24.md).

---

## 10. Research / fetch / search

| Element | Cascade / order | Config |
|---------|-----------------|--------|
| `web_search` | SearXNG → DDG → Bing → Wikipedia | `KAZMA_SEARXNG_URL`, ConfigStore `search.searxng_url`, compose profile `search` :8088 |
| `read_url` / KB ingest | SSRF-validate → Firecrawl → Jina (opt-in) → httpx (pin-IP if no proxy) → recovery Firecrawl→Jina→Playwright | `KAZMA_FETCH_BACKEND`, `KAZMA_FIRECRAWL_*`, `KAZMA_JINA_READER` (`1` to opt in); never pin through `proxy=` |

KB ingest and research use **`kazma_core.web_acquire`** (`fetch_text` / `search` / `crawl` profiles) over the shared recovery ladder in `read_url` — one I/O stack, product sinks stay separate.

### Knowledge Library (recall / inject — one hybrid stack)

| Path | Role |
|------|------|
| `KnowledgeIndex.search_all_sync` | **SoT retrieval** — Chroma semantic + FTS5 BM25 → RRF |
| `federated_search(..., kb_mode=inject\|all_active)` | Chat merge + tools; labels `store=knowledge` (no schema merge with V2) |
| `get_knowledge_auto_inject_block` | Same RRF via federated `kb_mode=inject` + prompt fence at caller |
| Smart re-index | Unchanged page hashes → skip; change → purge URL; gone scoped URLs pruned |
| Isolation | Per-lib Chroma `kazma_kb_*`; SQLite SoT; tenant/archive on list/inject |

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
| `kazma-core` memory | V2 beliefs / FTS / `MSATokenizer` | Full RAG policy |

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
| 2026-09 | HITL Gate Registry | Decision = `hitl_gates.db`; execution = checkpoint. No inferred Approved |
| 2026-09 | FanOut tri-state | Do not restore first-boolean-wins; web `claim_gate` stays 200/409 |
| 2026-09 | Turn Delivery V2 | `close_turn` only closer; no second `chat.js` painter |

---

## 11. Long-task mode & graph budgets

| Matter | SoT |
|--------|-----|
| Per-chat **budget** | ConfigStore `long_task.{thread_id}` via `/long on` (soft Research ~40 — may PARTIAL) |
| Per-chat **mission** | `/long mission` or `/mission on` — hard wall default **500** rounds / ~2500 steps |
| Baseline tool rounds | `agent.max_iterations` (Settings → Agent) |
| LangGraph step cap | **Derived** `resolve_turn_budgets()` → `recursion_limit` (mission uses `mission_recursion_limit()`) |
| Mission env knobs | `KAZMA_MISSION_MAX_ROUNDS`, `KAZMA_MISSION_RECURSION` |
| Continue after budget | `long_task.continue.{thread_id}` stored on exhaust; injected next turn **only for continuation-shaped replies** (≤8-word proceed/continue/yes/… — a fresh command never gets the directive; the stored context is cleared either way) |
| Partial behavior | A recursion-Partial **pauses** the long task (`pause_long_task`): baseline budgets, no mission framing, follow-up turns not consumed; record survives for `/long status` until TTL |
| Metrics | Prometheus `kazma_long_task_events_total{kind=…}` |
| HITL vs capacity | `/yolo` = danger bypass; `/long` = budgets only; mission ≠ infinite |

**Invariant:** raising Max tool rounds without deriving `recursion_limit` reintroduces `GraphRecursionError` on Research-depth tasks. Always go through `kazma_core.agent.long_task`. Budget mode is **not** “no limits”; use mission for run-until-done with a safety wall.

**Verify:** `/long` status; `/long mission` shows hard wall ≥100; Settings agent response includes `recursion_limit`; log `long_task` + `budget_recursion` / `mission_*` events.

---

## 16. Quick “X is related to Y” index

| If you touch… | Also check… |
|---------------|-------------|
| `sse_chat/` | `ws_chat.py`, `chat.js` projector, `turn_runtime.close_turn` |
| `long_task.py` / recursion | gateway, SSE, WS, agent_runner `recursion_limit` sites |
| HITL stale approve message | `hitl.py` debounce + soft copy; not “nothing executed” after success |
| `ws_chat.py` | same + YOLO ContextVar thread bind |
| `graph_tool_worker.py` HITL | all build sites, double-gate ContextVars, `hitl_gates.py` |
| `tool_registry.execute` | IDE service, swarm safety, MCP executor |
| `model_registry` one method | the other two methods + provider classes |
| `file_write._get_workspace` | IdeService, env_context, workspace_scope |
| `CANONICAL_DANGER_TOOLS` | kazma.yaml, Settings default, swarm alias, MCP patterns |
| `ConfigStore` safety keys | `get_hitl_config`, SettingsManager flat keys |
| `build_env_context` | agent_runner, SSE, WS, worker dispatch |
| Soul / self_improvement | all inject sites + prompt_fence |
| Swarm bus adapter | FanOut **tri-state** vs Null, IDE fail-closed UX |
| `read_url` recovery | knowledge_ingest, crawl, web_research |
| Document durable path | Web API, tools, gateway `/documents`, TUI — all → `DocumentIngestionService` only |
| Document parse execution | `DocumentService` (workers + chat transient attachments) — never import `documents.parsers` from gateway/UI |
| Document jobs vs metadata | `jobs_pg` multi-replica; `repository_pg` optional; GC is backend-agnostic (`repository.gc_mark`) |
| Document danger-ish redaction | UI `kazmaConfirm` only; API/tools ACL still apply |

### Document Intelligence symptoms

| Symptom | Related paths | Invariant |
|---------|---------------|-----------|
| Upload rejected 429/503/507 | `capacity.py`, Settings Documents | Truthful status + Retry-After |
| Job stuck | worker pool, leases, `recover_expired_leases` | Restart reclaim + heartbeat |
| Cross-tenant access | repository ACL | Tenant + actor on every read |
| Convert/redact unavailable | health renderers/mutators | 422/503, no silent fake success |
| Multi-replica inconsistency | readiness metadata/jobs backends + shared blob volume | Check `ops/readiness` before scaling |
| Parser import from gateway | architecture compliance tests | Forbidden modules list |

Guide: [Document Intelligence](../guide/document-intelligence) · Ops: [Document processing](./document-processing).

---

*Keep this document shorter than a full module dump — point at files, state invariants, and relationships. Full catalogs stay in `ARCHITECTURE_AND_SYSTEM_MAP.md`.*

## Wrong reminder date / memory overwrite

**Symptom**: agent schedules a reminder for the wrong date, or overwrites a user-asserted memory belief with an invented date.

**Root cause**: the model treated a relative phrase ("in 2 days") as an absolute event date instead of anchoring to a memory event; the post-turn extractor wrote the invented date as an inferred belief.

**Fix layers** (all in place):
1. **Commitment gate** (`safety/commitment/authorize.py`): `resolve_remind` anchors relative phrases to memory events and rewrites the tool args to the correct `fire_at` before execution.
2. **Source-trust gate** (`memory/belief_mutation.py:_mutate_functional`): a `user_explicit` functional belief cannot be superseded by a lower-trust (`llm_inferred`) source.
3. **Conservative auto-store** (`memory/belief_extractor._apply_beliefs_to_v2`): low-confidence inferred beliefs are dropped post-turn.

**Kill-switch**: `KAZMA_COMMITMENT_ENABLED=0` disables the gate. Check the commitment audit: `SELECT * FROM commitments WHERE act='remind' ORDER BY created_at DESC LIMIT 10`.

## Intent Engine

**Element:** `kazma_core/agent/intent/` — classify_turn → execute/constrain/loop

**Symptoms:**
- Wrong pipeline triggered (e.g., "build a PDF parser" → document execute)
- HITL card skipped on document generation
- Research topic mangled or routed to wrong path
- Model ignores system-prompt tool guidance

**Diagnosis:**
1. Check supervisor log for `[Supervisor] Intent Engine: focus=X route=Y acts=Z reason=W`
2. Verify kill-switches: `KAZMA_INTENT_ENGINE`, `KAZMA_INTENT_EXECUTE`, `KAZMA_INTENT_TIER2` are not `0`
3. Test classification: `python -c "from kazma_core.agent.intent.classify import classify_turn_sync; d = classify_turn_sync('reproduce this PDF'); print(d.route, [a.kind for a in d.acts], d.reason)"`
4. If route is unexpected → check `heuristics.py` regex patterns
5. If execute but no result → check handler registration in `registry.py`

**Key files:**
- `agent/intent/heuristics.py` — act detection (multi-label)
- `agent/intent/policy.py` — route decision (execute allowlist)
- `agent/intent/entities.py` — file resolution
- `agent/intent/classify.py` — single entry point
- `agent/intent/handlers/` — execute handlers (document, research, composer)

## HITL gate registry (2026-09-01)

**Symptom → first place to look.** Ghost/duplicate approval cards, a card
pre-stamped "Approved — running" with no click, a second danger question
visible only on the dashboard, "approve did nothing", or a turn declared
done while a question was outstanding → `kazma_core/safety/hitl_gates.py`
(the single lifecycle SoT, `kazma-data/hitl_gates.db`).

- `pending` is the ONLY state that renders live buttons; readers use the
  registry (`hitl_thread_status`, `close_turn`, `/api/pending-approvals`,
  chat.js `_serverGates`).
- A pending row + paused checkpoint ⇒ the turn stays OPEN (silence rule).
  Paused + no covering row ⇒ backfill from the snapshot and stay open.
  A pending row + NOT paused ⇒ orphan, settled in `close_turn`.
- Claim races: CAS — one winner, 409 carries the actual state/decision;
  same decision twice is 200.
- Watch `kazma_hitl_gate_reconciled_total`. Kill-switch
  `KAZMA_GATE_REGISTRY=0` is a thin execution fallback (live interrupt ⇒
  pending card), not a second decision author.
- Full spec: `docs/plans/HITL_GATE_REGISTRY_PLAN.md` + AGENTS.md §30.
