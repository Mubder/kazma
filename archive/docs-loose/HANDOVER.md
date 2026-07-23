# Kazma Project — Complete Handover

## 1. Executive Summary

### Project Purpose and Scope

Kazma is a multi-package Python framework (Python 3.11+) for autonomous AI agents. It provides a headless gateway for multi-platform chat (Telegram, Discord, Slack), a web dashboard, a terminal dashboard, and a swarm engine for multi-worker orchestration. The project is designed for edge deployment with minimal external dependencies.

The framework is built around a ReAct supervisor graph (LangGraph) that can use tools, memory, HITL approval gates, and multi-provider LLM routing. All provider/model configuration, API keys, and LLM client creation flow through a single `ModelRegistry` singleton.

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Entry Points                                    │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────────┐                       │
│  │ kazma    │  │ kazma-web    │  │ kazma-tui        │                       │
│  │ (CLI)    │  │ (Web UI)     │  │ (Terminal UI)    │                       │
│  └────┬─────┘  └──────┬───────┘  └────────┬─────────┘                       │
│       │               │                    │                                  │
│       └───────────────┼────────────────────┘                                  │
│                       │                                                      │
│                       ▼                                                      │
│  ┌──────────────────────────────────────────┐                               │
│  │ ModelRegistry (singleton)                │                               │
│  │ - providers, models, API keys, clients   │                               │
│  └──────────────────────┬───────────────────┘                               │
│                         │                                                   │
│                         ▼                                                   │
│  ┌──────────────────────────────────────────┐                               │
│  │ kazma-core                               │                               │
│  │ - agent loop, tools, memory, swarm,      │                               │
│  │   telemetry, tracing, retry, config store  │                               │
│  └──────┬─────────────────────────┬─────────┘                               │
│         │                         │                                         │
│         ▼                         ▼                                         │
│  ┌──────────────┐        ┌──────────────────┐                               │
│  │ kazma-gateway│        │ kazma-ui         │                               │
│  │ adapters/bus │        │ FastAPI/SSE/WS   │                               │
│  └──────┬───────┘        └────────┬─────────┘                               │
│         │                         │                                         │
│         ▼                         ▼                                         │
│  Telegram/Discord/Slack     Browser dashboard                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions and Principles

1. **Singleton ModelRegistry**: One source of truth for all provider/model config, API keys, and LLM client creation. Prevents config drift between CLI, Web UI, TUI, and gateway.
2. **Headless Polling Gateway**: Adapters poll their platform APIs instead of requiring webhooks. No public IP, no HTTPS tunnels, no ngrok. Works behind NAT and firewalls.
3. **SQLite-Only Persistence**: Runtime config, checkpoints, sessions, swarm tasks, and search all use SQLite. Zero external dependencies for edge deployment.
4. **Unified Message Bus**: `asyncio.Queue(maxsize=100)` normalizes inbound/outbound messages so the brain never sees platform-specific code.
5. **Async-First**: All I/O is async. Sync calls (psutil, file I/O) are wrapped in `asyncio.run_in_executor`.
6. **Web UI is Arabic-First, Bilingual**: Supports EN/AR toggle via cookie middleware and i18n helpers. TUI is English-only.
7. **TUI is Read-Only**: The terminal dashboard only consumes `ModelRegistry`, `HardwareMonitor`, `TraceStore`, `MetricsCollector`, and `SwarmEngine`. It never mutates configuration.
8. **Test-First**: The project follows TDD. Tests are in `tests/` and `kazma-tui/tests/`.

### All Entry Points

| Entry Point | Module | Description |
|-------------|--------|-------------|
| `kazma` | `kazma_cli.main:main` | CLI for status, serve, gateway, swarm, project, docs, completions, update, wizard, hub. |
| `kazma-web` | `kazma_ui.app:main` | FastAPI web server. Default port 8000. Arabic-first bilingual dashboard. |
| `kazma-tui` | `kazma_tui.app:main` | Textual terminal dashboard. English-only, metrics + chat. |

### Important Notes for the Next Agent

- Read `AGENTS.md` before touching any code. It defines non-negotiable boundaries (TUI package scope, no new dependencies, read-only ModelRegistry, English-only TUI).
- The `ModelRegistry` singleton must be initialized before any `KazmaAgent` is created. `tests/conftest.py` does this automatically; app startup does it in `kazma_ui/app.py` and `kazma_cli/main.py` callers.
- The gateway uses polling with mandatory jitter. Do not remove jitter or switch to blocking HTTP requests.
- All LLM clients must be obtained from `ModelRegistry.get_client()` or `get_model()`. Never instantiate `LLMProvider` directly.
- The TUI must not contain Arabic, RTL markers, or bilingual labels.
- When adding new UI routes or SSE events, follow the existing patterns in `kazma_ui/sse_chat.py` and `kazma_ui/swarm_sse.py`.
- Swarm persistence goes through `TaskStore`. Never write raw SQL outside the task store for swarm state.

---

## 2. Full Implementation History

This section lists the major work done on the project in rough chronological order. Each entry includes what changed, why, files affected, and gotchas.

### 2.1 Telegram Adapter Robustness Fixes (7 root causes)

**Commits:** `79913b8` (fix 7 robustness issues), `88e3ef4` (path regression and cancellation callback), `6d238c9` (session metadata enrichment), `39a14d5` (merge cleanup).

**Files affected:** `kazma-gateway/kazma_gateway/adapters/telegram.py`, `kazma-gateway/kazma_gateway/agent_handler.py`, `kazma-gateway/kazma_gateway/gateway.py`, `tests/test_gateway.py`, `tests/test_telegram_voice.py`.

