# Kazma TUI

A professional terminal-based dashboard for the Kazma framework, built with [Textual](https://textual.textualize.io/).

## Features

- **Metrics Dashboard** - Real-time display of CPU/Memory, RPM, latency, error rate, and active agents
- **Chat Interface** - Interactive chat with command support (`/help`, `/clear`, `/quit`)
- **ModelRegistry Integration** - Displays active provider and model from the centralized registry
- **English-Only UI** - Clean, professional interface

## Installation

```bash
pip install -e kazma-tui/ -e kazma-core/
```

## Usage

```bash
# Launch the TUI
kazma-tui

# Or via Python module
python -m kazma_tui
```

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+Q` | Quit |
| `Tab` | Switch panels |
| `Enter` | Send message |

## Chat Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/clear` | Clear chat log |
| `/quit` | Exit TUI |

## Architecture

```
kazma-tui/kazma_tui/
├── app.py          # Main Textual application
├── dashboard.py    # Metrics dashboard (CPU, RAM, RPM, latency, error rate, agents)
├── chat.py         # Chat interface with input and commands
├── header.py       # Header with active provider/model from ModelRegistry
├── footer.py       # Footer with keyboard shortcuts
├── __init__.py     # Package initialization
└── __main__.py     # CLI entry point
```

## Data Sources

- **HardwareMonitor** (`kazma_core.telemetry`) - CPU/RAM/GPU stats
- **MetricsCollector** (`kazma_core.swarm.metrics`) - Per-worker metrics (tokens, cost, latency)
- **TraceStore** (`kazma_core.tracing`) - In-memory trace ring buffer (RPM, total calls)
- **ModelRegistry** (`kazma_core.model_registry`) - Active provider/model info

## Development

```bash
# Run tests
python -m pytest kazma-tui/kazma_tui_tests/ -v

# Lint
python -m ruff check kazma-tui/

# Typecheck
python -m mypy kazma-tui/kazma_tui/
```

## Tests

191 tests covering:
- Header/footer widget rendering
- Dashboard metrics display and refresh
- Chat input, messages, and commands
- ModelRegistry integration
- English-only validation
- Read-only consumer validation
