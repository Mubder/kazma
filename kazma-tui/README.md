# Kazma TUI

A professional terminal-based **ops console** for the Kazma framework, built with [Textual](https://textual.textualize.io/).

**Shell v3 (2026-07):** design tokens, left **nav rail** (expand/collapse), Memory tab, equal metric cards, denser Chat/Files/Swarm panels.

| Phase | Shipped |
|-------|---------|
| v2 shell | Theme tokens, brand header, status bar, dense UI |
| Phase 2 | Dashboard memory health strip; Swarm/Files/Traces polish; shared `KAZMA_SHELL_CSS` |
| Phase 3 | Nav rail (1–7), Memory tab, Swarm sparklines, consolidator settings toggles |
| Phase 4 polish | Collapsible nav (`[`), graph search/clear, panel density, README refresh |

## Features

- **Metrics Dashboard** — CPU/RAM/VRAM, RPM, latency, errors, agents, memory health
- **Memory tab** — L1–L4 health, graph stats, **search**, **clear** (confirm)
- **Chat** — slash commands, autocomplete, streaming
- **Files** — directory tree, preview, IDE editor
- **Traces / Swarm** — call traces, workers, live sparklines
- **Settings** — themes, memory pipeline toggles
- **Nav rail** — full labels or key-only (narrow / `[` toggle)

## Installation

```bash
pip install -e kazma-tui/ -e kazma-core/
```

## Usage

```bash
kazma-tui
# or
python -m kazma_tui
```

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `1`–`7` | Dashboard · Memory · Chat · Files · Traces · Swarm · Settings |
| `m` | Memory tab |
| `[` | Collapse / expand left nav |
| `Ctrl+N` / `Ctrl+B` | Next / previous tab |
| `Ctrl+P` | Command palette |
| `Ctrl+F` | Focus chat input |
| `Ctrl+Q` | Quit |
| `?` | Help |
| `e` | Open selected file in editor (Files tab) |

## Chat Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/clear` | Clear chat log |
| `/model` | Show / switch model |
| `/memory` | Memory store stats |
| `/swarm` | Swarm dispatch / status |
| `/quit` | Exit TUI |

## Architecture

```
kazma-tui/kazma_tui/
├── app.py              # Main app (shell, bindings, HITL poll)
├── theme.py            # Design tokens + shared shell CSS
├── themes/
│   └── theme_manager.py
├── nav_rail.py         # Left rail (collapsible)
├── header.py           # Brand · provider/model
├── dashboard.py        # MetricCard grid + memory strip
├── memory_panel.py     # MemoryHealthPanel + MemoryTab (graph search/clear)
├── chat.py             # Streaming chat + slash autocomplete
├── files.py            # DirectoryTree + preview
├── editor.py           # Full-screen editor screen
├── traces.py           # Trace table
├── swarm.py            # Workers, tasks, sparklines
├── settings_panel.py   # Theme + memory toggles
├── footer.py
└── widgets/            # status_bar, toast, confirm_dialog, sparklines, …
```

## Data Sources

| Source | Package | Used for |
|--------|---------|----------|
| HardwareMonitor | `kazma_core.telemetry` | CPU/RAM/GPU |
| MetricsCollector | `kazma_core.swarm.metrics` | Latency, tokens, cost |
| TraceStore | `kazma_core.tracing` | RPM, call history |
| ModelRegistry | `kazma_core.model_registry` | Active provider/model |
| Knowledge graph | `kazma_core.swarm.memory.graph` | L2 search / clear / stats |
| Memory health | `kazma_core.memory.health` | Stack status chips |

## Development

```bash
python -m pytest kazma-tui/kazma_tui_tests/ -v
python -m ruff check kazma-tui/
python -m mypy kazma-tui/kazma_tui/
```

## Tests

Dashboard, chat, header, and panel unit tests under `kazma_tui_tests/`.
English-only UI validation included.
