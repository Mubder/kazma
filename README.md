# Kazma — 🇰🇼 كاظمة

**Production-grade autonomous AI agent framework with multi-platform gateway, RAG memory, and human-in-the-loop safety.**

![Tests](https://img.shields.io/badge/tests-1353_passing-brightgreen)
![Version](https://img.shields.io/badge/version-0.1.0-blue)
![License](https://img.shields.io/badge/license-MIT-blue)
![Docker](https://img.shields.io/badge/docker-ready-blue)
![Python](https://img.shields.io/badge/python-3.11_|_3.12-blue)

---

## 🌍 Overview

Kazma is an open-source framework for building reliable, culturally-aware AI agents. Built on LangGraph with SQLite checkpointing, it survives crashes, remembers across sessions, and enforces safety boundaries.

**Pillars:**

| Pillar | Description |
|:---|:---|
| **Headless Gateway** | Telegram + Discord adapters with rate limiting, session isolation, and platform-agnostic backend registry |
| **Durable Execution** | LangGraph + SQLite checkpointing — agents resume mid-task after SIGKILL |
| **RAG Memory** | VectorMemory (ChromaDB + sentence-transformers) — store/retrieve facts with provenance |
| **Human-in-the-Loop** | Approval gate for dangerous tools + shared-secret authenticated endpoint |
| **Sub-Agent Spawning** | Delegate tasks to child graphs with isolated contexts |
| **Cron Autonomy** | Scheduled agent actions with SQLite-backed persistence |
| **Cultural Moat** | Native Arabic support (MSA/Gulf dialects) with "Majlis Mode" protocol |
| **Docker Deployable** | Single `docker compose up` — 2 volumes, graceful shutdown |

---

## 🏗 Architecture

```
kazma-core/              Agent graph, ReAct supervisor, sub-agents, model router, cron
│   └── kazma_core/
│       ├── agent/            Graph builder, tool registry, sub-agent manager
│       ├── memory/           VectorMemory (ChromaDB RAG)
│       ├── models/           ModelRouter (deepseek, openrouter)
│       ├── safety/           HITL approval gate
│       └── cron/             CronScheduler (SQLite)
├── kazma-gateway/        Headless Gateway — adapters, SessionStore, rate limiting
│   └── kazma_gateway/
│       ├── adapters/         TelegramAdapter, DiscordAdapter
│       ├── stores/           SQLiteSessionStore, checkpoint store
│       └── gateway.py        GatewayManager, MessageMetrics, RateLimiter
├── kazma-ui/             FastAPI + Jinja2 dashboard (Arabic RTL)
│   └── kazma_ui/
│       ├── app.py            FastAPI app, shutdown handler
│       ├── gateway_monitor.py /api/gateway/status endpoint
│       ├── metrics.py        Prometheus /metrics endpoint
│       └── templates/        index.html (SSE chat, metrics, HITL cards)
├── kazma-tui/            Textual TUI with Arabic/RTL support
├── kazma-cli/            CLI entry point (status, serve, hub, docs)
├── kazma-memory/         SQLite FTS5 + Arabic tokenizer
├── kazma-skills/         YAML skill manifests + MCP server registry
├── kazma-providers/      LiteLLM router (multi-provider failover)
├── tests/                1,353 tests (pytest + asyncio)
├── docker-compose.yml    Single-command deployment
└── archive/              Deprecated (kazma-comms, kazma-connectors)
```

---

## 📦 Quick Start

### Prerequisites

- **Python 3.11+**
- **uv** (recommended) or pip
- **Docker** (optional, for production)

### Install

```bash
git clone https://github.com/Mubder/kazma.git
cd kazma

# Install with uv
uv sync
uv pip install -e ".[dev]"

# Run tests
uv run pytest tests/ -q
```

### Run

```bash
# Web UI (default port 8000)
uv run kazma-web

# Web UI with custom port
uv run kazma-web --port 8080

# Via Python module
uv run python -m kazma_ui.app --port 8080

# Terminal UI
uv run kazma-tui

# CLI
uv run kazma status
uv run kazma serve 8080
uv run kazma hub search <query>
```

Then open http://localhost:8000 (or your chosen port).

### Docker (Production)

```bash
cp .env.example .env   # fill in API keys
docker compose up -d
```

---

## ⚙️ Configuration

Kazma uses `kazma.yaml` at the project root:

```yaml
agent:
  name: "kazma"
  version: "0.1.0"
  language: "ar"
  rtl: true

gateway:
  rate_limits:
    telegram: 30     # requests/second
    discord: 5

safety:
  hitl:
    enabled: true
    tiers:
      safe: [read_file, search_files, memory_search]
      warning: [write_file, patch]
      danger: [shell_exec, file_delete]

models:
  providers:
    - name: deepseek
      base_url: https://api.deepseek.com/v1
      models: [deepseek-chat, deepseek-reasoner]
    - name: openrouter
      base_url: https://openrouter.ai/api/v1
      models: [openai/gpt-4o-mini]

storage:
  session_store_path: "kazma-data/sessions.db"
  checkpoint_path: "kazma-data/checkpoints.db"
  cron_path: "kazma-data/cron.db"

logging:
  level: info
  format: console
```

For overrides, copy to `kazma.local.yaml` (git-ignored). Env vars take precedence.

### Environment Variables

| Variable | Description | Default |
|:---|:---|:---|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | — |
| `DISCORD_BOT_TOKEN` | Discord bot token | — |
| `DEEPSEEK_API_KEY` | DeepSeek API key | — |
| `OPENROUTER_API_KEY` | OpenRouter API key | — |
| `KAZMA_SECRET` | HITL approval shared secret (optional) | — |
| `KAZMA_VECTOR_PATH` | VectorMemory storage path | `~/.kazma/vector_memory` |
| `KAZMA_VECTOR_COLLECTION` | ChromaDB collection name | `agent_memory` |
| `KAZMA_VECTOR_MODEL` | Sentence-transformers model | `all-MiniLM-L6-v2` |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key | — |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key | — |

---

## 🧪 Tests

```bash
# Full suite
uv run pytest tests/ -q

# With coverage
uv run pytest --cov=kazma_core --cov-report=html tests/

# Specific modules
uv run pytest tests/integration/test_rag_pipeline.py -v
uv run pytest tests/ -k "gateway" -v

# Docker smoke test
docker compose up --build -d
curl http://localhost:8000/api/gateway/status
```

### Test Modules

| Module | Tests | Coverage Area |
|:---|:---:|:---|
| `test_gateway.py` | 61 | GatewayManager, sessions, adapters, status |
| `test_rag_pipeline.py` | 6 | VectorMemory store → agent retrieve → response |
| `test_queue_processor.py` | — | Message queue processing |
| Integration tests | 6 | RAG end-to-end pipeline |

---

## 📐 Development

```bash
# Lint & format
ruff check .
ruff format .

# Type check
mypy kazma-core/kazma_core/

# Watch mode
uv run pytest tests/ -f
```

---

## 📜 License

MIT
