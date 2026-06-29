# Contributing to Kazma

Thank you for your interest in contributing to Kazma! This guide covers everything you need to get started.

## Table of Contents

- [Quick Start](#quick-start)
- [Development Setup](#development-setup)
- [Project Structure](#project-structure)
- [Code Style](#code-style)
- [Testing](#testing)
- [Skill Development](#skill-development)
- [Security Policy](#security-policy)
- [Pull Request Process](#pull-request-process)

---

## Quick Start

```bash
# 1. Fork the repository on GitHub, then clone your fork
git clone https://github.com/<your-username>/kazma.git
cd kazma

# 2. Create a feature branch
git checkout -b feature/my-new-feature

# 3. Install dependencies (Python 3.11+ required)
uv sync --all-extras

# 4. Run the test suite to confirm everything works
uv run pytest tests/ -v

# 5. Make your changes, commit, push, and open a PR
```

## Development Setup

### Prerequisites

| Tool    | Version | Install                                      |
|---------|---------|----------------------------------------------|
| Python  | 3.11+   | [python.org](https://python.org)             |
| uv      | 0.11+   | [astral.sh/uv](https://docs.astral.sh/uv/)  |
| Git     | 2.30+   | System package manager                       |

### Installing Dependencies

Kazma uses **uv** as the package manager (not pip or Poetry).

```bash
# Core dependencies
uv sync

# Including dev tools (pytest, ruff, mypy) and CLI extras
uv sync --all-extras

# Upgrade all dependencies
uv lock --upgrade && uv sync --all-extras
```

### Pre-commit Checks

Run these before every commit:

```bash
# Lint and fix
uv run ruff check .
uv run ruff format .

# Type check
uv run mypy kazma-core/kazma_memory/

# Tests with coverage
uv run pytest tests/ -v --cov=kazma_core --cov-report=term-missing
```

### Environment Variables

Copy `kazma.yaml` to `kazma.local.yaml` for local overrides (git-ignored). Environment variables take precedence over YAML config.

```bash
# Optional: set your preferred model
export KAZMA_MODEL="gpt-4o"

# Optional: enable connectors
export TELEGRAM_BOT_TOKEN="your-token"
export DISCORD_BOT_TOKEN="your-token"
```

## Project Structure

```
kazma/
├── kazma-core/            Core agent loop, tools, policy engine
│   └── kazma_core/
│       ├── agent.py       ReAct loop via LangGraph
│       ├── delegation/    Multi-agent orchestration
│       ├── hub/           Skill manifest, registry, validator
│       ├── security/      Linter, certification, audit trail
│       └── ...
├── kazma-memory/          sqlite-vec schemas, retrieval
├── kazma-skills/          YAML manifests wrapping MCP tools
├── kazma-connectors/      Telegram, Discord, Slack adapters
├── kazma-providers/       LiteLLM router, model switching
├── kazma-ui/              FastAPI + HTMX dashboard (Arabic RTL)
├── kazma-cli/             CLI entry points
├── examples/              Example custom skills (ALMuhalab)
├── tests/                 pytest + integration tests
├── docs/                  Documentation
├── kazma.yaml             Main configuration
├── kazma-permissions.yaml Division permission boundaries
└── pyproject.toml         Project metadata and tool config
```

### Key Design Decisions

- **Storage**: sqlite-vec ONLY — single-file persistence, no ChromaDB or PostgreSQL
- **Entry point**: `kazma-core/kazma_core/agent.py` — ReAct loop via LangGraph state machine
- **Config**: YAML-based (`kazma.yaml`) at project root
- **Observability**: OpenTelemetry + Langfuse tracing
- **Interface**: Arabic RTL dashboard via FastAPI + HTMX
- **Package manager**: uv (not pip/poetry)

## Code Style

### Formatting and Linting

Kazma uses **Ruff** for both linting and formatting:

```bash
# Check for issues
uv run ruff check .

# Auto-fix
uv run ruff check --fix .

# Format
uv run ruff format .
```

**Ruff rules enabled**: `E` (pycodestyle), `F` (pyflakes), `I` (isort), `N` (pep8-naming), `W` (warnings), `UP` (pyupgrade).

### Configuration

From `pyproject.toml`:

```toml
[tool.ruff]
target-version = "py311"
line-length = 100
```

### Type Hints

Type hints are **required** for all public APIs:

```python
def get_arabic_prompt(self, tool_name: str) -> str:
    """Get the Arabic prompt chain for a tool."""
    ...
```

Run type checking with:

```bash
uv run mypy kazma-core/kazma_memory/
```

### Docstrings

All public APIs must have docstrings. We use Google-style docstrings:

```python
def validate(self, skill_path: Path) -> ValidationResult:
    """Run all checks on a skill directory.

    Aggregates results from manifest, entry point, permissions,
    MCP servers, and security scans into a single ValidationResult.

    Args:
        skill_path: Path to the skill directory.

    Returns:
        ValidationResult with passed/errors/warnings/score.
    """
    ...
```

### Naming Conventions

| Element         | Convention          | Example                    |
|-----------------|---------------------|----------------------------|
| Modules         | snake_case          | `manifest_schema.py`       |
| Classes         | PascalCase          | `SkillValidator`           |
| Functions       | snake_case          | `validate_entry_point`     |
| Constants       | SCREAMING_SNAKE_CASE| `_ALLOWED_PERMISSIONS`     |
| Private attrs   | _leading_underscore | `self._path`               |

## Testing

### Running Tests

```bash
# Full suite
uv run pytest tests/ -v

# Specific test file
uv run pytest tests/test_hub_manifest.py -v

# With coverage report
uv run pytest tests/ --cov=kazma_core --cov-report=term-missing

# HTML coverage report
uv run pytest tests/ --cov=kazma_core --cov-report=html
```

### Test Structure

```
tests/
├── conftest.py                 Shared fixtures
├── test_agent_discovery.py     Agent discovery tests
├── test_hub_manifest.py        Manifest schema validation
├── test_hub_validator.py       Skill validator tests
├── test_permissions.py         Permission manager tests
├── test_rbac.py                RBAC tests
├── test_delegation_*.py        Multi-agent delegation
├── test_sandbox.py             Tool sandbox tests
├── unit/
│   └── test_agent.py           Unit tests
└── integration/
    └── __init__.py             Integration tests
```

### Testing Requirements

- **Coverage target**: 80% minimum for new code
- **Framework**: pytest with pytest-asyncio for async tests
- **Async tests**: Use `@pytest.mark.asyncio` decorator
- **CI**: All tests run on every push via GitHub Actions

### Writing Tests

Follow the existing patterns:

```python
"""Tests for MyNewFeature."""

from __future__ import annotations

import pytest
from kazma_core.my_module import MyFeature


class TestMyFeature:
    async def test_basic_functionality(self, tmp_path):
        """Test that basic functionality works."""
        feature = MyFeature(tmp_path)
        result = await feature.do_something()
        assert result.success is True

    async def test_edge_case(self, tmp_path):
        """Test edge case handling."""
        feature = MyFeature(tmp_path)
        with pytest.raises(ValueError):
            await feature.do_something(bad_input=True)
```

### Test Categories

| Category     | Location          | Purpose                          |
|-------------|-------------------|----------------------------------|
| Unit        | `tests/`          | Single-function tests            |
| Integration | `tests/integration/` | Multi-module interactions     |
| Security    | `tests/`          | Permission, auth, sandbox tests  |
| CLI         | `tests/`          | CLI command parsing and behavior |

### CLI Testing

The `kazma` CLI (`kazma-cli/kazma_cli/`) is tested alongside the rest of the
suite. When adding or changing a CLI command (core, gateway, swarm, hub,
project, or docs), add a test that exercises the command path. Tests typically
invoke the CLI handler directly or via `subprocess` and assert on exit code and
output. Keep the `completions.py` `SUBCMDS` list in sync with the available
commands so shell tab-completion stays accurate.

## Skill Development

Skills are the extension mechanism for Kazma. Each skill is a directory with a `skill_manifest.yaml` and Python code.

### Creating a Skill

```
my-skill/
├── skill_manifest.yaml    # Required: skill metadata
├── main.py                # Entry point (if specified in manifest)
└── ...
```

### Minimal Manifest

```yaml
name: my-skill
version: 1.0.0
description: "A brief description of what this skill does"
author: "Your Name"
license: MIT
```

### Full Manifest Example

```yaml
name: my-skill
version: 1.0.0
description: "A fully-specified example skill"
author: "Your Name"
license: Apache-2.0

capabilities:
  - data-processing
  - api-integration

dependencies:
  core: ">=0.1.0"
  optional:
    - numpy
    - pandas

mcp_servers:
  - name: my-api-server
    type: stdio
    command: ["python", "-m", "my_api_server"]

permissions:
  required:
    - file_read
    - network_outbound
  optional:
    - database_read

entry_point: main
config_schema:
  type: object
  properties:
    api_key:
      type: string
      description: "API key for external service"

min_core_version: "0.5.0"
tags:
  - data
  - example

homepage: "https://example.com/my-skill"
repository: "https://github.com/example/my-skill"
```

For the full manifest specification, see [docs/skill-manifest-spec.md](docs/skill-manifest-spec.md).

### Validating Your Skill

Use the Kazma Hub validator to check your skill before submitting:

```python
from pathlib import Path
from kazma_core.hub.validator import SkillValidator

validator = SkillValidator()
result = await validator.validate(Path("my-skill/"))

if result.passed:
    print(f"Skill passed with score: {result.score}/100")
else:
    for error in result.errors:
        print(f"ERROR: {error}")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
```

### Security Checks

The validator automatically scans for:
- `eval()` and `exec()` calls
- `os.system()` usage
- `__import__` dynamic imports
- Hardcoded secrets (API keys, passwords, tokens)

Each issue deducts from the security score (0-100).

## Security Policy

### Reporting Vulnerabilities

If you discover a security vulnerability, please report it responsibly:

1. **Do NOT** open a public GitHub issue
2. Email security details to the maintainers
3. Include: description, steps to reproduce, potential impact
4. Allow 90 days for a fix before public disclosure

### Audit Trail

Kazma maintains an audit log of all skill installations, permission changes, and security events. The audit trail is stored in SQLite and can be queried for compliance.

### Kazma-Certified Skills

Skills that pass all validation checks receive a **Kazma-Certified** badge. The certification process:

1. Manifest validation (all required fields present)
2. Entry point verification (declared file exists)
3. Permission check (only known permissions used)
4. MCP server validation (valid types: `stdio`, `sse`, `streamable-http`)
5. Security scan (no dangerous patterns)

Certified skills are recorded in `kazma-skills/kazma_skills/certified_servers.yaml`.

### Division Isolation

Enterprise deployments use division-based sandboxing. Each division (e.g., `gas_oil`, `tourism`, `general_trading`) has its own allowed MCP servers and denied servers. See `kazma-permissions.yaml` for the configuration format.

## Pull Request Process

### Before You Start

1. Check existing issues and PRs to avoid duplicate work
2. For large changes, open an issue first to discuss the approach
3. Fork the repository and create a feature branch from `main`

### Development Workflow

```bash
# 1. Create a feature branch
git checkout -b feature/my-new-feature

# 2. Make changes with tests (TDD encouraged)
# Write tests first, then implement

# 3. Run the full check suite
uv run ruff check .
uv run ruff format --check .
uv run mypy kazma-core/kazma_memory/
uv run pytest tests/ -v --cov=kazma_core

# 4. Commit with a clear message
git commit -m "feat: add my-new-feature with tests"

# 5. Push and open a PR
git push origin feature/my-new-feature
```

### PR Requirements

- [ ] All CI checks pass (lint + tests)
- [ ] New code has tests (80%+ coverage)
- [ ] Type hints on all public APIs
- [ ] Docstrings on all public APIs
- [ ] No hardcoded secrets or credentials
- [ ] Security scan passes (no `eval`, `exec`, `os.system`)
- [ ] Manifest validates if creating/modifying a skill

### PR Title Convention

Use [Conventional Commits](https://www.conventionalcommits.org/):

| Prefix     | When to use                        |
|-----------|------------------------------------|
| `feat:`   | New feature                        |
| `fix:`    | Bug fix                            |
| `docs:`   | Documentation only                 |
| `test:`   | Adding/fixing tests                |
| `refactor:`| Code restructuring (no behavior change) |
| `chore:`  | Tooling, CI, dependencies          |
| `security:`| Security fix or hardening          |

### Review Process

1. At least one maintainer approval required
2. CI must pass (lint + test jobs)
3. Address all review feedback
4. Squash-merge into `main`

### After Merge

- Delete your feature branch
- Update local `main`: `git pull origin main`
- If you created a skill, verify it installs: `kazma skill install my-skill`

---

## Questions?

Open a [GitHub Discussion](https://github.com/nousresearch/kazma/discussions) for questions, ideas, or help. For bugs, use [GitHub Issues](https://github.com/nousresearch/kazma/issues).

Thank you for contributing to Kazma! 🚀
