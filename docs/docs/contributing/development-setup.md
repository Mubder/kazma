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
git clone https://github.com/kazma-ai/kazma.git
cd kazma

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev,cli]"
```

## Project structure

```
kazma/
  kazma-core/          Core framework
  kazma-skills/        Built-in skills
  kazma-connectors/    External connectors
  kazma-providers/     LLM providers
  kazma-ui/            Dashboard
  kazma-cli/           CLI entry point
  kazma-memory/        Memory subsystem
  docs/                Documentation site
  tests/               Test suite
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
