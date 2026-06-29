# Kazma Project Handover

## 1. Executive Summary

### High-Level Architecture

Kazma is a multi-package Python framework (Python 3.11+) for autonomous AI agents. Its architecture is **headless gateway**-based: a polling-based message bus that does NOT require a public IP, HTTPS tunnels, or webhooks. Platform adapters (Telegram, Discord, Slack) poll their respective APIs, normalize messages into `IncomingMessage` objects, and enqueue them onto a shared `asyncio.Queue(maxsize=100)`. The agent brain (LangGraph ReAct loop) consumes from this queue and replies via the same adapter.

Three entry points exist:
- **`kazma`** (CLI) - `kazma_cli/main.py` - Terminal commands for gateway, swarm, settings
- **`kazma-web`** (Web UI) - `kazma_ui/app.py` - FastAPI + HTMX + Alpine.js dashboard (Arabic-first, bilingual)
- **`kazma-tui`** (TUI) - `kazma_tui/app.py` - Textual-based terminal dashboard (English-only, metrics/ops)

### Golden Rules (from AGENTS.md)

1. **Package Scope**: Only modify `kazma-tui/`. Do NOT modify `kazma-core/`, `kazma-ui/`, `kazma-cli/`, or `kazma-gateway/` unless explicitly required for imports.
2. **Dependencies**: Use only `textual` for TUI framework. No new dependencies without orchestrator approval.
3. **ModelRegistry**: TUI is a READ-ONLY consumer. Never call `set_active_profile()`, `ConfigStore.write()`, or any mutation methods.
4. **Language**: All TUI text must be in English. No Arabic, RTL markers, or bilingual labels.
5. **TDD**: Write tests before implementation.
6. **Dashboard refresh**: 2-second interval using Textual's `set_interval`.
7. **Missing metrics**: Always show "N/A" fallback, never crash.

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Singleton `ModelRegistry` | Single source of truth for providers, models, API keys. Prevents config drift. |
| Headless gateway (polling) | No public IP needed. Works behind NAT, corporate firewalls. |
| SQLite-only persistence | Zero external dependencies. Edge-deployable. Single-file DB. |
| `asyncio.Queue` message bus | Bounded (100), backpressure-safe, platform-agnostic. |
| Textual for TUI | Professional CSS-styled terminal UI with reactive attributes, timers, widget system. |
| FastAPI + HTMX + Alpine.js for Web UI | Server-rendered with reactive islands. No React/Vue build step. |
| LangGraph for agent loop | State machine with checkpointing, tool calling, human-in-the-loop. |
| Per-thread checkpoint locking | `CheckpointManager` with LRU-bounded `OrderedDict` of `asyncio.Lock` prevents race conditions. |

---

## 2. Repository Map

### Directory Breakdown

