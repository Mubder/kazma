# Architecture: Kazma Agent Framework

## Overview

Kazma is a multi-platform AI agent framework with a LangGraph supervisor brain,
swarm orchestration, cross-platform dispatch (Telegram/Discord/Slack/Web/TUI),
and an OpenAI-compatible LLM provider layer.

## System Components

### 1. Core Agent (`kazma-core/kazma_core/`)

**Agent Runner** (`agent_runner.py`):
- `KazmaAgent` — wires LLMProvider, ToolRegistry, ContextAuthority, CostCircuitBreaker
- `get_streaming_graph()` — builds and caches a LangGraph supervisor graph for SSE
- `run()` — executes the supervisor graph with AsyncSqliteSaver checkpointing

**Graph Builder** (`agent/graph_builder.py`):
- `build_supervisor_graph()` — ReAct loop: SUPERVISOR → TOOL_WORKER → SUPERVISOR → RESPOND
- `supervisor_node()` — calls LLM with tools, routes based on response
- 80% context compaction via `ContextAuthority`
- Personality injection (tagged system prompts)

**LLM Provider** (`llm_provider.py`):
- OpenAI-compatible httpx client (works with OpenAI, DeepSeek, NVIDIA NIM, Ollama, vLLM, etc.)
- Tool-calling support with automatic fallback for providers that don't support tools
- Captures response body on HTTP errors for debugging
- Fallback model support (LiteLLM router mode)

**Model Registry** (`model_registry.py`):
- Singleton provider/model management backed by SQLite ConfigStore
- `get_client(model)` — returns cached LLMProvider, auto-corrects provider/model mismatches
- `set_active_model()` — switches model and auto-switches provider if needed
- `find_provider_for_model()` — searches manual + discovered models
- `get_visible_models()` — returns user-selected models per provider

### 2. Swarm Engine (`kazma-core/kazma_core/swarm/`)

**Engine** (`engine.py`, ~1727 lines):
- `SwarmEngine.dispatch()` — main entry point with catch-all error handling
- `_dispatch_inner()` — auto-routing, pipeline/fanout/broadcast dispatch
- `_dispatch_worker()` — per-worker dispatch with circuit breaker, retry, timeout
- `_handle_handoff()` — worker-to-worker handoff with cycle detection (max depth 5)
- `_finalize_task()` — records metrics, persists to SQLite, LRU history cap (500)
- AutoScaler integration: `NoCapableWorkersError` → `maybe_scale()` → retry

**Workers** (`worker.py`):
- `SwarmWorker` (ABC) — base with lifecycle hooks, log ring buffer (100 entries)
- `InProcessWorker` — calls LLM directly; resolves provider per model; captures token/cost data
- Uses `system_prompt` from WorkerConfig or WorkerRegistry as fallback

**Task Model** (`task.py`):
- `SwarmTask` — id, type, prompt, workers, context, metadata, result
- `WorkerResult` — output, status, tokens_used, cost, duration_seconds, handoffs
- `SwarmDispatchContext` — structured context with system_prompt and blackboard
- `WorkerCapabilities` — role, expertise tags, tools, model_specialty

**Reliability** (`reliability.py` & `reliability_registry.py`):
- `CircuitBreaker` — closed/open/half-open with single-probe gating.
- `RetryPolicy` — exponential backoff with retryable predicate (skips auth/config errors).
- `TimeoutGuard` — configurable per-worker timeout.
- `FallbackChain` — ordered fallback workers on failure.
- **Persistent Circuit Breaker (3-Strike System):** Tracks `consecutive_tool_failures` persistently across both LangGraph `SupervisorState` and Swarm worker dispatches. When the threshold is reached, subsequent tool runs are completely bypassed to prevent infinite retry loops.
- **Advanced Tool-Denial Mocking (HTTP 400 Prevention):** To satisfy strict API format rules (LiteLLM/OpenAI), when the breaker trips mid-batch, Kazma automatically generates conformant `role: tool` mock responses containing override messages for all blocked tool calls.

**Routing Engine** (`routing_engine.py`):
- `UnifiedRouter` — A consolidated routing engine that manages task distribution.
- Performs semantic vector matching (via ChromaDB `SemanticRouter`) first, boosts scores based on dialect/language metrics, and falls back to a clean static `_keyword_match` overlap score when the vector database is offline or below matching thresholds.
- Eliminates legacy `BaseRouter` and redundant polymorphic router wrappers.

**Patterns** (`patterns.py`):
- `execute_pipeline()` — sequential stages with SQLite WAL stage-output logging, HITL checkpoint gating, synthetic refined output merging (`_synthesize_refined_output`), and `SelfImprovementSkill` hooks.
- `execute_fan_out()` — parallel dispatch with aggregation.
- `execute_conditional()` — router-based dynamic dispatch.
- Note: DAG-based pipeline topology execution (`topology.py`) is formally deprecated.

**Persistence** (`task_store.py`):
- SQLite with WAL mode, busy_timeout, auto-migration
- Full task round-trip (all fields preserved)
- `json_each()` for exact worker filtering
- Daily per-worker metrics aggregation

