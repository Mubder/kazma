---
id: architecture
title: Architecture
sidebar_label: Architecture
description: Kazma Architecture — code-audited reference (unified docs, v0.9+)
---
> A deep, source-referenced breakdown of the Kazma engine: the supervisor brain, the data path from user intent to tool execution, and the subsystems that make it durable, safe, and multilingual.

---

## 1. Philosophy: one brain, many mouths

Kazma is organized around a strict separation between **reasoning** (the LangGraph supervisor graph) and **transport** (the platform adapters). A single graph instance — built once by `build_supervisor_graph()` — serves every channel. The graph never sees platform-specific identifiers; adapters own those and re-attach them only when emitting a reply.

The CLI (`kazma ask`) and ACP stdio (`kazma acp`) are additional mouths: they build the **same** supervisor in-process (no uvicorn). Tokens stream via `register_delta_queue`. HITL uses a MemorySaver checkpointer plus graph `interrupt()` — TTY `y/N`, or ACP `session/request_permission`.

This yields three properties the rest of the system relies on:

1. **Provider freedom** — the brain talks to any OpenAI-compatible endpoint over `httpx`. No vendor SDK is imported.
2. **Channel parity** — a HITL pause, a tool call, and a streaming token are identical whether they originate from Telegram, the Web UI, or `kazma ask`.
3. **Durable state** — because platform IDs live outside the graph, the graph state is purely conversational and can be checkpointed, replayed, and resumed across restarts.

---

## 2. Package topology

Kazma is a monorepo of seven installable packages (declared in `pyproject.toml` `[tool.hatch.build.targets.wheel]`):

| Package | Path | Responsibility |
|---|---|---|
| `kazma-core` | `kazma-core/kazma_core/` | Agent runner, LLM provider, model registry, swarm engine, ConfigStore, safety, memory, **document intelligence** (`documents/`), skills, MCP, hub, compaction, Majlis (delegation = library/archive only — SwarmEngine is the live multi-worker path) |
| `kazma-gateway` | `kazma-gateway/kazma_gateway/` | Telegram/Discord/Slack adapters, agent handler (graph bridge), slash commands, session store |
| `kazma-ui` | `kazma-ui/kazma_ui/` | FastAPI app factory, SSE chat, swarm panel, settings, dashboard, i18n, static assets |
| `kazma-tui` | `kazma-tui/kazma_tui/` | Textual TUI dashboard (read-mostly consumer of core singletons) |
| `kazma-memory` | `kazma-memory/kazma_memory/` | Arabic tokenizer + SQLite/FTS5 search backend |
| `kazma-skills` | `kazma-skills/kazma_skills/` | Skill manifests (data) |
| `kazma-cli` | `kazma-cli/kazma_cli/` | The `kazma` command surface |

Console scripts (`pyproject.toml:73-76`):

```
kazma     = "kazma_cli.main:main"
kazma-tui = "kazma_tui.app:main"
kazma-web = "kazma_ui.app:main"
```

---

## 3. The supervisor brain (LangGraph)

The core graph is a **ReAct loop** built in `kazma-core/kazma_core/agent/graph_builder.py`.

### 3.1 Node topology

```mermaid
flowchart LR
    START([user message]) --> SUP[Supervisor Node]
    SUP -- "LLM calls tools" --> TW[Tool Worker Node]
    TW -- "tool results" --> AUTH{ContextAuthority<br/>check & enforce}
    AUTH -- "compact if ≥80%" --> SUP
    AUTH -- "under threshold" --> SUP
    SUP -- "no tool calls" --> RESP[Respond Node]
    RESP --> END([reply / SSE stream])
    TW -- "danger tool + HITL on" --> INT[LangGraph interrupt]
    INT -- "approval" --> TW
    INT -- "denial / timeout" --> RESP
```

