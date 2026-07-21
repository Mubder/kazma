---
id: development
title: Development
sidebar_label: Development
description: Kazma Development — code-audited reference (unified docs, v0.6.1+)
---
> Repository layout, environment setup, and the test/lint/typecheck commands used across the Kazma monorepo.

---

## 1. Repository layout

```
kazma/
├── kazma-core/          # Agent runner, LLM provider, swarm, ConfigStore, safety, memory, skills, MCP, hub
├── kazma-gateway/       # Telegram/Discord/Slack adapters, agent_handler package, slash commands
├── kazma-ui/            # FastAPI app, SSE chat, swarm panel, settings, i18n, static assets
├── kazma-tui/           # Textual TUI dashboard
├── kazma-memory/        # Arabic tokenizer + SQLite/FTS5 search backend
├── kazma-skills/        # Native skills + manifests
├── kazma-cli/           # The `kazma` command surface
├── docs/                # Docusaurus site — single SoT (content under docs/docs/)
├── archive/             # Retired docs (former docs-v2, legacy pages)
├── tests/               # Cross-cutting tests
├── examples/            # Example skills (e.g. almuhalab_custom_skills)
├── scripts/             # Ops: migrate, smoke, tools-catalog regen, …
├── kubernetes/          # Sample K8s manifests (verify ports vs compose)
├── kazma.yaml           # Main config
├── kazma-permissions.yaml
├── kazma-security.yaml
├── services.yaml
├── pyproject.toml       # Single hatchling build for all 7 packages
├── Dockerfile           # Main agent image
├── docker-compose.yml   # Main agent compose
├── setup.ps1            # Windows bootstrap
└── run.sh               # Minimal E2E reproduction
```

---

## 2. Environment setup

### 2.1 Install (editable, all extras)

```bash
git clone <repo> kazma && cd kazma
python -m venv .venv
```

Activate the venv for your platform:

```bash
# Linux / macOS / WSL
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
```

Extras (`pyproject.toml:37-71`):

| Extra | Contents |
|---|---|
| `rag` | `chromadb>=0.5.0`, `sentence-transformers>=3.0.0` |
| `dev` | `pytest`, `pytest-asyncio`, `pytest-cov`, `pytest-mock`, `ruff`, `mypy`, `locust` |
| `test` | pytest stack + `fakeredis` |
| `tracing` | `opentelemetry-*` exporters/instrumentors |
| `tui` | `textual>=8.0.0`, `python-bidi` |

### 2.2 Windows

```powershell
.\setup.ps1            # validates env, syncs venv, runs import check
```

### 2.3 uv (used by `run.sh`)

```bash
uv sync --extra dev --extra cli --extra tui --extra rag
```

---

## 3. Code style & conventions

From AGENTS.md:

- **Python:** type hints, docstrings, logging.
- `logger = logging.getLogger(__name__)` pattern.
- `from __future__ import annotations` for type hints.
- One concern per file; keep modules focused.
- **Compile-check Python before committing:** `python -c "import py_compile; py_compile.compile(r'&lt;file>', doraise=True); print('OK')"`
- **Syntax-check JS before committing:** `node --check "&lt;file>"`
- **PowerShell:** never `&&`/`||`; use `;` and `$LASTEXITCODE`.

---

## 4. Test / lint / typecheck

```bash
# Python compile check (fast smoke)
.venv/Scripts/python.exe -c "import py_compile; py_compile.compile(r'kazma-core/kazma_core/llm_provider.py', doraise=True); print('OK')"

# JS syntax check
node --check "kazma-ui/kazma_ui/static/js/chat.js"

# Run tests
python -m pytest kazma-core/tests/ -v
python -m pytest tests/ -v                 # cross-cutting
python -m pytest -k majlis -v              # specific

# Lint
python -m ruff check kazma-core/kazma_core/
python -m ruff check kazma-tui/kazma_tui/  # (per services.yaml)

# Type check
python -m mypy kazma-tui/kazma_tui/        # (per services.yaml)
```

Per-package commands are also declared in `services.yaml`:

```yaml
commands:
  install: "pip install -e kazma-tui/ -e kazma-core/"
  test: "python -m pytest kazma-tui/tests/ -v"
  lint: "python -m ruff check kazma-tui/kazma_tui/"
  typecheck: "python -m mypy kazma-tui/kazma_tui/"
```

---

## 5. The Docusaurus docs site (`docs/`)

Single documentation tree: **`docs/docs/`** (Docusaurus 3.x). Config: `docs/sidebars.js`, `docs/docusaurus.config.js`.

```bash
cd docs
npm install
npm start               # http://localhost:3000/kazma/
npm run build

# or via CLI:
kazma docs build
kazma docs serve
```

Regenerate the tools catalog after adding tools:

```bash
python scripts/generate_tools_catalog.py
```

Plan & archive policy: `docs/DOCS_CONSOLIDATION_PLAN.md`, `archive/README.md`.

---

## 6. Server lifecycle (development)

Restart the dev server (PowerShell, from AGENTS):

```powershell
Get-Process -Name python -ErrorAction SilentlyContinue |
  Where-Object { (Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.Id)).CommandLine -like '*uvicorn*kazma*' } |
  ForEach-Object { Stop-Process -Id $_.Id -Force }

cd 'G:\GitHubRepos\kazma'
& '.venv\Scripts\python.exe' -m uvicorn kazma_ui.app:create_app --factory --host 127.0.0.1 --port 9090
```

---

## 7. Contributing

See `CONTRIBUTING.md` (root) for the full guide. Quick rules:

- Branch off `main` for PRs.
- Keep public API signatures stable (the swarm refactor preserved them; do the same).
- Compile/syntax-check before committing.
- Run the relevant test suite.
- Document security implications of any new danger tool or config flag.

---

## Documentation Audit Notes

- The repo root has **many `.pytest_tmp_*` directories** from prior test runs — git-ignored clutter, safe to clean.
- `services.yaml` is scoped to `kazma-tui` commands; treat it as an example, not the canonical task runner for all packages.
- `run.sh` is a minimal end-to-end reproduction (installs, runs the full suite, exercises a live agent, writes `EVAL.md`) — useful for CI-like validation.
