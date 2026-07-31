<p align="center">
  <img src="https://raw.githubusercontent.com/Mubder/kazma/main/kazma-ui/kazma_ui/static/img/kazma-logo.png" alt="Kazma" height="80">
</p>

**Production-Grade Autonomous Agents with Deep Multilingual Intelligence**

Kazma is the reliable multi-agent framework built for real deployment. Cryptographic skill signing, triple-wired human-in-the-loop safety, durable execution, and native Arabic dialect support — all in one full-stack system with live Web UI, TUI, CLI, and multi-platform gateways.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Tests: 4,300+ passing](https://img.shields.io/badge/tests-4%2C300%2B%20passing-brightgreen.svg)](#-development)

---

## ⚡ At a Glance

| Lines of code | Tests | Commits | Contributors |
|---|---:|---:|---:|
| ~159K | 4,360 | 1,200+ | Solo |

![Kazma Dashboard](https://kazma.ai/screenshots/Hero-en.png)

---

## 📖 What does "Kazma" mean?

**Kazma** (كاظمة) was an ancient coastal oasis in Kuwait — a network of wells and a gateway for trade between civilizations. It was also the site of the legendary **Battle of Chains** (ذات السلاسل, 633 CE): the Persian army chained its soldiers into a single, rigid wall, yet Khalid ibn al-Walid shattered it through precise, decentralized maneuvering — and won.

We engineered Kazma on those same pillars — not as metaphor, but as architecture:

- 🏜️ **The Wells** — Deep memory that holds context across sessions. The agent draws from it when it needs to remember, like caravans drawing from oasis wells.
- 🚪 **The Gateway** — One supervisor brain routed to Telegram, Discord, Slack, Web, and TUI — the way ancient Kazma routed trade between civilizations.
- ⚔️ **Breaking the Chains** — The chains in that battle were rigidity — and rigidity is what makes monolithic agent pipelines fragile. Kazma runs decentralized swarm patterns and self-healing circuit breakers instead, adapting and recovering where rigid chains would snap.

---

## 🧩 Features

### 🧠 Agent Brain
LangGraph supervisor with a ReAct loop, tool calling, durable checkpointing, 80% context compaction, and **per-turn RAG retrieval** — memories injected on every message, not just at compaction.

### 🐝 Swarm Orchestration
Six dispatch patterns (broadcast, pipeline, fan-out, consult, conditional, dispatch) with circuit breakers, retries, and a self-improvement engine that learns from swarm outcomes automatically.

### 🔒 Triple-Wired Safety
Three independent HITL gates — graph interrupt, swarm bus, and pipeline checkpoints — ensure dangerous tools never execute without human approval. Downloaded Agent Skills are **integrity-verified (HMAC-SHA256)** at load — tampered skills are refused — and their content is injected behind an untrusted-data prompt fence.

### 🌐 Multi-Platform
Telegram, Discord, Slack, Web UI, and TUI — all powered by a single LangGraph supervisor. Platform IDs never enter LangGraph state.

### 📜 Arabic-Native
Custom Arabic tokenizer, RTL UI, Kuwaiti-dialect support, and the Majlis cultural protocol. Built in Kuwait, for the world.

### 🔌 Rich Ecosystem
- **Any LLM** — OpenAI, Anthropic, Gemini, DeepSeek, xAI, Ollama, and 15+ more via plain HTTP
- **MCP Marketplace** — One-click install from 85+ preset MCP servers with namespaced tools
- **Knowledge Library** — Ingest entire documentation sites into searchable RAG corpora with cited sources
- **IDE Subsystem** — Transport-agnostic coding backend: multi-tab editor, file-aware AI chat, `/ide` commands across all platforms
- **Time-Travel Replay** — Snapshot every iteration; restore, fork, and compare conversation paths
- **Encrypted Vault** — AES-256-GCM storage for API keys and credentials
- **Browser, Calendar & Documents** — Playwright automation, Google/Outlook calendar, PDF/DOCX/XLSX generation
- **Deep Research** — Multi-query web search → parallel acquire → digest → LLM synthesis with DOCX export

---

## 🆚 Why Kazma?

| Feature | Kazma | LangChain | CrewAI | AutoGPT | n8n |
|---|---|---|---|---|---|
| **Self-hosted, MIT** | ✅ | ✅ | ✅ | ✅ | ⚠️ Fair-code |
| **Arabic-native** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **HITL safety gates** | ✅ 3 layers | ❌ | ❌ | ⚠️ basic | ❌ |
| **Swarm orchestration** | ✅ 6 patterns | ⚠️ via LangGraph | ✅ | ❌ | ❌ |
| **Built-in IDE** | ✅ Web+TUI | ❌ | ❌ | ❌ | ❌ |
| **Encrypted vault** | ✅ AES-256 | ❌ | ❌ | ❌ | ❌ |
| **MCP marketplace** | ✅ 85+ servers | ❌ | ❌ | ❌ | ❌ |
| **Time-travel replay** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Skill integrity** | ✅ HMAC-SHA256 | ❌ | ❌ | ❌ | ❌ |
| **Web UI included** | ✅ | ❌ | ❌ | ❌ | ✅ |

---

## 📸 Screenshots

| Dashboard | Web IDE | Chat with HITL |
|---|---|---|
| ![Dashboard](https://kazma.ai/screenshots/Dashboard-en.png) | ![IDE](https://kazma.ai/screenshots/IDE-en.png) | ![Chat](https://kazma.ai/screenshots/Chat-en.png) |

| Swarm Task Builder | Skills | MCP Servers |
|---|---|---|
| ![Swarm](https://kazma.ai/screenshots/Swarm-Task-Builder-en.png) | ![Skills](https://kazma.ai/screenshots/Skills-en.png) | ![MCP](https://kazma.ai/screenshots/MCP-en.png) |

---

## 🚀 Quick Start

> **Requires Python 3.11+** (3.12–3.13 recommended).

### 1. Clone & Install

```bash
git clone https://github.com/Mubder/kazma.git
cd kazma
```

**Option A — uv (recommended):**

```bash
uv venv --python 3.13
uv sync --all-extras
```

**Option B — pip + venv:**

```bash
# Linux / macOS / WSL
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[rag,dev]"

# Windows (PowerShell)
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[rag,dev]"
```

> **Extras:** `rag` = vector memory; `dev` = tests/lint; `web` = Playwright; `document` = PDF/DOCX/XLSX; `database` = Postgres/MySQL/Mongo. Install everything with `pip install -e ".[all]"` or `uv sync --all-extras`.

### 2. Configure

```bash
# Linux / macOS / WSL
cp .env.example .env

# Windows (PowerShell)
Copy-Item .env.example .env
```

Edit `.env` — set **at least one** LLM key:

```dotenv
OPENAI_API_KEY=sk-...
```

### 3. Run

```bash
# Web UI (default: http://127.0.0.1:9090)
kazma serve

# Terminal UI
kazma-tui
```

> **Full guides:** [Quickstart](docs/docs/guide/quickstart.md) · [Configuration](docs/docs/guide/configuration.md) · [Troubleshooting](docs/docs/guide/troubleshooting-and-workarounds.md)

---

## 🏗 Architecture

```
User (Telegram/Discord/Slack/Web/TUI)
    ↓
Platform Adapter (isolates platform IDs)
    ↓
Supervisor Graph (LangGraph ReAct loop)
    ├── ContextAuthority (80% compaction + per-turn RAG retrieval)
    ├── UnifiedToolExecutor (LocalToolRegistry + native skills + MCP)
    ├── IdeService (workspace-scoped file/exec/git)
    ├── HITL Gate (interrupt before danger tools)
    └── LLM Provider (any OpenAI-compatible endpoint)
    ↓
SwarmEngine (when multi-agent is needed)
    ├── 6 dispatch patterns
    ├── Reliability layer (circuit breaker, retry, timeout)
    ├── Self-improvement (auto-learning feedback loop)
    └── V2 Cognitive Engine (bi-temporal beliefs + PPR recall)
```

Built on: `LangGraph` · `FastAPI` · `SQLite` (WAL) · `Postgres` · `Docker` · `sentence-transformers` · `sqlite-vec`

Full diagrams: [Architecture](docs/docs/guide/architecture.md) · [System Map](docs/ARCHITECTURE_AND_SYSTEM_MAP.md)

---

## 🧠 Memory & RAG

Kazma remembers by default — 4-layer memory with intelligent consolidation:

| Layer | Backend | Role |
|---|---|---|
| L1 | ChromaDB | Vector agent memory |
| L2 | SQLite graph | Structured SPO triples with FTS |
| L3 | FTS5 | Full-text search |
| L4 | sqlite-vec | Sparse vectors |

- **Reciprocal Rank Fusion** (k=60) blends all 4 layers — fail-closed durable writes
- **Per-turn RAG injection** — relevant memories are injected on every message
- **Auto-store + Consolidator** — durable facts written post-turn; LLM consolidator writes clean SPO triples into the property graph every N turns
- **Soul Store** — self-improvement prompt deltas from success/failure analysis, injected behind prompt fences

Deep dive: [Memory & RAG](docs/docs/guide/memory-and-rag.md)

---

## 🔒 Safety by Design

Three independent gates. All fail-closed by default.

1. **Graph interrupt** — pauses before `file_write`, `shell_exec`, `vault_retrieve`, and all danger-tier tools
2. **Swarm bus** — `/swarm` dispatches require HITL approval for dangerous operations
3. **Pipeline checkpoints** — multi-stage pipelines pause at configured steps

Multi-platform approvals: interactive sliding cards in Web, inline buttons on Telegram/Discord/Slack.

See: [Security & Safety](docs/docs/guide/security-and-safety.md)

---

## 🐝 Swarm in 30 Seconds

```bash
# Add workers
kazma swarm worker add researcher --model deepseek-chat --provider deepseek

# Run a pipeline
kazma swarm pipeline --workers researcher,builder,validator "Build a CLI tool"

# Fan out and vote
kazma swarm fanout --workers a,b,c --aggregation vote "Best approach?"

# Check results
kazma swarm history
kazma swarm metrics
```

Workers automatically **learn from outcomes** via the self-improvement engine — success patterns are reinforced, failure patterns are corrected in the worker's system prompt.

Full guide: [Swarm Orchestration](docs/docs/guide/swarm-orchestration.md)

---

## 📦 Project Structure

```
kazma-core/       Agent runner, LLM provider, swarm engine, memory/RAG, IDE, safety
kazma-gateway/    Telegram/Discord/Slack adapters, slash commands
kazma-ui/         FastAPI web app, IDE page, SSE chat, dashboard
kazma-tui/        Textual terminal dashboard + IDE editor
kazma-memory/     Arabic tokenizer + FTS5 search backend
kazma-skills/     Native skills (vault, database, crawler, coding, …)
kazma-cli/        The `kazma` command surface
```

---

## 📖 Documentation

| Document | What's inside |
|---|---|
| [Docs home](docs/docs/intro.md) | Full documentation map |
| [Quickstart](docs/docs/guide/quickstart.md) | Install paths, minimal config, first message |
| [Architecture](docs/docs/guide/architecture.md) | Engine internals, data-flow diagrams |
| [Configuration](docs/docs/guide/configuration.md) | `kazma.yaml`, ConfigStore, providers |
| [Environment variables](docs/docs/reference/environment-variables.md) | Every important env var |
| [Tools catalog](docs/docs/reference/tools-catalog.md) | Built-in + native skill tools |
| [CLI Reference](docs/docs/guide/cli-reference.md) | Complete command tree |
| [Swarm](docs/docs/guide/swarm-orchestration.md) | Patterns, reliability, checkpoints |
| [IDE](docs/docs/products/ide.md) | Web/TUI/chat coding backend |
| [Security & Safety](docs/docs/guide/security-and-safety.md) | Three HITL gates, vault, skill signing |
| [Production checklist](docs/docs/ops/production-checklist.md) | Go-live checklist |
| [System map](docs/ARCHITECTURE_AND_SYSTEM_MAP.md) | Full monorepo engineering map |

---

## 🏗 Built Alongside Kazma

These projects grew up next to Kazma — each one taught us something about agents, trust, and real interfaces that shaped the framework.

| Project | Description | Status |
|---|---|---|
| [IndexArc](https://github.com/Mubder/IndexArc) | Portable personal vault for secrets, API keys, and notes. Offline (Ollama) or cloud. | Open Source |
| ShipX | AI delivery platform via WhatsApp — text or voice in Khaleeji Arabic. | In Development |
| KCA | Institutional Intelligence System — Genesis, OS, Guardian, Network, Evolution. | In Development |

---

## 🧪 Development

**4,300+ tests passing** across 5 suites.

```bash
uv sync --all-extras
pytest                          # All 5 test suites
ruff check kazma-core/          # Lint
mypy kazma-core/                # Type check
```

See: [Development](docs/docs/guide/development.md) · [CONTRIBUTING.md](CONTRIBUTING.md)

---

## 🎯 Who is this for?

- **Indie developers** who want a self-hosted agent that doesn't phone home
- **Arabic-speaking teams** who need native RTL and dialect support
- **Startups & enterprises** evaluating production-grade multi-agent orchestration
- **Contributors** looking for a well-tested, well-documented AI framework to build on

MIT-licensed, production-tested, 1,200+ commits. Built solo in Kuwait with full-stack execution.

[🌐 kazma.ai](https://kazma.ai) · [🐙 GitHub](https://github.com/Mubder/kazma) · [💬 Try the live demo](https://kazma-demo.fly.dev/) · [📧 Pilots & partnerships](mailto:admin@kazma.ai)

---

## 📜 License

MIT — see [LICENSE](LICENSE).
