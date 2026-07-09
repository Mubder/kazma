# Kazma - كاظمه


**Production-grade autonomous AI agent framework with multi-platform gateway, RAG memory, and human-in-the-loop safety.**


![Tests](https://img.shields.io/badge/tests-3500+_suite-brightgreen)
![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.11_|_3.12-blue)
![Version](https://img.shields.io/badge/version-0.3.0-blue)
![Portability](https://img.shields.io/badge/portability-linux_|_macOS_|_Windows_|_docker_|_WSL-brightgreen)
![Docker](https://img.shields.io/badge/docker-ready-blue)
[![Framework][langgraph-shield]][langgraph-url]
[![Package Manager][uv-shield]][uv-url]
[![Status][status-shield]][status-url]
![swarm-orchestration](https://img.shields.io/badge/swarm-orchestration-blueviolet.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-05998b?style=flat&logo=fastapi&logoColor=white)
![ChromaDB](https://img.shields.io/badge/ChromaDB-6a5acd?style=flat&logo=chromadb&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-07405e?style=flat&logo=sqlite&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ed?style=flat&logo=docker&logoColor=white)
![Prometheus](https://img.shields.io/badge/Prometheus-e6522c?style=flat&logo=prometheus&logoColor=white)
![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-000000?style=flat&logo=opentelemetry&logoColor=white)
![Locust](https://img.shields.io/badge/Locust-2.45.0-red)
![k6](https://img.shields.io/badge/k6-load_testing-7d64ff)
<!-- LINKS -->
[python-shield]: https://img.shields.io/badge/python-3.11+-blue.svg
[python-url]: https://www.python.org/downloads/
[langgraph-shield]: https://img.shields.io/badge/LangGraph-build-brightgreen.svg
[langgraph-url]: https://www.langchain.com/langgraph
[uv-shield]: https://img.shields.io/badge/uv-managed-purple.svg
[uv-url]: https://github.com/astral-sh/uv
[tests-shield]: https://img.shields.io/badge/tests-3500+_suite-success.svg
[tests-url]: https://github.com/Mubder/kazma
[license-shield]: https://img.shields.io/github/license/Mubder/kazma.svg
[license-url]: https://github.com/Mubder/kazma/blob/main/LICENSE
[status-shield]: https://img.shields.io/badge/status-production_ready-gold.svg
[status-url]: https://kazma.ai
---

## 🌍 Overview

Kazma is an open-source framework for building reliable, culturally-aware AI agents. Built on LangGraph with SQLite checkpointing, it survives crashes, remembers across sessions, and enforces safety boundaries.

**Pillars:**

| Pillar | Description |
|:---|:---|
| **Headless Gateway** | Telegram + Discord + Slack adapters with rate limiting, session isolation, and platform-agnostic backend registry |
| **Durable Execution** | LangGraph + SQLite checkpointing — agents resume mid-task after SIGKILL |
| **Atomic ConfigStore** | SQLite-backed runtime settings with WAL journaling, process-wide singleton, and batch transactions — concurrent writes never corrupt |
| **RAG Memory** | VectorMemory (ChromaDB + sentence-transformers) + FTS5 full-text search — store/retrieve facts with provenance |
| **Human-in-the-Loop** | 3-tier approval gates (graph interrupt + swarm bus + MCP classification) for danger tools, fail-closed by default, wired across all platforms |
| **Sub-Agent Spawning** | Delegate tasks to child graphs: in-process (SubAgentManager) or distributed (Swarm Panel) |
| **Swarm Orchestration** | Multi-worker panel — health monitoring, dispatch, per-worker start/stop, circuit breaker badges, lifecycle control |
| **Cron Autonomy** | Scheduled agent actions with SQLite-backed persistence |
| **Cultural Moat** | Native Arabic support (MSA/Gulf dialects) with "Majlis Mode" protocol |
| **Docker Deployable** | Single `docker compose up` — 2 volumes, graceful shutdown |





---

## 🚀 Phase 3: Production Hardening (July 2026)

| Feature | Description |
|:---|:---|
| **Config Migration UI** | `/api/config/migrate/*` endpoints — status, run, rollback, export for config/task/session stores |
| **Chaos Testing Framework** | Failure injection engine with 10 predefined experiments (LLM latency, DB errors, network partitions, circuit breaker force-open, resource exhaustion) + UI endpoints `/api/chaos/*` |
| **Load Testing Infrastructure** | Locust + k6 test suites for swarm dispatch, SSE/HITL, mixed workloads; CI integration on main branch |
| **Adapter Extraction** | Clean SwarmOutputTarget ABC with Telegram (direct Bot API + Gateway fallback), Discord, Slack adapters — removed 150+ lines of inline routing from swarm_dispatch.py |
| **WebSocket → SSE HITL** | Deprecated `/ws/chat` (410 Gone); active `/api/chat/stream` with full HITL approval flow via `approval_required` events |




---

## 📦 Quick Start
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

Kazma ships with **three entry points**:

| Entry point | Command | Description |
|:---|:---|:---|
| **Web UI** | `kazma-web` (or `kazma serve`) | FastAPI dashboard with chat, swarm panel, settings |
| **Terminal UI** | `kazma-tui` | Textual TUI with English-only metrics/chat dashboard |
| **CLI** | `kazma` | Banner, status, hub, gateway, and swarm management |

```bash
# --- Web UI (default port 8000) ---
uv run kazma-web                 # start WebUI
uv run kazma-web --port 8080     # custom port
uv run kazma serve 8080          # same thing via the CLI
uv run python -m kazma_ui.app --port 8080   # via Python module

# --- Terminal UI ---
uv run kazma-tui

# --- CLI ---
uv run kazma                     # banner + config check + status overview
uv run kazma status              # real system status (gateway, swarm, server)
uv run kazma serve 8080          # start WebUI server on a custom port
uv run kazma hub search <query>  # search the skill marketplace
uv run kazma swarm status        # show swarm workers and health
```

Then open http://localhost:8000 (or your chosen port).

### Docker (Production)

```bash
cp .env.example .env   # fill in API keys
docker compose up -d
```

> **Troubleshooting:** See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for common errors and solutions.

---

## 🖥️ CLI Commands

The `kazma` CLI is the unified control plane for Kazma. Run `kazma` with no
arguments for a banner, config check, and status overview, or `kazma help` for
a quick command list. Commands are grouped into **Core**, **Gateway**, **Swarm**,
**Hub**, **Project**, and **Docs**.

### Core Commands

| Command | Description |
|:---|:---|
| `kazma` | Banner, config check, and status overview |
| `kazma status` | Real system status (gateway, swarm, server health) |
| `kazma serve [port]` | Start the WebUI server (default port `8000`) |
| `kazma wizard` | Interactive skill installation wizard |
| `kazma completion <bash\|zsh\|powershell\|install>` | Generate or install shell tab-completion |
| `kazma update [--check] [--force] [--yes]` | Check for and install Kazma CLI updates |
| `kazma help` / `--help` / `-h` | Show help |

### Gateway Commands

| Command | Description |
|:---|:---|
| `kazma gateway status` | Show gateway adapter status (Telegram, Discord, Slack) |
| `kazma gateway start` | Start the gateway |
| `kazma gateway stop` | Stop the gateway |
| `kazma gateway restart` | Restart the gateway (stop + start) |
| `kazma gateway refresh` | Refresh/reload gateway adapters |

### Swarm Commands

| Command | Description |
|:---|:---|
| `kazma swarm status` | Show swarm workers and health |
| `kazma swarm workers` | List all registered workers |
| `kazma swarm worker add <name>` | Add a worker |
| `kazma swarm worker spawn <name> <role>` | Spawn a dynamic worker |
| `kazma swarm worker remove <name>` | Remove a worker |
| `kazma swarm dispatch <worker> <prompt>` | Dispatch a task to a single worker |
| `kazma swarm broadcast <prompt>` | Broadcast a prompt to all workers |
| `kazma swarm consult <prompt> --workers a,b` | Consult multiple workers |
| `kazma swarm pipeline --workers a,b,c <prompt>` | Sequential pipeline |
| `kazma swarm fanout --workers a,b <prompt>` | Fan-out / fan-in |
| `kazma swarm history` | Task history (filterable) |
| `kazma swarm task <id>` | Show task detail |
| `kazma swarm metrics [--worker W]` | Worker metrics |
| `kazma swarm start` | Start all workers |
| `kazma swarm stop` | Stop all workers |
| `kazma swarm approve <task_id>` | Approve a HITL checkpoint |
| `kazma swarm reject <task_id>` | Reject a HITL checkpoint |
| `kazma swarm circuit-breaker [worker] [--reset]` | Circuit breaker status or reset |

### Hub Commands (skill marketplace)

| Command | Description |
|:---|:---|
| `kazma hub register <name>` | Register a skill locally |
| `kazma hub search <query>` | Search the skill marketplace |
| `kazma hub install <name>` | Install a skill |
| `kazma hub list` | List installed skills |
| `kazma hub info <name>` | Show skill details |
| `kazma hub validate <path>` | Validate a skill manifest |
| `kazma hub uninstall <name>` | Uninstall a skill |
| `kazma hub submit <path>` | Submit a skill to the hub |
| `kazma hub status` | Show hub connection status |
| `kazma hub badge <name>` | Show a skill's certification badge |
| `kazma hub certified` | List certified skills |
| `kazma hub stats` | Show hub statistics |
| `kazma hub check-certification <name>` | Check certification requirements |

### Project Commands

| Command | Description |
|:---|:---|
| `kazma project init` | Initialize a `.kazma/` project directory |
| `kazma project show` | Show project configuration |
| `kazma project validate` | Validate project configuration |

### Docs Commands

| Command | Description |
|:---|:---|
| `kazma docs build` | Build the Docusaurus documentation site |
| `kazma docs serve [port]` | Serve documentation locally (default port `3000`) |

### Examples

```bash
# Gateway lifecycle
kazma gateway start
kazma gateway status

# Add and dispatch a swarm worker
kazma swarm worker add researcher --model gpt-4o-mini --provider openai --type in-process --role researcher
kazma swarm dispatch researcher "Summarize today's news in 3 bullets"

# Orchestrate a pipeline across three workers
kazma swarm pipeline --workers researcher,writer,reviewer "Draft a blog post about LangGraph"

# Fan-out with vote aggregation
kazma swarm fanout --workers a,b,c "Rate this PR 1-10" --aggregation vote

# Consult two experts and get a synthesized answer
kazma swarm consult "Best database for time-series?" --workers dba,architect --context "100TB scale"

# Approve a paused HITL checkpoint
kazma swarm approve task_42

# Inspect task history and metrics
kazma swarm history --type pipeline --status completed --page 1 --page-size 20
kazma swarm metrics --worker researcher
```

### CLI Quick Reference

| What you want to do | Command |
|:---|:---|
| See overall status | `kazma status` |
| Start the Web UI | `kazma serve` (or `kazma-web`) |
| Start the terminal UI | `kazma-tui` |
| Start the gateway | `kazma gateway start` |
| List swarm workers | `kazma swarm workers` |
| Send one task to one worker | `kazma swarm dispatch <worker> <prompt>` |
| Send one task to all workers | `kazma swarm broadcast <prompt>` |
| Chain workers sequentially | `kazma swarm pipeline --workers a,b <prompt>` |
| Run the same task in parallel | `kazma swarm fanout --workers a,b <prompt>` |
| Get expert opinions + synthesis | `kazma swarm consult <prompt> --workers a,b` |
| Approve a paused task | `kazma swarm approve <id>` |
| Search the skill marketplace | `kazma hub search <query>` |
| Install a skill | `kazma hub install <name>` |
| Initialize a project | `kazma project init` |
| Install shell completion | `kazma completion install` |
| Check for and install updates | `kazma update` |

> Full syntax, flags, and exit codes for every command are in the
> [CLI Reference](docs/docs/api-reference/cli-reference.md).

---

## ✨ Features

### 🧠 Agent Core

| ✅ | Feature | Description |
|:---:|:---|:---|
| ✅ | ReAct Supervisor | LangGraph-based agent with tool-calling loop and SQLite checkpointing |
| ✅ | Durable Checkpoints | Agents resume mid-task after crash — SQLite-backed graph state |
| ✅ | Atomic ConfigStore | SQLite runtime settings — WAL, singleton connection, batch transactions, crash-safe multi-key writes |
| ✅ | Sub-Agent Spawning | Delegate tasks to child graphs (in-process) or swarm workers (distributed) |
| ✅ | Swarm Orchestration | Multi-worker panel with health monitoring, dispatch, consult, pipeline, and lifecycle control |
| ✅ | WorkerRegistry | Persistent worker phonebook with CRUD — survives reboots, JSON-backed |
| ✅ | Smart-Fallback Routing | Zero dispatch failures — auto-delegates to generalist workers |
| ✅ | 4-Layer Memory | ChromaDB + NetworkX + SQLite FTS5 + sqlite-vec with RRF blending |
| ✅ | Pipeline Engine | Researcher → Refiner → Builder → Validator DAG execution |
| ✅ | Refiner Middleman | Synthesizes collective output into clean Markdown report cards |
| ✅ | SQLite Pipeline Logger | Every step persisted for Web UI diagnostics |
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
| ✅ | MCP Bridge | UnifiedToolExecutor — unified local + MCP tool routing with HITL gates and per-server auth/trust |

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
| ✅ | i18n System | Complete internationalization layer with 400+ Arabic translations and 70+ RTL CSS selectors |
| ✅ | Arabic Typography | Cairo font for native Arabic rendering |
| ✅ | HITL Approval UI | Inline approve/deny panel for tiered tool-safety gates — fully wired across Web, Telegram, Discord, and Slack |
| ✅ | Session History | Load and browse prior conversations from any session |
| ✅ | Agents Page | Dedicated page for agent inspection and control |
| ✅ | Swarm Panel | Redesigned tabbed UI with Task Builder (orchestration pattern selector, worker multi-select with capability badges, advanced options), Active Tasks (SSE-connected live progress, HITL checkpoints, handoff chains), Results Dashboard (pipeline steps, fan-out cards, consult comparison, conditional routing), Worker Registry (cards with metrics, circuit breaker badges, per-worker start/stop, add/remove, dynamic spawn), Task History (searchable/filterable table with detail modal) |
| ✅ | Telemetry | SSE telemetry with deduplicated route streaming and null-safe toast notifications |


### 🌍 Platform

| ✅ | Feature | Description |
|:---:|:---|:---|
| ✅ | Telegram Adapter | Full bot support with MarkdownV2, typing indicators, voice transcription |
| ✅ | Discord Adapter | Native Markdown, rate-limited |
| ✅ | Slack Adapter | Polling-based Web API adapter with 429 retry |
| ✅ | Cross-Platform Gateway | Platform-agnostic backend registry, reply metadata envelope |
| ✅ | Web UI | FastAPI + Jinja2 dashboard with 12-tab settings, SSE chat, provider management, Arabic RTL |
| ✅ | Terminal UI | Textual TUI with English-only metrics/chat dashboard |

### 🔒 Safety & Security

| ✅ | Feature | Description |
|:---:|:---|:---|
| ✅ | HITL Approval Gates | 3-tier approval system (graph interrupt + swarm bus + MCP classification). Danger tools (file_write, shell_exec, code_exec) require human approval on all platforms. Fail-closed by default. |
| ✅ | MCP Tool Security | Per-server auth (bearer/header tokens) + trust levels + HITL gate for danger-tier MCP tools |
| ✅ | Cost Circuit Breaker | Budget-aware — halts agent when limit reached. Per-worker breaker state with UI badges |
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

### Swarm Panel UI

The redesigned Swarm Panel at `/swarm` provides a tabbed interface:

- **Task Builder** — Orchestration pattern selector (dispatch, broadcast, pipeline, fan-out, consult, conditional), worker multi-select with capability badges (role + expertise tags), prompt/context textareas, advanced options (timeout, max retry count, aggregation strategy, validation schema), conditional routing rules
- **Active Tasks** — SSE-connected live progress with per-worker status events, HITL checkpoint cards with approve/reject buttons, handoff chain visualization
- **Results Dashboard** — Pattern-specific result views: pipeline step-by-step, fan-out per-worker grid, consult side-by-side comparison with synthesized answer, conditional routing decision display. Sub-tab filtering by orchestration type
- **Worker Registry** — Worker cards with name, status, role, model, capabilities, and per-worker metrics (success rate, avg latency, cost). Add/remove workers. Dynamic spawn form with expertise tags, tools, and model specialty
- **Task History** — Searchable and filterable table with pagination. Click any row to view full task detail with worker results and handoff chains

### Orchestration patterns

The swarm engine supports these worker orchestration patterns:

- `dispatch` for one worker
- `broadcast` for all registered workers
- `pipeline` for sequential handoff between workers
- `fan_out` for concurrent execution of the same prompt across selected workers
- `consult` for collecting independent role-aware worker opinions and synthesizing a consolidated answer
- `conditional` for routing to different workers based on a router worker's evaluation of the prompt
- `auto` routing via `UnifiedRouter` — when `workers=["auto"]`, the engine performs high-accuracy task distribution using a multi-stage routing pipeline: it queries `SemanticRouter` first for semantic similarity vector distance (via ChromaDB), applies language/dialect-aware score boosting metrics to the weights, and falls back gracefully to a high-performance static token-overlap keyword match (`_keyword_match`) if vector databases are offline or return below-threshold scores.

Fan-out supports `first_valid`, `merge_all`, `vote`, `synthesize`, and
`collect` aggregation strategies. Parallel dispatches are bounded by
`swarm.max_concurrent` (default `5`), and requests can override the limit with
`max_concurrent` when needed.

#### Consult mode

`consult` sends the prompt to each selected worker independently with a
role-aware system prompt derived from that worker's `WorkerCapabilities`
(role, expertise, model specialty). Workers never see each other's opinions.
After collecting the opinions, the engine runs an orchestrator synthesis step
that produces a `synthesized_output` referencing every successful worker by
name. Results include both `individual_opinions` and `synthesized_output`.

Consult handles partial failure by synthesizing from available opinions
(status `partial`), all-failure by returning no synthesis (status `failed`),
and the single-worker edge case with a passthrough synthesis. Completed
consult tasks are persisted to the in-memory task history and are queryable
via `GET /api/swarm/tasks?type=consult`.

#### Conditional routing
`conditional` uses a two-step dispatch. First, a router worker evaluates the
prompt and outputs a routing decision (e.g., `"code"`, `"research"`). The
engine then looks up that decision in `metadata.routes` (a dict mapping
decision strings to worker names) and dispatches to the matched worker.

If the router output does not match any route key, the engine falls back to
the worker named in `metadata.default` (if set), or returns `status=failed`
with a `"No route matched."` error listing the available routes.

If the router worker itself fails or times out, the task halts immediately
with `status=failed` (or `status=timeout`) and no downstream worker
executes. The routing decision is always recorded in
`metadata.route_taken` (or `null` when no route was taken).

### Reliability layer

The swarm engine includes a reliability layer in `kazma_core/swarm/reliability.py`
with five components that protect worker dispatches:

- **RetryPolicy** — configurable retry with exponential backoff and jitter.
  Per-worker via `engine.set_retry_policy()`. Default `max_retries=0` for
  backward compatibility.
- **CircuitBreaker** — per-worker closed/open/half-open state machine. Tracks `consecutive_tool_failures` persistently across both LangGraph `SupervisorState` and Swarm worker dispatches. It trips persistently after 3 consecutive failures to prevent infinite retry loops. To guarantee compliance with strict API format rules (LiteLLM/OpenAI) and prevent HTTP 400 validation errors, when the breaker trips mid-batch, Kazma automatically generates conformant `role: tool` mock responses containing override messages for all blocked tool calls.
- **TimeoutGuard** — per-task timeout via `asyncio.wait_for` with clean
  coroutine cancellation. Configurable `on_timeout` behavior:
  `fail` (terminal), `retry` (counts against retry budget), `skip` (continue
  without worker). Default 300s, `timeout=0` rejected.
- **OutputValidator** — validates worker output against Pydantic BaseModel,
  dict schema, or JSON schema before acceptance. Parses string output as JSON
  when the schema expects a structure. No schema = skip validation. Invalid
  output triggers retry. Error details surfaced in result.
- **BoundedConcurrency** — `asyncio.Semaphore` wrapper (default 5) for
  limiting parallel dispatches. Configurable per engine, task
  (`metadata.max_concurrent`), or global. Semaphore released on failure/timeout.
  Applied to `fan_out`, `broadcast`, and `consult` patterns.

The dispatch chain is: retry -> timeout -> circuit breaker -> validation ->
bounded concurrency. All components are configurable per-worker or per-task.

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
│  POST /workers/{name}/start  POST /workers/{name}/stop │
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
│       ├── i18n.py           Internationalization (150+ AR translations, cookie locale)
│       └── (no services.py facade; UI uses agent/engine public surface + limited internals)
│       ├── gateway_monitor.py /api/gateway/status endpoint
│       └── metrics.py        Prometheus /metrics endpoint
├── kazma-tui/            Textual TUI with English-only metrics/chat dashboard
├── kazma-cli/            CLI entry point (status, serve, hub, docs, wizard, project)
├── kazma-memory/         SQLite FTS5 + Arabic tokenizer
├── kazma-skills/         YAML skill manifests + MCP server registry
├── tests/
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
  version: "0.2.0"
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

138 tests passing across `kazma-core/kazma_core_tests/`, `kazma-gateway/kazma_gateway_tests/`, `kazma-ui/kazma_ui_tests/`, and `tests/test_service_facade.py` (actual count verified via pytest --collect-only; some skips for optional deps). See `tests/` for full suite.

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

## 📚 Public APIs & Facades

To maintain clean module boundaries and robust testing, Kazma utilizes backward-compatible facades and strictly-defined public API boundaries:

1. **Agent Message Handler Facade** (`kazma_gateway/agent_handler.py`):
   - Serves as the central API facade re-exporting all subpackage capabilities from the decomposed `kazma_gateway/agent_handler/` package.
   - Strictly keeps `chromadb` imports completely out of its file scope to satisfy AST parsing and multi-tenant isolation tests.
2. **Settings and Configuration Manager**:
   - `get_config_store()`: Process-wide atomic SQLite-backed settings singleton with automatic WAL journaling. Avoid direct instantiation.
3. **Swarm Engine Facade**:
   - `SwarmEngine` public interface serves as the primary orchestration entry point, delegating complex subtasks to `reliability_registry`, `phonebook`, and `checkpoint_manager`.

### 🗄️ Optional RAG Deployment

Kazma's 4-Layer Memory system automatically degrades gracefully when Vector Databases (like ChromaDB) are not deployed or offline:
- **Vector/Semantic Memory (RAG)** is fully optional. If `KAZMA_VECTOR_COLLECTION` or ChromaDB is unavailable, the `UnifiedRouter` and `UnifiedMemoryStore` fall back gracefully to a high-performance, local, thread-safe SQLite FTS5 BM25 search.
- Development and local tests can be run fully headless and offline with zero vector database requirements.

### 💻 Clear Workspace Setups

To configure safe local workspaces for execution:
1. Run `.\setup.ps1` (or `setup.sh` on POSIX systems) to automatically initialize dependencies, set up local configuration directories, and compile schemas.
2. Portability is strictly enforced. No absolute paths or home directories are hardcoded. Workspace constraints can be defined dynamically under the `configure_workspace(workspace_dir)` API to lock execution down to specific project subdirectories.

---

## 📚 Documentation

- [CLI Reference](docs/docs/api-reference/cli-reference.md)
- [Slash Commands Reference](docs/slash-commands.md)
- [Portability Policy](docs/portability.md)
- [Context Compaction](docs/compaction.md)
- [Skill Manifest Spec](docs/skill-manifest-spec.md)
- [Changelog](CHANGELOG.md)

---

## 📜 License

MIT
