# Kazma — كاظمه

**An autonomous AI agent framework with a LangGraph brain, swarm orchestration, Arabic-first design, and human-in-the-loop safety.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Tests: 3,800+ passing](https://img.shields.io/badge/tests-3%2C800%2B%20passing-brightgreen.svg)](#-development)

---

## ✨ What is Kazma?

Kazma is a multi-platform AI agent framework that lets you build, deploy, and orchestrate intelligent agents across Telegram, Discord, Slack, a web dashboard, and a terminal UI — all powered by a single LangGraph supervisor brain.

**Key capabilities:**

- 🧠 **LangGraph supervisor** — a ReAct loop with tool calling, durable checkpointing, 80% context compaction, and **per-turn RAG retrieval** (memories injected on every message, not just at compaction)
- 🐝 **Swarm orchestration** — six patterns (dispatch, broadcast, pipeline, fan-out, consult, conditional) with circuit breakers, retries, and self-improvement
- 💻 **IDE subsystem** — transport-agnostic coding backend (Web, TUI, all chat platforms): multi-tab editor, file-aware AI chat, `/ide` commands, per-task workspace targeting, GitHub clone-from-chat
- 🔒 **Human-in-the-loop safety** — three independent HITL gates ensure dangerous tools never execute without approval
- 🔑 **Encrypted secret vault** — AES-256-GCM encrypted storage for API keys and credentials
- 🌐 **Any LLM provider** — OpenAI, Anthropic, DeepSeek, Google Gemini, xAI, OpenRouter, Ollama, LM Studio, NVIDIA NIM — via plain HTTP, no SDK lock-in
- 🇸🇦 **Arabic-native** — custom Arabic tokenizer, RTL UI, Kuwaiti-dialect support, and the Majlis cultural protocol
- 💾 **Memory & RAG** — ChromaDB vector memory with per-turn retrieval injection. **Pluggable embeddings** — local sentence-transformers or NVIDIA NIM / any OpenAI-compatible endpoint (config flip, no code change)
- 📱 **Responsive web UI** — multi-tab code editor, AI chat, find/replace, unified dialog system — works on mobile with a slide-in nav drawer

---

## 🚀 Quick Start

> **Requires Python 3.11+ (3.13 recommended).** Kazma supports Python 3.11 through 3.14.

### Install

```bash
git clone https://github.com/Mubder/kazma && cd kazma
```

**Option A — uv (recommended for all platforms):**

```bash
# Install uv if you don't have it (one-time):
#   Linux / macOS / WSL:   curl -LsSf https://astral.sh/uv/install.sh | sh
#   Windows (PowerShell):  irm https://astral.sh/uv/install.ps1 | iex

# Create venv with Python 3.13 and install ALL dependencies in one step:
uv venv --python 3.13
uv sync --all-extras
```

**Option B — pip + venv:**

**Linux / macOS / WSL:**

```bash
python3 -m venv .venv --python 3.13    # or just: python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[all]"                # installs core + rag + dev + tui + observability + web
```

**Windows (PowerShell):**

```powershell
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[all]"
```

> **WSL note:** If you see `error: externally-managed-environment`, you must use a venv (above) — never `pip install` system-wide on Debian/Ubuntu.

### Configure

```bash
# Linux / macOS / WSL
cp .env.example .env

# Windows (PowerShell)
Copy-Item .env.example .env
```

Edit `.env` — set at least one provider key:
```dotenv
OPENAI_API_KEY=sk-...
```

### Run

```bash
kazma serve          # Web UI → http://127.0.0.1:9090
# or
kazma-tui            # Terminal dashboard
```

That's it. Open the dashboard, go to Chat, and start talking.

---

## 📖 Documentation

All technical documentation lives in [`docs-v2/`](docs-v2/):

| Document | What's inside |
|---|---|
| [Quickstart](docs-v2/docs/quickstart.md) | Install paths, minimal config, first message |
| [Architecture](docs-v2/docs/architecture.md) | Engine internals, data-flow diagrams, observability |
| [Configuration](docs-v2/docs/configuration.md) | Every `kazma.yaml` key, every env var, override precedence |
| [CLI Reference](docs-v2/docs/cli-reference.md) | Complete command tree with examples |
| [Swarm Orchestration](docs-v2/docs/swarm-orchestration.md) | Patterns, aggregation, reliability, self-improvement |
| [Memory & RAG](docs-v2/docs/memory-and-rag.md) | ChromaDB, FTS5, compaction injection, Arabic tokenizer |
| [Security & Safety](docs-v2/docs/security-and-safety.md) | Three HITL gates, danger-tool lists, vault |
| [Skills, MCP & Tools](docs-v2/docs/skills-mcp-and-tools.md) | Skill manifests, HMAC signing, MCP, secret vault |
| [Gateways & Platforms](docs-v2/docs/gateways-and-platforms.md) | Telegram/Discord/Slack/Web/TUI adapters |
| [Arabic & Cultural](docs-v2/docs/arabic-cultural-features.md) | i18n, RTL, Majlis protocol, dialect support |
| [Deployment](docs-v2/docs/deployment.md) | Docker, Kubernetes, Windows, production checklist |
| [Troubleshooting](docs-v2/docs/troubleshooting-and-workarounds.md) | Provider limits, SQLite, known gotchas |
| [Roadmap](docs-v2/docs/roadmap-and-future.md) | What exists vs. what's planned |

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

Workers automatically **learn from outcomes** via the self-improvement engine — success patterns are reinforced and failure patterns are corrected in the worker's system prompt.

---

## 🔒 Safety by Design

Kazma has **three independent HITL gates** so dangerous tools never run silently:

1. **Graph interrupt** — single-agent chat pauses before `file_write`, `shell_exec`, `vault_retrieve`, etc.
2. **Swarm bus** — `/swarm` dispatches require approval for danger tools
3. **Pipeline checkpoints** — multi-stage pipelines can pause at configured steps

All gates are **fail-closed** by default. See [Security & Safety](docs-v2/docs/security-and-safety.md).

---

## 🔑 Secret Vault

Store API keys and credentials encrypted at rest:

```bash
# .env
KAZMA_VAULT_KEY=your-passphrase
```

Then in chat:
> **You:** "Store my OpenAI key: sk-..."
> **Kazma:** calls `vault_store("openai_key", "sk-...", category="llm")` → encrypted ✓
>
> **You:** "What's my OpenAI key?"
> **Kazma:** calls `vault_retrieve("openai_key")` → *HITL approval required* → returns value

AES-256-GCM + PBKDF2 (600k iterations). Retrieval and deletion require human approval. See [Skills & Tools → Secret Vault](docs-v2/docs/skills-mcp-and-tools.md#6-secret-vault-encrypted-credential-storage).

---

## 🏗 Architecture at a Glance

```
User (Telegram/Discord/Slack/Web/TUI)
    ↓
Platform Adapter (isolates platform IDs)
    ↓
Supervisor Graph (LangGraph ReAct loop)
    ├── ContextAuthority (80% compaction + per-turn RAG retrieval)
    ├── UnifiedToolExecutor (LocalToolRegistry + native skills + MCP)
    ├── IdeService (workspace-scoped file/exec/git + env_context awareness)
    ├── HITL Gate (interrupt before danger tools)
    └── LLM Provider (any OpenAI-compatible endpoint)
    ↓
SwarmEngine (when multi-agent is needed)
    ├── 6 dispatch patterns
    ├── Reliability layer (circuit breaker, retry, timeout)
    ├── Self-improvement (auto-learning feedback loop)
    └── UnifiedMemoryAdapter (ChromaDB + FTS5 + sqlite-vec, pluggable embeddings)
```

Full diagrams in [Architecture](docs-v2/docs/architecture.md).

---

## 📦 Project Structure

```
kazma-core/       Agent runner, LLM provider, swarm engine, memory/RAG, IDE service, safety
kazma-gateway/    Telegram/Discord/Slack adapters, slash commands, /ide commands
kazma-ui/         FastAPI web app, IDE page, SSE chat, dashboard, settings
kazma-tui/        Textual terminal dashboard + IDE editor screen
kazma-memory/     Arabic tokenizer + FTS5 search backend
kazma-skills/     Native skills (vault, database, web crawler, coding skills, …)
kazma-cli/        The `kazma` command surface
```

---

## 🧪 Development

**3,800+ tests passing** across 5 suites (core, gateway, UI, TUI, and root integration).

```bash
uv sync --all-extras            # Install all deps (or: pip install -e ".[rag,dev]")
pytest                          # Run all 5 test suites (3,981 collected)
ruff check kazma-core/          # Lint
mypy kazma-core/                # Type check
```

> **Windows PowerShell:** the venv activation command is `.venv\Scripts\Activate.ps1`, not `source .venv/bin/activate`. See [Quick Start](#-quick-start) above.

See [Development](docs-v2/docs/development.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

---

## 📜 License

MIT — see [LICENSE](LICENSE).