```
G:\GitHubRepos\kazma\
├── kazma-core/              Core library (THE foundation)
│   └── kazma_core/
│       ├── agent/           Agent loop, graph builder, state, sub-agents
│       ├── swarm/           Swarm orchestration engine (6 patterns)
│       ├── tools/           Built-in tools (file_read, file_write, web_search, etc.)
│       ├── mcp/             MCP client/server integration
│       ├── hub/             Skill manifest, registry, validator
│       ├── security/        Security linter, dependency scanner
│       ├── safety/          HITL approval gates
│       ├── memory/          Vector store, FTS5 search
│       ├── cron/            Scheduled agent actions
│       ├── delegation/      Multi-agent orchestration
│       ├── docs/            Doc generator
│       ├── cli/             CLI helpers
│       ├── models/          Model definitions
│       ├── model_registry.py   ** CRITICAL ** Singleton ModelRegistry
│       ├── telemetry.py     ** CRITICAL ** HardwareMonitor (CPU/RAM/GPU)
│       ├── tracing.py       ** CRITICAL ** TraceStore + KazmaTracer
│       ├── retry.py         ** CRITICAL ** Retry with exponential backoff
│       ├── config_store.py  ** CRITICAL ** SQLite-backed runtime config
│       ├── llm_provider.py  LLM client abstraction
│       ├── providers.py     Provider presets (OpenAI, Anthropic, DeepSeek, etc.)
│       ├── cost_breaker.py  Budget circuit breaker
│       ├── authority.py     Context authority (80% compaction threshold)
│       ├── compaction.py    Auto-summarization
│       ├── shutdown.py      Global shutdown signal
│       └── state.py         AgentState definition
│
├── kazma-gateway/           Gateway adapters and message bus
│   └── kazma_gateway/
│       ├── gateway.py       ** CRITICAL ** GatewayManager, BaseAdapter, IncomingMessage, OutboundMessage
│       ├── adapters/
│       │   ├── telegram.py  ** CRITICAL ** Telegram adapter (polling + webhook)
│       │   ├── discord.py   Discord adapter
│       │   └── slack.py     Slack adapter (Socket Mode)
│       ├── stores/
│       │   ├── checkpoint.py ** CRITICAL ** AsyncSqliteSaver + CheckpointManager
│       │   └── sqlite.py    SQLiteSessionStore
│       ├── agent_handler.py Bridge: IncomingMessage -> LangGraph -> reply
│       ├── rate_feedback.py Rate limit feedback messages
│       ├── slash_commands.py 12 slash commands
│       ├── suggestions.py   Proactive next-step hints
│       └── swarm_notify.py  Telegram group notifications
│
├── kazma-tui/               Textual TUI dashboard
│   ├── kazma_tui/
│   │   ├── app.py           Main KazmaTUI App class
│   │   ├── dashboard.py     ** KEY FILE ** MetricsDashboard (6 metric cards, 2s refresh)
│   │   ├── chat.py          ChatPanel with input, messages, commands
│   │   ├── header.py        HeaderProviderModel (provider/model from ModelRegistry)
│   │   ├── footer.py        FooterShortcuts (Ctrl+Q, Tab, Enter)
│   │   ├── __init__.py      Package version
│   │   └── __main__.py      CLI entry point
│   └── tests/
│       ├── test_comprehensive.py  Full integration tests
│       ├── test_dashboard.py      Dashboard widget tests
│       ├── test_chat.py           Chat interface tests
│       ├── test_header_footer.py  Header/footer tests
│       ├── test_foundation.py     Foundation structure tests
│       └── test_app_async.py      Async app launch tests
│
├── kazma-ui/                Web UI (FastAPI + HTMX + Alpine.js)
│   └── kazma_ui/
│       ├── app.py           ** CRITICAL ** FastAPI app factory (45KB, wires everything)
│       ├── sse_chat.py      ** KEY FILE ** SSE chat router (LangGraph astream_events)
│       ├── chat.py          WebSocket chat handler
│       ├── dashboard.py     Dashboard route
│       ├── settings.py      12-tab settings page
│       ├── models_route.py  Models & Ollama management
│       ├── swarm_panel.py   Swarm management UI (44KB)
│       ├── swarm_sse.py     SSE streaming for swarm tasks
│       ├── i18n.py          Internationalization (150+ Arabic translations)
│       ├── auth.py          KAZMA_SECRET middleware
│       ├── session_manager.py Shared session store
│       ├── templates/       Jinja2 templates (dashboard, chat, swarm, settings, etc.)
│       └── static/          CSS, JS (Alpine.js, Chart.js)
│
├── kazma-cli/               CLI entry points
│   └── kazma_cli/
│       ├── main.py          Click-based CLI with subcommands
│       ├── gateway.py       `kazma gateway` subcommands (status, start, stop, restart, refresh)
│       ├── swarm.py         `kazma swarm` subcommands (status, workers, dispatch, broadcast, etc.)
│       ├── update.py        `kazma update` self-updater
│       ├── project.py       `kazma project init/show/validate`
│       ├── banner.py        Startup banner
│       └── completions.py   Shell tab completion
│
├── kazma-providers/         Provider configuration (largely superseded by kazma_core/providers.py)
├── kazma-memory/            Memory/context management (SQLite FTS5 + sqlite-vec)
├── kazma-skills/            Skill definitions and manifests
├── kazma-data/              Runtime data (checkpoints.db, sessions.db, settings.db, images/)
├── tests/                   100+ test files covering all packages
├── data/                    Static data files
├── docs/                    Docusaurus documentation site
├── examples/                Example custom skills (ALMuhalab)
├── scripts/                 Setup and utility scripts
├── archive/                 Archived/deprecated code
├── models/                  Model definitions
├── library/                 Library resources
├── skills/                  Skill resources
├── validation/              Validation artifacts
│
├── pyproject.toml           Project config (hatchling build, all packages)
├── kazma.yaml               Main configuration (agent, models, llm, gateway, swarm, etc.)
├── kazma-security.yaml      Security configuration
├── kazma-permissions.yaml   Division permission boundaries
├── docker-compose.yml       Docker deployment
├── Dockerfile               Container image
├── AGENTS.md                Mission guidance (golden rules)
├── CONTRIBUTING.md          Contribution guidelines
├── CHANGELOG.md             Sprint-by-sprint changelog (Sprints 1-11)
├── ARCHITECTURE_CHANGE.md   Tantivy -> SQLite FTS5 migration doc
└── uv.lock                  Lockfile for uv package manager
```

