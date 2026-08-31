---
sidebar_position: 1
---

# Development Setup

## Prerequisites

- Python 3.11+
- Node.js 18+ (for docs)
- Git

## Clone and install

```bash
git clone https://github.com/Mubder/kazma.git
cd kazma

# Create virtual environment
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
# Same extras as setup.sh / setup.ps1 (rag + tests + TUI).
# There is no [cli] extra — kazma-cli is in the wheel.
pip install -e ".[rag,dev,tui]"
# or: uv sync --extra rag --extra dev --extra tui
```

## Activate the git hooks

`pre-commit` comes with the `dev` extra, but the hooks do nothing until you
install them into your clone — do this once, per clone:

```bash
pre-commit install
```

Every commit then runs two AST-only gates (~8s, no app boot):

- **import gates** — every product module still imports, and no `kazma_*`
  import reference dangles after a file is moved or deleted.
- **static gates** — no blocking DB call inside `async def`, no
  fire-and-forget `asyncio.create_task`, every registered tool carries a HITL
  tier, and web-tool output is fenced as untrusted.

Run them against the whole tree at any time:

```bash
pre-commit run --all-files
```

Both hooks go through `scripts/run_gates.py`, which finds the project
virtualenv itself. `pre-commit` sanitises `PATH` for `system` hooks, so a
bare `python` in the hook entry would resolve to *pre-commit's* interpreter
(no pytest), and a relative `.venv/Scripts/python.exe` fails on Windows —
which is why these hooks silently never ran there before 2026-08-29. If your
virtualenv lives somewhere unusual, point the shim at it:

```bash
export KAZMA_GATE_PYTHON=/path/to/python
```

## Project structure

```
kazma/
  kazma-core/          Agent, LLM providers, swarm, V2 memory, IDE
  kazma-gateway/       Telegram / Discord / Slack
  kazma-ui/            FastAPI dashboard + Settings
  kazma-tui/           Textual dashboard
  kazma-cli/           `kazma` CLI
  kazma-skills/        Native skills
  docs/                Docusaurus site
  tests/               Cross-cutting tests
```

## Run tests

```bash
pytest
pytest --cov=kazma_core
pytest tests/test_checkpoint.py
```

## Linting

```bash
ruff check .
ruff format .
mypy kazma-core/
```

## Documentation

```bash
cd docs
npm install
npm run start    # local dev server
npm run build    # production build
```
