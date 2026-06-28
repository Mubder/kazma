# Kazma - كاظمة

**Production-grade autonomous AI agent framework with multi-platform gateway, RAG memory, and human-in-the-loop safety.**

![Tests](https://img.shields.io/badge/tests-2,382+_passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.11_|_3.12-blue)
![Version](https://img.shields.io/badge/version-0.1.0-blue)
![Portability](https://img.shields.io/badge/portability-linux_|_macOS_|_Windows_|_docker_|_WSL-brightgreen)
![Docker](https://img.shields.io/badge/docker-ready-blue)

---

## 🌍 Overview

Kazma is an open-source framework for building reliable, culturally-aware AI agents. Built on LangGraph with SQLite checkpointing, it survives crashes, remembers across sessions, and enforces safety boundaries.

**Pillars:**

| Pillar | Description |
|:---|:---|
| **Headless Gateway** | Telegram + Discord + Slack adapters with rate limiting, session isolation, and platform-agnostic backend registry |
| **Durable Execution** | LangGraph + SQLite checkpointing — agents resume mid-task after SIGKILL |
| **RAG Memory** | VectorMemory (ChromaDB + sentence-transformers) + FTS5 full-text search — store/retrieve facts with provenance |
| **Human-in-the-Loop** | Approval gate for dangerous tools + shared-secret authenticated endpoint |
| **Sub-Agent Spawning** | Delegate tasks to child graphs: in-process (SubAgentManager) or distributed (Swarm Panel) |
| **Swarm Orchestration** | Multi-worker panel — health monitoring, dispatch, lifecycle control |
| **Cron Autonomy** | Scheduled agent actions with SQLite-backed persistence |
| **Cultural Moat** | Native Arabic support (MSA/Gulf dialects) with "Majlis Mode" protocol |
| **Docker Deployable** | Single `docker compose up` — 2 volumes, graceful shutdown |

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

### Windows Setup

For Windows users, a PowerShell bootstrap script is provided that installs
dependencies, validates the Python version, and configures portable paths:

```powershell
# From PowerShell (Run as Administrator for optional PATH integration)
.\setup.ps1
```

The script:
- Validates Python 3.11+ is available
- Creates a virtual environment with `uv` (falls back to `pip`)
- Installs all dependencies including optional extras
- Configures portable, user-writable data paths (no hardcoded home folders)
- Optionally installs PowerShell tab completion for the `kazma` CLI

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

## ✨ Features

### 🧠 Agent Core

| ✅ | Feature | Description |
|:---:|:---|:---|
| ✅ | ReAct Supervisor | LangGraph-based agent with tool-calling loop and SQLite checkpointing |
| ✅ | Durable Checkpoints | Agents resume mid-task after crash — SQLite-backed graph state |
| ✅ | Sub-Agent Spawning | Delegate tasks to child graphs (in-process) or swarm workers (distributed) |
| ✅ | Swarm Orchestration | Multi-worker panel with health monitoring, dispatch, and lifecycle control |
| ✅ | Cron Autonomy | Scheduled agent actions with SQLite persistence |
| ✅ | Auto-Summarization | Context compaction when token window exceeds 4K threshold |
| ✅ | Model Router | Multi-provider routing (DeepSeek, OpenRouter) with intelligent selection |
| ✅ | Retry & Backoff | Exponential backoff with configurable attempts, min/max wait |
| ✅ | Time Travel | Snapshot-based replay engine — rewind to any iteration |
| ✅ | Knowledge Graph | NetworkX MultiDiGraph backend with KG memory adapter |
| ✅ | FTS5 Memory | SQLite full-text search with BM25 ranking — keyword search alongside vector search |

### 🔧 Tools

| ✅ | Feature | Description |
|:---:|:---|:---|
| ✅ | Web Search | DuckDuckGo-powered search returning markdown results |
| ✅ | URL Reader | Fetch + extract readable content via trafilatura (8K char cap) |
| ✅ | Code Execution | Sandboxed Python subprocess (`-I` isolated, 30s timeout, 512MB limit) |
| ✅ | Image Generation | pollinations.ai-backed image gen, saved to `kazma-data/images/` |
| ✅ | Vision Analysis | Analyze images via LLM vision capabilities |
| ✅ | Voice Transcription | Telegram voice message transcription via STT |
| ✅ | File I/O | Read, write, list, and search files through the agent (with HITL gates) |
| ✅ | Export Session | Save conversation history to file |
| ✅ | MCP Bridge | UnifiedToolExecutor — unified local + MCP tool routing across all registries |

### 🎭 Experience

| ✅ | Feature | Description |
|:---:|:---|:---|
| ✅ | 8 Personalities | `default`, `friendly_expert`, `concise`, `gulf_engineer`, `creative_partner`, `sysadmin`, `teacher`, `code_reviewer` |
| ✅ | Runtime Switching | `/personality` — instant, zero-token change at any time |
| ✅ | 12 Slash Commands | `/help`, `/reset`, `/status`, `/model`, `/memory`, `/cost`, `/undo`, `/edit`, `/replay`, `/personality`, `/context`, `/config` |
| ✅ | Quick Reply Buttons | Telegram inline keyboards for HITL approvals + personality selection |
| ✅ | Proactive Suggestions | Post-task next-step hints + automatic tool-intent detection |
| ✅ | Rate Feedback | Friendly cooldown messages when user hits rate limits |
| ✅ | Context Indicator | Token usage report with role breakdown via `/context` |
| ✅ | Message Edit/Delete | `/undo` and `/edit` with platform-level sync |
| ✅ | Shell Completions | Bash, zsh, and PowerShell tab completion for all CLI commands |
| ✅ | Project Init | `.kazma/` directory system — rules, context, personality, tools |

### 🎨 Web UI

| ✅ | Feature | Description |
|:---:|:---|:---|
| ✅ | Dashboard | FastAPI + Jinja2 dashboard with 12-tab settings, SSE chat, Arabic RTL |
| ✅ | Dark Mode | Theme toggle with accessible dropdown contrast (WCAG-compliant) |
| ✅ | Model Selection | Chat-model selector with provider switch on save, SSE model passthrough, API key validation |
| ✅ | Bilingual UI | EN/AR language toggle with cookie middleware and shared Jinja2Templates |
| ✅ | i18n System | Complete internationalization layer with 150+ Arabic translations and 71 RTL CSS selectors |
| ✅ | Arabic Typography | Cairo font for native Arabic rendering |
| ✅ | HITL Approval UI | Inline approve/deny panel for tiered tool-safety gates |
| ✅ | Session History | Load and browse prior conversations from any session |
| ✅ | Agents Page | Dedicated page for agent inspection and control |
| ✅ | Swarm Panel | Worker table, dispatch form, and lifecycle controls at `/swarm |
| ✅ | Telemetry | SSE telemetry with deduplicated route streaming and null-safe toast notifications |
| ✅ | Service Facade | Zero private attribute access from UI — all access via the service layer |

### 🌍 Platform

| ✅ | Feature | Description |
|:---:|:---|:---|
| ✅ | Telegram Adapter | Full bot support with MarkdownV2, typing indicators, voice transcription |
| ✅ | Discord Adapter | Native Markdown, rate-limited |
| ✅ | Slack Adapter | Socket Mode with 429 retry and event parsing |
| ✅ | Cross-Platform Gateway | Platform-agnostic backend registry, reply metadata envelope |
| ✅ | Web UI | FastAPI + Jinja2 dashboard with 12-tab settings, SSE chat, provider management, Arabic RTL |
| ✅ | Terminal UI | Textual TUI with Arabic/RTL support |

### 🔒 Safety & Security

| ✅ | Feature | Description |
|:---:|:---|:---|
| ✅ | HITL Approval Gates | Tiered tool approval: safe/warning/danger, inline keyboard approve/deny |
| ✅ | Cost Circuit Breaker | Budget-aware — halts agent when limit reached |
| ✅ | RBAC Permissions | Role-based access control for tools and commands |
| ✅ | Security Linter | Static analysis for security anti-patterns |
| ✅ | Dependency Scanner | Vulnerability scanning for Python dependencies |
| ✅ | Audit Trail | Full disclosure logging and certification chain |
| ✅ | Disclosure System | Automatic capability disclosure on first interaction |

### 🏗 Deploy & Monitor

| ✅ | Feature | Description |
|:---:|:---|:---|
| ✅ | Docker Deployable | Single `docker compose up` — 2 volumes, graceful shutdown |
| ✅ | Prometheus Metrics | `/metrics` endpoint for monitoring |
| ✅ | SSE Telemetry | Real-time server-sent events for hardware + agent status |
| ✅ | Hardware Telemetry | Async CPU, RAM, GPU monitoring |
| ✅ | MCP Server | IDE integration via MCP protocol (VS Code extensions) |
| ✅ | Kazma Hub | Skill marketplace — search, install, publish, certify |
| ✅ | Docusaurus Docs | Full documentation site with security guides |
| ✅ | Portability | Runs on Linux, macOS, Windows, Docker, and WSL — no OS-specific hooks |

---

## 🐝 Swarm Orchestration

Kazma supports two sub-agent delegation modes:

| Mode | Mechanism | Use Case |
|:---|:---|:---|
| **In-Process** | `SubAgentManager` — child LangGraph graphs in the same Python process | Quick parallel subtasks, isolated context |
| **Distributed Swarm** | Swarm Panel — register workers, dispatch tasks, monitor health via Web UI | Multi-machine deployments, Telegram bot workers |

### Swarm Architecture

```
┌──────────────────────────────────────────────────────┐
│                  🐝 Swarm Panel (/swarm)              │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │  Workers  │  │  Dispatch │  │  Start/Stop All   │   │
│  │  Table    │  │  Form     │  │  Controls         │   │
│  └──────────┘  └──────────┘  └──────────────────┘   │
├──────────────────────────────────────────────────────┤
│                  REST API (/api/swarm/*)              │
│  GET /status  POST /dispatch  POST /workers          │
│  DELETE /workers/{name}  POST /start  POST /stop     │
├──────────────────────────────────────────────────────┤
│              Backend (kazma_core.swarm)               │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐              │
│  │Worker-1 │  │Worker-2 │  │Worker-N │  ...          │
│  │(online) │  │(busy)   │  │(offline)│              │
│  └─────────┘  └─────────┘  └─────────┘              │
└──────────────────────────────────────────────────────┘
```

### Swarm Config (`kazma.yaml`)

```yaml
swarm:
  enabled: true
  max_concurrent: 3
  workers:
    - name: "builder"
      model: "deepseek-chat"
      provider: "deepseek"
      type: "in-process"
      role: "leaf"
    - name: "reviewer"
      model: "claude-sonnet-4"
      provider: "anthropic"
      type: "telegram"
      bot_token: "${TELEGRAM_BOT_TOKEN_2}"
      role: "leaf"
    - name: "researcher"
      model: "gpt-4o-mini"
      provider: "openai"
      type: "in-process"
      role: "orchestrator"

  dispatch:
    default_strategy: "round-robin"
    retry_on_failure: true
    max_retries: 2
```

### Web UI Panel

Access at `/swarm` when the server is running:

```bash
uv run kazma-web
# Then open http://localhost:8000/swarm
```

The panel shows:
- **Worker table**: name, model, provider, type, health status (🟢 online / 🟡 busy / 🔴 offline)
- **Add Worker form**: register in-process or Telegram bot workers
- **Dispatch form**: select workers, enter task, send
- **Start/Stop All**: lifecycle control for the entire swarm

---

## 🏗 Architecture

```
kazma-core/              Agent graph, ReAct supervisor, sub-agents, model router, cron
│   └── kazma_core/
│       ├── agent/            Graph builder, UnifiedToolExecutor, sub-agent manager
│       ├── memory/           VectorMemory (ChromaDB RAG), Knowledge Graph adapter
│       ├── models/           ModelRouter (deepseek, openrouter), provider discovery
│       ├── safety/           HITL approval gate, RBAC permissions
│       ├── security/         Linter, dependency scanner, audit trail, disclosure
│       ├── cron/             CronScheduler (SQLite)
│       ├── mcp/              MCP bridge + UnifiedToolExecutor tool router
│       └── tools/            15+ built-in tools (web, code, image, vision, files)
├── kazma-gateway/        Headless Gateway — adapters, SessionStore, rate limiting
│   └── kazma_gateway/
│       ├── adapters/         TelegramAdapter, DiscordAdapter, SlackAdapter
│       ├── stores/           SQLiteSessionStore, unified checkpoint store
│       ├── gateway.py        GatewayManager, MessageMetrics, RateLimiter
│       ├── dispatcher.py     MessageDispatcher, slash command routing
│       ├── suggestions.py    Post-task hints + tool-intent detection
│       ├── rate_feedback.py  Friendly rate-limit cooldown messages
│       └── mcp_server.py     IDE MCP server
├── kazma-ui/             FastAPI + Jinja2 dashboard (Arabic RTL, bilingual EN/AR)
│   └── kazma_ui/
│       ├── app.py            FastAPI app, shutdown handler, SSE endpoints
│       ├── services.py       Service facade layer — zero private attr access from UI
│       ├── i18n.py           Internationalization (150+ AR translations, cookie locale)
│       ├── gateway_monitor.py /api/gateway/status endpoint
│       └── metrics.py        Prometheus /metrics endpoint
├── kazma-tui/            Textual TUI with Arabic/RTL support
├── kazma-cli/            CLI entry point (status, serve, hub, docs, wizard, project)
├── kazma-memory/         SQLite FTS5 + Arabic tokenizer
├── kazma-skills/         YAML skill manifests + MCP server registry
├── kazma-providers/      LiteLLM router (multi-provider failover)
├── tests/                2,382+ tests (pytest + asyncio)
├── docs/                 Docusaurus documentation site
├── docker-compose.yml    Single-command deployment
├── setup.sh              POSIX bootstrap (Linux / macOS / WSL)
├── setup.ps1             Windows PowerShell bootstrap
└── archive/              Deprecated (kazma-comms, kazma-connectors)
```

**Portability:** Kazma runs on any Linux, macOS, Windows, Docker, or WSL machine with zero modifications. No hardcoded home paths, no OS-specific hooks, no architecture assumptions. [Read the policy →](docs/portability.md)

---

## 💬 Slash Commands

Kazma resolves slash commands **instantly (<50ms)** without any LLM call. Full reference at [docs/slash-commands.md](docs/slash-commands.md).

| Command | Category | Description |
|:---|:---|:---|
| `/help` | Info | List all available commands grouped by category |
| `/status` | Info | Gateway health overview |
| `/model` | Info | Show active model |
| `/memory` | Info | Report memory usage |
| `/cost` | Info | Token spend this session |
| `/reset` | Session | Clear conversation history |
| `/undo` | Session | Remove last agent response |
| `/edit <text>` | Session | Correct last agent response |
| `/replay list` | Session | Show available snapshots |
| `/replay <N>` | Session | Replay from iteration N |
| `/replay compare <A> <B>` | Session | Diff two replay runs |
| `/replay clear` | Session | Purge all snapshots |
| `/personality` | Tool | Show or switch personality |
| `/context` | Tool | Context window token usage |
| `/config` | Tool | Interactive config wizard (7 sub-commands) |

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

2382+ collected and passing (a small number may be skipped due to missing optional deps: chromadb, duckduckgo_search, trafilatura).

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

## 📚 Documentation

- [Slash Commands Reference](docs/slash-commands.md)
- [Portability Policy](docs/portability.md)
- [Context Compaction](docs/compaction.md)
- [Skill Manifest Spec](docs/skill-manifest-spec.md)
- [Changelog](CHANGELOG.md)

---

## 📜 License

MIT