### Key Files to Read First

| Priority | File | Why |
|----------|------|-----|
| 1 | `AGENTS.md` | Golden rules. Non-negotiable constraints. |
| 2 | `kazma-core/kazma_core/model_registry.py` | Singleton that owns all provider/model config. Every subsystem depends on it. |
| 3 | `kazma-gateway/kazma_gateway/gateway.py` | `GatewayManager`, `BaseAdapter`, `IncomingMessage` - the message bus contract. |
| 4 | `kazma-core/kazma_core/swarm/engine.py` | `SwarmEngine` - 6 orchestration patterns, reliability layer, HITL checkpoints. |
| 5 | `kazma-ui/kazma_ui/app.py` | FastAPI app factory - wires all subsystems together. Shows full dependency graph. |
| 6 | `kazma-core/kazma_core/config_store.py` | `ConfigStore` - SQLite-backed runtime config with YAML fallback. |
| 7 | `kazma-core/kazma_core/tracing.py` | `TraceStore` + `KazmaTracer` - observability layer. |
| 8 | `kazma-gateway/kazma_gateway/stores/checkpoint.py` | `CheckpointManager` - per-thread locking for LangGraph checkpoints. |
| 9 | `kazma-tui/kazma_tui/dashboard.py` | `MetricsDashboard` - the main TUI widget consuming all data sources. |
| 10 | `tests/conftest.py` | Shared fixtures - shows how ModelRegistry is initialized in tests. |

---

## 3. Architectural Deep Dive

### The Registry Pattern

**File**: `kazma-core/kazma_core/model_registry.py` (lines 1-490)

The `ModelRegistry` is a true Python singleton (module-level `_registry` variable) that owns ALL provider configuration, API keys, and LLM client creation. It replaced the former `UnifiedModelRegistry`.

**Singleton lifecycle:**
```python
from kazma_core.model_registry import initialize_model_registry, get_model_registry, reset_model_registry

# 1. Create (called once at app startup)
registry = initialize_model_registry(config_store)

# 2. Retrieve (used everywhere)
registry = get_model_registry()

# 3. Teardown (tests only)
reset_model_registry()
```

**Why centralized over distributed config:**
- Prevents config drift between UI, CLI, TUI, and gateway
- Single point of truth for active provider/model
- LLM client caching (one `LLMProvider` per provider name)
- Model discovery via `/models` endpoint with caching
- Backward-compatible with legacy `llm.*` config keys

**Key methods:**

| Method | Returns | Notes |
|--------|---------|-------|
| `get_active_profile()` | `dict` with provider, base_url, model, api_key (masked) | Falls back to legacy `llm.*` keys |
| `set_active_provider(provider, base_url, model, api_key)` | normalized profile dict | Persists to ConfigStore, invalidates cached client |
| `set_active_model(model)` | None | Changes model within active provider |
| `get_client(model=None)` | `LLMProvider` | Cached by provider name |
| `get_model(model_id)` | `LLMProvider` | Looks up provider from model ID |
| `discover_models(provider_name)` | `list[str]` | Hits `/models` endpoint, caches results |
| `list_providers()` | `list[dict]` | All providers from DB or default presets |
| `upsert_provider(data)` | `dict` | Insert or update provider entry |
| `save_model_profile(name, data)` | `dict` | Save named model profile |
| `list_unified_options()` | `dict` | Unified model/provider/profile metadata |

**Storage keys (backward-compatible):**
- `providers.list` - JSON array of provider entries
- `providers.health.*` - Per-provider health status
- `models.saved.*` - Named model profiles
- `models.defaults.*` - Task-specific model defaults
- `llm.model` - Legacy model name
- `registry.active_provider` - Current active provider
- `registry.active_model` - Current active model
- `registry.discovered_models` - Cached model discovery results