**What changed and why:**
1. **Webhook conflict / 409 on getUpdates**: Telegram returns HTTP 409 Conflict if a webhook is still registered when polling starts. The adapter now calls `deleteWebhook(drop_pending_updates=False)` on startup before entering the poll loop.
2. **Invalid or revoked bot token / silent 401**: Every `getUpdates` call returns 401 when the token is bad, but the adapter reported "connected". The adapter now calls `getMe` on startup and sets `self._running = False` if the token fails, surfacing the error.
3. **Rate-limited outbound sends (429)**: `send()` now retries up to 3 times with exponential backoff, reads `retry_after` from Telegram's response, and adds jitter.
4. **Markdown parse errors (400)**: Agent responses can contain unescaped `_`, `*`, `` ` ``, `[`. If `sendMessage` returns 400, the adapter retries once with `parse_mode` removed so the message still reaches the user.
5. **Connection failures and long-poll timeouts**: `listen()` catches `httpx.TimeoutException`, `httpx.ConnectError`, and generic exceptions, logs them, and always returns to the mandatory jitter sleep instead of crashing.
6. **Voice file download size cap**: Voice/audio downloads are capped at 10MB both via `Content-Length` header and by checking actual downloaded bytes (stream-based bypass protection). Files that exceed the limit are rejected safely.
7. **Callback query cancellation and inline keyboard handling**: Inline keyboard callbacks (HITL approve/deny, personality selection) are parsed and enqueued as synthetic `IncomingMessage` objects. `_answer_callback_query` dismisses the loading indicator. The callback queue reference is stored so webhook ingress can also enqueue callbacks.

**Path regression fix (`88e3ef4`):** A previous refactor broke the relative file path for voice downloads and webhook routing. The fix restored the correct path construction for `https://api.telegram.org/file/bot<token>/<file_path>` and ensured the webhook router is mounted under `/api/webhooks/telegram`.

**Cancellation callback fix:** `BaseAdapter.start()` now adds a done callback that ignores `CancelledError` and logs unexpected adapter listen task crashes so `_running` is reset properly.

**Gotchas:**
- If the Telegram adapter reports "connected" but never receives messages, check whether a webhook is still set via another tool/BotFather. `deleteWebhook` handles it at startup.
- The adapter does not use aiogram. All HTTP is direct `httpx`.
- Emoji reactions (`setMessageReaction`) are fire-and-forget; failures are logged at debug level only.
- Voice transcription requires `voice_enabled=true` and a configured STT provider (`openai` or `groq`).

### 2.2 CheckpointManager `aput_writes` Fix

**Commit:** `1b7ec21` (`fix(gateway): implement checkpoint async write delegation`).

**Files affected:** `kazma-gateway/kazma_gateway/stores/checkpoint.py`.

**What changed:** `CheckpointManager` originally only forwarded `aput` to the underlying `AsyncSqliteSaver`. LangGraph also calls `aput_writes` for pending writes. The wrapper now implements `aput_writes` with the same per-thread `asyncio.Lock` pattern used in `aput`, preventing lost pending writes during concurrent graph execution.

**Why:** Without this, resumable graphs could lose intermediate tool-call results or HITL interrupt state, causing resume failures after a crash.

**Gotchas:**
- The lock cache uses an `OrderedDict` with LRU eviction at `_MAX_THREAD_LOCKS = 10_000`. Long-running systems with many unique thread IDs could evict locks, but the worst case is a lock re-created per thread (no correctness issue, only memory churn).
- The manager uses WAL mode (`PRAGMA journal_mode=WAL`) and `PRAGMA synchronous=NORMAL` for concurrent reads.

### 2.3 Retry / Auth Error Mapping (401/403 Friendly Messages)

**Commit:** `227215f` (`Use runtime LLM config at startup and clarify auth failures`).

**Files affected:** `kazma-core/kazma_core/retry.py`, `kazma-core/kazma_core/llm_provider.py`, `kazma-ui/kazma_ui/sse_chat.py`, `kazma-ui/kazma_ui/app.py`.

**What changed:**
- `retry.py` gained `friendly_llm_error()` and `friendly_tool_error()`.
- These functions extract HTTP status codes from exception chains and map 401/403 to a clear message: "The model request was rejected due to an invalid or missing API key. Go to Settings > Models/Providers and update your credentials."
- Connection errors and timeouts are mapped to "The model service is unavailable. Please try again in a moment."
- The SSE chat router now does a pre-stream API key validation check: if the provider is a real cloud URL and the configured key is empty/`not-needed`, it returns an immediate SSE error frame with the same help message.

**Why:** Users previously saw raw `httpx` exceptions or 401 deep in the graph. The friendly mapping routes users to the correct fix (update credentials) and avoids exposing raw stack traces.

**Gotchas:**
- `retry_llm_call` does NOT retry on 4xx errors. Auth failures are surfaced immediately after retries are exhausted.
- `_extract_http_status_code` scans the exception chain and also parses string messages for "401" and "403" to catch providers that do not set `response.status_code`.

### 2.4 Runtime LLM Config Hydration

**Commit:** `227215f` (same as auth mapping). Also `86caa4a` / `f80c8c0` (unify provider registry across settings, swarm, CLI, TUI).

**Files affected:** `kazma-ui/kazma_ui/sse_chat.py`, `kazma-ui/kazma_ui/app.py`, `kazma-ui/kazma_ui/models_route.py`, `kazma-core/kazma_core/model_registry.py`, `kazma-core/kazma_core/settings_manager.py`.

**What changed:**
- The SSE chat endpoint (`POST /api/chat/stream`) now reads a `model` field from the request body and calls `registry.set_active_model()` and `llm_provider.reconfigure()` before streaming.
- `kazma_ui/app.py` creates the `ConfigStore` and calls `initialize_model_registry(config_store)` before constructing the `KazmaAgent`, ensuring the agent's call to `get_model_registry()` succeeds.
- The provider switch endpoint (`POST /api/provider/switch`) routes through `ModelRegistry.set_active_provider()` and reconfigures the live `llm_provider` so the next chat turn uses the new model without restarting the server.

**Why:** Previously, saving settings in the UI updated the ConfigStore but the running graph continued using a stale LLM client. Runtime hydration closes that gap.

**Gotchas:**
- `set_active_model()` invalidates the cached client for the active provider so the next `get_client()` picks up the new model.
- `llm_provider.reconfigure()` is called on the live instance owned by the agent/graph so in-flight streams are not disrupted, but the next turn uses the new config.
- The TUI is read-only and never calls these mutation methods. It only displays the active profile via `HeaderProviderModel`.

### 2.5 Unified ModelRegistry Singleton Refactor

**Commits:**
- `0ff7b73` — feat(core): implement singleton ModelRegistry as single source of truth for providers.
- `02c6621` — fix(core): SettingsManager uses local ModelRegistry instead of global singleton.
- `64abcf1` — fix(tests): initialize ModelRegistry singleton in test_service_facade.
- `7173051` — refactor(core): route all LLM client creation through ModelRegistry.
- `944b3f1` — refactor(cli): route model/provider lookups through ModelRegistry.
- `944b3f1` — refactor(ui): route all model/provider logic through ModelRegistry.
- `86caa4a` / `f80c8c0` — Unify provider and model registry across settings, swarm, CLI, and TUI.

**Files affected:** `kazma-core/kazma_core/model_registry.py`, `kazma-core/kazma_core/settings_manager.py`, `kazma-ui/kazma_ui/models_route.py`, `kazma-ui/kazma_ui/settings.py`, `kazma-ui/kazma_ui/sse_chat.py`, `kazma-ui/kazma_ui/swarm_panel.py`, `kazma-cli/kazma_cli/completions.py`, `kazma-cli/kazma_cli/main.py`, `tests/conftest.py`, `tests/test_model_registry.py`, `tests/test_settings.py`, `tests/test_service_facade.py`.

**What changed:**
- Replaced the previous distributed/legacy provider config with a single module-level singleton `_registry` in `kazma_core/model_registry.py`.
- Public lifecycle functions: `initialize_model_registry(config_store)`, `get_model_registry()`, `reset_model_registry()` (tests only).
- All LLM client creation in the agent and tools was routed through `registry.get_client()` or `registry.get_model()`.
- The Web UI settings page, models route, SSE chat, and swarm panel were updated to read/write providers only through the registry.
- The CLI completions module was updated to read available models/providers from `list_unified_options()`.
- `tests/conftest.py` added an autouse fixture `_init_model_registry` that creates a fresh `ConfigStore` in a temp path and initializes the registry before every test.

**Why:** Centralization prevents split-brain provider state, duplicated LLM clients, stale API keys, and inconsistent model lists between CLI/Web/TUI.

**Gotchas:**
- Calling `get_model_registry()` before `initialize_model_registry()` raises `RuntimeError` with a clear message.
- The registry caches clients by provider name. Mutating a provider entry invalidates only that provider's cached client.
- The registry is backward-compatible with legacy `llm.base_url`, `llm.api_key`, and `llm.model` keys; it falls back to these when no active provider is set.
- `UnifiedModelRegistry` is kept as an alias to `ModelRegistry` for old imports.

### 2.6 TUI Replacement

**Commits:**
- `341799f` — chore(tui): delete old TUI directory and archive router.py.
- `2a1433f` — feat(tui): create TUI foundation with Textual app, package structure, and entry point.
- `9325982` — feat(tui): add header with provider/model info and footer with keyboard shortcuts.
- `dcccdd2` — feat(tui): add metrics dashboard with CPU/RAM/RPM/latency/error rate/agents.
- `0525b02` — feat(tui): implement chat interface with input, messages, and commands.
- `99f644a` — Add comprehensive TUI tests for all components and integration flows.
- `a6ac8e6` — Add MetricCard widget and VRAM metrics.
- `a69c332` — fix: add mypy override for kazma_core imports and remove unused type: ignore.

**Files affected:**
- Deleted: old Arabic-focused Textual-based TUI (removed from repository).
- Archived: `kazma-providers/router.py` moved to `archive/kazma-providers/`.
- New: `kazma-tui/kazma_tui/__init__.py`, `__main__.py`, `app.py`, `dashboard.py`, `chat.py`, `header.py`, `footer.py`.
- New tests: `kazma-tui/tests/test_comprehensive.py`, `test_dashboard.py`, `test_chat.py`, `test_header_footer.py`, `test_foundation.py`, `test_app_async.py`.
- Updated: `pyproject.toml` (entry point `kazma-tui`, `textual>=8.0.0` dependency).

**What changed:**
- Built a new Textual-based TUI from scratch with a vertical layout: header, metrics dashboard, chat panel, footer.
- `HeaderProviderModel` reads the active provider/model from `ModelRegistry` and displays "Kazma TUI | provider / model".
- `MetricsDashboard` displays 6 metrics in a 3x2 grid, refreshed every 2 seconds via `set_interval`.
- `ChatPanel` provides a scrollable message log and an input field, with slash commands `/help`, `/clear`, `/quit`.
- `FooterShortcuts` shows `Ctrl+Q Quit | Tab Switch | Enter Send`.
- `MetricCard` color-codes values: normal (green), warning (yellow), critical (red).
- `VRAM (GB)` was added to the dashboard using `HardwareMonitor` GPU/VRAM metrics via `nvidia-smi`.

**Why:** The old Arabic-focused Textual-based TUI was Arabic-only, hard to maintain, and did not integrate with the new metrics infrastructure. Textual provides CSS-styled widgets, reactive attributes, and a modern Python API.

**Gotchas:**
- The TUI is English-only. Do not add Arabic or RTL markers.
- `ModelRegistry` is read-only in the TUI. The header never calls mutation methods.
- The dashboard refresh interval is 2 seconds. If `HardwareMonitor`, `TraceStore`, `MetricsCollector`, or `SwarmEngine` are unavailable, the widget falls back to "N/A" without crashing.
- `MetricsDashboard` uses `asyncio.get_event_loop()` and `asyncio.ensure_future()` to update hardware metrics asynchronously. If the event loop is not running, it falls back to `loop.run_until_complete()` in tests.

### 2.7 VRAM Metric Integration

**Commit:** `a6ac8e6` (`Add MetricCard widget and VRAM metrics`).

**Files affected:** `kazma-tui/kazma_tui/dashboard.py`, `kazma-core/kazma_core/telemetry.py`, `tests/test_telemetry.py`.

**What changed:**
- Added `MetricCard` widget with `status` (normal/warning/critical) and color-coded Rich markup.
- Added a `VRAM (GB)` card to `MetricsDashboard` showing `used / total` GB.
- `_determine_vram_status()` marks critical when usage >90%, warning when >70%, normal otherwise.
- `HardwareMonitor` parses `nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits` and returns GPU/VRAM metrics. If `nvidia-smi` is missing or fails, it returns zeros gracefully.

**Why:** GPU/VRAM is critical for edge inference monitoring (e.g., Ollama/LM Studio on local GPUs).

**Gotchas:**
- Non-NVIDIA systems will show `VRAM: N/A` or `0.0 / 0.0 GB` because the adapter falls back to zeros.
- `nvidia-smi` is invoked as an async subprocess. If the subprocess hangs, it times out after 5 seconds and returns zeros.
- The TUI uses `HardwareMonitor` through `MetricsDashboard._get_hardware_monitor()` with lazy initialization.

### 2.8 Swarm Task Persistence, Active Tasks, Results Dashboard, and Modal Fixes

**Commit:** `4ba0700` (`fix(swarm): persist tasks, flatten results, and fix active task/modal UI bugs`).

**Files affected:** `kazma-core/kazma_core/swarm/task_store.py`, `kazma-core/kazma_core/swarm/engine.py`, `kazma-ui/kazma_ui/swarm_panel.py`, `kazma-ui/kazma_ui/swarm_sse.py`, `kazma-ui/kazma_ui/templates/swarm.html`, `kazma-ui/kazma_ui/static/js/swarm.js`, `tests/test_swarm_task_store.py`, `tests/test_swarm_ui_panel.py`, `tests/test_swarm_cross_flows.py`.

**What changed:**
- `TaskStore` persists swarm tasks to `kazma-data/swarm_tasks.db` with two tables: `swarm_tasks` (id, type, prompt, status, workers, result, timestamps, cost, tokens, metadata) and `swarm_worker_metrics` (worker, date, tasks_completed, tasks_failed, avg_latency, total_tokens, total_cost; PK worker+date).
- Tasks are persisted on terminal states (completed, failed, timeout) and paused HITL states. Worker metrics are aggregated daily.
- `SwarmEngine` automatically initializes `TaskStore` and calls `persist_task()` in `_finalize_task()`. Paused pipelines are restored on startup via `restore_paused_tasks()`.
- `kazma_ui/swarm_panel.py` added `_flatten_swarm_task()` because `SwarmTask.to_dict()` nests result fields under a `result` key, but the UI dashboard expects `task_id`, `status`, `worker_results`, `aggregated_output`, `synthesized_output`, `duration_seconds`, `total_cost`, `total_tokens`, etc., at the top level.
- The Results Dashboard in the web UI now displays pattern-specific views (pipeline step-by-step, fan-out per-worker grid, consult side-by-side comparison + synthesized answer, conditional routing decision) and sub-tab filtering by pattern.
- Active Tasks cards are created optimistically when a task is dispatched and updated via SSE. The modal for task details was fixed by ensuring the task detail endpoint (`GET /api/swarm/tasks/{id}`) returns the flattened shape and the JS modal builder reads from the correct keys.

**Why:** Without persistence, task history and worker metrics were lost on restart. Without flattening, the UI showed empty results and non-clickable modals.

**Gotchas:**
- The in-memory `_task_history` in `SwarmEngine` is a cache. `TaskStore` is the source of truth for history across restarts.
- `restore_paused_tasks()` reads paused tasks from SQLite and repopulates the `HITLCheckpointHandler` on engine startup.
- The `_flatten_swarm_task()` helper is the canonical bridge between the core `SwarmTask` model and the web UI. Any new field that the UI needs must be promoted there.
- The SSE event bus (`SSEEventBus`) is monkey-patched onto the `SwarmEngine` via `wire_engine_events()` in `kazma_ui/swarm_sse.py`. If events are missing, verify that the engine was wired before the task started.

### 2.9 Additional Fixes and Refactors Discovered in the Codebase

- **Architecture change: Tantivy -> SQLite FTS5** (`12e7876`, `8b7fedd`, `08f88fe`, `a2922b5`, `3fc297d`, `de3202c`, `d352660`). Removed the Rust/maturin dependency. Replaced `kazma_memory/tantivy_backend.py`, `migration.py`, `benchmark.py`, `report_store.py` with SQLite FTS5 + `sqlite-vec` in `kazma_memory/search_backend.py` and `arabic_tokenizer.py`. Added 20 SQLite search tests. This is documented in `ARCHITECTURE_CHANGE.md`.
- **Service facade / zero private attribute access** (`a80b806`, `a87c8e2`). The UI was refactored to use a service facade instead of reaching into private attributes. Cross-area integration tests (`tests/test_swarm_cross_flows.py`) were added to cover pipeline HITL, consult partial failure, fan-out fallback, and unified registry assertions.
- **Cron double-fire guard** (`f57eeae`). Added `_in_flight` set to `CronScheduler` so long-running jobs are not dispatched twice if the poll interval fires before completion.
- **ReAct iteration counter** (`91cb17a`). Fixed `supervisor_node` to increment the iteration counter on the tool-call path.
- **Knowledge Graph edge attribute consistency** (`ff4c0e1`). Renamed `relation_type` to `relation` across engine and adapter so queries and exports are consistent.
- **Settings export content-type** (fixed in `kazma-ui/kazma_ui/settings.py`). The `/api/settings/export` endpoint returns `text/yaml` with a `Content-Disposition` attachment header. This was a deferred bug in `BUG_FIX_TASK.md`.
- **Checkpoint concurrency lock** (fixed in `kazma-gateway/kazma_gateway/stores/checkpoint.py`). Added an `asyncio.Lock` to prevent concurrent `aput` / `aput_writes` initialization from causing `database is locked` errors. This was another deferred bug in `BUG_FIX_TASK.md`.
- **Dark mode dropdown contrast, model selection pipeline, bilingual language system** (remediation-R2, Sprint 9). WCAG-compliant dropdowns, chat model selector with SSE model passthrough, EN/AR cookie middleware with 150+ Arabic translations.
- **Architecture remediation** (Sprint 8): race condition fixes, dead code removal, `UnifiedToolExecutor`, unified session stores, HITL approval UI, session history loading, Windows `setup.ps1`, PowerShell completions, portable paths, env var configuration, Cairo Arabic font.

### 2.10 Unified Providers & Connectors Hub

**Commit:** `f3b0945` (`feat(ui): unified Providers & Connectors management hub`).

**Files affected:** `kazma-ui/kazma_ui/providers.py` (new), `kazma-ui/kazma_ui/models.py`, `kazma-ui/kazma_ui/app.py`, `kazma-ui/kazma_ui/templates/settings.html`, `kazma-ui/kazma_ui/static/js/settings.js`, `kazma-ui/kazma_ui/i18n.py`, `tests/test_settings.py`, `docs/TROUBLESHOOTING.md`.

**What changed and why:**
- Created a single unified hub (`providers.py`) for managing all LLM providers AND platform connector tokens (Telegram, Discord, Slack, Email, Webhook) through a consistent API.
- LLM provider operations are backed by `ModelRegistry` methods (`list_providers()`, `upsert_provider()`, `delete_provider()`, `toggle_provider()`, `set_provider_health()`, `discover_models()`, `list_model_profiles()`, `save_model_profile()`, `delete_model_profile()`).
- Platform connector operations are backed by `ConfigStore` with keys under `connectors.{name}.*`.
- All secrets (API keys and connector tokens) are masked with `****XXXX` (last 4 chars) before being sent to the UI. The masking helper `_mask_secret()` handles values shorter than 4 characters with `***`.
- Masked-placeholder preservation: when the UI sends back a masked value (e.g., `****1234`), the backend detects it via `_is_masked_placeholder()` and preserves the existing secret instead of overwriting it with the placeholder.
- Test-before-save enforcement: the frontend disables the **Save** button until **Test Connection** succeeds. Providers are tested via `GET {base_url}/models` with Bearer auth; connectors are tested with platform-specific health checks (Telegram `getMe`, Discord `users/@me`, Slack `auth.test`).
- New Pydantic models in `models.py`: `ProviderUpdateRequest`, `ConnectorUpdateRequest`, `ProviderTestResponse`, `ConnectorTestResponse`, `ModelProfileUpdateRequest`, `MaskedSecretResponse`.
- Comprehensive integration tests added in `tests/test_settings.py` (`TestUnifiedProvidersRouterAPI` class) covering CRUD, masking, secret preservation, toggle, discover, and connector test-missing-token scenarios.
- Troubleshooting guide updated with a new "Provider & Connector Connectivity" section (section 2a) covering masked-secret issues, test-before-save behavior, and platform-specific test diagnostics.

**Key features:**
- **API key masking**: `****XXXX` pattern (last 4 chars) for all secrets.
- **Test-before-save**: Save disabled until connection test succeeds.
- **Masked-placeholder preservation**: Editing a provider with `****1234` in the key field keeps the original secret.
- **Unified CRUD**: Single set of endpoints for providers, connectors, and model profiles.
- **14 total endpoints**: 6 for providers, 3 for model profiles, 5 for connectors.

**Gotchas:**
- The `create_providers_router(config_store)` factory requires a `ConfigStore` instance for connector persistence. The `ModelRegistry` singleton must be initialized before the router is mounted.
- `_CONNECTOR_PLATFORMS` is a fixed tuple: `("telegram", "discord", "slack", "email", "webhook")`. Adding a new platform connector type requires updating this constant.
- The connector test for generic platforms (email, webhook) always returns `success: True` with a "Token configured" message since there is no standard health-check endpoint.
- Provider health status is stored via `registry.set_provider_health(name, status)` where status is `"healthy"`, `"degraded"`, or `"down"`. This is used by the UI to color-code provider cards.

---

## 3. Repository Map (Detailed)

This section breaks down every package and directory. For each, it lists responsibilities, key files, public APIs, and dependencies.

### 3.1 `kazma-core/`

The core library. Everything else depends on it. Entry point package is `kazma_core`.

#### 3.1.1 Submodules

- **`agent/`**: ReAct supervisor graph, graph builder, state definitions, sub-agent manager, tool registry.
  - `agent/__init__.py`: Exports `KazmaAgent`, `AgentConfig`, `load_config`.
  - `agent/graph_builder.py`: `build_supervisor_graph()`, `supervisor_node()`, `tool_worker_node()`. Compiles the LangGraph app with checkpointing.
  - `agent/state.py`: `SupervisorState`, `NodeName`, `initial_supervisor_state()`.
  - `agent/sub_agent.py`: `SubAgentManager`, `set_sub_agent_manager()`, `get_sub_agent_manager()`.
  - `agent/tool_registry.py`: `LocalToolRegistry`, `UnifiedToolExecutor`, tool schema generation.

- **`swarm/`**: Multi-worker orchestration engine.
  - `swarm/engine.py`: `SwarmEngine`, `get_swarm_engine()`, `set_swarm_engine()`. Central async orchestrator with 6 patterns and reliability layer.
  - `swarm/patterns.py`: `execute_pipeline()`, `resume_pipeline()`, `execute_fan_out()`, `execute_conditional()`, `PatternExecution`.
  - `swarm/consultation.py`: `execute_consult()`, `ConsultationConfigurationError`.
  - `swarm/router.py`: `CapabilityRouter`, `NoCapableWorkersError`.
  - `swarm/task.py`: `SwarmTask`, `TaskResult`, `TaskStatus`, `TaskType`, `WorkerResult`, `WorkerCapabilities`, `HandoffRecord`.
  - `swarm/task_store.py`: `TaskStore` (SQLite persistence).
  - `swarm/metrics.py`: `MetricsCollector`, `WorkerMetricSnapshot`.
  - `swarm/tracing.py`: `TracingEmitter`, OpenTelemetry-compatible spans.
  - `swarm/reliability.py`: `RetryPolicy`, `CircuitBreaker`, `TimeoutGuard`, `OutputValidator`, `FallbackChain`, `BoundedConcurrency`.
  - `swarm/worker.py`: `SwarmWorker` (abstract), `InProcessWorker`.
  - `swarm/checkpoint.py`: `HITLCheckpoint`, `HITLCheckpointHandler`.
  - `swarm/blackboard.py`: `BlackboardStore`, `SwarmDispatchContext`.
  - `swarm/aggregator.py`: `ResultAggregator`, aggregation strategies (`first_valid`, `merge_all`, `vote`, `synthesize`, `collect`).
  - `swarm/config.py`: `SwarmConfig`, `WorkerConfig`.
  - `swarm/manager.py`: `SwarmManager` (backward-compatible facade over `SwarmEngine`).
  - `swarm/handoff.py`: `HandoffRequest`, `request_handoff()`.
  - `swarm/memory/`: Memory subsystem backing per-turn RAG and the memory tools. Includes `embedder.py` (**pluggable embeddings** — local `sentence-transformers` or any OpenAI-compatible `/embeddings` endpoint such as NVIDIA NIM/NeMo Retriever, configured under `memory.embedding` in `kazma.yaml`), `adapter.py` (retrieval adapter over the shared `agent_memory` ChromaDB collection), `fts5.py`, `sqlite_vec.py`, `graph.py`.

- **`tools/`**: Built-in tools.
  - `file_read.py`, `file_write.py`, `web_search.py`, `read_url.py`, `image_gen.py`, `vision_analyze.py`, `code_exec.py`, `send_message.py`, `export_session.py`, `context_cmd.py`, `personality_cmd.py`.

- **`memory/`**: RAG memory helpers.
  - `vector_store.py`: `VectorMemory` (ChromaDB + sentence-transformers; optional `rag` extra). Backs the unified `agent_memory` collection shared by both the memory tools and per-turn RAG retrieval. As of v0.5.0 the agent retrieves memories every turn (iteration 0), not just at compaction; tools and RAG now read/write the same persistent `agent_memory` collection (previously split into an ephemeral `kazma_global` collection that caused a silent write/read split).
  - `fts5.py`: FTS5 helper functions.

- **`ide/`**: **NEW (v0.5.0).** Transport-agnostic coding backend powering the Web IDE page (`/ide`) and the TUI editor. Cross-platform `/ide` commands (Windows + Unix).
  - `env_context.py`: Workspace environment context (cwd, env vars, repo identity) surfaced to the agent for code tasks.
  - `service.py`: The IDE service that coordinates file operations, test runs, and agent-driven edits independently of the frontend transport (Web or TUI).
  - `workspace_scope.py`: Workspace scoping/boundaries that confine file and command operations to the active project root.

- **`hub/`**: Skill marketplace (Kazma Hub).
  - `api.py`, `cli.py`, `loader.py`, `registry.py`, `validator.py`, `manifest_schema.py`, `versioning.py`, `badges.py`.

- **`security/`**: Security tooling.
  - `linter.py`, `dependency_scanner.py`, `disclosure.py`, `hardening.py`, `certification.py`, `audit_trail.py`, `ssrf.py`.

- **`delegation/`**: Multi-agent orchestration protocol.
  - `protocol.py`, `discovery.py`, `orchestrator.py`, `security.py`, `swarm.py`.

- **`cron/`**: Scheduled agent actions.
  - `scheduler.py`: `CronScheduler`, `SQLiteCronStore`.

- **`safety/`**: HITL approval gates.
  - `hitl.py`: HITL configuration and approval logic.

- **`mcp/`**: Model Context Protocol.
  - `manager.py`: MCP server manager.

- **`models/`**: Model definitions and discovery.
  - `models/__init__.py`, `models/router.py`, `models/discovery.py`.

- **`cli/`**: CLI helpers.
  - `wizard.py`: Interactive skill installation wizard.

- **`docs/`**: Documentation generator.
  - `docs/__init__.py`.

#### 3.1.2 Top-Level Core Files

- `model_registry.py`: **CRITICAL.** `ModelRegistry` singleton. Functions: `initialize_model_registry`, `get_model_registry`, `reset_model_registry`. Classes: `ModelRegistry`.
- `config_store.py`: **CRITICAL.** `ConfigStore` (SQLite-backed runtime config with YAML fallback).
- `telemetry.py`: **CRITICAL.** `HardwareMonitor`, `TelemetrySnapshot`, `parse_nvidia_smi_output()`.
- `tracing.py`: **CRITICAL.** `TraceStore`, `TraceEntry`, `KazmaTracer`, `TracingBackend`, `create_tracer()`.
- `retry.py`: **CRITICAL.** `retry_llm_call`, `retry_tool_call`, `friendly_llm_error`, `friendly_tool_error`, `load_retry_config`.
- `llm_provider.py`: `LLMConfig`, `LLMProvider`, `LLMResponse`, `ToolCall`.
- `providers.py`: `PROVIDER_PRESETS`, `list_providers()`.
- `agent_runner.py`: `run_agent()`, older agent runner interface.
- `settings_manager.py`: `SettingsManager` (reads/writes via ConfigStore).
- `cost_breaker.py`: `CostCircuitBreaker`.
- `authority.py`: `ContextAuthority` (80% compaction threshold).
- `compaction.py`: Auto-summarization logic.
- `state.py`: `AgentState`.
- `time_travel.py`: Snapshot-based replay engine.
- `shutdown.py`: `signal_shutdown()`, `is_shutting_down()`.
- `url_utils.py`: `normalize_provider_url()`, `get_dummy_api_key()`.
- `token_counter.py`, `tokenizer.py`, `kuwaiti_tokenizer.py`, `msa_tokenizer.py`, `dialect_detector.py`, `cultural_context.py`, `tone_adapter.py`: Language and tokenization support.
- `audit_logger.py`, `authorization_flow.py`, `rbac.py`, `permissions.py`, `division_sandbox.py`, `tool_sandbox.py`: Security and RBAC.
- `mcp_client.py`: MCP client.
- `streaming.py`: Streaming utilities.
- `summarizer.py`: Summarization utilities.
- `majlis.py`: Multi-agent negotiation protocol.
- `pacing.py`: Rate pacing.
- `personality.py`: Personality system.
- `router.py`: Legacy routing.
- `docs.py`: Doc generation.

#### 3.1.3 Public APIs and Dependencies

- **Public APIs**: `initialize_model_registry`, `get_model_registry`, `ConfigStore`, `HardwareMonitor`, `get_trace_store`, `KazmaAgent`, `SwarmEngine`, `TaskStore`, `MetricsCollector`, `GatewayManager` (re-exported via `kazma_gateway`).
- **Dependencies**: `fastapi`, `uvicorn`, `langgraph`, `langgraph-checkpoint-sqlite`, `aiosqlite`, `httpx`, `pydantic`, `tenacity`, `psutil`, `textual`, `aiogram`, `websockets`, `duckduckgo-search`, `trafilatura`, `networkx`, `click`, `rich`, `langfuse`, `opentelemetry-*`, `pyyaml`, `cryptography`, `jinja2`, `python-multipart`, `markdown`.
- **Optional extras**: `dev` (pytest, ruff, mypy), `tui` (textual), `rag` (chromadb, sentence-transformers).

### 3.2 `kazma-gateway/`

Headless gateway and adapters.

- **`kazma_gateway/gateway.py`**: `GatewayManager`, `BaseAdapter`, `IncomingMessage`, `OutboundMessage`, `RateLimiter`, `MessageMetrics`, `SessionStore`.
- **`kazma_gateway/adapters/telegram.py`**: `TelegramAdapter` (polling + optional webhook ingress, voice transcription, emoji reactions, inline keyboard callbacks).
- **`kazma_gateway/adapters/discord.py`**: `DiscordAdapter`.
- **`kazma_gateway/adapters/slack.py`**: `SlackAdapter` (polling-based Web API — no Socket Mode).
- **`kazma_gateway/stores/checkpoint.py`**: `CheckpointManager`, `create_checkpoint_manager()`, `create_checkpointer()`.
- **`kazma_gateway/stores/sqlite.py`**: `SQLiteSessionStore`.
- **`kazma_gateway/agent_handler.py`**: `create_graph_handler()`, `_build_initial_state()`. Bridges `IncomingMessage` to the LangGraph brain and routes replies back through the gateway.
- **`kazma_gateway/slash_commands.py`**: 12 Telegram slash commands (`/help`, `/reset`, `/status`, `/model`, etc.).
- **`kazma_gateway/rate_feedback.py`**: Rate limit feedback messages.
- **`kazma_gateway/suggestions.py`**: Proactive next-step hints.
- **`kazma_gateway/swarm_notify.py`**: Telegram group notifications for swarm progress.
- **`kazma_gateway/mcp_server.py`**: MCP server for IDE integration.
- **`kazma_gateway/__init__.py`**: Exports `GatewayManager`.
- **`tests/`**: Gateway-specific tests (bounded queue, lifecycle, status).

**Public APIs**: `GatewayManager`, `BaseAdapter`, `IncomingMessage`, `OutboundMessage`, `TelegramAdapter`, `DiscordAdapter`, `SlackAdapter`, `create_checkpoint_manager`, `SQLiteSessionStore`, `create_graph_handler`.

**Dependencies**: `kazma-core`, `fastapi`, `httpx`, `aiogram` (for some utilities), `websockets`.

### 3.3 `kazma-ui/`

FastAPI web application.

- **`kazma_ui/app.py`**: **CRITICAL.** FastAPI app factory. Wires config, ModelRegistry, agent, SSE chat, telemetry, dashboard, models route, workspace API, swarm panel, gateway, MCP, cron, health, HITL approval, and graceful shutdown.
- **`kazma_ui/sse_chat.py`**: `create_sse_chat_router()`. Streams LangGraph `astream_events` as SSE frames (`token`, `tool_call`, `tool_result`, `done`, `error`).
- **`kazma_ui/chat.py`**: WebSocket chat handler.
- **`kazma_ui/dashboard.py`**: Dashboard route, `set_dashboard_context()`, `set_templates()`.
- **`kazma_ui/settings.py`**: 12-tab settings page (models, connectors, appearance, tools, MCP, skills, etc.).
- **`kazma_ui/providers.py`**: **NEW.** Unified providers & connectors router with 14 endpoints. Single hub for LLM provider CRUD, platform connector token management, model profile CRUD, and connection testing. Backed by `ModelRegistry` for providers and `ConfigStore` for connectors. All secrets masked with `****XXXX`. Factory: `create_providers_router(config_store)`.
- **`kazma_ui/models_route.py`**: `/api/models`, `/api/ollama/*`, model discovery and provider switching.
- **`kazma_ui/swarm_panel.py`**: Swarm Panel router (`/swarm`, `/api/swarm/*`). Dispatches tasks, manages workers, history, metrics, circuit breakers, HITL checkpoints.
- **`kazma_ui/swarm_sse.py`**: `SSEEventBus`, `create_sse_router()`, `wire_engine_events()`. Real-time swarm task SSE streaming.
- **`kazma_ui/gateway_monitor.py`**: `create_gateway_router()` for `/api/gateway/*` status/metrics.
- **`kazma_ui/telemetry_route.py`**: `create_telemetry_router()` for `/api/telemetry/stream` (real hardware metrics SSE).
- **`kazma_ui/metrics.py`**: `create_metrics_router()` for Prometheus `/metrics` endpoint.
- **`kazma_ui/auth.py`**: `create_auth_middleware()` enforcing `KAZMA_SECRET` on sensitive endpoints.
- **`kazma_ui/i18n.py`**: `make_translator()` for EN/AR bilingual support.
- **`kazma_ui/session_manager.py`**: Shared in-memory session store for chat history across SSE and WebSocket transports.
- **`kazma_ui/hitl_approval.py`**: `_get_pending_approvals()` for enumerating interrupted threads from checkpoint DB.
- **`kazma_ui/workspace_api.py`**: File browser API for the workspace tab.
- **`kazma_ui/mcp_ui.py`**: MCP management UI routes.
- **`kazma_ui/skills_ui.py`**: Skill management UI routes.
- **`kazma_ui/agents.py`**: Agents page routes.
- **`kazma_ui/models.py`**: Model/profile data models (deprecated in favor of ModelRegistry).
- **`kazma_ui/templates/`**: Jinja2 templates (dashboard, chat, settings, swarm, agents, workspace, error, etc.).
- **`kazma_ui/static/`**: CSS, JS (Alpine.js, Chart.js, HTMX, i18n, dark mode).
- **`kazma_ui/__main__.py`**: Module entry point.

**Public APIs**: `create_app(config_path)`, `create_sse_chat_router()`, `create_swarm_router()`, `create_models_router()`, `create_gateway_router()`, `create_telemetry_router()`.

**Dependencies**: `kazma-core`, `kazma-gateway`, `fastapi`, `uvicorn`, `jinja2`, `python-multipart`, `httpx`, `websockets`, `markdown`.

### 3.4 `kazma-cli/`

Command-line interface.

- **`kazma_cli/main.py`**: CLI entry point. Subcommands: status, serve, wizard, hub, docs, completion, project, gateway, swarm, update.
- **`kazma_cli/gateway.py`**: `kazma gateway` subcommands (status, start, stop, restart, refresh).
- **`kazma_cli/swarm.py`**: `kazma swarm` subcommands (status, workers, worker add/spawn/remove, dispatch, broadcast, consult, pipeline, fanout, history, task, metrics, start, stop, approve, reject, circuit-breaker).
- **`kazma_cli/update.py`**: `kazma update` self-updater (PyPI or git/editable).
- **`kazma_cli/project.py`**: `kazma project init/show/validate`.
- **`kazma_cli/banner.py`**: Startup banner, config checks, status overview.
- **`kazma_cli/completions.py`**: Shell tab completion for bash, zsh, PowerShell; model/provider listing.
- **`kazma_cli/__init__.py`**: Package version.

**Public APIs**: `main()`.

**Dependencies**: `kazma-core`, `kazma-ui`, `kazma-gateway`, `click`, `rich`, `httpx`.

### 3.5 `kazma-tui/`

New Textual terminal UI.

- **`kazma_tui/app.py`**: `KazmaTUI` App class, `main()` entry point. Composes header, dashboard, chat, footer.
- **`kazma_tui/dashboard.py`**: `MetricsDashboard` with 6 `MetricCard` widgets. Refresh interval 2 seconds. Sources: `HardwareMonitor`, `TraceStore`, `MetricsCollector`, `SwarmEngine`.
- **`kazma_tui/header.py`**: `HeaderProviderModel` (read-only ModelRegistry consumer).
- **`kazma_tui/footer.py`**: `FooterShortcuts` (Ctrl+Q, Tab, Enter).
- **`kazma_tui/chat.py`**: `ChatPanel` with message log, input, slash commands.
- **`kazma_tui/__init__.py`**: Package version.
- **`kazma_tui/__main__.py`**: Module entry point.
- **`tests/`**: TUI tests.

**Public APIs**: `main()`, `KazmaTUI`, `MetricsDashboard`, `ChatPanel`, `HeaderProviderModel`, `FooterShortcuts`, `MetricCard`.

**Dependencies**: `kazma-core`, `textual`, `psutil`.

**Constraints (from `AGENTS.md`)**:
- Only modify `kazma-tui/`.
- Use only `textual`.
- Read-only ModelRegistry.
- English-only UI text.
- TDD.

### 3.6 `kazma-providers/`

Provider configuration package.

- **`kazma_providers/__init__.py`**: Minimal package. Most provider logic has moved to `kazma-core/kazma_core/providers.py` and `kazma-core/kazma_core/model_registry.py`.
- **`archive/kazma-providers/`**: Contains `router.py` which was archived during the ModelRegistry refactor.

**Public APIs**: None significant. Do not add new provider logic here; use `ModelRegistry`.

### 3.7 `kazma-memory/`

Memory/context management.

- **`kazma_memory/__init__.py`**: Exports `SearchBackend`, `SQLiteMemoryBackend`, `ArabicTokenizer`.
- **`kazma_memory/search_backend.py`**: `SearchBackend` (FTS5 + sqlite-vec hybrid, BM25 ranking, Arabic tokenization bridge).
- **`kazma_memory/arabic_tokenizer.py`**: `ArabicTokenizer` (Kuwaiti + MSA normalization, stemming, stop-word removal). Backward-compatible alias `ArabicTantivyTokenizer`.

**Public APIs**: `SearchBackend`, `SQLiteMemoryBackend`, `ArabicTokenizer`.

**Dependencies**: `sqlite-vec` (if used), `kazma-core`.

### 3.8 `kazma-skills/`

Skill definitions.

- **`kazma_skills/__init__.py`**: Package init.
- **`kazma_skills/manifest.py`**: Skill manifest helpers.
- **`manifests/`**: Skill manifest files.

**Public APIs**: Manifest helpers.

### 3.9 `tests/`

Test infrastructure.

- **`tests/conftest.py`**: Shared pytest fixtures. Autouse `_init_model_registry` fixture initializes `ModelRegistry` for each test. Also imports `kazma_ui.i18n` early so Jinja2 templates get the `t` global.
- **`tests/unit/`**: Unit tests.
- **`tests/integration/`**: Integration tests.
- **100+ test files** covering all packages.

**Public APIs / Fixtures**: `_init_model_registry`, `agent_config`, `agent`.

### 3.10 `archive/`

Deprecated/archived code.

- **`archive/kazma-comms/`**: Archived communications package.
- **`archive/kazma-connectors/`**: Archived connectors package.
- **`archive/kazma-providers/`**: Archived `router.py` and other provider code superseded by `ModelRegistry`.

**Rule**: Do not restore archived code. It is kept for reference only.

### 3.11 Root Config Files

- **`pyproject.toml`**: Project metadata, dependencies, entry points, build config (hatchling), pytest/ruff/mypy settings. Key sections: `[project]`, `[project.optional-dependencies]`, `[project.scripts]`, `[build-system]`, `[tool.hatch.build.targets.wheel]`, `[tool.pytest.ini_options]`, `[tool.ruff]`, `[tool.mypy]`.
- **`kazma.yaml`**: Main runtime configuration. Sections: `agent`, `models`, `llm`, `mcp`, `system_prompt`, `storage`, `memory`, `skills`, `connectors`, `gateway`, `safety`, `ui`, `logging`, `time_travel`, `swarm`.
- **`docker-compose.yml`**: Docker deployment with two services and volumes.
- **`.env.example`**: Template for environment variables (tokens, API keys, `KAZMA_SECRET`, `KAZMA_WORKSPACE`, etc.).
- **`AGENTS.md`**: Mission guidance (golden rules).
- **`CONTRIBUTING.md`**: Contribution guidelines.
- **`CHANGELOG.md`**: Sprint-by-sprint changelog.
- **`ARCHITECTURE_CHANGE.md`**: Tantivy -> SQLite FTS5 migration notes.
- **`BUG_FIX_TASK.md`**: Bug fix task status and deferred items.
- **`architecture.md`**: TUI replacement architecture overview.

---

## 4. Architectural Deep Dive

### 4.1 The Singleton ModelRegistry

**File:** `kazma-core/kazma_core/model_registry.py`

**Implementation details:**
- The module holds a module-level `_registry: ModelRegistry | None = None`.
- `initialize_model_registry(config_store)` creates the instance, calls `_deserialize()` to load active profile and discovered models from `ConfigStore`, and returns it.
- `get_model_registry()` returns the instance or raises `RuntimeError` if uninitialized.
- `reset_model_registry()` sets `_registry = None` (tests only).

**Lifecycle:**
```python
from kazma_core.config_store import ConfigStore
from kazma_core.model_registry import initialize_model_registry, get_model_registry

cs = ConfigStore()
initialize_model_registry(cs)
registry = get_model_registry()
profile = registry.get_active_profile()
client = registry.get_client()
```

**Why it replaced distributed LLM config:**
- Before the refactor, provider settings were scattered across `kazma.yaml`, `kazma_ui/models.py`, `kazma-core/providers.py`, and local `LLMProvider` instances. This caused stale clients, mismatched model lists, and duplicated API keys.
- The singleton centralizes active profile, provider list, saved profiles, model defaults, and discovered models. Every subsystem reads from and writes to the same object.
- LLM clients are cached per provider in `self._clients`, reducing connection overhead and ensuring the same config is used everywhere.

**Which modules use it:**
- `kazma-core`: agent, tools, swarm, settings manager, model discovery.
- `kazma-ui`: app factory, SSE chat, models route, settings, swarm panel.
- `kazma-cli`: completions, status, gateway, swarm commands.
- `kazma-tui`: header (read-only).

**Read-only rules for TUI:**
- Allowed: `get_active_profile()`, `get_client()`, `get_model()`, `list_providers()`, `list_unified_options()`, `get_discovered_models()`.
- Forbidden: `set_active_provider()`, `set_active_model()`, `upsert_provider()`, `delete_provider()`, `toggle_provider()`, `save_model_profile()`, `ConfigStore.set()`, `ConfigStore.write()`.

**Backward compatibility with legacy config:**
- If no active provider is set, `get_active_profile()` falls back to `llm.base_url`, `llm.api_key`, and `llm.model` from `ConfigStore` (which itself falls back to `kazma.yaml`).
- Storage keys `providers.list`, `models.saved.*`, `models.defaults.*`, `llm.model`, `registry.active_provider`, `registry.active_model`, `registry.discovered_models` are preserved.
- `UnifiedModelRegistry` is an alias for `ModelRegistry`.

### 4.2 Headless Gateway & Adapters

**File:** `kazma-gateway/kazma_gateway/gateway.py`

**Message bus design:**
```
Telegram/Discord/Slack adapter.listen()
        -> IncomingMessage
        -> asyncio.Queue(maxsize=100)
        -> GatewayManager._consume()
        -> MessageHandler (brain)
        -> OutboundMessage
        -> adapter.send()
```

- All adapters inherit from `BaseAdapter`.
- `BaseAdapter.listen()` is a polling loop that must:
  - Check `shutdown_event.is_set()` and exit cleanly.
  - Call `await self.jitter_sleep(shutdown_event)` between poll cycles (1-3s randomized delay).
  - Enqueue `IncomingMessage` objects.
- `BaseAdapter.start()` spawns the listen task and adds a done callback that resets `_running` if the task crashes.
- `GatewayManager` owns the queue, starts/stops adapters, consumes the queue, and dispatches to the registered handler.
- `GatewayManager.send()` routes outbound messages by platform prefix (`telegram:12345` -> `TelegramAdapter`).
- `RateLimiter` is a token-bucket limiter used by adapters for outbound sends.
- `MessageMetrics` tracks inbound/outbound/error counts.
- `SessionStore` is an abstract persistent cache for `thread_id -> context_metadata`. The concrete implementation is `SQLiteSessionStore` in `kazma_gateway/stores/sqlite.py`. The brain uses this to route replies without leaking platform IDs into graph state.

**Telegram adapter fixes:** See section 2.1.

**CheckpointManager and AsyncSqliteSaver:**
- `CheckpointManager` wraps `AsyncSqliteSaver` from `langgraph-checkpoint-sqlite`.
- It implements `aput`, `aput_writes`, `aget`, `aget_tuple`, `adelete_thread`.
- Per-thread locking is done via `_get_lock(thread_id)` which uses an LRU-bounded `OrderedDict` of `asyncio.Lock`.
- WAL mode and `synchronous=NORMAL` are set on the underlying aiosqlite connection.

**Retry logic with jitter and friendly error mapping:**
- `kazma-core/kazma_core/retry.py` uses `tenacity` for exponential backoff.
- LLM calls use `retry_llm_call`; tool calls use `retry_tool_call`.
- Configurable via `kazma.yaml` (`retry.max_attempts`, `retry.min_wait`, `retry.max_wait`) or defaults.
- Retryable exceptions: `ConnectionError`, `TimeoutError`, `asyncio.TimeoutError`, `httpx.TimeoutException`, `httpx.ConnectError`, `httpx.RemoteProtocolError`.
- 4xx errors are not retried. 401/403 are mapped to friendly messages via `friendly_llm_error()`.

### 4.3 Swarm Engine

**File:** `kazma-core/kazma_core/swarm/engine.py`

**6 orchestration patterns:**
1. **`dispatch`** (single worker): Direct dispatch to one worker with fallback chain support.
2. **`broadcast`** (all workers in parallel): Sends the same task to all registered workers (or a subset). Uses `BoundedConcurrency`.
3. **`pipeline`** (sequential chain): Workers execute one after another, sharing a `BlackboardStore`. Supports `metadata.hitl_checkpoints` for pausing at specific steps.
4. **`fan_out`** (concurrent workers with aggregation): Dispatches to multiple workers concurrently, then aggregates results using `ResultAggregator`. Supports `first_valid`, `merge_all`, `vote`, `synthesize`, `collect` strategies.
5. **`consult`** (independent opinions + synthesis): Each worker gives an independent opinion; a final synthesis worker combines them. Persists `individual_opinions` and `synthesized_output`.
6. **`conditional`** (router-based routing): First worker routes the task; engine dispatches to the mapped worker via `metadata.routes`. Records `metadata.route_taken`.

**TaskStore persistence and SQLite schema:**
- Database: `kazma-data/swarm_tasks.db`.
- Tables:
  - `swarm_tasks(id, type, prompt, status, workers, result, created_at, started_at, completed_at, cost, tokens, metadata)`.
  - `swarm_worker_metrics(worker, date, tasks_completed, tasks_failed, avg_latency, total_tokens, total_cost; PRIMARY KEY(worker, date))`.
- `TaskStore.persist_task(task)` is called on terminal and paused states.
- `TaskStore.list_tasks()` supports pagination, filtering by status/type/worker.
- `TaskStore.get_paused_tasks()` restores HITL checkpoint state after restart.

**SSE streaming and event bus:**
- `kazma-ui/kazma_ui/swarm_sse.py` provides `SSEEventBus` and `create_sse_router()`.
- `wire_engine_events(engine, bus)` monkey-patches `engine.dispatch`, `engine._dispatch_worker`, and `engine._finalize_task` to emit events:
  - `task_started`
  - `worker_started`
  - `worker_progress`
  - `worker_completed`
  - `checkpoint` (HITL pause)
  - `handoff` (delegation chain)
  - `task_completed`
- The event bus stores per-task history and replays it to reconnecting clients.

**HITL checkpoints:**
- Pipeline `metadata.hitl_checkpoints` is a list of 1-based step indices.
- After a checkpoint step, `execute_pipeline()` returns `PatternExecution(status="paused")` with checkpoint metadata.
- `SwarmEngine._handle_pipeline_checkpoint()` stores the paused state in `HITLCheckpointHandler` and persists it to `TaskStore`.
- `POST /api/swarm/tasks/{id}/approve` calls `engine.approve_checkpoint()` which resumes from the next step using `resume_pipeline()`.
- `POST /api/swarm/tasks/{id}/reject` calls `engine.reject_checkpoint()` which aborts the pipeline.
- `metadata.checkpoint_timeout` enables auto-reject after a timeout.

**Worker metrics:**
- `MetricsCollector` tracks per-worker `tokens_used`, `cost`, `duration_seconds`, `success`/`failure`.
- Data is flushed to `TaskStore` on every `record()` call.
- Aggregated via `GET /api/swarm/workers/{name}/metrics` and `GET /api/swarm/workers/metrics/all`.
- `TracingEmitter` emits OpenTelemetry spans for task hierarchy: `swarm.task.{id}`, `swarm.dispatch.{worker}`, `llm.call.{model}`, `tool.execute.{tool}`, `swarm.aggregate.{strategy}`, `swarm.synthesize`, `swarm.handoff.{from}->{to}`.

**Recent fixes:**
- Persistence, results flattening, active task cards, modal detail view (commit `4ba0700`).
- Cross-area integration flows (commit `a87c8e2`).
- No dual worker registry: `swarm_panel.py` uses `get_swarm_engine()` exclusively and delegates adds/removes to the shared engine.
- Prompt validation for all patterns (empty/whitespace prompts rejected with HTTP 400).

### 4.4 Hybrid UI Architecture

**Web UI (Arabic-first, bilingual) vs TUI (English-only):**

| Concern | Web UI | TUI |
|---------|--------|-----|
| Language | Arabic default, EN/AR toggle | English only |
| Framework | FastAPI + Jinja2 + HTMX + Alpine.js | Textual |
| Auth | `KAZMA_SECRET` middleware on sensitive routes | None (local terminal) |
| Chat | SSE streaming (`/api/chat/stream`) and WebSocket | Inline chat panel |
| Settings | 12-tab settings page with SQLite persistence | Not supported (read-only) |
| Swarm | Full swarm panel with SSE task streaming | Shows active agents only |
| Metrics | Real-time SSE telemetry (`/api/telemetry/stream`) | 2-second dashboard refresh |
| i18n | Cookie-based `kazma-lang`, 150+ Arabic translations | None |

**Which layer handles which concern:**
- **Core logic (all concerns)**: `kazma-core`.
- **Persistence**: `ConfigStore` (settings), `TaskStore` (swarm), `CheckpointManager` (checkpoints), `SQLiteSessionStore` (gateway sessions), `SQLiteMemoryBackend` (memory).
- **UI presentation**: `kazma-ui` (web) and `kazma-tui` (terminal).
- **Gateway/platform integration**: `kazma-gateway`.
- **CLI commands**: `kazma-cli`.

**Data flow from UI -> API -> Engine -> Persistence:**
```
Web UI (JS/Alpine) -> POST /api/swarm/dispatch
    -> kazma_ui/swarm_panel.py -> _resolve_engine() -> SwarmEngine
        -> kazma_core/swarm/engine.py -> dispatch/broadcast/pipeline/etc.
            -> kazma_core/swarm/worker.py -> InProcessWorker
            -> kazma_core/swarm/task_store.py -> persist_task()
            -> kazma_core/swarm/metrics.py -> record_worker_result()
            -> kazma_ui/swarm_sse.py -> SSEEventBus.emit()
```

### 4.5 Observability

**TraceStore (in-memory ring buffer + WebSocket broadcast):**
- File: `kazma-core/kazma_core/tracing.py`.
- Global singleton `get_trace_store()`.
- Default capacity 500 entries. Stores `TraceEntry` with timestamp, type, label, status, duration, tokens, cost, details.
- WebSocket endpoint `/ws/dashboard` registers/unregisters clients and broadcasts new traces.
- Stats: `total_cost`, `total_tokens`, `total_llm_calls`, `total_tool_calls`, `total_traces`, `uptime_seconds`.

**HardwareMonitor (CPU/RAM/GPU/VRAM via nvidia-smi):**
- File: `kazma-core/kazma_core/telemetry.py`.
- `TelemetrySnapshot` fields: `cpu`, `ram_used_gb`, `ram_total_gb`, `gpu`, `vram_used_gb`, `vram_total_gb`, `timestamp`, `error`.
- CPU/RAM via `psutil` wrapped in `run_in_executor`.
- GPU/VRAM via `nvidia-smi` async subprocess. Falls back to zeros if unavailable.
- `stream(interval)` yields continuous snapshots for SSE telemetry.

**MetricsCollector (per-worker metrics):**
- File: `kazma-core/kazma_core/swarm/metrics.py`.
- `MetricsCollector(task_store=task_store)`.
- Methods: `record()`, `record_worker_result()`, `get_worker_metrics()`, `get_worker_aggregate()`, `get_all_metrics()`.
- Thread-safe in-memory accumulator with optional SQLite flush.

**KazmaTracer backends (Langfuse, OpenTelemetry, console):**
- File: `kazma-core/kazma_core/tracing.py`.
- `KazmaTracer(backend, config)`.
- Backends: `langfuse`, `opentelemetry`, `console` (default).
- Methods: `trace_llm_call()`, `trace_tool_execution()`, `trace_state_transition()`, `trace_compaction()`, `flush()`, `shutdown()`.
- Every trace is also written to the in-memory `TraceStore`.

### 4.6 Unified Secrets Management Hub

**File:** `kazma-ui/kazma_ui/providers.py`

**Architecture overview:**
The Providers & Connectors Hub unifies all secret management (LLM provider API keys and platform connector tokens) behind a single set of REST endpoints with consistent masking, CRUD, and test-before-save semantics.

```
┌─────────────────────────────────────────────────────────┐
│                  Web UI (Settings)                       │
│  Providers & Connectors tab                              │
│  - LLM Providers sub-tab                                │
│  - Platform Connectors sub-tab                           │
│  - Model Profiles sub-tab                                │
└───────────────┬─────────────────────────────────────────┘
                │ REST API
                ▼
┌─────────────────────────────────────────────────────────┐
│         providers.py (create_providers_router)           │
│                                                          │
│  _mask_secret()         ****XXXX masking                 │
│  _is_masked_placeholder()  Detect UI placeholders        │
│  _mask_provider_entry()  Mask provider API keys          │
│  _mask_connector_entry() Mask connector tokens            │
└───────┬────────────────────────────────┬────────────────┘
        │                                │
        ▼                                ▼
┌──────────────────────┐    ┌──────────────────────────────┐
│  ModelRegistry       │    │  ConfigStore                 │
│  (LLM providers)     │    │  (Platform connectors)       │
│  - list_providers()  │    │  - connectors.{name}.token   │
│  - upsert_provider() │    │  - connectors.{name}.enabled │
│  - delete_provider() │    │  - connectors.{name}.*       │
│  - toggle_provider() │    │                              │
│  - set_provider_health│   │                              │
│  - discover_models() │    │                              │
│  - model profiles    │    │                              │
└──────────────────────┘    └──────────────────────────────┘
```

**Dual backing stores:**
- **LLM Providers** are stored in `ModelRegistry` via `providers.list` in `ConfigStore`. All provider CRUD goes through `registry.upsert_provider()`, `registry.delete_provider()`, `registry.toggle_provider()`.
- **Platform Connectors** (Telegram, Discord, Slack, Email, Webhook) are stored directly in `ConfigStore` under `connectors.{name}.*` keys (e.g., `connectors.telegram.token`, `connectors.slack.app_token`).

**Masking rules:**
- `_mask_secret(value)` returns `****XXXX` where `XXXX` is the last 4 characters. Values shorter than 4 characters return `***`.
- `_is_masked_placeholder(value)` detects common patterns: `***`, `****`, or any value containing `****`. This is used to prevent the UI from accidentally overwriting real secrets with masked placeholders.
- `_is_secret_key(key)` checks if a config key name contains hints like `token`, `secret`, `password`, `key`, or `api_key`.

**Test-before-save semantics:**
- **Providers**: `POST /api/providers/{name}/test` calls `GET {base_url}/models` with `Authorization: Bearer {api_key}`. Returns latency in ms on success, or the HTTP error. Updates provider health status via `registry.set_provider_health()`.
- **Connectors**: `POST /api/connectors/{name}/test` uses platform-specific checks:
  - Telegram: `GET https://api.telegram.org/bot{token}/getMe`
  - Discord: `GET https://discord.com/api/v10/users/@me` with `Authorization: Bot {token}`
  - Slack: `POST https://slack.com/api/auth.test` with `Authorization: Bearer {token}`
  - Generic (email, webhook): Always succeeds if token is present.
- The frontend disables the **Save** button until **Test Connection** returns `success: true`.

**Model Profile management:**
- `GET /api/models/profiles` lists saved profiles with masked API keys.
- `POST /api/models/profiles` saves a named profile under `models.saved.{name}` in `ConfigStore`. Masked-placeholder preservation applies.
- `DELETE /api/models/profiles/{name}` removes a saved profile.

---

## 5. Development Environment & Tooling

### 5.1 Setup with uv

Prerequisites:
- Python 3.11+ (<3.14).
- `uv` 0.11+ (`https://docs.astral.sh/uv/`).
- Git.

```powershell
# Enter the repository
cd G:\GitHubRepos\kazma

# Install all dependencies (core + dev + tui + rag)
uv sync --all-extras

# Verify the environment
uv run python -c "from kazma_core.model_registry import get_model_registry; print('OK')"
```

### 5.2 Install All Extras

`uv sync --all-extras` installs:
- Core dependencies from `pyproject.toml`.
- `dev` extra: pytest, pytest-asyncio, pytest-cov, ruff, mypy.
- `tui` extra: textual, python-bidi.
- `rag` extra: chromadb, sentence-transformers.

### 5.3 Run Full Test Suite

```powershell
# Full suite
uv run pytest tests/ -v

# With coverage
uv run pytest tests/ -v --cov=kazma_core --cov-report=term-missing
```

### 5.4 Run Package-Specific Tests

```powershell
# TUI tests
uv run pytest kazma-tui/tests/ -v

# Core model registry
uv run pytest tests/test_model_registry.py -v

# Gateway
uv run pytest tests/test_gateway.py -v

# Swarm
uv run pytest tests/test_swarm_*.py -v
```

### 5.5 Lint and Format

```powershell
# Lint
uv run ruff check .

# Auto-fix
uv run ruff check --fix .

# Format
uv run ruff format .
```

### 5.6 Type Checking

```powershell
# Strict mypy on kazma_core
uv run mypy kazma-core/kazma_core/

# kazma-memory (has mypy override for missing imports)
uv run mypy kazma-memory/kazma_memory/

# kazma-ui (may require additional stubs)
uv run mypy kazma-ui/kazma_ui/
```

Note: `pyproject.toml` has `[[tool.mypy.overrides]] module = "kazma_core.*" ignore_missing_imports = true`.

### 5.7 Run the Web UI

```powershell
# Default port 8000
uv run kazma-web

# Custom port
uv run kazma-web --port 8080

# Or via module
uv run python -m kazma_ui
```

### 5.8 Run the TUI

```powershell
uv run kazma-tui

# Or via module
uv run python -m kazma_tui
```

### 5.9 Run the CLI

```powershell
# Status
uv run kazma status

# Serve web UI
uv run kazma serve

# Gateway control
uv run kazma gateway status
uv run kazma gateway start
uv run kazma gateway stop
uv run kazma gateway restart
uv run kazma gateway refresh

# Swarm control
uv run kazma swarm status
uv run kazma swarm workers
uv run kazma swarm worker add --name analyst --model gpt-4o-mini --provider openai
uv run kazma swarm dispatch --workers analyst --task "Summarize the report"
uv run kazma swarm metrics

# Other
uv run kazma project init
uv run kazma completion bash
uv run kazma update --check
```

### 5.10 Verify ModelRegistry Initialization

```powershell
uv run python -c "
from kazma_core.config_store import ConfigStore
from kazma_core.model_registry import initialize_model_registry, get_model_registry

cs = ConfigStore()
initialize_model_registry(cs)
registry = get_model_registry()
profile = registry.get_active_profile()
print('Provider:', profile['provider'])
print('Model:', profile['model'])
print('Base URL:', profile['base_url'])
print('OK')
"
```

---

## 6. Operational Guide for the Next Agent

### 6.1 Common Tasks

#### Add a New TUI Metric or Widget

1. Read `AGENTS.md` boundaries. Only modify `kazma-tui/` unless importing from `kazma-core`.
2. Add a new `MetricCard` or widget in `kazma-tui/kazma_tui/dashboard.py` (or a new file if it is a standalone widget).
3. Update `MetricsDashboard.compose()` to include the new widget.
4. Update `MetricsDashboard._do_refresh()` to fetch data and call `update_card()`.
5. Handle missing data with "N/A" fallback. Do not crash.
6. Add/update tests in `kazma-tui/tests/test_dashboard.py`.
7. Use `set_interval` for periodic refresh (default 2 seconds).
8. Use English-only labels.

#### Add a New Swarm Pattern or Worker Type

1. Define the new `TaskType` in `kazma-core/kazma_core/swarm/task.py` if needed.
2. Implement the pattern logic in `kazma-core/kazma_core/swarm/patterns.py` (or a new module if large).
3. Add dispatch routing in `kazma-core/kazma_core/swarm/engine.py` `dispatch()`.
4. Add UI payload coercion in `kazma-ui/kazma_ui/swarm_panel.py` `_coerce_task_type()`.
5. Add UI tab/view in `kazma-ui/kazma_ui/templates/swarm.html` and `static/js/swarm.js`.
6. Add tests in `tests/test_swarm_<pattern>.py` and `tests/test_swarm_ui_panel.py`.
7. Document the pattern in the API quick reference.

For a new worker type:
1. Subclass `SwarmWorker` in `kazma-core/kazma_core/swarm/worker.py` or a new file.
2. Implement `dispatch()`, `start()`, `stop()`, `status()`, and `worker_type`.
3. Add factory routing in `SwarmEngine._create_worker()`.
4. Add UI worker type mapping in `kazma-ui/kazma_ui/swarm_panel.py` `_build_worker_config()` and `_coerce_task_type()`.

#### Add a New Adapter

1. Create a new module in `kazma-gateway/kazma_gateway/adapters/<platform>.py`.
2. Subclass `BaseAdapter` from `kazma-gateway/kazma_gateway/gateway.py`.
3. Implement `listen(queue, shutdown_event)` with mandatory `jitter_sleep()` between poll cycles.
4. Implement `send(outbound)` returning `bool`.
5. Build `IncomingMessage` with normalized `sender_id` and `context_metadata` carrying platform IDs.
6. Add tests in `tests/test_<platform>_adapter.py`.
7. Register the adapter in `kazma-ui/kazma_ui/app.py` and `kazma-cli/kazma_cli/gateway.py` if it should be user-configurable.
8. Add rate limit config in `kazma.yaml` under `gateway.rate_limits`.

#### Add a New UI Route or SSE Event

1. For a new FastAPI route, add the endpoint in the appropriate router module (e.g., `kazma-ui/kazma_ui/settings.py`).
2. If it needs auth, ensure it is protected by `KAZMA_SECRET` middleware (`kazma-ui/kazma_ui/auth.py`).
3. For a new SSE event, follow the frame contract in `kazma-ui/kazma_ui/sse_chat.py` or `kazma-ui/kazma_ui/swarm_sse.py`:
   ```
   event: <type>
data: <json>


   ```
4. Add/update tests in `tests/test_sse_chat.py` or `tests/test_swarm_sse.py`.
5. Update the frontend JS in `kazma-ui/kazma_ui/static/js/` to consume the event.

#### Add a New LLM Provider via the Web UI

1. Open **Settings > Providers & Connectors** and select the **LLM Providers & Models** sub-tab.
2. Click **Add Provider** and fill in: Name, Display Name, Base URL, API Key, and available Models.
3. Click **Test Connection**. The backend calls `GET {base_url}/models` with the API key and reports latency or the exact HTTP error.
4. Once the test succeeds, the **Save** button becomes enabled. Click **Save** to persist.
5. The provider is stored via `ModelRegistry.upsert_provider()` and appears in the provider list with a masked API key (`****XXXX`).
6. To add model discovery, click **Discover Models** after saving. The backend calls the provider's `/models` endpoint.
7. Add tests in `tests/test_settings.py` (`TestUnifiedProvidersRouterAPI`).

#### Add a New Platform Connector via the Web UI

1. Open **Settings > Providers & Connectors** and select the **Platform Connectors** sub-tab.
2. Click **Edit** on the desired connector (Telegram, Discord, Slack, Email, Webhook).
3. Enter the token and any extra fields (e.g., Slack App Token, Discord Guild ID, allowed users).
4. Click **Test Connection**. The backend performs a platform-specific health check:
   - Telegram: `GET https://api.telegram.org/bot{token}/getMe`
   - Discord: `GET https://discord.com/api/v10/users/@me` with `Authorization: Bot {token}`
   - Slack: `POST https://slack.com/api/auth.test` with the bot token
   - Generic (email, webhook): always succeeds if token is present
5. Once the test succeeds, click **Save**. The token is stored under `connectors.{name}.token` in `ConfigStore`.
6. After saving, click **Refresh Gateway** or restart the server so the new token is picked up by the gateway adapters.

#### Use the Test Connection Button to Diagnose Issues

1. Open **Settings > Providers & Connectors** and find the provider or connector card.
2. Click **Test Connection**. The button triggers a non-destructive health check.
3. **For LLM providers**: The test hits `{base_url}/models` with Bearer auth. Common failure reasons:
   - `Cannot connect to {base_url}`: URL is wrong or the server is unreachable.
   - `HTTP 401`: API key is invalid, expired, or missing.
   - `HTTP 403`: API key lacks permission for the `/models` endpoint.
   - `HTTP 404`: Base URL does not expose `/models` (some providers use `/v1/models`).
4. **For platform connectors**: The test uses platform-specific endpoints (see above). Common failures:
   - `No token configured`: Token field is empty.
   - `HTTP 401`: Token is revoked or invalid.
   - `invalid_auth` (Slack): Bot token or app token is wrong.
5. The **Save** button stays disabled until the test succeeds. Fix the URL/token and retry.
6. Provider health status (`healthy`, `degraded`, `down`) is stored via `ModelRegistry.set_provider_health()` and reflected in the UI card color.

#### Change Model/Provider Behavior

1. All changes go through `kazma-core/kazma_core/model_registry.py`.
2. If adding a new provider preset, update `kazma-core/kazma_core/providers.py` `PROVIDER_PRESETS`.
3. If adding model discovery logic, update `ModelRegistry.discover_models()`.
4. If adding a new model/profile field, update `ModelRegistry.save_model_profile()` and `list_model_profiles()`.
5. Update `kazma-ui/kazma_ui/models_route.py` and `kazma-ui/kazma_ui/sse_chat.py` to reflect changes in the live provider.
6. Update `kazma-cli/kazma_cli/completions.py` if new models/providers should appear in tab completion.
7. Update tests in `tests/test_model_registry.py`, `tests/test_models_route.py`, `tests/test_completions.py`.

#### Persist a New Kind of Task or Metric

1. Swarm tasks and worker metrics: extend `kazma-core/kazma_core/swarm/task_store.py` schema and `persist_task()` / `record_worker_metric()`.
2. Gateway sessions: extend `kazma-gateway/kazma_gateway/stores/sqlite.py`.
3. Settings/config: use `ConfigStore.set()` with a category.
4. Cron jobs: extend `kazma-core/kazma_core/cron/scheduler.py` and `SQLiteCronStore`.
5. If adding a new DB table, always create it in `_init_db()` with `IF NOT EXISTS` and indexes.
6. Write tests that verify the persisted data survives a `TaskStore`/`ConfigStore` round-trip.

### 6.2 Prohibited Patterns (Critical)

- **No hardcoded model/provider logic outside ModelRegistry.** Do not embed provider URLs, API key handling, or model lists in UI, CLI, or gateway code. Route everything through `ModelRegistry`.
- **No bypassing ModelRegistry for LLM clients.** Always use `registry.get_client()` or `registry.get_model()`. Never instantiate `LLMProvider` directly except inside `ModelRegistry`.
- **No mutating ModelRegistry from TUI.** The TUI is read-only. Do not call `set_active_provider()`, `set_active_model()`, `upsert_provider()`, or `ConfigStore.write()`.
- **No Arabic/RTL in TUI.** All TUI labels and help text must be in English.
- **No creating second ModelRegistry instances.** Use the module-level singleton functions. Do not instantiate `ModelRegistry` directly outside tests or the `initialize_model_registry` call.
- **No using global mutable state without locks.** Use `asyncio.Lock` for async state, `threading.Lock` for sync state, and `asyncio.Queue` for message passing.
- **No blocking the event loop.** Wrap sync calls in `loop.run_in_executor`. Use `httpx` instead of `requests`. Use `aiosqlite` instead of synchronous `sqlite3` in async contexts.
- **No patching adapters instead of using the jitter/listen contract.** Adapters must implement `listen()` with `jitter_sleep()`. Do not add ad-hoc sleep loops or bypass `BaseAdapter`.
- **No bypassing TaskStore for swarm persistence.** Swarm tasks, worker metrics, and HITL checkpoints must be persisted through `TaskStore`.
- **No creating new LLMProvider instances directly.** Use `registry.get_client()`. The only exception is inside `ModelRegistry` itself.

### 6.3 Required Patterns

- **Type hints and docstrings** on all public APIs. Use `from __future__ import annotations`.
- **Module-level logger:** `logger = logging.getLogger(__name__)`.
- **`from __future__ import annotations`** at the top of every Python file.
- **TDD / tests first.** Write tests before implementation. Tests are in `tests/` and `kazma-tui/tests/`.
- **Use public APIs, not private attributes.** For example, use `engine.worker_names`, `engine.metrics_collector`, `engine.task_store` (properties) rather than `engine._workers`, `engine._metrics_collector`, `engine._task_store` directly.
- **Graceful degradation with "N/A" fallback.** The TUI and metrics endpoints must never crash when a data source is unavailable.
- **Conventional commits:** `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`, `security:`, `perf:`.
- **One class per TUI widget file.** Keep widgets modular.

### 6.4 Troubleshooting & Gotchas

#### ModelRegistry not initialized

**Symptom:** `RuntimeError: ModelRegistry not initialized. Call initialize_model_registry() first.`
**Cause:** A test or entry point created a `KazmaAgent` or called `get_model_registry()` before initializing the registry.
**Fix:**
- In tests, ensure `tests/conftest.py` fixture is loaded (it is autouse).
- In app startup, call `initialize_model_registry(config_store)` before creating the agent.
- In CLI commands that need the registry, import from `kazma_core.model_registry` and initialize if needed.

#### Task history lost on restart

**Symptom:** Swarm tasks disappear after server restart.
**Cause:** `SwarmEngine` was created without a `TaskStore`, or a different `TaskStore` instance was used than the one in `SwarmManager`.
**Fix:** Verify `kazma-ui/kazma_ui/app.py` creates one shared `TaskStore` and passes it to `SwarmManager`. Check `kazma_core/kazma_core/swarm/task_store.py` `_DEFAULT_DB = "kazma-data/swarm_tasks.db"` and that the file is writable.

#### Active Tasks not showing

**Symptom:** A dispatched task does not appear in the Active Tasks tab.
**Cause:** SSE event bus not wired, or the task object is not added to the UI's optimistic list.
**Fix:**
- Confirm `wire_engine_events(engine, _sse_bus)` was called in `kazma_ui/swarm_panel.py` `_current_engine()`.
- Verify the JS in `swarm.js` listens to `/api/swarm/tasks/{id}/stream` and creates a card on `task_started`.
- Check that the dispatch endpoint returns a `task_id` and that the frontend stores it.

#### Results Dashboard empty

**Symptom:** The Results Dashboard shows tasks but no worker results.
**Cause:** `SwarmTask.to_dict()` nests results under `result`, but the UI expects top-level fields.
**Fix:** Use `_flatten_swarm_task()` in `kazma_ui/swarm_panel.py` before returning tasks to the UI. Ensure new fields are promoted in the flatten helper.

#### Clicking task does not open modal

**Symptom:** Task card click does nothing or modal shows empty content.
**Cause:** Modal DOM placement, missing detail fetch, or wrong key access in the JS modal builder.
**Fix:**
- Confirm `GET /api/swarm/tasks/{id}` returns the flattened task shape.
- Verify the modal HTML element is present in `swarm.html` and the JS opens it with the correct data keys.
- Check browser console for JS errors.

#### Telegram webhook conflict / 401 mapping

**Symptom:** Telegram adapter reports connected but never receives messages, or chat shows "invalid API key" errors.
**Cause:** Webhook still set, or bad token, or 401 not mapped.
**Fix:**
- Check `deleteWebhook` is called at adapter startup (`kazma_gateway/adapters/telegram.py`).
- Verify `getMe` validation fails gracefully and sets `_running = False`.
- Confirm `retry.py` `friendly_llm_error()` maps 401/403 to the settings update message.
- If using the Web UI, ensure the SSE chat router's pre-stream key validation is active.

#### CheckpointManager deadlock

**Symptom:** Graph execution hangs or `database is locked` errors.
**Cause:** Concurrent writes to the same `thread_id` without acquiring the per-thread lock, or a lock held across an await point that never returns.
**Fix:**
- Ensure `CheckpointManager.aput` and `aput_writes` wrap the underlying saver call in `async with lock`.
- Do not hold a checkpoint lock while calling other I/O or user code.
- Check `active_locks` property for runaway lock growth.

#### GPU telemetry fallback

**Symptom:** TUI dashboard shows `VRAM: N/A` or GPU 0% on a system with a GPU.
**Cause:** `nvidia-smi` not in PATH, driver issue, or subprocess timeout.
**Fix:**
- Verify `nvidia-smi` runs from the command line.
- Check `HardwareMonitor` logs for subprocess errors.
- The code intentionally falls back to zeros; non-NVIDIA systems are expected to show N/A.

### 6.5 Other Operational Notes

- **Config precedence:** Environment variables override `ConfigStore` DB values; DB values override `kazma.yaml` defaults.
- **Workspace path:** `kazma-ui/kazma_ui/app.py` configures `kazma-data/workspace` via `configure_workspace()` to avoid creating folders at the drive root on Windows. Override with `KAZMA_WORKSPACE` env var.
- **CORS:** Default CORS origins are `http://localhost:8000` and `http://127.0.0.1:8000`. Override with `KAZMA_CORS_ORIGINS` env var (comma-separated).
- **Auth:** `KAZMA_SECRET` gates sensitive endpoints (`/api/settings`, `/api/swarm`, `/api/mcp`, `/api/skills`, `/api/models`, `/api/ollama`). Read-only endpoints and page routes are open when the secret is unset.
- **Docker:** `docker compose up -d` starts the services. Health check: `curl http://localhost:8000/health`.

---

## 7. File & API Quick Reference

### 7.1 Most Important Files

| File | Responsibility |
|------|----------------|
| `AGENTS.md` | Mission boundaries and non-negotiable rules. Read first. |
| `kazma-core/kazma_core/model_registry.py` | Singleton ModelRegistry. Source of truth for providers/models/clients. |
| `kazma-core/kazma_core/config_store.py` | SQLite-backed runtime config with YAML fallback. |
| `kazma-core/kazma_core/telemetry.py` | HardwareMonitor (CPU/RAM/GPU/VRAM). |
| `kazma-core/kazma_core/tracing.py` | TraceStore, KazmaTracer, WebSocket broadcast. |
| `kazma-core/kazma_core/retry.py` | Retry decorators and friendly error mapping. |
| `kazma-core/kazma_core/swarm/engine.py` | SwarmEngine (6 patterns, reliability, HITL). |
| `kazma-core/kazma_core/swarm/task_store.py` | SQLite persistence for swarm tasks and metrics. |
| `kazma-core/kazma_core/swarm/metrics.py` | MetricsCollector for per-worker metrics. |
| `kazma-core/kazma_core/swarm/patterns.py` | Pipeline, fan-out, conditional pattern implementations. |
| `kazma-core/kazma_core/swarm/reliability.py` | RetryPolicy, CircuitBreaker, TimeoutGuard, OutputValidator, FallbackChain, BoundedConcurrency. |
| `kazma-gateway/kazma_gateway/gateway.py` | GatewayManager, BaseAdapter, IncomingMessage, OutboundMessage, message bus. |
| `kazma-gateway/kazma_gateway/adapters/telegram.py` | TelegramAdapter (polling, webhook, voice, reactions, callbacks). |
| `kazma-gateway/kazma_gateway/stores/checkpoint.py` | CheckpointManager with per-thread locking. |
| `kazma-ui/kazma_ui/app.py` | FastAPI app factory; wires all subsystems. |
| `kazma-ui/kazma_ui/providers.py` | **NEW.** Unified providers & connectors router (14 endpoints). Masking, CRUD, test-before-save. |
| `kazma-ui/kazma_ui/sse_chat.py` | SSE chat streaming from LangGraph. |
| `kazma-ui/kazma_ui/swarm_panel.py` | Swarm Panel API and UI flattening. |
| `kazma-ui/kazma_ui/swarm_sse.py` | Swarm task SSE event bus and engine wiring. |
| `kazma-cli/kazma_cli/main.py` | CLI entry point. |
| `kazma-tui/kazma_tui/app.py` | Textual TUI app. |
| `kazma-tui/kazma_tui/dashboard.py` | Metrics dashboard. |
| `tests/conftest.py` | Shared pytest fixtures and ModelRegistry autouse setup. |

### 7.2 Key Functions and Classes

| Function/Class | File | Purpose |
|----------------|------|---------|
| `initialize_model_registry(cs)` | `model_registry.py` | Create singleton registry. |
| `get_model_registry()` | `model_registry.py` | Retrieve singleton. |
| `ModelRegistry.get_active_profile()` | `model_registry.py` | Active provider/model. |
| `ModelRegistry.get_client(model=None)` | `model_registry.py` | Cached LLM client. |
| `ConfigStore` | `config_store.py` | SQLite config with YAML fallback. |
| `HardwareMonitor.get_stats()` | `telemetry.py` | CPU/RAM/GPU/VRAM snapshot. |
| `get_trace_store()` | `tracing.py` | In-memory trace store singleton. |
| `KazmaTracer` | `tracing.py` | Tracing backend wrapper. |
| `retry_llm_call` / `retry_tool_call` | `retry.py` | Tenacity retry decorators. |
| `friendly_llm_error` / `friendly_tool_error` | `retry.py` | User-friendly error mapping. |
| `SwarmEngine.dispatch(task)` | `swarm/engine.py` | Dispatch a swarm task. |
| `SwarmEngine.broadcast(task)` | `swarm/engine.py` | Broadcast to all workers. |
| `SwarmEngine.spawn_worker(...)` | `swarm/engine.py` | Add a worker at runtime. |
| `SwarmEngine.approve_checkpoint(id)` | `swarm/engine.py` | Resume paused HITL pipeline. |
| `TaskStore.persist_task(task)` | `swarm/task_store.py` | Persist task and metrics. |
| `MetricsCollector.get_all_metrics()` | `swarm/metrics.py` | Aggregated worker metrics. |
| `CapabilityRouter.route(...)` | `swarm/router.py` | Auto-route by capability overlap. |
| `execute_pipeline(...)` | `swarm/patterns.py` | Pipeline pattern with HITL. |
| `execute_fan_out(...)` | `swarm/patterns.py` | Fan-out with aggregation. |
| `execute_conditional(...)` | `swarm/patterns.py` | Conditional routing. |
| `execute_consult(...)` | `swarm/consultation.py` | Consult + synthesis. |
| `GatewayManager` | `gateway.py` | Message bus orchestrator. |
| `BaseAdapter` | `gateway.py` | Adapter contract. |
| `TelegramAdapter` | `adapters/telegram.py` | Telegram polling/webhook adapter. |
| `create_checkpoint_manager(path)` | `stores/checkpoint.py` | Factory for CheckpointManager. |
| `create_app(config_path)` | `kazma_ui/app.py` | FastAPI app factory. |
| `create_providers_router(cs)` | `kazma_ui/providers.py` | Unified providers & connectors router. |
| `create_sse_chat_router(...)` | `kazma_ui/sse_chat.py` | SSE chat router. |
| `create_swarm_router(...)` | `kazma_ui/swarm_panel.py` | Swarm panel router. |
| `create_sse_router(...)` | `kazma_ui/swarm_sse.py` | Swarm SSE router. |
| `main()` | `kazma_cli/main.py` | CLI entry. |
| `KazmaTUI` | `kazma_tui/app.py` | TUI app. |
| `MetricsDashboard` | `kazma_tui/dashboard.py` | TUI metrics dashboard. |

### 7.3 Important REST/SSE/WebSocket Endpoints

| Endpoint | Method | Description | File |
|----------|--------|-------------|------|
| `/` | GET | Dashboard (root) | `kazma_ui/app.py` |
| `/health` | GET | Health check | `kazma_ui/app.py` |
| `/api/status` | GET | Subsystem init status | `kazma_ui/app.py` |
| `/api/chat/stream` | POST | SSE chat streaming | `kazma_ui/sse_chat.py` |
| `/api/chat/sessions` | GET | List chat sessions | `kazma_ui/sse_chat.py` |
| `/api/chat/sessions/{id}/messages` | GET | Session message history | `kazma_ui/sse_chat.py` |
| `/api/telemetry/stream` | GET | SSE hardware telemetry | `kazma_ui/telemetry_route.py` |
| `/api/telemetry` | GET | Mock telemetry (legacy) | `kazma_ui/app.py` |
| `/ws/dashboard` | WS | Trace dashboard feed | `kazma_ui/app.py` |
| `/ws/chat` | WS | WebSocket chat | `kazma_ui/app.py` |
| `/api/settings/*` | various | Settings CRUD/export/import | `kazma_ui/settings.py` |
| `/api/models` | GET | Unified model/provider options | `kazma_ui/models_route.py` |
| `/api/provider/active` | GET | Active provider profile | `kazma_ui/sse_chat.py` |
| `/api/provider/switch` | POST | Switch provider/model | `kazma_ui/sse_chat.py` |
| `/api/ollama/*` | various | Ollama management | `kazma_ui/models_route.py` |
| `/api/gateway/status` | GET | Gateway status | `kazma_ui/gateway_monitor.py` |
| `/api/gateway/refresh-adapters` | POST | Hot-reload adapters | `kazma_ui/app.py` |
| `/api/swarm/status` | GET | Swarm worker status | `kazma_ui/swarm_panel.py` |
| `/api/swarm/dispatch` | POST | Dispatch a task | `kazma_ui/swarm_panel.py` |
| `/api/swarm/tasks` | GET | Task history with filters | `kazma_ui/swarm_panel.py` |
| `/api/swarm/tasks/{id}` | GET | Task detail | `kazma_ui/swarm_panel.py` |
| `/api/swarm/tasks/{id}/stream` | GET | SSE swarm task events | `kazma_ui/swarm_sse.py` |
| `/api/swarm/tasks/{id}/approve` | POST | Approve HITL checkpoint | `kazma_ui/swarm_panel.py` |
| `/api/swarm/tasks/{id}/reject` | POST | Reject HITL checkpoint | `kazma_ui/swarm_panel.py` |
| `/api/swarm/workers` | POST | Add worker | `kazma_ui/swarm_panel.py` |
| `/api/swarm/workers/{name}` | DELETE | Remove worker | `kazma_ui/swarm_panel.py` |
| `/api/swarm/workers/spawn` | POST | Spawn dynamic worker | `kazma_ui/swarm_panel.py` |
| `/api/swarm/workers/{name}/metrics` | GET | Worker metrics | `kazma_ui/swarm_panel.py` |
| `/api/swarm/workers/metrics/all` | GET | All worker metrics | `kazma_ui/swarm_panel.py` |
| `/api/swarm/workers/{name}/circuit-breaker/reset` | POST | Reset breaker | `kazma_ui/swarm_panel.py` |
| `/api/swarm/circuit-breakers` | GET | All breaker status | `kazma_ui/swarm_panel.py` |
| `/api/swarm/start` | POST | Start all workers | `kazma_ui/swarm_panel.py` |
| `/api/swarm/stop` | POST | Stop all workers | `kazma_ui/swarm_panel.py` |
| `/api/approve/{thread_id}` | POST | HITL graph approval/deny | `kazma_ui/app.py` |
| `/api/pending-approvals` | GET | List pending HITL approvals | `kazma_ui/app.py` |
| `/api/metrics` | GET | Prometheus metrics | `kazma_ui/metrics.py` |
| `/api/providers` | GET | List LLM providers with masked keys | `kazma_ui/providers.py` |
| `/api/providers` | POST | Add/update LLM provider | `kazma_ui/providers.py` |
| `/api/providers/{name}` | DELETE | Delete LLM provider | `kazma_ui/providers.py` |
| `/api/providers/{name}/toggle` | POST | Enable/disable provider | `kazma_ui/providers.py` |
| `/api/providers/{name}/test` | POST | Test provider connection | `kazma_ui/providers.py` |
| `/api/providers/{name}/discover` | POST | Discover provider models | `kazma_ui/providers.py` |
| `/api/models/profiles` | GET | List saved model profiles | `kazma_ui/providers.py` |
| `/api/models/profiles` | POST | Save model profile | `kazma_ui/providers.py` |
| `/api/models/profiles/{name}` | DELETE | Delete model profile | `kazma_ui/providers.py` |
| `/api/connectors` | GET | List platform connectors with masked tokens | `kazma_ui/providers.py` |
| `/api/connectors` | POST | Add/update connector | `kazma_ui/providers.py` |
| `/api/connectors/{name}` | DELETE | Delete connector | `kazma_ui/providers.py` |
| `/api/connectors/{name}/test` | POST | Test connector connection | `kazma_ui/providers.py` |
| `/api/connectors/{name}/toggle` | POST | Enable/disable connector | `kazma_ui/providers.py` |
| `/api/webhooks/telegram` | POST | Telegram webhook ingress | `kazma_gateway/adapters/telegram.py` |

### 7.4 CLI Commands

| Command | Subcommands | Description |
|---------|-------------|-------------|
| `kazma status` | - | Real server/gateway/swarm health. |
| `kazma serve [port]` | - | Start Web UI. |
| `kazma gateway` | status, start, stop, restart, refresh | Gateway lifecycle. |
| `kazma swarm` | status, workers, worker add/spawn/remove, dispatch, broadcast, consult, pipeline, fanout, history, task, metrics, start, stop, approve, reject, circuit-breaker | Swarm orchestration. |
| `kazma project` | init, show, validate | Project-level config. |
| `kazma completion` | bash, zsh, powershell, install | Shell completions. |
| `kazma update` | --check, --force, --yes | Self-update. |
| `kazma wizard` | - | Skill install wizard. |
| `kazma hub` | search, install, list, etc. | Hub marketplace. |
| `kazma docs` | build, serve | Docusaurus docs. |

### 7.5 Configuration Keys

| Key | Purpose | Stored In |
|-----|---------|-----------|
| `llm.model` | Legacy default model | ConfigStore/YAML |
| `llm.base_url` | Legacy LLM base URL | ConfigStore/YAML |
| `llm.api_key` | Legacy API key | ConfigStore/YAML |
| `providers.list` | JSON array of provider entries | ConfigStore |
| `registry.active_provider` | Active provider name | ConfigStore |
| `registry.active_model` | Active model name | ConfigStore |
| `registry.discovered_models` | Cached discovered models JSON | ConfigStore |
| `models.saved.{name}` | Saved model profile | ConfigStore |
| `models.defaults.{task}` | Task-specific model default | ConfigStore |
| `connectors.telegram.token` | Telegram bot token | ConfigStore/YAML/env |
| `connectors.telegram.allowed_users` | Whitelist of Telegram user IDs | ConfigStore |
| `connectors.discord.token` | Discord bot token | ConfigStore/env |
| `connectors.slack.token` | Slack bot token | ConfigStore/env |
| `connectors.slack.app_token` | Slack app token | ConfigStore/env |
| `storage.path` | Checkpoint DB path | YAML |
| `swarm.enabled` | Enable swarm | YAML |
| `swarm.group_chat_id` | Telegram group chat for swarm | YAML/env `SWARM_CHAT_ID` |
| `swarm.workers` | Static worker definitions | YAML |
| `safety.hitl.require_approval_for` | Tool names requiring approval | YAML |
| `ui.rtl` | Right-to-left UI direction | YAML |
| `logging.langfuse.enabled` | Enable Langfuse tracing | YAML |
| `time_travel.enabled` | Enable snapshot replay | YAML |

---

## 8. Known Issues & Deferred Work

### 8.1 Already Resolved Swarm Fixes

The following swarm issues were resolved in recent commits and are included here to document what was fixed:
- **Task persistence**: `TaskStore` persists tasks on terminal and paused states; history survives restart (commit `4ba0700`).
- **Results flattening**: `_flatten_swarm_task()` in `kazma_ui/swarm_panel.py` bridges the nested `SwarmTask` shape to the flat UI shape (commit `4ba0700`).
- **Active Tasks**: Optimistic card creation + SSE updates (commit `4ba0700`).
- **Modal detail**: Task detail endpoint returns flattened data; JS modal builder reads correct keys (commit `4ba0700`).
- **Cross-area flows**: Pipeline HITL, consult partial failure, fan-out fallback, consult UI end-to-end, decoupled SwarmManager, prompt validation, no dual registry (commit `a87c8e2`).
- **Capability routing**: `CapabilityRouter` auto-routes `workers=["auto"]` by keyword overlap (commit `b87f794`).
- **Reliability layer**: Retry, circuit breaker, timeout, output validation, bounded concurrency, fallback chains (commits `15d63e5`, `ff8d8ec`, `df7cae1`, `cc69795`).
- **Handoff mechanism**: Workers can delegate to other workers mid-task with multi-hop support (commit `b95643c`).
- **Dynamic spawning**: `POST /api/swarm/workers/spawn` creates workers at runtime (commit `356277e`).
- **HITL checkpoints**: Pipeline pause/resume/reject with persisted state (commit `cffb917`).
- **SSE streaming**: `GET /api/swarm/tasks/{id}/stream` with event history replay (commit `79e781d`).

### 8.2 Remaining TODOs / FIXMEs / Deferred Items

The following are known limitations or deferred work discovered in the codebase. They are not blockers but should be tracked for future sprints.

- **Checkpoint concurrency lock (historical deferred bug):** `BUG_FIX_TASK.md` listed a `database is locked` issue in `kazma-core/kazma_core/checkpoint.py` (legacy file). The current gateway `CheckpointManager` in `kazma-gateway/kazma_gateway/stores/checkpoint.py` already uses per-thread locks. If the legacy `kazma_core/checkpoint.py` is still used by any code, it should be audited for the same lock pattern.
- **Settings export content-type (historical deferred bug):** `BUG_FIX_TASK.md` listed an issue where `/api/settings/export` returned `application/json`. Verify the endpoint in `kazma-ui/kazma_ui/settings.py` returns `text/yaml` with a `Content-Disposition` header.
- **Providers hub tests now exist:** Prior to the Providers & Connectors Hub (commit `f3b0945`), there was no dedicated test coverage for the unified provider/connector API endpoints. The `TestUnifiedProvidersRouterAPI` class in `tests/test_settings.py` now covers CRUD, masking, secret preservation, toggle, discover, and connector test scenarios. The hub's `_mask_secret()` and `_is_masked_placeholder()` helpers are implicitly tested through the API integration tests but do not have standalone unit tests; consider adding them if edge cases arise (e.g., Unicode secrets, empty strings, very long keys).
- **Local STT provider:** `TelegramAdapter._transcribe_voice()` logs "Local STT provider not yet implemented" for `voice_provider="local"`. Only `openai` and `groq` are implemented.
- **Vector memory optional dependency:** RAG memory via ChromaDB/sentence-transformers requires the `rag` extra. Without it, the UI logs a hint at startup but does not fail.
- **TUI chat is not wired to the agent:** The TUI chat panel is a local UI with `/help`, `/clear`, `/quit` commands. It does not send messages to the agent LLM. This is by design for the current milestone; a future enhancement could integrate with the SSE chat router or a local agent runner.
- **Mock `/api/telemetry` endpoint:** `kazma-ui/kazma_ui/app.py` still defines a mock `/api/telemetry` endpoint returning random token/VRAM data. The real telemetry SSE endpoint is `/api/telemetry/stream`. The mock endpoint is kept for backward compatibility with old dashboard JS; it may be removed in a future cleanup.
- **PowerShell completions:** The CLI completions module supports PowerShell but the installation path detection may need adjustment for non-standard PowerShell profiles.
- **Documentation site:** The Docusaurus `docs/` site is built with npm. It is not automatically deployed in CI; deployment is manual.
- **Docker health checks:** The `docker-compose.yml` does not include explicit health checks. Add them if production deployment requires them.
- **Type checking strictness:** `kazma_core` has `ignore_missing_imports = true` in mypy config. As the project matures, stubs should be added and this override removed.
- **TUI tests:** While comprehensive, the TUI tests rely on `textual` pilot/test harness. Some async tests may be flaky on very slow CI runners.
- **Swarm modal edge cases:** If a task result contains handoff chains with many hops, the modal UI may overflow. Consider pagination or collapsible sections.
- **Gateway adapter refresh race:** `POST /api/gateway/refresh-adapters` stops old adapters, clears the list, and restarts new ones. There is a brief window where the gateway has no adapters. Consider a transactional swap in a future refactor.
- **Arabic tokenizer maintenance:** `ArabicTokenizer` supports Kuwaiti dialect and MSA. Adding new dialects would require extending the tokenizer rules and tests in `kazma-memory/kazma_memory/arabic_tokenizer.py` and `tests/test_arabic_tokenizer.py`.

### 8.3 Known Limitations

- **GPU telemetry requires NVIDIA:** `HardwareMonitor` uses `nvidia-smi`. AMD/Intel GPUs are not supported and will report zeros/N/A.
- **TUI read-only:** The TUI cannot change models or settings. Use the Web UI or CLI for mutations.
- **Swarm HITL checkpoints are engine-local decisions:** The checkpoint handler is in-memory, but the paused state is persisted to SQLite. Restoring after a restart requires `restore_paused_tasks()` to be called on startup.
- **Telegram adapter is polling-only by default:** Webhook ingress is optional and requires a public URL. The primary path is manual `getUpdates` polling.
- **Model discovery depends on provider endpoints:** `discover_models()` calls `/models` on the provider's base URL. Providers that do not expose this endpoint return empty lists.

---

*End of handover. The next agent should start by reading `AGENTS.md`, then this document, then the specific files in the File & API Quick Reference for the area they are changing.*
