<p align="center">
  <img src="https://raw.githubusercontent.com/Mubder/kazma/main/kazma-ui/kazma_ui/static/img/kazma-logo.png" alt="Kazma" height="80">
</p>

**Self-hosted multi-platform agents with deep multilingual intelligence**

Kazma is the reliable multi-agent framework built for real deployment. Cryptographic skill signing, triple-wired human-in-the-loop safety, durable execution, and native Arabic dialect support — all in one full-stack system with live Web UI, TUI, CLI, and multi-platform gateways.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Tests: 4,243](https://img.shields.io/badge/tests-4%2C243-brightgreen.svg)](#-development)

---

## ⚡ At a Glance

<!-- Update from METRICS.md via: python scripts/generate_metrics.py --write -->
| Lines of code | Tests | Commits | Contributors |
|---|---:|---:|---:|
| ~248K Python (~271K Total) | 4,319 | 1,799+ | 7 |

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

### 🧠 Agent Brain & V2 Cognitive Memory
LangGraph supervisor with a ReAct loop, tool calling, durable checkpointing, 80% context compaction, and **Pure V2 Cognitive Memory** — bi-temporal belief tracking (`valid_from` / `valid_until`), Local Ego-Graph Personalized PageRank (PPR), hybrid FTS + vector episode retrieval, and prompt-fenced per-turn context injection. **Knowledge Library** stays a separate store but can inject into chat (labeled) with federated search. Optional **Neo4j** dual-write and Postgres/Qdrant adapters — SQLite remains the zero-config default.

### 🛡️ Non-Stop Execution & Self-Healing Engine
Autonomous watchdog (`supervised_invoke()`) tracks node heartbeats, detects stalls, and performs automatic checkpoint rollbacks to clean state with `[KAZMA RECOVERY]` system reflection injection. Features model failover chains with per-provider cooldowns, durable SQLite **LLM Call Ledger** (`kazma-data/llm_calls.db`), orphan task startup recovery, and an automatic HITL approval timeout watchdog.

### 🐝 Swarm Orchestration & Autoscaler
Six dispatch patterns (broadcast, pipeline, fan-out, consult, conditional, dispatch) with a **Dynamic Swarm Autoscaler** that auto-spawns specialist workers from templates (`coder`, `researcher`, `generalist`) with automatic best-model-per-task routing (coding, reasoning, vision). Inspect and drive it live from the **Swarm Panel** in the Web UI (worker registry, active tasks, dispatch, metrics).

```yaml
swarm:
  max_workers: 8
  autoscale: true
  templates: [coder, researcher, generalist]
```

### 🔒 Triple-Wired Safety
Three independent HITL gates — graph interrupt, swarm bus, and pipeline checkpoints — ensure dangerous tools never execute without human approval. Downloaded Agent Skills are **integrity-verified (HMAC-SHA256)** at load, and Soul evolution deltas are injected behind an untrusted-data prompt fence (`<kazma:data untrusted>`).

### 🧭 Commitment Layer (resolve-before-act)
A policy gate between the LLM and durable mutations. Before the agent can schedule, send, execute, or change config, Kazma **resolves its intent against memory** — so a model can't invent a date and schedule it over a real belief. Ambiguous acts raise a **semantic clarify/confirm card** with per-option buttons on Web, Telegram, Discord, and Slack. Modes (strict / balanced / autonomous / yolo) and kill-switches for every enforcement layer. Docs: [Commitment Layer](docs/docs/guide/commitment-layer.md).

### 🌐 Multi-Platform
Telegram, Discord, Slack, Web UI, and TUI — all powered by a single LangGraph supervisor. Platform IDs never enter LangGraph state. **Steer any running task** mid-flight with `/steer` (soft), `/steer!` (pause + inject), or `/abort` — no need to wait for the turn to finish.

### 📜 Arabic-Native
Custom Arabic tokenizer, RTL UI, Kuwaiti-dialect support, and the Majlis cultural protocol. Built in Kuwait, for the world.

### 📄 Document Intelligence Platform
Production document pipeline: streamed intake → content-addressed quarantine → MIME/OOXML/PDF policy sniff → **isolated subprocess parse/OCR** → durable job queue (leases, retries, dead-letter) → optional Knowledge Library index, generate/convert/redact, and ops (capacity, GC, immutable audit).

| Surface | Entry |
|---|---|
| Web | `/documents` + Settings → **Documents** |
| API | `/api/documents/*` (upload, jobs, convert, redact, index, ops) |
| Agent | `document-platform` tools (`document_import`, `document_read`, `document_index`, …) |
| Chat | `/documents` / `/docs` slash commands |
| TUI | Documents tab |

- **Hostile-by-default** — XXE/macro/polyglot/encryption rejection, prompt fences (`<kazma:data untrusted>`), optional **ClamAV** malware scan (`auto`/`on`/`off`)
- **Jobs can multi-replica** — Postgres job claims (`SKIP LOCKED`); document **metadata** is still single-replica SQLite unless `KAZMA_DOCUMENTS_METADATA_BACKEND=postgres`
- **Canary rollout** — `enabled` / `shadow` / `default_authoritative` live ConfigStore flags
- **Certification** — `python scripts/certify_documents.py` (+ hostile corpus, soak)

Docs: [Document Intelligence](docs/docs/guide/document-intelligence.md) · [Ops](docs/docs/ops/document-processing.md) · [Security](docs/docs/security/document-security.md) · [Phases 0–10](docs/docs/guide/document-phases.md)

### 🔌 Rich Ecosystem
- **Any LLM** — OpenAI, Anthropic, Gemini, DeepSeek, xAI, Ollama, and 15+ more via plain HTTP with Vision Capability Routing
- **MCP Marketplace** — One-click install from 85+ preset MCP servers with namespaced tools
- **Hardened Scraping & Auto-Retry** — 5 MB streaming byte limits (`KAZMA_FETCH_MAX_BYTES`), binary payload gates, exponential 5xx backoff, `robots.txt` options (`KAZMA_CRAWL_RESPECT_ROBOTS`), and automatic doubled `max_tokens` retry on truncation
- **Knowledge Library** — Ingest entire documentation sites into searchable RAG corpora with cited sources; **document_index** bridges platform uploads into the same libraries
- **IDE Subsystem** — Transport-agnostic coding backend: multi-tab editor, file-aware AI chat, `/ide` commands across all platforms
- **Time-Travel Replay & Branching** — Snapshot every iteration to SQLite WAL (`snapshots.db`); restore in-place (`/replay`), fork threads (`/fork`), and compare paths
- **Encrypted Vault** — AES-256-GCM storage for API keys and credentials
- **Browser, Calendar & Document generation** — Playwright automation, Google/Outlook calendar, legacy `generate_pdf`/`docx`/`xlsx` skills, plus the full **Document Intelligence** platform above
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
| **Document Intelligence** | ✅ secure pipeline | ⚠️ ad-hoc | ❌ | ❌ | ⚠️ limited |

---

## 📸 Screenshots

| Dashboard | Web IDE | Chat with HITL |
|---|---|---|
| ![Dashboard](https://kazma.ai/screenshots/Dashboard-en.png) | ![IDE](https://kazma.ai/screenshots/IDE-en.png) | ![Chat](https://kazma.ai/screenshots/Chat-en.png) |

| Swarm Task Builder | Skills | MCP Servers |
|---|---|---|
| ![Swarm](https://kazma.ai/screenshots/Swarm-Task-Builder-en.png) | ![Skills](https://kazma.ai/screenshots/Skills-en.png) | ![MCP](https://kazma.ai/screenshots/MCP-en.png) |

> **Documents:** open `/documents` after `kazma serve` for secure upload → parse → index (see [Document Intelligence](docs/docs/guide/document-intelligence.md)).

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

> **Extras:** `rag` = vector memory; `dev` = tests/lint; `web` = Playwright; `document` = simple PDF/DOCX/XLSX generators; `document-platform` = full Document Intelligence engines (OCR helpers, WeasyPrint, PyMuPDF); `database` = Postgres/MySQL/Mongo; `postgres` = multi-replica shared state (also document jobs/metadata when configured). Install everything with `pip install -e ".[all]"` or `uv sync --all-extras`.

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

Open **http://127.0.0.1:9090/documents** for Document Intelligence (upload → parse → index).

> **Full guides:** [Quickstart](docs/docs/guide/quickstart.md) · [Configuration](docs/docs/guide/configuration.md) · [Document Intelligence](docs/docs/guide/document-intelligence.md) · [Troubleshooting](docs/docs/guide/troubleshooting-and-workarounds.md)

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
    ├── DocumentIngestionService (durable docs: CAS + jobs + isolated parse)
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

## 🧠 Pure V2 Cognitive Memory & RAG

Kazma operates on a **V2 Cognitive Engine** for personal memory (single SoT for chat recall) plus an optional **Knowledge Library** product merge — unified in chat, **not** merged into one schema table:

| Component | Architecture | Role |
|---|---|---|
| **Bi-Temporal Beliefs** | SQLite SoT | Functional / set / state beliefs with temporal scrubbing; Dashboard topology paints from SQLite |
| **Episode Retrieval** | FTS5 + `sqlite-vec` | Sparse + dense fusion for “what we said” |
| **Associative PPR** | Local Ego-Graph | Multi-hop weight expansion over the belief graph |
| **Knowledge Library** | Separate store | Docs + citations; inject / federated search (`MEM` / `KB` labels); **document_index** from Document Intelligence |
| **Optional Neo4j** | Dual-write | Belief triples when configured; never required for install |
| **Procedural Memory** | Parametric Action DAGs | Tool skills with confidence smoothing and quarantine |

- **Per-Turn Prompt-Fenced RAG** — Beliefs/episodes (and optional KB) wrapped in `<kazma:data untrusted>` fences.
- **Settings → Memory** — Isolation, KB inject toggles, backends, Neo4j Test/Sync, and embedder in one tab.
- **Durable Task Queue (`memory_ops.db`)** — Post-turn extraction, micro-consolidation, partitioned reconsolidation for large corpora.
- **Automated Nightly Backups & Exports** — Native `sqlite3.backup()` plus JSONL / GraphML on a 24h scheduler.
- **Prompt-Fenced Soul Engine** — Self-improvement deltas via `ConfigStore` + untrusted fence.

Deep dive: [Memory & RAG](docs/docs/guide/memory-and-rag.md) · [Memory best path](docs/docs/guide/memory-best-path.md)

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
kazma-core/       Agent runner, LLM provider, swarm, memory/RAG, IDE, safety, documents/
kazma-gateway/    Telegram/Discord/Slack adapters, slash commands (/documents)
kazma-ui/         FastAPI web app, /documents, Settings, IDE, SSE chat, dashboard
kazma-tui/        Textual terminal dashboard + IDE + Documents tab
kazma-memory/     Arabic tokenizer + FTS5 search backend
kazma-skills/     Native skills (document-platform, vault, database, crawler, …)
kazma-cli/        The `kazma` command surface
```

---

## 📖 Documentation

| Document | What's inside |
|---|---|
| [Docs home](docs/docs/intro.md) | Full documentation map |
| [Document Intelligence](docs/docs/guide/document-intelligence.md) | Secure document pipeline (ingest → OCR → index → ops) |
| [Document processing ops](docs/docs/ops/document-processing.md) | Metrics, capacity, GC, multi-replica readiness |
| [Document security](docs/docs/security/document-security.md) | Threat model, sandbox, malware, fences |
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

**4,243+ tests passing** across 5 suites.

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

MIT-licensed, production-tested, 1,668+ commits. Built in Kuwait 🇰🇼 with full-stack execution.

[🌐 kazma.ai](https://kazma.ai) · [🐙 GitHub](https://github.com/Mubder/kazma) · [💬 Try the live demo](https://kazma-demo.fly.dev/) · [📧 Pilots & partnerships](mailto:admin@kazma.ai)

---

## 📜 License

MIT — see [LICENSE](LICENSE).
