---
id: development
title: Development
sidebar_label: Development
description: Kazma Development — code-audited reference (unified docs, v0.9+)
---
> Repository layout, environment setup, and the test/lint/typecheck commands used across the Kazma monorepo.

---

## 1. Repository layout

```
kazma/
├── kazma-core/          # Agent runner, LLM provider, swarm, ConfigStore, safety, V2 memory, skills, MCP
├── kazma-gateway/       # Telegram/Discord/Slack adapters, agent_handler, slash commands
├── kazma-ui/            # FastAPI app, SSE chat, swarm panel, settings, i18n, static assets
├── kazma-tui/           # Textual TUI dashboard
├── kazma-skills/        # Native skills + manifests
├── kazma-cli/           # The `kazma` command surface
├── docs/                # Docusaurus site — single SoT (content under docs/docs/)
├── tests/               # Cross-cutting tests
├── examples/            # Example skills
├── scripts/             # Ops: migrate, smoke, guard, tools-catalog regen, …
├── kubernetes/          # Sample K8s manifests (Hub service, not the main agent)
├── kazma.yaml           # Main config
├── kazma-permissions.yaml
├── kazma-security.yaml
├── pyproject.toml       # Single hatchling build (6 packages in the wheel)
├── Dockerfile           # Main agent image
├── docker-compose.yml   # Host 9090 → container 8000
├── setup.ps1            # Windows bootstrap (uv sync rag+dev+tui)
├── setup.sh             # Linux / macOS / WSL bootstrap
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
# Same extras as setup.sh / setup.ps1:
pip install -e ".[rag,dev,tui]"
# or: uv sync --extra rag --extra dev --extra tui
```

There is **no `[cli]` extra** and no `[tracing]` extra. Full extra table: [Quickstart §2](quickstart#path-b--editable-install-manual).

### 2.2 Windows

```powershell
.\setup.ps1            # Python 3.11+, uv, uv sync rag+dev+tui, import check
```

### 2.3 uv (used by `setup.sh` / `run.sh`)

```bash
uv sync --extra rag --extra dev --extra tui
# Full optional set (heavy):
# uv sync --all-extras
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

# Run tests — prefer the crash-tolerant chunked runner (full suite ~5 min)
python scripts/fast_test.py
python -m pytest tests/test_system_install_allowlist.py -v   # one file

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

Plan & archive policy: `docs/plans/done/DOCS_CONSOLIDATION_PLAN.md` (completed; retired trees live under `docs/audits/archive/`).

---

## 6. Server lifecycle (development)

Restart the dev server (PowerShell, from AGENTS):

```powershell
Get-Process -Name python -ErrorAction SilentlyContinue |
  Where-Object { (Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.Id)).CommandLine -like '*uvicorn*kazma*' } |
  ForEach-Object { Stop-Process -Id $_.Id -Force }

cd 'G:\GitHubRepos\kazma'
& '.venv\Scripts\python.exe' -m uvicorn kazma_ui.app:create_app --factory --host 127.0.0.1 --port 9090 --ws-ping-interval 20 --ws-ping-timeout 20
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
