---
id: quickstart
title: Quickstart
sidebar_label: Quickstart
description: Kazma Quickstart — code-audited reference (unified docs, v0.6.1+)
---
> Get Kazma running and answering messages in under 10 minutes. This guide covers the three install paths and the shortest path to a working chat.

---

## 1. Prerequisites

| Requirement | Detail |
|---|---|
| **Python** | `>=3.11, &lt;3.14` (declared in `pyproject.toml`). |
| **Git** | For cloning / `kazma update` (git install path). |
| **An LLM provider key** | At least one of: OpenAI, Anthropic, Google (ADC), DeepSeek, xAI, OpenRouter, NVIDIA NIM, Mistral, Together, Cohere, Fireworks, Perplexity, AI21 — or a local server (Ollama / LM Studio). Azure OpenAI and AWS Bedrock are also supported natively. |
| **Node.js** (optional) | Only if you want to build/serve the Docusaurus docs site (`docs/`). Not required to run Kazma itself. |

> **Note on extras:** Core `pip install -e .` installs everything needed to run the agent, Web UI, TUI, gateways, and swarm. The `rag` extra (`chromadb`, `sentence-transformers`) is required **only** for vector memory / RAG — see [Memory & RAG](memory-and-rag).

---

## 2. Install

Choose one path. All three produce the same console scripts: `kazma`, `kazma-tui`, `kazma-web`.

> **Repo README** also has a short Windows-friendly Quick Start (ports, `ERR_CONNECTION_RESET`). Prefer this guide for depth.

### Path A — Editable install (recommended for development)

```bash
git clone <your-repo-url> kazma
cd kazma
python -m venv .venv
```

Activate the venv for your platform:

```bash
# Linux / macOS / WSL (bash, zsh)
source .venv/bin/activate
```

```powershell
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

```cmd
:: Windows (CMD)
.venv\Scripts\activate.bat
```

```bash
pip install -e ".[rag,dev]"
# or install everything at once:
pip install -e ".[all]"
# or: uv sync --all-extras
```

Optional extras (only install the ones you need):

| Extra | Packages | Enables |
|---|---|---|
| `[rag]` | `chromadb>=0.5.0`, `sentence-transformers>=3.0.0`, `sqlite-vec>=0.1.6` | Vector memory / RAG (4-layer UnifiedMemoryAdapter) |
| `[dev]` | `pytest`, `pytest-asyncio`, `ruff`, `mypy`, `locust`, … | Tests + linting |
| `[tui]` | `textual>=8.0.0`, `python-bidi>=0.4.0` | Terminal dashboard (`kazma-tui`) |
| `[observability]` | `prometheus-client>=0.20.0` | `/metrics` scrape endpoint |
| `[web]` | `playwright>=1.40` | Browser-automation skill (run `playwright install chromium` after) |
| `[document]` | `reportlab>=4.0.0`, `python-docx>=1.1.0`, `openpyxl>=3.1.0` | Document-generator skill (PDF/DOCX/XLSX) |
| `[database]` | `psycopg[binary]>=3.1.0`, `pymysql>=1.1.0`, `pymongo>=4.7.0` | Postgres/MySQL/Mongo in the database-client skill |
| `[postgres]` | `psycopg[binary,pool]>=3.1.0`, `langgraph-checkpoint-postgres>=2.0.0` | Multi-replica SaaS shared state |
| `[all]` | meta — installs every extra above | Convenience |

> The native skills (browser, calendar, document-generator, database) **always load** — their tools are registered even when the optional package is absent; calling a tool whose backend isn't installed returns a friendly install-hint string instead of failing. Install the extra to activate that backend.

### Path B — Production Docker Compose

```bash
cp .env.example .env      # then edit .env (see step 3)
docker compose up -d --build
```

The container runs `uvicorn kazma_ui.app:create_app --factory --host 0.0.0.0 --port 9090` as the non-root `kazma` user. Health check hits `/api/gateway/status` every 30 s. See [Deployment](deployment).

### Path C — Windows native (`setup.ps1`)

```powershell
# From a PowerShell prompt in the repo root:
.\setup.ps1
```

`setup.ps1` creates the venv, installs editable with extras, and bootstraps data directories. Never chain PowerShell commands with `&&` or `||` — use `;` and check `$LASTEXITCODE`.

---

## 3. Configure

Kazma reads configuration from three layers (in increasing precedence for runtime reads):

1. **`kazma.yaml`** — declarative defaults (the source of truth on first boot).
2. **ConfigStore (SQLite)** — `kazma-data/settings.db`; overrides `kazma.yaml` after first boot.
3. **Environment variables** — win in specific helpers (e.g. `KAZMA_SECRET`, `KAZMA_VECTOR_PATH`).

> Full precedence rules are documented in [Configuration → Override Precedence](configuration#override-precedence).

### Minimal `.env`

Copy `.env.example` to `.env` and set at least one provider key:

```bash
# Linux / macOS / WSL
cp .env.example .env
```

```powershell
# Windows (PowerShell)
Copy-Item .env.example .env
```

```dotenv
# Required for the OpenAI provider:
OPENAI_API_KEY=sk-...

# Or, for DeepSeek (key stored via provider config, not a dedicated env var —
# see Configuration). Local servers (Ollama/LM Studio) need no key.

