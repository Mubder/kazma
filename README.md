# Kazma — كاظمه

**An autonomous AI agent framework with a LangGraph brain, swarm orchestration, Arabic-first design, and human-in-the-loop safety.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

---

## ✨ What is Kazma?

Kazma is a multi-platform AI agent framework that lets you build, deploy, and orchestrate intelligent agents across Telegram, Discord, Slack, a web dashboard, and a terminal UI — all powered by a single LangGraph supervisor brain.

**Key capabilities:**

- 🧠 **LangGraph supervisor** — a ReAct loop with tool calling, durable checkpointing, and 80% context compaction with memory injection
- 🐝 **Swarm orchestration** — six patterns (dispatch, broadcast, pipeline, fan-out, consult, conditional) with circuit breakers, retries, and self-improvement
- 🔒 **Human-in-the-loop safety** — three independent HITL gates ensure dangerous tools never execute without approval
- 🔑 **Encrypted secret vault** — AES-256-GCM encrypted storage for API keys and credentials
- 🌐 **Any LLM provider** — OpenAI, Anthropic, DeepSeek, Google Gemini, xAI, OpenRouter, Ollama, LM Studio, NVIDIA NIM — via plain HTTP, no SDK lock-in
- 🇸🇦 **Arabic-native** — custom Arabic tokenizer, RTL UI, Kuwaiti-dialect support, and the Majlis cultural protocol
- 💾 **Memory & RAG** — ChromaDB vector memory with automatic retrieval injection during compaction
- 📱 **Responsive web UI** — works on mobile with a slide-in nav drawer

---

## 🚀 Quick Start

### Install

```bash
git clone <repo-url> kazma && cd kazma
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[rag,dev]"
```

### Configure

```bash
cp .env.example .env
# Edit .env — set at least one provider key:
#   OPENAI_API_KEY=sk-...
```

### Run

```bash
kazma serve          # Web UI → http://127.0.0.1:8000
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
    ├── ContextAuthority (80% compaction + memory injection)
    ├── ToolRegistry (built-in + native skills + MCP)
    ├── HITL Gate (interrupt before danger tools)
    └── LLM Provider (any OpenAI-compatible endpoint)
    ↓
SwarmEngine (when multi-agent is needed)
    ├── 6 dispatch patterns
    ├── Reliability layer (circuit breaker, retry, timeout)
    ├── Self-improvement (auto-learning feedback loop)
    └── 4-layer memory adapter (ChromaDB + FTS5 + NetworkX + sqlite-vec)
```

Full diagrams in [Architecture](docs-v2/docs/architecture.md).

---

## 📦 Project Structure

```
kazma-core/       Agent runner, LLM provider, swarm engine, memory, safety
kazma-gateway/    Telegram/Discord/Slack adapters, slash commands
kazma-ui/         FastAPI web app, SSE chat, dashboard, settings
kazma-tui/        Textual terminal dashboard
kazma-memory/     Arabic tokenizer + FTS5 search backend
kazma-skills/     Native skills (vault, database, web crawler, …)
kazma-cli/        The `kazma` command surface
```

---

## 🧪 Development

```bash
pip install -e ".[rag,dev]"     # Install with dev deps
pytest tests/ -v                # Run tests
ruff check kazma-core/          # Lint
mypy kazma-core/                # Type check
```

See [Development](docs-v2/docs/development.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

---

## 📜 License

MIT — see [LICENSE](LICENSE).
