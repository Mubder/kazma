---
sidebar_position: 1
---

# Architecture

Kazma is built as a modular Python framework with clear separation of concerns.

## Module structure

```
kazma-core/              Core agent framework
  agent_runner.py        Agent loop and lifecycle
  model_registry.py      Model/provider registry (singleton)
  swarm/                 Swarm orchestration engine
    engine.py            SwarmEngine — dispatch, consult, broadcast
    patterns.py          Sequential stage patterns (pipeline, fanout, conditional)
    reliability.py       Reliability registry and circuit breaker state management
    safety.py            SafetyMiddleware — HITL danger-tool gating
    bus.py               SwarmMessageBus — platform-agnostic streaming
  routing_engine.py      UnifiedRouter — semantic vector search + dialect boosting + keyword overlap fallback
  memory/                4-layer co-processing memory
      vector.py          Layer 1 — ChromaDB global semantic search
      graph.py           Layer 2 — NetworkX knowledge graph
      fts5.py            Layer 3 — SQLite FTS5 lexical + BM25
      sqlite_vec.py      Layer 4 — sqlite-vec local embeddings
      adapter.py         RRF blending across all 4 layers
      pipeline_logger.py SQLite-backed pipeline diagnostics
  delegation/            Multi-agent delegation protocol
  security/              Security auditing and hardening
  hub/                   Skill registry and management
  skills/                Self-improvement engine

kazma-gateway/           Platform adapters
  adapters/
    telegram.py          Telegram bot adapter
    telegram_bus.py      Telegram SwarmMessageBus adapter (rich cards + HITL)
    discord.py           Discord adapter
    slack.py             Slack adapter

kazma-memory/            Persistent memory subsystem
  search_backend.py      SQLite FTS5 + Arabic tokenizer
  arabic_tokenizer.py    Arabic text normalizer with dialect support

kazma-ui/                Web dashboard (FastAPI + Jinja2)
kazma-tui/               Terminal dashboard (Textual + TextArea selection)
kazma-cli/               Command-line interface (11 commands)
kazma-skills/            Built-in skill manifests
kazma-core/kazma_core/chaos/  Chaos testing framework
loadtests/               Load testing infrastructure (Locust + k6)
```

## Key architectural principles

### Singleton ModelRegistry
All LLM interactions flow through `ModelRegistry` — a process-wide singleton. No component creates its own LLM client. Workers dispatch tasks via `registry.get_client().chat()`.

### 4-Layer Co-Processing Memory
Queries fan out to all 4 backends in parallel, then blend via Reciprocal Rank Fusion (RRF, k=60). See `docs/architecture/MEMORY.md` for the full query flow.

### Smart-Fallback Routing (Unified Routing)
If no specialist worker matches a task, the `UnifiedRouter` manages distribution. It resolves tasks using semantic similarity vector search, language/dialect-aware score boosting, and falls back to precise token-overlap keyword matching. If still unmapped, it auto-delegates to any available generalist worker, guaranteeing zero dispatch failures.

### WorkerRegistry — Single Source of Truth
Workers are persisted in `swarm_registry.json`. The REST API, CLI, and Web UI all read/write through the same CRUD interface. Workers survive reboots. Worker capabilities are strictly sandboxed via persistent `tools` arrays in the registry, enforcing the Principle of Least Privilege.

### SwarmMessageBus
Workers stream logs/outputs to the active platform adapter (Telegram/Discord/Slack) without knowing the specific platform. Formatted Swarm Report cards with inline HITL approval/reject buttons.

### Pipeline Engine
Robust, sequential stage execution (e.g., Researcher → Refiner → Builder → Validator). Each stage forwards context to the next. Every step is logged to SQLite via WAL-mode `pipeline_logger` for Web UI diagnostics. After completion, the Refiner synthesizes a Markdown report card, and the `SelfImprovementSkill` hook triggers to analyze pipeline outputs and suggest improvements.

### SafetyMiddleware
Danger-tier tool calls (`shell_exec`, `file_write`, `python_exec`, `spawn_agent`) are gated behind operator approval via the SwarmMessageBus. Approval cards expire after 60s.

## Phase 3: Production Hardening (July 2026)

### Chaos Testing Framework (`kazma-core/kazma_core/chaos/`)
A comprehensive failure injection system for resilience testing:
- **FailureInjector** — Central registry for latency, errors, timeouts, circuit breaker opens, network partitions, resource exhaustion
- **10 Predefined Experiments** — LLM latency/errors/timeouts, DB slow/errors, message bus partition, tool executor failures, swarm degradation, gateway adapter errors, circuit breaker force-open, resource exhaustion
- **UI Endpoints** (`/api/chaos/*`) — List experiments, run experiment, list active injections, stop injection, create custom injections
- **Scoped Experiments** — `chaos_experiment()` context manager for test-time injections
- **Target Components** — LLM provider, database, message bus, tool executor, swarm engine, gateway adapter

### Config Migration UI (`/api/config/migrate/*`)
Runtime database schema migration management:
- `GET /api/config/migrate/status` — Migration status for config, task, session stores
- `POST /api/config/migrate/run` — Run pending migrations (optionally filtered by store/target version)
- `POST /api/config/migrate/rollback` — Rollback to target version
- `POST /api/config/migrate/export` — Export config + migrations as YAML

### Load Testing Infrastructure (`loadtests/`)
- **Locust Test Suite** — Swarm dispatch, WebSocket/SSE/HITL, mixed workloads
- **k6 Test Suite** — Advanced scenarios with custom metrics and thresholds
- **Runner Script** — `python loadtests/run_loadtests.py` for CI/local runs
- **CI Integration** — GitHub Actions runs load tests on main branch pushes

### Adapter Extraction (`kazma-gateway/kazma_gateway/agent_handler/swarm_output.py`)
Clean platform output abstraction:
- **SwarmOutputTarget** ABC — Abstract base for platform output adapters
- **TelegramSwarmOutputTarget** — Direct Bot API + Gateway fallback (dedicated bot token support)
- **DiscordSwarmOutputTarget** / **SlackSwarmOutputTarget** — Gateway adapter routing
- **Factory Pattern** — `send_swarm_output()` high-level dispatch function
- Removed 150+ lines of inline routing logic from `swarm_dispatch.py`

### WebSocket → SSE HITL Migration
- **Deprecated** — `/ws/chat` returns 410 Gone, redirects to SSE
- **Active** — `/api/chat/stream` with full HITL support
- **HITL Flow** — SSE emits `approval_required` event → frontend prompts → `POST /api/approve/{thread_id}` → graph resumes via `Command(resume=...)`
- **Platform-agnostic** — Same SSE contract works across Web/Telegram/Discord/Slack
