# Kazma (كزمة)

Autonomous AI agent framework — Python 3.11+, asyncio-native, sqlite-vec only.

## Architecture

```
kazma-core/          Agent loop (ReAct), tool registry, policy engine, event bus
kazma-memory/        sqlite-vec schemas, retrieval, provenance tagging
kazma-skills/        YAML manifests wrapping MCP tools
kazma-connectors/    Telegram, Discord, Slack adapters
kazma-providers/     LiteLLM router, model switching
kazma-ui/            FastAPI + HTMX dashboard (Arabic RTL)
kazma-cli/           Install, diagnostics, migrations
tests/               pytest + integration tests
```

## Key Design Decisions

- **Storage**: sqlite-vec ONLY. Single-file persistence. No ChromaDB, no PostgreSQL.
- **Entry point**: `kazma-core/kazma_core/agent.py` — ReAct loop via LangGraph state machine.
- **Config**: YAML-based (`kazma.yaml`) at project root.
- **Observability**: OpenTelemetry + Langfuse tracing.
- **Interface**: Arabic RTL dashboard via FastAPI + HTMX.

## Quick Start

```bash
# Install with uv (preferred)
uv sync

# Or with pip
pip install -e ".[dev,cli]"

# Run tests
pytest tests/

# Start dashboard
uvicorn kazma_ui.app:app --reload
```

## Development

```bash
# Lint
ruff check .
ruff format .

# Type check
mypy kazma-core/kazma_memory/

# Coverage
pytest --cov=kazma_core --cov-report=html tests/
```

## Configuration

Copy `kazma.yaml` to `kazma.local.yaml` for local overrides (git-ignored).
Environment variables take precedence over YAML config.

## License

MIT