**Critical constraint for TUI**: The TUI MUST only call read-only methods (`get_active_profile()`, `get_client()`, `list_providers()`, etc.). NEVER call `set_active_profile()`, `set_active_provider()`, `set_active_model()`, `upsert_provider()`, or any ConfigStore write methods.

### The Hybrid UI Pattern

Kazma has two distinct UI layers with different purposes:

| Aspect | TUI (`kazma-tui`) | Web UI (`kazma-ui`) |
|--------|-------------------|---------------------|
| Framework | Textual | FastAPI + HTMX + Alpine.js |
| Language | English only | Arabic-first, bilingual (AR/EN toggle) |
| Purpose | Metrics/ops dashboard | Full interaction layer |
| Data sources | HardwareMonitor, TraceStore, MetricsCollector, SwarmEngine | Agent, Gateway, Swarm, Settings |
| Refresh | 2-second `set_interval` | SSE + WebSocket real-time |
| Auth | None (local terminal) | KAZMA_SECRET middleware |
| Entry point | `kazma-tui` command | `kazma-web` command |

**Separation of concerns:**
- TUI reads metrics and displays them. Never mutates state.
- Web UI handles chat, settings, gateway management, swarm orchestration.
- Both consume `ModelRegistry` (read-only for TUI, read-write for Web UI).

### Persistence & Resilience

#### AsyncSqliteSaver with Per-Thread Locking

**File**: `kazma-gateway/kazma_gateway/stores/checkpoint.py`

The `CheckpointManager` wraps `AsyncSqliteSaver` with per-thread `asyncio.Lock` to prevent race conditions during concurrent state writes to the same `thread_id`.

```python
class CheckpointManager(BaseCheckpointSaver):
    def __init__(self, saver: AsyncSqliteSaver, max_locks: int = 10_000):
        self._locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        # LRU eviction when max_locks exceeded
```

**Key properties:**
- `active_locks` - Number of thread locks currently held
- `conn` - Exposes underlying aiosqlite connection
- Uses WAL journal mode for concurrent reads
- `PRAGMA synchronous=NORMAL` for performance

**Factory function:**
```python
manager = await create_checkpoint_manager("kazma-data/checkpoints.db")
graph = builder.compile(checkpointer=manager)
```

#### Retry Logic with Exponential Backoff

**File**: `kazma-core/kazma_core/retry.py`

Uses `tenacity` for configurable retry logic:

```python
@retry_llm_call  # Decorator for LLM calls
async def call_llm(...): ...

@retry_tool_call  # Decorator for tool executions
async def execute_tool(...): ...
```

**Configuration (from kazma.yaml or defaults):**
- `max_attempts`: 3
- `min_wait`: 2 seconds
- `max_wait`: 10 seconds
- `wait_exponential(multiplier=1, min=2, max=10)`

**Retryable exceptions:** `ConnectionError`, `TimeoutError`, `asyncio.TimeoutError`, `httpx.TimeoutException`, `httpx.ConnectError`, `httpx.RemoteProtocolError`

**NOT retried:** 4xx HTTP errors (bad request, auth failures)

**Friendly error mapping:**
- `friendly_llm_error(exc)` - Maps LLM failures to user-friendly messages
- `friendly_tool_error(exc)` - Maps tool failures to user-friendly messages

#### TraceStore for Observability

**File**: `kazma-core/kazma_core/tracing.py`

In-memory ring buffer (default 500 entries) with WebSocket broadcasting:

```python
trace_store = get_trace_store()  # Global singleton
trace_store.add(TraceEntry(...))  # Add entry
trace_store.recent(limit=50)      # Get recent entries
trace_store.stats()               # Get aggregated stats
```

**Stats returned:**
```python
{
    "total_cost": 0.1234,
    "total_tokens": 15000,
    "total_llm_calls": 42,
    "total_tool_calls": 18,
    "total_traces": 500,
    "uptime_seconds": 3600.0,
}
```

**Tracing backends:**
1. **Langfuse** (primary) - Full dashboard at localhost:3000
2. **OpenTelemetry** (fallback) - Jaeger/Zipkin at localhost:16686
3. **Console** (testing) - Stdout logging

**Traced operations:**
- `trace_llm_call()` - LLM API calls
- `trace_tool_execution()` - Tool executions
- `trace_state_transition()` - Agent state changes
- `trace_compaction()` - Context compaction events