**Worker Registry** (`registry.py`):
- Thread-safe JSON-backed worker catalog
- Singleton via `get_worker_registry()`
- `WorkerEntry` — name, expertise, roles, model, provider, system_prompt

**Auto-Scaler** (`autoscaler.py`):
- `WorkerTemplate` — blueprint with capabilities, min/max instances
- `AutoScaler` — matches templates to tasks, spawns instances, reaps idle ones
- Persists templates to `swarm_templates.json`

### 3. Gateway (`kazma-gateway/kazma_gateway/`)

**Agent Handler** (`agent_handler.py`, ~1306 lines):
- `create_graph_handler()` — creates the async message handler closure
- Platform isolation: graph state never sees chat_id/user_id (stored in SessionStore)
- Slash command intercept: `/help`, `/reset`, `/model`, `/status`, `/cost`, etc.
- Swarm dispatch: `/swarm <task>`, "use the swarm to...", auto-routing
- Interactive model selector: `/models` with Telegram inline keyboards
- Output routing: mirrors swarm results to Telegram group (Phase 5)

**Telegram Adapter** (`adapters/telegram.py`):
- Manual getUpdates polling with jitter, optional webhook ingress
- 429 retry with exponential backoff, parse_mode fallback
- Voice message transcription (openai/local/groq STT)
- Inline keyboard builders for model selection and HITL approvals
- Callback query handling (model_provider:, model_select:, hitl:, personality:)

**Slash Commands** (`slash_commands.py`):
- `resolve_slash_command()` — instant resolution without LLM calls
- Commands: /help, /reset, /status, /model, /memory, /cost, /replay, /config, /personality, /context

### 4. Web UI (`kazma-ui/kazma_ui/`)

**App** (`app.py`):
- FastAPI factory with SSE chat, swarm panel, settings, providers, MCP, agents
- GatewayManager wired with TelegramBusAdapter at startup
- SubAgentManager for parallel child agent graphs

**Swarm Panel** (`swarm_panel.py`, ~1451 lines):
- Full REST API: workers CRUD, dispatch, tasks, metrics, templates, export
- SSE streaming for live task updates
- Output routing config: `GET/PUT /api/swarm/output-target`
- Server-side task search and CSV/JSON export

**Frontend** (`static/js/`):
- `chat.js` — SSE chat with model dropdown, bidirectional sidebar sync
- `swarm.js` — Full swarm panel logic (dispatch, workers, metrics, templates, output routing)
- `app.js` — Sidebar model dropdown with localStorage persistence

### 5. TUI (`kazma-tui/kazma_tui/`)
- Textual-based dashboard (CPU/RAM metrics, chat, header with provider/model)
- Read-only ModelRegistry consumer
- Commands: /help, /clear, /quit, /model

## Data Flow

```
User Message (Telegram/Discord/Web/TUI)
     │
     ▼
┌─────────────┐     ┌──────────────────┐
│  Gateway     │────▶│  Agent Handler   │
│  (Adapter)   │     │                  │
│  Normalizes  │     │  1. Slash cmd?   │
│  to Incoming │     │  2. Swarm cmd?   │
│  Message     │     │  3. Model cmd?   │
└─────────────┘     │  4. Graph invoke │
                     └──────┬───────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
     ┌──────────────┐ ┌──────────┐ ┌────────────┐
     │ LangGraph    │ │ Swarm    │ │ Slash Cmd  │
     │ Supervisor   │ │ Engine   │ │ Resolver   │
     │ (ReAct loop) │ │          │ │ (instant)  │
     └──────┬───────┘ └────┬─────┘ └────────────┘
            │               │
            ▼               ▼
     ┌──────────────┐ ┌──────────────────────┐
     │ LLM Provider  │ │ Workers (InProcess)  │
     │ (httpx)       │ │ + Circuit Breaker    │
     │               │ │ + Retry + Timeout    │
     │ OpenAI        │ │ + Auto-Scaler        │
     │ DeepSeek      │ │ + Output Routing     │
     │ NVIDIA NIM    │ └──────────────────────┘
     │ Ollama        │
     │ vLLM          │
     └───────────────┘
```

## Key Design Decisions

1. **Platform Isolation**: Graph state never contains platform IDs; SessionStore maps thread_id → context
2. **Provider Auto-Correction**: Model switches automatically switch the provider to match
3. **Best-Effort Output Routing**: Group routing errors are logged, never raised
4. **Swarm NL Dispatch**: Both explicit `/swarm` and bare "swarm" keyword trigger auto-routing
5. **Tool Fallback**: Providers that reject tools (NVIDIA NIM) get automatic retry without tools

## Configuration

All runtime config is stored in SQLite via `ConfigStore`:
- `providers.list` — provider entries with base_url, api_key, models
- `registry.active_provider` / `registry.active_model` — active selection
- `providers.<name>.selected_models` — user-selected models per provider
- `swarm.output_target` — Telegram group routing config
- `connectors.telegram.token` / `connectors.telegram.enabled`
