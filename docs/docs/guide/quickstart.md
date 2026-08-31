---
id: quickstart
title: Quickstart
sidebar_label: Quickstart
description: Kazma Quickstart — code-audited reference (unified docs, v0.9+)
---
> Get Kazma running and answering messages in under 10 minutes. This guide is the install source of truth — keep it in lockstep with `pyproject.toml`, `setup.ps1` / `setup.sh`, and Settings → Packages.

---

## 1. Prerequisites

| Requirement | Detail |
|---|---|
| **Python** | `>=3.11, &lt;3.15` (declared in `pyproject.toml`). 3.12 or 3.13 recommended. |
| **Git** | For cloning / operator upgrades via [`kazma update`](../ops/kazma-update) (git install path). |
| **An LLM provider key** | At least one of: OpenAI, Anthropic, Google (ADC), DeepSeek, xAI, OpenRouter, NVIDIA NIM, Mistral, Together, Cohere, Fireworks, Perplexity, AI21 — or a local server (Ollama / LM Studio). Azure OpenAI and AWS Bedrock are also supported natively. |
| **Node.js** (optional) | Only if you want to build/serve the Docusaurus docs site (`docs/`). Not required to run Kazma itself. |

> **Note on extras:** A bare `pip install -e .` already includes the agent, Web UI, TUI (`textual` is a core dep), gateways, swarm, and the document-generation libraries. The **`rag` extra** (`chromadb`, `sentence-transformers`, `sqlite-vec`) is what you add for vector memory / dense recall — see [Memory & RAG](memory-and-rag). There is **no `[cli]` extra**; `kazma-cli` ships in the wheel.

---

## 2. Install

Choose one path. All of them produce the same console scripts: `kazma`, `kazma-tui`, `kazma-web`.

> **Repo README** has a short Windows-friendly Quick Start (ports, `ERR_CONNECTION_RESET`). Prefer this guide for depth.

### Path A — Bootstrap script (recommended)

```bash
git clone https://github.com/Mubder/kazma.git
cd kazma
```

```powershell
# Windows (PowerShell) — never chain with && / ||; use ; and $LASTEXITCODE
.\setup.ps1
```

```bash
# Linux / macOS / WSL
chmod +x setup.sh
./setup.sh
```

The script: requires Python 3.11+, installs `uv` if missing, runs
`uv sync --extra rag --extra dev --extra tui`, copies `.env.example` → `.env`
when needed, and import-checks LangGraph / aiosqlite / textual. It does **not**
install `[all]` (that pulls torch, Playwright, WeasyPrint, Temporal, E2B).

### Path B — Editable install (manual)

```bash
git clone https://github.com/Mubder/kazma.git kazma
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
# Same extras as the bootstrap scripts:
uv sync --extra rag --extra dev --extra tui
# or:
pip install -e ".[rag,dev,tui]"
# Everything:
# uv sync --all-extras
# pip install -e ".[all]"
```

Optional extras (only install the ones you need; Settings → Packages can add them later):

| Extra | Packages | Enables |
|---|---|---|
| `[rag]` | `chromadb`, `sentence-transformers`, `sqlite-vec` | Vector memory / dense recall |
| `[dev]` | `pytest`, `pytest-asyncio`, `pytest-cov`, `pytest-mock`, `ruff`, `mypy`, `locust`, `pre-commit` | Tests + lint + git hooks |
| `[test]` | pytest stack + `pytest-timeout` + `fakeredis` | CI-lighter than `dev` |
| `[tui]` | `textual`, `python-bidi` | Terminal dashboard (`kazma-tui`; `textual` is also core) |
| `[observability]` | `prometheus-client` | `/metrics` scrape endpoint |
| `[web]` | `playwright` | Browser-automation skill (then `playwright install chromium`) |
| `[push]` | `pywebpush` | Web Push for turn-complete (self-disables if missing) |
| `[document]` | `reportlab`, `python-docx`, `openpyxl`, `pypdf`, … | Same libs as core; extra kept for explicit installs |
| `[ocr]` | `pytesseract`, `pdf2image`, `pillow` | OCR (needs system Tesseract) |
| `[convert]` | `weasyprint` | HTML→PDF (needs OS fonts / GTK on some hosts) |
| `[document-platform]` | `document` + `ocr` + `convert` + `pymupdf` + `pypdfium2` | Full Document Intelligence engines |
| `[docling]` | `docling` | Local hard-PDF salvage after PyMuPDF |
| `[index]` | `tree-sitter`, `tree-sitter-python`, `tree-sitter-javascript` | Codebase index (regex fallback always works) |
| `[sandbox]` | `e2b-code-interpreter` | Firecracker `python_exec` (`E2B_API_KEY`) |
| `[durable]` | `temporalio` | Temporal-wrapped swarm (`KAZMA_TEMPORAL_HOST`) |
| `[database]` | `psycopg`, `pymysql`, `pymongo` | Extra drivers for the database-client skill |
| `[postgres]` | `psycopg[binary,pool]`, `langgraph-checkpoint-postgres` | Multi-replica shared state |
| `[all]` | meta — every extra above | Convenience; heavy |

> Native skills (browser, calendar, document-generator, **document-platform**, database) **always load**. Calling a tool whose backend isn't installed returns an install-hint instead of crashing. Install the extra to activate that backend.

**Document Intelligence first use:** open `/documents` after start (core text parsers work without extras). For convert/redact engines: `pip install -e ".[document-platform]"`. Optional system packages: Tesseract (OCR), ClamAV (malware scan), LibreOffice (some conversions). Guide: [Document Intelligence](document-intelligence).

### Path C — Production Docker Compose

```bash
cp .env.example .env      # then edit .env (see step 3)
docker compose up -d --build
```

The image installs `.[rag,postgres,document-platform]`, listens on **container port 8000**, and compose maps **host 9090 → 8000** (`HOST_PORT` to override). Health check hits `/health/ready` every 30 s (300 s start period). See [Deployment](deployment).

Do **not** start with raw `python -m uvicorn` on Windows — that forces a Proactor loop and silently drops Postgres checkpoints. Use `kazma serve` / the guard.

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
  router: kazma       # Kazma's own router — not an import of LiteLLM
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
```

On a watched host (Scheduled Task / systemd / launchd), pick up a `git pull` with the guard — do not kill `python`/`uvicorn` by hand:

```powershell
& '.venv\Scripts\python.exe' scripts\service\kazma_guard.py --reload
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

### `kazma ask` (no web server)

The same LangGraph supervisor, without uvicorn. Tokens stream to stdout;
tool lines go to stderr.

```bash
kazma ask "What files define the supervisor graph?"
kazma ask --plan "Add a rate limiter to the API"
kazma ask --json "fix the tests"          # NDJSON events (token/tool/done)
echo "summarize README.md" | kazma ask --json --no-stream -
```

On a **TTY**, danger tools prompt `y/N` (or `a` = allow for this session).
Piped stdin / `kazma ask -` **fail closed** (no HITL on a consumed stdin).
`--yolo` is the explicit headless escape hatch. Workspace is cwd
(`--workspace PATH` to override).

**ACP:** `kazma acp` is Agent Client Protocol JSON-RPC on stdio. Point Zed /
JetBrains at that command. The agent streams `session/update` chunks and
tool calls, and asks the editor to approve danger tools via
`session/request_permission`.

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
