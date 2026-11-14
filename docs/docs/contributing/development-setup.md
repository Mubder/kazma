---
sidebar_position: 1
---

# Development Setup

> This page is published at [kazma.ai/docs/contributing/development-setup/](https://kazma.ai/docs/contributing/development-setup/) ([Arabic](https://kazma.ai/ar/docs/contributing/development-setup/)).

## Prerequisites

- Python 3.11.5+ (soupsieve 4, which closes three 2026 advisories, needs it)
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
# A lighter set, by hand (rag + tests + TUI); setup.sh / setup.ps1 install Express, Full or Custom.
# There is no [cli] extra — kazma-cli is in the wheel.
pip install -e ".[rag,dev,tui]"
# or: uv sync --inexact --extra rag --extra dev --extra tui --extra postgres
```

`uv sync` here is for a **first install** into a fresh environment. On an
installed Kazma, update with `python -m kazma_cli update` instead: a bare
`uv sync` removes every package outside the extras it names
([Kazma update](../ops/kazma-update#the-short-version)).

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

The site's build packages are kept by `npm audit fix` (never `--force`) and
`docs/package-lock.json`. `package.json` names Mermaid's major version
(`overrides`, Mermaid 12 since 2026-11-13, with the KaTeX 0.18 that fixes
Mermaid 11's KaTeX advisory): every diagram in the docs must parse with it
(`tests/test_docs_mermaid_diagrams_parse.py`, which needs `npm install` here
and Playwright's Chromium). It also holds `uuid` on 11.x for the dev server's
`sockjs`, which asks for 8.x and whose advisory every Docusaurus package
inherited, and `tinypool` on 2.x and `serialize-javascript` on
7.x (2026-11-11): Docusaurus 3.10.2 (the newest) holds both a major version
back, and their critical and high advisories are fixed only there. The site
builds with both (`npm run build`), which needs Node 20, as Docusaurus does.
Do not run `npm audit fix` without checking `npm ls @docusaurus/preset-classic`
after it: on 2026-11-11 it moved the preset back to 3.7.0, a second, older
Docusaurus beside 3.10.2. What the audit still reports is low and has no
fixed release yet (`deepmerge`, in the SVG plugin's
build-time configuration merge).