#### HardwareMonitor

**File**: `kazma-core/kazma_core/telemetry.py`

Async hardware telemetry collector:

```python
monitor = HardwareMonitor()
snapshot = await monitor.get_stats()
# TelemetrySnapshot(cpu=45.2, ram_used_gb=16.4, ram_total_gb=32.0,
#                   gpu=88.0, vram_used_gb=14.2, vram_total_gb=24.0)
```

**Implementation details:**
- CPU/RAM via `psutil` (sync calls wrapped in `asyncio.run_in_executor`)
- GPU/VRAM via `nvidia-smi` subprocess (pure asyncio)
- Graceful fallback when nvidia-smi unavailable (non-NVIDIA systems)
- `stream()` method for continuous telemetry yielding

### The Swarm Engine

**File**: `kazma-core/kazma_core/swarm/engine.py` (58KB, 1200+ lines)

The `SwarmEngine` is the central async orchestrator for swarm workers with 6 orchestration patterns:

| Pattern | Description | File |
|---------|-------------|------|
| `dispatch` | Single worker dispatch | `engine.py` |
| `broadcast` | All workers in parallel | `engine.py` |
| `pipeline` | Sequential worker chain with HITL checkpoints | `patterns.py` |
| `fan_out` | Concurrent workers with aggregation strategies | `patterns.py` |
| `consult` | Independent opinions with synthesis | `consultation.py` |
| `conditional` | Router-based conditional dispatch | `patterns.py` |

**Reliability layer:**
- `CircuitBreaker` - Per-worker failure tracking (closed/open/half-open)
- `RetryPolicy` - Per-worker retry with exponential backoff
- `TimeoutGuard` - Per-task timeout enforcement
- `OutputValidator` - Schema validation for worker output
- `BoundedConcurrency` - `asyncio.Semaphore` wrapper
- `FallbackChain` - Ordered fallback worker list

**HITL Checkpoints:**
- Pipelines pause at configured steps
- `POST /api/swarm/tasks/{id}/approve` resumes
- `POST /api/swarm/tasks/{id}/reject` aborts
- Configurable timeout with auto-reject
- State persists to SQLite for crash recovery

**Worker types:**
- `InProcessWorker` - In-process LLM dispatch
- `TelegramWorker` - Telegram bot-based dispatch

**Singleton access:**
```python
from kazma_core.swarm.engine import get_swarm_engine, set_swarm_engine

engine = get_swarm_engine()  # Returns None if not initialized
set_swarm_engine(engine)     # Called at app startup
```

### The Gateway Pattern

**File**: `kazma-gateway/kazma_gateway/gateway.py`

**Message flow:**
```
Platform Adapter (polling) -> IncomingMessage -> asyncio.Queue(maxsize=100)
    -> GatewayManager._consume() -> MessageHandler (Brain)
    -> OutboundMessage -> Platform Adapter (send)
```

**Key classes:**
- `IncomingMessage` - Normalized inbound message (platform, sender_id, text, context_metadata)
- `OutboundMessage` - Platform-targeted outbound message
- `BaseAdapter` - Abstract base for platform adapters
- `GatewayManager` - Orchestrates adapters, queue, consumer, shutdown
- `RateLimiter` - Token-bucket rate limiting
- `MessageMetrics` - Throughput/error tracking
- `SessionStore` - Abstract persistent side-cache for platform context

**Adapter contract:**
```python
class BaseAdapter(ABC):
    name: str = "unknown"

    async def listen(self, queue, shutdown_event) -> None:
        """Poll platform, enqueue IncomingMessage. MUST include jitter."""
        ...

    async def send(self, outbound: OutboundMessage) -> bool:
        """Deliver outbound message to platform."""
        ...
```

**Jitter contract:** Every `listen()` implementation MUST call `await self.jitter_sleep(shutdown_event)` between poll cycles (1-3s randomized delay).

### The ConfigStore Pattern

**File**: `kazma-core/kazma_core/config_store.py`

SQLite-backed runtime configuration with YAML fallback:

```python
store = ConfigStore()  # Default: kazma-data/settings.db
store.get("llm.model", "gpt-4o-mini")  # DB overrides YAML
store.set("llm.model", "gpt-4", category="llm")  # Persist to DB
store.export_yaml()  # Merge DB + YAML -> YAML string
store.import_yaml(yaml_str)  # Import from YAML
```