- **Supervisor node** (`graph_builder.py:supervisor_node`) — calls the active LLM with the registered tools and conversation history. Routes based on whether the response contains tool calls.
- **Tool worker node** (`graph_builder.py:336 tool_worker_node`) — executes pending tool calls. This is where the HITL gate lives: if `hitl_config` is supplied and a tool is on the danger list, the node calls LangGraph `interrupt()` (line 493) and suspends until resumed.
- **ContextAuthority** (`authority.py:37 check_and_enforce`) — invoked inside the supervisor node (`graph_builder.py:167`) **before** the LLM call. If `should_compact()` returns true (token count ≥ 80% of the window), it summarises and rebuilds the message list.
- **Respond node** — finalises the assistant reply for streaming.

### 3.2 The ReAct loop in code

The graph is constructed by `build_supervisor_graph()` (`graph_builder.py:661`). The inner `_tool_worker` closure receives `hitl_config` (line 739) — this threading is what activates the gate. Two real build sites pass it (the third does not — see [Security & Safety](security-and-safety#the-graph-gate)).

```python
# agent_runner.py — the streaming graph used by the Web UI's SSE endpoint
def get_streaming_graph(self):
    hitl_config = {
        "enabled": self._config.get("safety.hitl.enabled", True),
        "require_approval_for": self._config.get(
            "safety.hitl.require_approval_for",
            DEFAULT_DANGER_TOOLS,
        ),
        "approval_timeout_seconds": self._config.get(
            "safety.hitl.approval_timeout_seconds", 60
        ),
    }
    graph = build_supervisor_graph(
        model=self.model,
        tools=self.tools,
        hitl_config=hitl_config,        # <-- gate active
        checkpointer=self._checkpointer,
    )
    return graph
```

### 3.3 Durable execution

- **Checkpointer:** `AsyncSqliteSaver` (from `langgraph-checkpoint-sqlite`) on `kazma-data/checkpoints.db` (configured in `kazma-ui/kazma_ui/app.py:724-726`).
- **Thread identity:** each conversation has a `thread_id` (derived from sender id, e.g. `gw-telegram-12345`, or a fresh UUID4 — see `agent_handler/store.py:34 _resolve_thread`).
- **Crash recovery:** HITL pauses persist in the checkpointer. On restart, `restore_paused_tasks()` (`swarm/checkpoint_manager.py:182`) reloads paused swarm tasks and re-arms their auto-reject timeouts. Graph-path pauses survive because they live in the checkpointer.
- **⚠️ Incomplete shutdown (audit C3):** `_on_shutdown()` must drain cron scheduler, swarm `_task_handles`/`stop_all()`, TaskStore, VectorMemory, FTS, and close HTTP pool / gateway. In-flight swarm work and cron LangGraph jobs continue during uvicorn teardown; SQLite/Chroma can corrupt on hard kill. Full remediation: see `kazma-ui/kazma_ui/app.py:_on_shutdown` — must stop cron first, then drain swarm, then close remaining components.
- **Time travel:** `/replay list | &lt;iter> | compare &lt;a> &lt;b> | clear` (slash command) and `time_travel` config (`kazma.yaml:111-114`, `max_snapshots: 50`).

---

## 4. End-to-end data flow

```mermaid
sequenceDiagram
    participant U as User (Telegram/Web/...)
    participant A as Platform Adapter
    participant S as SessionStore
    participant H as AgentHandler (graph bridge)
    participant G as Supervisor Graph
    participant L as LLM Provider (httpx)
    participant T as ToolRegistry
    participant M as Bus (HITL)

    U->>A: text message
    A->>S: put(thread_id, {chat_id, user_id, ...})
    A->>H: IncomingMessage (no platform IDs in body)
    H->>G: graph.ainvoke({messages:[...]}, config={thread_id})
    loop ReAct
        G->>L: POST /chat/completions (tools=...)
        L-->>G: tool_calls or final text
        alt danger tool
            G->>M: interrupt(approval_input)
            Note over G,M: graph SUSPENDED
            M-->>U: approval request (inline button / SSE event)
            U->>M: approve
            M->>G: Command(resume={"approved":true})
        end
        G->>T: execute(tool, args)
        T-->>G: ToolResult
    end
    G-->>H: final state
    H->>S: get(thread_id)  // rehydrate platform IDs
    H->>A: OutboundMessage(target_id, text)
    A-->>U: reply
```

Key invariants enforced along this path:

| Invariant | Enforced by | Location |
|---|---|---|
| Platform IDs never enter graph state | `_PLATFORM_KEYS` frozen set + `_build_initial_state` | `agent_handler/store.py:16,95` |
| Reply routes back to the correct chat | `_build_target_id(platform, ctx)` | `agent_handler/store.py:146` |
| Wrong-provider model never hits wrong endpoint | `get_client()` auto-correction | `model_registry.py:275-303` |
| Danger tools pause, never execute silently | `interrupt()` + `_hitl_approved` flag | `graph_builder.py:483-506` |
| Swapped provider invalidates stale clients | `set_active_model` clears cache | `model_registry.py:248` |

---

## 5. The LLM provider layer

`kazma-core/kazma_core/llm_provider.py` is a thin, **SDK-free** `httpx` client. It speaks the OpenAI Chat Completions wire format to anything compatible. Most providers (OpenAI, DeepSeek, Groq, Mistral, Together, Cohere, Fireworks, Perplexity, AI21, xAI, OpenRouter, NVIDIA NIM, Ollama, LM Studio) work through this generic `LLMProvider` with `Authorization: Bearer`.

Four providers have **dedicated native classes** (their auth or request schema differs from the OpenAI wire format) and are dispatched in `model_registry.get_client()` / `get_model()` / `get_client_by_provider()`:

| Provider | Class | Why native |
|---|---|---|
| Google Gemini | `GeminiProvider` (`google_llm.py`) | Vertex AI, ADC, computed base URL |
| Anthropic | `AnthropicProvider` (`anthropic_llm.py`) | `/messages` schema, `x-api-key` + `anthropic-version` |
| Azure OpenAI | `AzureProvider` (`azure_llm.py`) | `api-key` header + `api-version` query param, deployment routing |
| AWS Bedrock | `BedrockProvider` (`bedrock_llm.py`) | SigV4 signing + Converse API |

See [LLM Providers](../reference/llm-providers) for the full list and setup.

### 5.1 Provider resolution

`ModelRegistry.get_client(model=None)` (`model_registry.py:252`) returns a cached `LLMProvider` for the active profile. The critical safety net:

```python
# model_registry.py:275-303 (paraphrased)
if effective_model:
    owner = self.find_provider_for_model(effective_model)
    if owner and owner["name"].lower() != provider_name.lower():
        # e.g. a DeepSeek model requested while OpenAI is active
        provider_name = owner["name"]      # auto-correct
        if model is None:
            self._active_provider = owner_name
            self._config_store.set("registry.active_provider", owner_name, ...)
```

This is why "never change model without provider" is a hard rule — see [Provider/Model Resolution](#) warnings in [Configuration](configuration).

### 5.2 The NVIDIA NIM tool-fallback workaround

Some providers (notably NVIDIA NIM) reject tool definitions with `404 "Function not found"`. The client detects this and retries once **without** tools so the caller still gets a text answer (`llm_provider.py:285-300`):

```python
nim_function_not_found = status_code == 404 and "function" in detail_lower
tool_schema_error = (
    status_code in (400, 422)
    and any(tok in detail_lower for tok in ("tool", "function"))
)
if tools and (nim_function_not_found or tool_schema_error):
    logger.warning("Provider rejected tool definitions; retrying without tools.")
    payload.pop("tools", None)
    payload.pop("tool_choice", None)
    resp = await client.post("/chat/completions", json=payload)
```

> **Do not remove this branch.** Removing it breaks tool-using agents on NVIDIA NIM and other strict providers.

### 5.3 Streaming

Token streaming is `LLMProvider.chat_stream()` consumed by `invoke_llm_chat()` (`llm_stream.py`), which injects synthetic `on_chat_model_stream` events for SSE/WS. Kill-switch: `KAZMA_LLM_STREAM=0` (blocking `chat()`). See [API & Extension Points](api-and-extension-points#sse-event-contract).

### 5.4 Cost & retry

| Concern | Mechanism | Location |
|---|---|---|
| Per-call cost | `(prompt_tokens * in_cost/1M) + (completion_tokens * out_cost/1M)` | `llm_provider.py:411-422` |
| Cost ceiling | `CostCircuitBreaker` (default $0.50, 5-min silence) — env `KAZMA_MAX_COST`, `KAZMA_SILENCE_WINDOW` | `cost_breaker.py` |
| Retries | `tenacity`-based decorators, network/timeout only, **no 4xx** | `retry.py:39-109` |
| Rate-limit (429) handling | Retry-After + 3-attempt exponential backoff; exhausted 429 is `transient=True` + `kind=rate_limit_exhausted` (supervisor skips same-provider re-retry; failover still fires) | `llm_provider.py`, `anthropic_llm.py` |

> The cost breaker is a standalone dataclass; the agent layer must drive it via `record_cost` / `should_halt`. It is not auto-wired into `chat()`.

### 5.5 Strict schemas & structured outputs

Tool parameter objects generated from type hints (`agent/tool_schema.py`) are **closed JSON Schema**: `additionalProperties: false` on the root and nested objects that have `properties`. `required` is parameters without Python defaults (provider-safe). Free-form `dict[K, V]` parameters stay open (`additionalProperties` is the value schema) so `env={...}` still works.

OpenAI Structured Outputs for **tools** is opt-in: `KAZMA_STRICT_TOOLS=1` stamps `function.strict: true` and promotes every property into `required`, wrapping former optionals as `anyOf: [T, {type: null}]`. Tools that cannot satisfy that contract (open dicts) stay unstrict so Anthropic / Gemini / local servers do not 400.

Structured JSON **replies** (not tool calls) use `LLMProvider.chat(..., response_format={"type": "json_schema", ...})` / `json_object`. Helper: `json_schema_response_format()`. The supervisor loop does **not** attach this on every turn. Providers that reject `response_format` are retried once without it. Native Anthropic/Bedrock accept the kwarg for signature parity and ignore it.

### 5.6 Tool hooks (PreToolUse / PostToolUse)

Programmable callbacks around `LocalToolRegistry.execute` and MCP unified execute (`agent/tool_hooks.py`). Claude Code–style:

| Event | Can | Cannot |
|-------|-----|--------|
| **PreToolUse** | Deny the call, or rewrite arguments | Skip HITL, skip commitment, auto-approve danger tools |
| **PostToolUse** | Append a short observation note | Undo a tool that already ran |

In-process: `register_pre_tool_hook` / `register_post_tool_hook` (matcher glob or `a|b`). Operator commands: YAML/ConfigStore `agent.hooks.pre_tool` / `post_tool` — JSON on stdin, JSON on stdout (or exit 2 = deny). Spawned with `asyncio.to_thread(subprocess.run)` (Windows SelectorEventLoop). Empty lists = no-op. Kill-switch: `KAZMA_TOOL_HOOKS=0`. A crashing hook **fail-opens** (the tool still runs); security stays HITL + commitment.

### 5.7 Plan mode

First-class inspect-then-propose (`agent/plan_mode.py`). While `/plan` is on for a thread, the supervisor unions `read_only` + `no_writes` into `hard_constraints`. `filter_tools_for_constraints` removes write/exec tools from the LLM schema; the tool worker allowlist blocks them if the model still names one. YOLO cannot expand that allowlist.

`/plan go` or a short **Proceed** / **approve** reply exits plan mode, drops those tags, and injects an execute system note so the same graph run implements the plan. HITL still gates danger tools. Kill-switch: `KAZMA_PLAN_MODE=0`.

---

## 6. Swarm orchestration (overview)

When the supervisor needs more than one agent, control passes to `SwarmEngine` (`kazma-core/kazma_core/swarm/engine.py:103`). Six dispatch patterns are supported as a `TaskType` enum (`swarm/task.py:65`): `DISPATCH`, `BROADCAST`, `PIPELINE`, `FAN_OUT`, `CONSULT`, `CONDITIONAL`.

```mermaid
flowchart TB
    subgraph SwarmEngine
        DI[dispatch_inner]
        DI -->|single| DSP[dispatch]
        DI -->|all| BCAST[broadcast]
        DI -->|ordered| PIPE[pipeline + blackboard]
        DI -->|parallel| FAN[fan-out + aggregate]
        DI -->|opinions| CONS[consult + synthesize]
        DI -->|router| COND[conditional routes]
    end
    DSP & BCAST & PIPE & FAN & CONS & COND --> W[Worker / InProcessWorker]
    W --> REL[ReliabilityRegistry]
    REL --> CB[CircuitBreaker]
    REL --> RT[RetryPolicy]
    REL --> TO[TimeoutGuard]
    REL --> OV[OutputValidator]
    REL --> BC[BoundedConcurrency]
    PIPE -.->|hitl_checkpoints| CK[CheckpointManager]
```

Handoffs between workers are guarded against infinite recursion: `MAX_HANDOFF_DEPTH = 5` and `MAX_VISITS = 2` (per-worker visit count, not a boolean set) live in `swarm/handoff_guards.py:16-17`. See [Swarm Orchestration](swarm-orchestration) for the full pattern catalog.

The engine also exposes `get_autoscaler()` (lazy) — when a task has no matching registered worker, it auto-spawns one from `swarm_templates.json` and selects the best available model for the task kind. See [Swarm Orchestration §14](swarm-orchestration#14-dynamic-autoscaler--worker-templates).

---

## 7. The memory subsystems (overview)

Kazma's chat memory is the **V2 Cognitive Engine** (bi-temporal belief graph, PPR recall). The V1 4-layer RRF stack was removed in the V1→V2 cutover; V2 is the single stack. Supporting pieces:

| Subsystem | Backing | Used by | Status |
|---|---|---|---|
| **V2 Cognitive Engine** | Bi-temporal belief graph + 4-tier episodes + sqlite-vec (`memory_state.db`); **pgvector** when Postgres is on | Chat (per-turn recall, tools, auto-store, compaction) + swarm + self-improvement | ✅ **Chat default** — single read/write path (`recall()` from `memory/recall.py`) |
| **Local Ego-Graph PPR** | Personalized PageRank over the belief/episode graph (2-hop, N≤200) | V2 recall boost | ✅ `memory/ppr.py` |
| **Consolidator** | LLM/heuristic belief + episode extraction, prompt-fenced | Post-turn write pipeline | ✅ `memory/consolidator.py` |
| **Durable consolidation queue** | SQLite-backed worker queue (`memory_ops.db`) | Async belief extraction, macro-sleep, backup/export | ✅ `memory/task_queue.py` |
| **Knowledge Library** | Per-lib `kazma_kb_*` Chroma + SQLite chunks | Docs RAG (`knowledge_*` tools) | ✅ Isolated from chat memory |

Write path: `mutate_belief` is the single INSERT choke. Payload-object beliefs also get a hub `related_to` edge (`memory/ego_anchor.py`) so the canvas does not show disconnected concepts. Optional Postgres state mirror receives **tombstones** on invalidate/supersede/clear; optional Neo4j dual-write deletes those edges too.

Schedulers (started from `start_memory_worker()`): 6h `macro_sleep` (decay + ego-anchor backfill + FTS `*_docsize` drift rebuild), 24h backup/export + mirror-drift warning, 24h reconsolidation, 15m commitment GC.

Config: `memory.*` flags use **ConfigStore ← kazma.yaml** (`kazma_core.memory.config`); V2 is on at `memory.v2.use_new_stack: true`. Full details in [Memory & RAG](memory-and-rag). Audit: [`AUDIT_MEMORY_SYSTEM_2026-08-24.md`](https://github.com/Mubder/kazma/blob/main/docs/audits/AUDIT_MEMORY_SYSTEM_2026-08-24.md).

---

## 8. Arabic & cultural layer

Kazma is Arabic-native by default (`agent.language: ar`, `agent.rtl: true`). Three components implement this:

1. **Arabic tokenizer** (`kazma-memory/kazma_memory/arabic_tokenizer.py`) — diacritics removal, Alef/Yeh/Teh-Marbuta normalization, Tatweel stripping, Kuwaiti-dialect stop words, basic stemmer. Feeds the FTS5 `content_arabic` column.
2. **i18n + RTL UI** (`kazma-ui/kazma_ui/i18n.py`, `static/css/kazma.css`) — inline `TRANSLATIONS` dict (EN/AR), per-request `dir`/`lang`, Calibri-first font stack, 16px RTL base, readability floor on small classes.
3. **Majlis Protocol** (`kazma-core/kazma_core/majlis.py`) — a 4-phase Gulf cultural conversational flow (GREETING → SOCIAL → TRANSACTION → FAREWELL) with Kuwaiti-dialect defaults and cultural modifiers (Ramadan, Eid, National Day).

See [Arabic & Cultural Features](arabic-cultural-features).

---

## 9. Observability (current state)

| Signal | Source | Status |
|---|---|---|
| Structured logs | `logging` (JSON format option in `kazma.yaml`) | ✅ Active |
| Swarm metrics | `MetricsCollector` (in-memory + SQLite) — `tasks_completed`, `tasks_failed`, `avg_latency`, `total_tokens`, `total_cost` | ✅ Active |
| Tracing spans | In-house `TraceStore` (ring buffer + WebSocket to dashboard) + `TracingEmitter` (swarm, stdlib-only) | ✅ Active |
| Web research tools | `read_url` / `web_search` / `crawl_site` / research save+digest (`tools/read_url.py`, `web_research.py`) | ✅ Active — [Web research](web-research) |
| SSE telemetry | `/api/chat/stream` events; telemetry router | ✅ Active |
| **Langfuse** | `KazmaTracer` with `backend="langfuse"`; `logging.langfuse.enabled: auto` turns on when keys exist | ✅ **Wired** (`KAZMA_LANGFUSE=0` kill-switch) |
| **OpenTelemetry** | — | 🔴 **Removed** (dead code + dead deps purged; Langfuse + Console remain) |
| **Prometheus** | `/metrics` + `/api/metrics` (`kazma_ui/metrics.py`) | ✅ Active |

### OpenTelemetry — removed (Option A)

OpenTelemetry was **declared as a dependency with real code, but was never reachable at runtime** — no config path selected `backend="opentelemetry"`. The `[tracing]` extra (6 packages) was pure dead weight (never imported).

**Removed in the July 2026 cleanup:**
- `_init_opentelemetry()` method + all four `_trace_*_otel()` methods from `KazmaTracer`
- `OPENTELEMETRY` enum value from `TracingBackend`
- `opentelemetry-api` + `opentelemetry-sdk` from core deps
- Entire `[tracing]` optional extra (6 packages) from `pyproject.toml`
- `otlp_endpoint` field + `"opentelemetry"` from valid backends in `TracingConfig`

Tracing now has two backends: **Langfuse** (primary, **auto-on when keys exist**) and **Console** (fallback). The in-house `TraceStore` (ring buffer + WebSocket dashboard) and the swarm's stdlib-only `TracingEmitter` (OTel-compatible span format, no OTel package) remain unchanged.

---

## 10. Cross-cutting data stores

All SQLite stores in Kazma share the same concurrency model, centralized in `config_store.py:apply_sqlite_pragmas()`:

```sql
PRAGMA journal_mode=WAL;       -- concurrent readers, single writer
PRAGMA busy_timeout=5000;      -- 5 s wait on lock
PRAGMA synchronous=NORMAL;     -- WAL-safe, faster than FULL
```

| Store | Path | Purpose |
|---|---|---|
| ConfigStore | `kazma-data/settings.db` | Runtime settings (overrides `kazma.yaml`) |
| LangGraph checkpointer | `kazma-data/checkpoints.db` | Conversation state, HITL pauses |
| TaskStore | `kazma-data/swarm_tasks.db` | Swarm tasks + worker metrics |
| Time-travel snapshots | `kazma-data/snapshots.db` | `/replay` history |
| Hub registry | `~/.kazma/hub/registry.db` | Installed skills |
| Vector memory | `~/.kazma/vector_memory` | ChromaDB persistent client |
| Session store (gateway) | configurable | Platform ID ↔ thread_id mapping |
| Document store | `{documents.storage_root}/documents.db` + CAS tree | Document Intelligence metadata + content-addressed blobs (default under `kazma-data/document-store`) |

### Document Intelligence (subsystem)

Durable document ingest/parse/OCR/index/generate lives under
`kazma_core/documents/`. **Public durable boundary:** `DocumentIngestionService`
(Web `/api/documents/*`, tools, gateway `/documents`, TUI). **Execution boundary:**
`DocumentService` (isolated subprocess parsers). Job claiming can use Postgres
(`jobs_pg.py`); document **metadata remains SQLite** (single-replica honesty on
readiness). Full guide: [Document Intelligence](./document-intelligence.md) ·
[Phase map](./document-phases.md) · [Security](../security/document-security.md).

---

## Safety & resilience (cross-cutting)

Two cross-cutting subsystems sit across the supervisor and the tools:

- **Non-Stop & Self-Healing engine.** `supervised_invoke()` wraps graph
  execution with node heartbeats and stall detection; on a stall it rolls back
  to the last durable checkpoint, injects a `[KAZMA RECOVERY]` reflection, and
  resumes up to N attempts. Exhausted primary models fail over down
  `agent.nonstop.failover.chain` with per-model cooldowns (without mutating the
  active profile). A durable **LLM Call Ledger** (`kazma-data/llm_calls.db`)
  records every call; stranded swarm tasks are requeued on startup; and a
  watchdog auto-denies stale HITL approvals after
  `safety.hitl.approval_timeout_seconds`. Details: [Recent features → Non-Stop](./recent-features#4d-non-stop-execution--self-healing-engine-2026-08).
- **Commitment Layer (resolve-before-act).** A policy gate between the LLM and
  durable mutations. Before scheduling/sending/executing/config-changing,
  `authorize_effect` resolves intent against memory and policy; ambiguous acts
  raise a **semantic clarify/confirm** interrupt card on every platform. It runs
  in `tool_worker_node` before the HITL split so it can rewrite tool args first.
  Dedicated guide: [Commitment Layer](./commitment-layer).

---

## Documentation Audit Notes

- **Memory (2026-07 cutover):** V2 (bi-temporal beliefs + PPR recall) **is** the single memory stack (per-turn recall, tools, auto-store, compaction). The V1 4-layer RRF adapter was removed; earlier notes referencing `UnifiedMemoryAdapter` / `VectorMemory` are obsolete.
- **Build-site line numbers refreshed:** AGENTS.md cited "app.py ~line 966" for the startup recompile. The real site is `kazma-ui/kazma_ui/app.py:741-751` inside `_on_startup()` (line 721). `graph_builder.py:966` is an unrelated `aiosqlite.connect`.
- **`agent_handler` is a package, not a file:** The gateway's `agent_handler.py` was decomposed into the `agent_handler/` package (`store.py`, `graph.py`, `commands.py`, …).
- **`UnifiedModelRegistry`** is just an alias for `ModelRegistry` (`model_registry.py:950`).
