---
id: quickstart
title: Quickstart
sidebar_label: Quickstart
description: Kazma Quickstart — code-audited reference (unified docs, v0.9+)
---
> This page is published at [kazma.ai/docs/quickstart/](https://kazma.ai/docs/quickstart/) ([Arabic](https://kazma.ai/ar/docs/quickstart/)).

> Get Kazma running and answering messages in under 10 minutes. This guide is the install source of truth — keep it in lockstep with `pyproject.toml`, `setup.cmd` / `setup.ps1` / `setup.sh`, and Settings → Packages.
>
> Once Kazma is running, open **`/` — that is chat**, not the dashboard. On a new install its [setup guide](#the-setup-guide) connects the model first. Inspectors live under **More**.

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
# Windows (PowerShell or Command Prompt)
.\setup.cmd
```

`setup.cmd` runs `setup.ps1`. A fresh Windows refuses to run PowerShell script
files ("running scripts is disabled on this system": its execution policy is
Restricted), so `setup.ps1` cannot be started directly there; `setup.cmd` is not a
PowerShell script, and it starts PowerShell with the policy bypassed for that
one process. No setting is changed, and a policy set by Group Policy still
wins.

```bash
# Linux / macOS / WSL
chmod +x setup.sh
./setup.sh
```

The script installs `uv` when it is missing (and through it a Python, when
this machine has none), Kazma's base packages, then asks **how to install
Kazma** and hands over to `kazma setup install` (the uv it installs is one
release, downloaded from uv's GitHub release and checked against the SHA-256
the script records before it is unpacked; a download that does not match stops
setup and installs nothing):

| Install | What it is |
|---|---|
| **Express** (the default) | Everything that needs no Docker: the least Kazma can lack, in one run. |
| **Full** | Every part, Docker, PostgreSQL and private search among them. Docker needs the CPU's virtualization; on Windows a restart may come in the middle. |
| **Custom** | A checklist that starts from Express; each part says what Kazma lacks without it. |

The parts, and what Kazma lacks without each (`kazma setup install --list`
prints the same list):

| Part | Express | Full | Without it |
|---|---|---|---|
| Meaning search (bge-m3) | yes | yes | memory recall and the Knowledge Library match words only |
| Document reading and writing (PyMuPDF, PDFium, WeasyPrint) | yes | yes | PDFs are read by the simpler built-in readers (weaker on Arabic and tables) |
| Kazma's test suite (pytest, ruff) | yes | yes | Kazma's tests cannot run here |
| Code structure (tree-sitter) | yes | yes | the code index reads code by pattern only |
| Database drivers (MySQL, MongoDB, Postgres) | yes | yes | the database tools reach SQLite files only |
| Push notifications (Web Push) | yes | yes | no push notification when a task finishes (the chat still says it) |
| Metrics for Prometheus | yes | yes | no Prometheus metrics (the Dashboard still shows Kazma's state) |
| OCR with Arabic and English (Tesseract) | yes | yes | a scanned PDF or a photo of a page gives no text |
| Neural OCR for Arabic (its models) | yes | yes | OCR reads with Tesseract only (weaker Arabic, no Arabic-Indic digits) |
| Office documents (LibreOffice) | yes | yes | Office files cannot be read or converted |
| Malware scan (ClamAV, refreshed daily) | yes | yes | documents are not scanned for malware |
| Arabic fonts | yes | yes | Office's Protected View shows Kazma's Word and PowerPoint files in another face; on Linux Arabic may be drawn as boxes |
| Node.js | yes | yes | MCP servers that need npx cannot start (health reads degraded) |
| Browser tools (Playwright and Chromium) |  | yes | no browser tools; pages are read without running their JavaScript |
| Docling (the hardest PDFs) |  | yes | a PDF the main readers cannot read gets no second local try |
| PostgreSQL 16 as a service |  | yes | SQLite: a restore goes back to the last backup (every 6 hours) |
| Docker |  | yes | no containers (documents parse in a limited process; no private search) |
| Locked document worker (a container per document) |  | yes | documents are read in a limited process on this machine (not qualified for hostile files) |
| Private web search (SearXNG) |  | yes | web search uses public engines' pages directly (or Tavily with your key) |
| E2B cloud sandbox |  | yes | no E2B sandbox for python_exec |
| Temporal (durable swarm steps) |  | yes | swarm runs are not resumed through Temporal |
| Neo4j memory graph driver |  | yes | the memory graph stays in SQLite |
| Kazma as a service (its guard) | yes | yes | you start Kazma yourself (kazma serve), and nothing restarts it |

How a run goes:

- **Which Kazma.** Its first lines say the version and commit it installs,
  and what Kazma is, in Kazma's blue (readable on a light or a dark terminal;
  plain when `NO_COLOR` is set or the output goes to a file).
- **Everything is checked first.** Before anything is done it lists every
  problem it can see: too little disk space, no network, an administrator
  terminal needed, and whether Docker can run here at all (virtualization off
  in the firmware, or a Windows virtual machine whose host does not pass it
  through). Then a short summary (the parts to install, those already there
  and those left out, by name) and one question. Every part's state and every
  step's command go to `.kazma/install.log`; `--dry-run` and `--check` print
  them. Keys pressed before a question (a stray Enter) are not taken as its
  answer.
- **An administrator window.** On Windows, system packages and the service
  need administrator rights: `setup.ps1` opens an administrator window for
  itself and goes on there (`-NoElevate` stays in the same window). On Linux
  and macOS each step asks for `sudo` itself.
- **It finishes, or says what is left.** A step that fails (a cut-off
  download) is tried again, three times in all; a part that still fails stops
  only what needs it, and the end names what Kazma lacks for each part not
  installed. What was chosen and how each part ended is recorded in
  `kazma-data/setup_record.json`; running setup again (after a restart, or to
  repair) checks everything and continues where it stopped. Everything said,
  and every program's output, goes to `.kazma/install.log`.
- **Kazma opens.** With the service installed it waits for Kazma to answer and
  opens the browser on its setup guide (below).

### The setup guide

`/setup` takes a new install to a working model, in English or Arabic. Setup
opens it; until a model is set up, the chat page shows a card that opens it;
Settings → Providers & Connectors opens it again (**Setup guide**), as does
the page search (Ctrl+K).

1. **Welcome**: what Kazma is, and the language.
2. **Model**: either
   - **a cloud provider**: pick it (each has a link to where its API keys are
     made), paste the key, and the guide lists the provider's models; or
   - **a model on this computer**: the guide looks for Ollama and LM Studio
     here, says which run and how many models each has, and where to download
     one that does not; or give the address of **a server on another computer
     on your network** (vLLM, llama.cpp, LocalAI, LM Studio or Ollama there,
     such as `192.168.1.20:8000`). It needs no key.

   Pick a model and the guide sends it one short message with the key as you
   typed it. Only when it answers does the guide save the provider and make
   the model the one Kazma uses (model and provider together): a key that is
   refused is never saved. Run the guide again to change the model: a key
   saved before can be left empty. Azure OpenAI, AWS Bedrock and endpoints of your own need
   more than a key, and are set up in Settings → Providers.
3. **Extras**, each optional: chat apps (Telegram, Discord, Slack), Memory →
   About me, mail and calendar, and what was installed (Settings → Packages).
4. **Done**: the model in use and a few things to try.

The guide's routes are `/api/setup/*`; everything but the status is the
administrator's, as Settings is.

```bash
kazma setup install --list                     # every part, what Kazma lacks without it
kazma setup install --check                    # what is there, what is missing; changes nothing
kazma setup install --mode full --dry-run      # the plan; runs nothing
kazma setup install --mode express -y          # no questions
kazma setup install --with browser --without push
```

`setup.cmd` (and `setup.ps1`) take `-Mode express|full|custom` and `-Yes`; `setup.sh` takes
`--mode` and `-y`. Without a terminal (a script, CI) setup installs Kazma's own
packages for the mode and shows the rest as the plan; `-Yes` / `-y` installs
everything.

Settings → Packages shows the install: which one it was and when, each part's
packages, a part the install left out as **Not chosen** (never "Missing"), and
the command that adds a part, `kazma setup install --with <part>`, which also
installs what the part needs outside Python (Chromium for the browser tools,
for example).

The system programs come from the OS's package manager:

| What | Windows (winget) | macOS (Homebrew) | Debian / Ubuntu / WSL (apt) | Fedora (dnf) |
|---|---|---|---|---|
| Visual C++ runtime (PyTorch and ONNX Runtime load it) | `Microsoft.VCRedist.2015+.x64`, first | not needed | not needed | not needed |
| OCR | `UB-Mannheim.TesseractOCR`, plus the Arabic and English models (a pinned release, checked by SHA-256) | `tesseract`, `tesseract-lang` | `tesseract-ocr`, `tesseract-ocr-ara`, `tesseract-ocr-eng` | `tesseract`, `tesseract-langpack-ara` |
| Office conversion | `TheDocumentFoundation.LibreOffice` | `--cask libreoffice` | `libreoffice-writer` | `libreoffice-writer` |
| Malware scan | `Cisco.ClamAV`, plus a daily `freshclam` task | `clamav`, plus a daily `freshclam` launchd job | `clamav`, `clamav-freshclam` | `clamav`, `clamav-update` |
| Arabic fonts | IBM Plex Sans Arabic from Kazma's bundle, installed for all users (a per-user copy of it is retired) | IBM Plex Sans Arabic from Kazma's bundle, in `~/Library/Fonts` | `fonts-noto-core`, plus IBM Plex Sans Arabic in `~/.local/share/fonts/kazma` | `google-noto-sans-arabic-fonts`, plus IBM Plex Sans Arabic in `~/.local/share/fonts/kazma` |
| Node.js | `OpenJS.NodeJS.LTS` | `node` | `nodejs`, `npm` | `nodejs`, `npm` |
| Docker (Full, or any part that runs containers) | Kazma's own engine: Docker Engine in the `kazma-engine` WSL distro, started at boot by the `KazmaEngine` task with nobody signed in (WSL 2 first; every download pinned by SHA-256). `--docker desktop` installs `Docker.DockerDesktop` instead | Docker Desktop (cask) | `docker.io`, started at boot, this account in the `docker` group | `moby-engine`, started at boot, this account in the `docker` group |

Docker Desktop starts only when someone signs in, so after an unattended
restart (a Windows update, a power cut) its containers wait; Kazma's engine
does not. When Windows shuts down or restarts, the `KazmaEngineShutdown` service stops the engine's
containers cleanly first (each gets its own stop time, up to two minutes in all), so nothing they just
wrote is lost. Express installs no container part, so it installs no engine. On
Windows `kazma setup engine` installs, repairs or checks it on its own
(`--check`, `--dry-run`, `-y`; `--remove` takes it away, refused while
another project's containers live in it), and Settings → System chooses which
engine Kazma uses. On Linux, Kazma's engine is the system's Docker service.

`kazma setup system` installs these programs alone (`--check`, `--dry-run`,
`-y` as above), and `kazma doctor` shows the same checks and names the command
for anything missing. On a brand-new Windows, winget may not have set up its
package list yet (it answers 0x8A15000F to every install); setup registers
the list itself and carries on. Postgres as a service on its own is
`kazma setup postgres` (the operations guide, "Postgres as an OS service").

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
# The Python packages of an Express install, by hand:
uv sync --inexact --extra rag --extra document-platform --extra dev --extra index --extra database --extra push --extra observability --extra ocr
# or:
pip install -e ".[rag,document-platform,dev,index,database,push,observability,ocr]"
# Everything:
# uv sync --all-extras
# pip install -e ".[all]"
```

`uv sync` here is for a **first install** into a fresh environment. On an
installed Kazma, update with `python -m kazma_cli update` instead: a bare
`uv sync` removes every package outside the extras it names
([Kazma update](../ops/kazma-update#the-short-version)).

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

**Document Intelligence first use:** open `/documents` after start (core text parsers work without extras). For convert/redact engines: `pip install -e ".[document-platform]"`; it also brings PyMuPDF, the PDF reader that puts Arabic PDF text, numbers and table cells back in reading order. Optional system packages: Tesseract (OCR; Arabic OCR is not qualified — it does not read Arabic-Indic digits), ClamAV (malware scan), LibreOffice (legacy `.doc`/`.xls`/`.ppt`, some conversions, full-quality Arabic PDFs). Documents are parsed in a separate worker process with memory and CPU limits; that default does not confine files or network, so for documents you do not trust use the container worker profile. Guide: [Document Intelligence](document-intelligence).

**Windows: ClamAV.** Production turns malware fail-closed on, so uploads are rejected until `clamscan` can actually scan. Install Cisco ClamAV, then download the virus database with `freshclam` and schedule `freshclam` to run every day: the package ships the programs and no signatures, and signatures more than 7 days old degrade document readiness. Kazma runs `clamdscan` when that program is on `PATH`, and `clamscan` otherwise. `clamdscan` needs a running `clamd`. The Windows folder contains both executables, so do not add that folder to `PATH` unless the daemon is running. Leave `clamdscan` off `PATH` and put only `clamscan` there. Steps: [How do I enable malware scanning?](./faq#how-do-i-enable-malware-scanning).

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

Installed by setup with its service part (Express and Full have it), Kazma is
already running and setup opened the browser on it. `kazma serve` is for an
install without the service: Path B, or Custom with the service left out.

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

- Memory recall is automatic: on every turn Kazma looks up what the question is about in earlier conversations and stored facts, and gives the model nothing when nothing matches; every finished turn is remembered ([Memory & RAG → `recall()`](memory-and-rag#recall)). The old README's "4-layer memory pipeline" was removed in the V2 memory cutover.
- `tiktoken` is **not** a declared dependency; token counting falls back to a chars/4 heuristic unless you `pip install tiktoken` yourself.