**Storage:**
- SQLite table: `settings (key TEXT PK, value TEXT, category TEXT, updated_at TEXT)`
- YAML fallback: `kazma.yaml` (supports dotted keys like `llm.model`)
- Thread-safe with `threading.Lock`

---

## 4. The 'Next Agent' Playbook

### Development Environment

#### Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.11+ (<3.14) | [python.org](https://python.org) |
| uv | 0.11+ | [astral.sh/uv](https://docs.astral.sh/uv/) |
| Git | 2.30+ | System package manager |

#### Initialize with uv

```powershell
# Clone and enter directory
cd G:\GitHubRepos\kazma

# Install all dependencies (core + dev + tui + rag)
uv sync --all-extras

# Verify installation
uv run python -c "from kazma_core.model_registry import get_model_registry; print('OK')"
```

#### Run Tests

```powershell
# Full suite (tests/ directory)
uv run pytest tests/ -v

# TUI tests only
uv run pytest kazma-tui/tests/ -v

# With coverage
uv run pytest tests/ -v --cov=kazma_core --cov-report=term-missing

# Specific test file
uv run pytest tests/test_model_registry.py -v

# Specific test function
uv run pytest tests/test_model_registry.py::TestModelRegistry::test_get_active_profile -v
```

#### Lint and Type Check

```powershell
# Lint
uv run ruff check .

# Auto-fix
uv run ruff check --fix .

# Format
uv run ruff format .

# Type check
uv run mypy kazma-core/kazma_memory/
```

#### Launch TUI

```powershell
# Via entry point
uv run kazma-tui

# Via module
uv run python -m kazma_tui
```

#### Launch Web UI

```powershell
# Via entry point
uv run kazma-web

# Via module
uv run python -m kazma_ui

# Custom port
uv run kazma-web --port 8080
```

#### Verify ModelRegistry

```powershell
# Quick verification
uv run python -c "
from kazma_core.config_store import ConfigStore
from kazma_core.model_registry import initialize_model_registry, get_model_registry

cs = ConfigStore()
registry = initialize_model_registry(cs)
profile = registry.get_active_profile()
print(f'Provider: {profile[\"provider\"]}')
print(f'Model: {profile[\"model\"]}')
print(f'Base URL: {profile[\"base_url\"]}')
"
```

### Contribution Guidelines

#### Prohibited Patterns

1. **Do NOT mutate ModelRegistry from TUI.** Never call:
   - `set_active_provider()`
   - `set_active_model()`
   - `upsert_provider()`
   - `ConfigStore.write()`
   - `ConfigStore.set()`

2. **Do NOT add new dependencies** without orchestrator approval.

3. **Do NOT modify** `kazma-core/`, `kazma-ui/`, `kazma-cli/`, or `kazma-gateway/` unless explicitly required for imports.

4. **Do NOT use Arabic** in TUI code. All UI text must be in English.

5. **Do NOT hardcode paths.** Use `Path` objects and relative paths. Environment variables override defaults.

6. **Do NOT use global mutable state** without proper synchronization (locks, events).

7. **Do NOT block the event loop.** Use `asyncio` for all I/O. Wrap sync calls in `run_in_executor`.

#### Required Patterns

1. **Type hints** on all public APIs:
   ```python
   def get_stats(self) -> TelemetrySnapshot:
       ...
   ```

2. **Docstrings** on all public APIs (Google style):
   ```python
   def get_active_profile(self) -> dict[str, str]:
       """Return the active provider profile.

       Returns a dict with keys ``provider``, ``base_url``, ``model``,
       ``api_key`` (masked).
       """
       ...
   ```

3. **Logging** with module-level logger:
   ```python
   logger = logging.getLogger(__name__)
   ```

4. **Future annotations** for type hints:
   ```python
   from __future__ import annotations
   ```

5. **Graceful degradation** with "N/A" fallback:
   ```python
   _NA = "N/A"

   def _format_cpu(self, value: float | None) -> str:
       if value is None:
           return f"CPU: {_NA}"
       return f"CPU: {value:.1f}%"
   ```

6. **Test-first (TDD)**: Write tests before implementation.

7. **Modular widgets**: One class per file for TUI widgets.

8. **Conventional commits**: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`, `security:`

### Troubleshooting

#### Common Gotchas

| Issue | Cause | Fix |
|-------|-------|-----|
| `RuntimeError: ModelRegistry not initialized` | `initialize_model_registry()` not called | Call it in `conftest.py` or app startup |
| `Queue full` warnings | Message bus saturated (100 messages) | Check adapter polling rate; increase `max_queue_size` |
| TUI shows "N/A" everywhere | Data sources not available | Ensure `kazma-core` dependencies are installed |
| `409 Conflict` on Telegram | Webhook still set from previous setup | `TelegramAdapter` calls `deleteWebhook` on start |
| `CheckpointManager` deadlock | Thread lock not released | Check `active_locks` property; ensure `aput` completes |
| `CostCircuitBreaker` blocks chat | Budget exceeded | Reset via `/cost` command or restart |
| Settings not persisting | `ConfigStore` DB not writable | Check `kazma-data/` directory permissions |
| `nvidia-smi` timeout | GPU driver issue | `HardwareMonitor` falls back gracefully to CPU-only |
| Arabic text in TUI | Wrong language setting | TUI must always use English; check `AGENTS.md` |

#### What Breaks If You Patch Instead of Refactor

| Anti-pattern | Consequence |
|--------------|-------------|
| Direct `sqlite3.connect()` instead of `ConfigStore` | Config drift between UI, CLI, TUI |
| Creating new `LLMProvider` instances instead of `registry.get_client()` | No client caching, duplicated connections |
| Bypassing `GatewayManager` to send messages directly | No rate limiting, no metrics, no correlation IDs |
| Using `requests` instead of `httpx` for Telegram API | Blocks event loop, breaks async architecture |
| Adding a second `ModelRegistry` instance | Config drift, split-brain provider state |
| Using `global` variables for state | Race conditions, test pollution |
| Hardcoding `thread_id` in checkpoint calls | Breaks multi-user session isolation |

---

## 5. Implementation History

### Chronological Summary of Major Refactors

| Sprint | Period | Focus | Key Changes |
|--------|--------|-------|-------------|
| Sprint 1 | Early 2026 | Core Foundation | ReAct supervisor, SQLite checkpointing, multi-provider LLM, Web UI dashboard, CLI |
| Sprint 2 | Early 2026 | Gateway & Multi-Platform | Unified adapter framework, Telegram/Discord adapters, session store, rate limiting |
| Sprint 3 | Mid 2026 | Advanced Agent & Safety | Sub-agents, cron autonomy, HITL approval gates, RAG memory, hardware telemetry |
| Sprint 4+ | Mid 2026 | UX, Security & Ecosystem | Slash commands, personalities, Slack adapter, image gen, knowledge graph, security linter, RBAC |
| Sprint 7 | June 2026 | Web UI Rebuild & Memory | Complete Web UI rebuild (12 settings tabs), SQLite config_store, FTS5 memory |
| Sprint 8 | June 2026 | Architecture Remediation | Race condition fixes, dead code removal, UnifiedToolExecutor, service facade, HITL approval UI |
| Sprint 9 | June 2026 | UI Bug Fixes & Bilingual | Dark mode, model selection pipeline, bilingual EN/AR toggle |
| Sprint 10 | June 2026 | Swarm Engine Overhaul | Fan-out, consult, conditional patterns, capability router, circuit breaker, retry, timeout, HITL checkpoints, TaskStore, metrics/tracing, SSE streaming, Swarm Panel UI |
| Sprint 11 | June 2026 | CLI Control Plane | Gateway CLI commands, swarm CLI commands, real health status, update command, completions |
| Latest | June 2026 | ModelRegistry + TUI | Singleton ModelRegistry, Textual TUI dashboard, unified provider routing |

### Architecture Changes

| Change | Date | Rationale |
|--------|------|-----------|
| Tantivy -> SQLite FTS5 | Jan 2025 | Remove Rust/maturin dependency for edge deployment |
| UnifiedModelRegistry -> Singleton ModelRegistry | June 2026 | Single source of truth, prevent config drift |
| Old curses TUI -> Textual TUI | June 2026 | Professional framework with CSS styling, reactive attributes |
| Multiple tool registries -> UnifiedToolExecutor | June 2026 | Eliminate duplication, single routing point |
| Scattered session stores -> Unified SessionStore | June 2026 | Single persistence layer, crash-recovery routing |

### Known Deferred Tasks

| Task | Description | Status |
|------|-------------|--------|
| VRAM metric in TUI | Add VRAM usage card to dashboard with color-coded thresholds | **DONE** (uses HardwareMonitor via nvidia-smi) |
| `router.py` archival | Move `kazma-providers/router.py` to `archive/` | **DONE** |
| Old TUI deletion | Remove curses-based Arabic TUI | **DONE** |
| Textual TUI build | New English-only metrics dashboard | **DONE** (191 tests, all passing) |
| ModelRegistry refactor | Centralize all provider/model config | **DONE** (singleton pattern) |

### Test Infrastructure

**Location**: `tests/` directory (100+ test files)

**Shared fixtures** (`tests/conftest.py`):
```python
@pytest.fixture(autouse=True)
def _init_model_registry(tmp_path):
    """Initialize the ModelRegistry singleton for tests."""
    from kazma_core.config_store import ConfigStore
    from kazma_core.model_registry import initialize_model_registry, reset_model_registry

    db_path = str(tmp_path / "test_registry.db")
    cs = ConfigStore(db_path=db_path)
    initialize_model_registry(cs)
    yield
    reset_model_registry()
```

**Test configuration** (`pyproject.toml`):
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
addopts = "-v --tb=short"
```

**Running tests:**
```powershell
# All tests
uv run pytest tests/ -v

# TUI tests only
uv run pytest kazma-tui/tests/ -v

# With coverage
uv run pytest tests/ -v --cov=kazma_core --cov-report=term-missing

# Specific test
uv run pytest tests/test_model_registry.py::TestModelRegistry::test_get_active_profile -v
```

### Configuration Reference

**Main config**: `kazma.yaml`
```yaml
agent:
  name: kazma
  version: 0.1.0
  language: ar
  rtl: true

models:
  default: gpt-4o-mini
  router: litellm
  fallback: gpt-4o-mini

llm:
  base_url: https://api.openai.com/v1
  model: gpt-4o-mini
  max_tokens: 4096
  temperature: 0.7
  timeout: 60.0

gateway:
  rate_limits:
    telegram: 30
    discord: 5
    slack: 1

swarm:
  enabled: true
  workers:
    - name: core
      type: telegram_bot
      model: mimo-v2.5-pro
      provider: xiaomi
```

**Environment variables** (`.env.example`):
```bash
TELEGRAM_BOT_TOKEN=your-token
DISCORD_BOT_TOKEN=your-token
DEEPSEEK_API_KEY=your-key
OPENAI_API_KEY=your-key
# KAZMA_SECRET=your-secret
# KAZMA_VECTOR_PATH=~/.kazma/vector_memory
# KAZMA_VECTOR_COLLECTION=agent_memory
# KAZMA_VECTOR_MODEL=all-MiniLM-L6-v2
```

**Docker deployment:**
```bash
docker compose up -d
# Health check: curl http://localhost:8000/api/gateway/status
```

---

## Quick Reference Card

| What | Where | How |
|------|-------|-----|
| Start Web UI | `kazma-ui/` | `uv run kazma-web` |
| Start TUI | `kazma-tui/` | `uv run kazma-tui` |
| Run CLI | `kazma-cli/` | `uv run kazma status` |
| Run tests | `tests/` | `uv run pytest tests/ -v` |
| Run TUI tests | `kazma-tui/tests/` | `uv run pytest kazma-tui/tests/ -v` |
| Lint | root | `uv run ruff check .` |
| Format | root | `uv run ruff format .` |
| Type check | root | `uv run mypy kazma-core/kazma_memory/` |
| ModelRegistry | `kazma-core/kazma_core/model_registry.py` | `get_model_registry()` |
| ConfigStore | `kazma-core/kazma_core/config_store.py` | `ConfigStore()` |
| TraceStore | `kazma-core/kazma_core/tracing.py` | `get_trace_store()` |
| HardwareMonitor | `kazma-core/kazma_core/telemetry.py` | `HardwareMonitor()` |
| SwarmEngine | `kazma-core/kazma_core/swarm/engine.py` | `get_swarm_engine()` |
| GatewayManager | `kazma-gateway/kazma_gateway/gateway.py` | `GatewayManager()` |
| CheckpointManager | `kazma-gateway/kazma_gateway/stores/checkpoint.py` | `await create_checkpoint_manager()` |
