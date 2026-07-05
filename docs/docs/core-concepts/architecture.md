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
