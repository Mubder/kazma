# Mission Guidance: TUI Replacement

## Mission Boundaries (NEVER VIOLATE)

**Package Scope:** Only modify `kazma-tui/` package. Do NOT modify `kazma-core/`, `kazma-ui/`, `kazma-cli/`, or `kazma-gateway/` unless explicitly required for imports.

**Dependencies:** Use only `textual` for TUI framework. Do NOT add new dependencies without orchestrator approval.

**ModelRegistry:** TUI is a READ-ONLY consumer. Never call `set_active_profile()`, `ConfigStore.write()`, or any mutation methods.

**Language:** All UI text must be in English. No Arabic, RTL markers, or bilingual labels.

## Mission Directives

**Tools:** Use `textual` framework for TUI development. Use `pytest` for testing.

**Skills:** Follow TDD approach. Write tests before implementation.

**Dependencies:**
- `textual` — TUI framework (install via pip)
- `kazma-core` — HardwareMonitor, MetricsCollector, TraceStore, ModelRegistry
- `psutil` — CPU/RAM metrics (via HardwareMonitor)

**Other:**
- Dashboard refresh interval: 2 seconds
- Handle missing metrics with "N/A" fallback
- Use Textual's `set_interval` for periodic updates

## Coding Conventions

- Follow existing Kazma code style (type hints, docstrings, logging)
- Use `logger = logging.getLogger(__name__)` pattern
- Use `from __future__ import annotations` for type hints
- Keep widgets modular (one class per file)

## Testing & Validation Guidance

**Unit Tests:** Test each widget in isolation with mocked data sources.

**Integration Tests:** Test full app launch with mocked ModelRegistry and metrics.

**Manual Verification:** Launch TUI and verify:
1. Header shows provider/model
2. Dashboard shows metrics
3. Chat accepts input
4. Footer shows shortcuts
5. Ctrl+Q exits cleanly