# Optional: Telegram gateway
# TELEGRAM_BOT_TOKEN=123456:ABC...

# Optional: protect HITL approval endpoints (recommended for any non-localhost deploy)
# KAZMA_SECRET=generate-a-long-random-string
```

> **Important:** Only `OPENAI_API_KEY` and `KAZMA_API_KEY` are read as generic env-var fallbacks by the LLM provider. Other providers (DeepSeek, Anthropic, xAI, …) are keyed through the ConfigStore provider list / `kazma.yaml` — there are **no** dedicated `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` env vars read by the code. See [Configuration → API keys](configuration#api-keys).

### Check `kazma.yaml`

Open `kazma.yaml` and confirm:

```yaml
agent:
  name: kazma
  language: ar        # 'ar' enables RTL + Arabic UI; 'en' for English
  rtl: true

models:
  default: gpt-4o-mini
  router: litellm     # string only — see Configuration for what this actually does
  fallback: gpt-4o-mini

llm:
  base_url: https://api.openai.com/v1
  model: gpt-4o-mini
  max_tokens: 4096
  temperature: 0.7
  timeout: 60.0
  input_cost_per_1m: 0.15
  output_cost_per_1m: 0.6

ui:
  host: 127.0.0.1
  port: 9090
```

The complete key-by-key reference is in [Configuration](configuration).

---

## 4. Run

### Web UI (most common)

```bash
kazma serve              # default host 127.0.0.1, port 9090
kazma serve 9091         # if 9090 is taken (common on Windows + WSL/Docker)
# or:
python -m uvicorn kazma_ui.app:create_app --factory --host 127.0.0.1 --port 9091
```

Open the URL printed in the terminal (e.g. `http://127.0.0.1:9090` or `:9091`). Use **http**, not https. Navigate to **Chat** and send a message.

Smoke-test:

```bash
curl http://127.0.0.1:9091/health
```

> **Binding security:** Default host is **`127.0.0.1`**. Non-loopback (`KAZMA_HOST=0.0.0.0`) **requires** a strong `KAZMA_SECRET` or the process exits. On loopback, a secret is auto-generated for the process if unset (not persisted).  
> **Windows `ERR_CONNECTION_RESET` on :9090:** often a stale WSL/Docker **portproxy** on 9090, not Kazma — see [Troubleshooting §15.0](troubleshooting-and-workarounds#150-browser-err_connection_reset-on-http1270019090-windows) and run `kazma serve 9091`.

### TUI

```bash
kazma-tui
```

A Textual dashboard with tabs for Dashboard, Chat, Files, Traces, Swarm, Settings. The TUI is primarily a read-only observability view of the core singletons (it initializes `ModelRegistry` and `SwarmEngine` on first launch if they don't exist).

### Verify with the CLI

```bash
kazma status
```

Probes the running server (`/api/gateway/status`, `/api/swarm/status`) and prints Python/Kazma versions, config path, and key package versions.

---

## 5. Send your first message

Once `kazma serve` is running, the fastest loop is the Web UI chat. Behind the scenes:

1. Your text is `POST`ed to `/api/chat/stream` (SSE).
2. The supervisor node calls the active LLM with the registered tools.
3. If the LLM invokes a **danger tool** (e.g. `file_write`, `shell_exec`), execution **pauses** and an `approval_required` SSE event is emitted.
4. Approve via the Web UI button → `POST /api/approve/\{thread_id\}` → the graph resumes with `Command(resume=\{"approved": true\})`.

You can watch tool calls, token usage, and cost stream back as SSE events. See [API & Extension Points → SSE event contract](api-and-extension-points#sse-event-contract).

---

## 6. Enable a second channel (Telegram, optional)

```dotenv
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
```

```yaml
connectors:
  telegram:
    enabled: true
```

Restart the server. The Telegram adapter polls via long-polling (or a webhook at `/api/webhooks/telegram` if configured). Try `/help`, `/status`, `/model` in the bot.

> **Platform isolation:** Your Telegram `chat_id`, `user_id`, and `message_id` **never** enter the LangGraph state — they live in the `SessionStore` and are re-attached on reply via `_build_target_id()`. See [Gateways & Platforms](gateways-and-platforms).

---

## 7. Next steps

| If you want to… | Read |
|---|---|
| Understand the engine | [Architecture](architecture) |
| Tune every setting | [Configuration](configuration) |
| Add a custom tool / skill | [Skills, MCP & Tools](skills-mcp-and-tools) |
| Run a multi-worker swarm | [Swarm Orchestration](swarm-orchestration) |
| Lock down a production deploy | [Security & Safety](security-and-safety) + [Deployment](deployment) |
| Use Kazma in Arabic | [Arabic & Cultural Features](arabic-cultural-features) |

---

## Documentation Audit Notes

- The previous README's "4-layer memory pipeline" is **partially wired** — see [Memory & RAG → Honest status](memory-and-rag#honest-status-notes). Quickstart deliberately avoids implying automatic memory retrieval, since RAG in the chat path requires the LLM to voluntarily call `memory_search`.
- `tiktoken` is **not** a declared dependency; token counting falls back to a chars/4 heuristic unless you `pip install tiktoken` yourself.
