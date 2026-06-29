# User Testing

Testing surface, required testing skills/tools, resource cost classification per surface.

## Validation Surface

**TUI Application** — Terminal-based UI using Textual framework.

### Testing Tools
- Manual execution: `python -m kazma_tui` or `kazma-tui` command
- Visual inspection: Screenshot/terminal capture
- Unit tests: `python -m pytest kazma-tui/tests/`

### Setup
1. Install dependencies: `pip install -e kazma-tui/ -e kazma-core/`
2. Run TUI: `python -m kazma_tui`
3. Verify header, dashboard, chat, footer

## Validation Prerequisites

- `textual` package installed
- `kazma-core` package installed
- ModelRegistry singleton available (auto-initialized on import)

## Validation Concurrency

**Max Concurrent Validators:** 1 (TUI is single-instance terminal app)

**Resource Cost:** Low — TUI uses minimal CPU/RAM, no web server required.
