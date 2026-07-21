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

> **Requires Python 3.11+** (3.12–3.13 recommended). Declared range: 3.11–3.14.

### 1. Clone

```bash
git clone https://github.com/Mubder/kazma.git
cd kazma
```

### 2. Install

**Option A — uv (recommended):**

```bash
# Install uv once if needed:
#   Linux/macOS/WSL:  curl -LsSf https://astral.sh/uv/install.sh | sh
#   Windows PS:       irm https://astral.sh/uv/install.ps1 | iex

uv venv --python 3.13
uv sync --all-extras
# Activate (optional for `uv run`, required for bare `kazma`):
#   Linux/macOS:  source .venv/bin/activate
#   Windows PS:   .venv\Scripts\Activate.ps1
```

**Option B — pip + venv:**

```bash
# Linux / macOS / WSL
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[rag,dev]"
```

```powershell
# Windows (PowerShell)
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[rag,dev]"
```

> **WSL:** If you see `externally-managed-environment`, always use a venv — never system-wide `pip install`.  
> **Extras:** `rag` = vector memory; `dev` = tests/lint. For a fuller install: `pip install -e ".[all]"` if that extra is defined in your checkout, or `uv sync --all-extras`.

### 3. Configure

```bash
# Linux / macOS / WSL
cp .env.example .env
```

```powershell
# Windows
Copy-Item .env.example .env
```

Edit `.env` — set **at least one** LLM key (example for OpenAI-compatible):

```dotenv
OPENAI_API_KEY=sk-...
# Optional for any non-loopback / multi-user deploy (auto-generated on loopback if unset):
# KAZMA_SECRET=long-random-string
# KAZMA_HOST=127.0.0.1
```

Other providers (DeepSeek, Anthropic, …) are usually configured in the Web UI **Settings → Providers** or `kazma.yaml` after first start — see [Configuration](docs/docs/guide/configuration.md).

### 4. Run the Web UI

```bash
# Default bind: 127.0.0.1 , default port: 9090
kazma serve
# or explicit port:
kazma serve 9091
```

```powershell
# Windows (from repo root, venv active):
kazma serve 9091
# equivalent:
python -m kazma_cli.main serve 9091
```

Then open the URL printed in the terminal, e.g. **http://127.0.0.1:9091/**  
(Use **http**, not https, for local serve.)

Health check (server must be running):

```bash
curl http://127.0.0.1:9091/health
```

**TUI instead of browser:**

```bash
kazma-tui
```

### 5. If the browser can’t open the page

| Symptom | Likely cause | Fix |
|---------|----------------|-----|
| **ERR_CONNECTION_RESET** on `:9090` (Windows / WSL) | Stale **portproxy** or WSL IP changed; or Kazma not on `0.0.0.0` | After reboot: Admin `.\scripts\wsl_fixed_access.ps1` + WSL `./scripts/start-web.sh` → **http://127.0.0.1:9090/** ([WSL fixed access](docs/docs/ops/wsl-fixed-access.md)) |
| Connection refused | Server not running / wrong port | Start `kazma serve` and use the printed URL |
| Instant exit: “old hardcoded default” secret | `KAZMA_SECRET=kazma-local-dev-secret` | Unset it or set a new random secret |
| Instant exit: non-loopback needs secret | `KAZMA_HOST=0.0.0.0` without secret | Set `KAZMA_SECRET` or use `KAZMA_HOST=127.0.0.1` |

Confirm what is listening:

```powershell
# Windows
Get-NetTCPConnection -LocalPort 9090 -State Listen -ErrorAction SilentlyContinue |
  Select-Object OwningProcess
# Expect a python process when Kazma is up — not only svchost/iphlpsvc
```

Full guide: [Quickstart](docs/docs/guide/quickstart.md) · [Troubleshooting](docs/docs/guide/troubleshooting-and-workarounds.md) · [Production checklist](docs/docs/ops/production-checklist.md)

---

## 📖 Documentation

All technical documentation lives under [`docs/docs/`](docs/docs/) (Docusaurus).  
Build/serve: `cd docs && npm start` → <http://localhost:3000/kazma/>

| Document | What's inside |
|---|---|
| [Docs home](docs/docs/intro.md) | Full documentation map |
| [Quickstart](docs/docs/guide/quickstart.md) | Install paths, minimal config, first message |
| [Architecture](docs/docs/guide/architecture.md) | Engine internals, data-flow diagrams, observability |
| [Configuration](docs/docs/guide/configuration.md) | `kazma.yaml` keys, ConfigStore, providers |
| [Environment variables](docs/docs/reference/environment-variables.md) | Every important env var |
| [Tools catalog](docs/docs/reference/tools-catalog.md) | Built-in + native skill tools |
| [CLI Reference](docs/docs/guide/cli-reference.md) | Complete command tree with examples |
| [Slash commands](docs/docs/reference/slash-commands.md) | Gateway slash command reference |
| [Swarm](docs/docs/guide/swarm-orchestration.md) | Patterns, reliability, checkpoints |
| [IDE](docs/docs/products/ide.md) | Web/TUI/chat coding backend |
| [Security & Safety](docs/docs/guide/security-and-safety.md) | Three HITL gates, danger tools, vault |
| [Production checklist](docs/docs/ops/production-checklist.md) | Go-live checklist |
| [Troubleshooting](docs/docs/guide/troubleshooting-and-workarounds.md) | Provider limits, SQLite, known gotchas |
| [System map](docs/ARCHITECTURE_AND_SYSTEM_MAP.md) | Full monorepo engineering map |

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

All gates are **fail-closed** by default. See [Security & Safety](docs/docs/guide/security-and-safety.md).

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

AES-256-GCM + PBKDF2 (600k iterations). Retrieval and deletion require human approval. See [Skills & Tools → Secret Vault](docs/docs/guide/skills-mcp-and-tools.md).

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

Full diagrams in [Architecture](docs/docs/guide/architecture.md).

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

See [Development](docs/docs/guide/development.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

---

## 📜 License

MIT — see [LICENSE](LICENSE).
