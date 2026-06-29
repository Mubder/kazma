# TUI Replacement Report

## 1. Old TUI Analysis

### Files
- `kazma-tui/kazma_tui/tui.py` (244 lines) -- the main TUI implementation
- `kazma-tui/kazma_tui/__init__.py` -- empty module docstring
- `kazma-tui/kazma_tui/__main__.py` -- entry point for `python -m kazma_tui`

### What It Already Is
The old TUI is **already Textual-based** (not curses). It is a single-file Arabic-focused chat interface. The description of it being "244 lines using curses" is inaccurate -- it imports from `textual.app`, `textual.widgets`, and `textual.containers`.

### Imports from kazma-core
- `kazma_core.agent.AgentConfig` -- agent configuration dataclass
- `kazma_core.agent.KazmaAgent` -- the main agent class
- `kazma_core.agent.load_config` -- loads config from YAML
- `kazma_core.config_store.ConfigStore` (lazy import in `_resolve_runtime_model_name`)
- `kazma_core.model_registry.UnifiedModelRegistry` (lazy import in `_resolve_runtime_model_name`)

### External Dependencies
- `textual>=8.0.0` -- TUI framework (already used)
- `rich` -- Rich text rendering (via Textual)
- `python-bidi>=0.4.0` -- Arabic/RTL text reordering (optional dependency)

### Entry Points
- **CLI script**: `kazma-tui = "kazma_tui.tui:main"` (defined in root `pyproject.toml`)
- **Module**: `python -m kazma_tui` calls `main()` from `tui.py`
- **`main()` function**: Creates `AgentConfig`, instantiates `KazmaTUI`, calls `app.run()`

### Current Layout
- Status bar (top): Shows version, model name, tool count
- Chat area (middle): `RichLog` widget for conversation
- Input row (bottom): `ArabicInput` widget + prompt label (RTL layout)
- No panels for metrics, traces, or hardware stats

---

## 2. Textual Framework Summary

### Core Concepts
- **App class**: Subclass `App`, implement `compose()` to yield widgets
- **CSS styling**: Use `.tcss` files or `CSS` class variable for layout/styling
- **Reactive attributes**: `reactive()` descriptors that auto-refresh widgets on change
- **Watch methods**: `watch_<attr>()` called when reactive attributes change
- **Compute methods**: `compute_<attr>()` for derived values (cached, auto-updated)
- **Data binding**: `data_bind()` connects parent/child reactives automatically

### Key Widgets Available
| Widget | Purpose |
|--------|---------|
| `Header` | App title bar |
| `Footer` | Key bindings display |
| `Static` | Simple text/content display with `update()` method |
| `RichLog` | Scrollable rich text log (already used) |
| `DataTable` | Tabular data with sorting, cursor, zebra stripes |
| `Input` | Text input field |
| `Label` | Simple text label |
| `Button` | Clickable button |
| `ProgressBar` | Progress indicator |
| `Tree` | Hierarchical tree view |
| `Markdown` | Markdown rendering |
| `Switch` | Toggle switch |
| `Checkbox` | Checkbox widget |
| `Select` | Dropdown selection |
| `Collapsible` | Expandable/collapsible sections |

### Layout Containers
| Container | Purpose |
|-----------|---------|
| `Horizontal` | Side-by-side layout |
| `Vertical` | Stacked layout |
| `VerticalScroll` | Scrollable vertical |
| `Grid` | Grid-based layout |
| `Container` | Generic container |

### Real-Time Updates
1. **`set_interval(seconds, callback)`**: Call a method periodically (best for polling metrics)
2. **Reactive attributes**: Set `self.some_attr = new_value` and the widget auto-refreshes
3. **`@work` decorator**: Run async/threaded workers without blocking UI
4. **`run_worker(coroutine, exclusive=True)`**: Background tasks that don't block the event loop

### Multi-Panel Dashboard Pattern
```python
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Static, DataTable

class Dashboard(App):
    CSS = """
    Screen { layout: grid; grid-size: 2; }
    #left-panel { height: 1fr; }
    #right-panel { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="left-panel"):
                yield DataTable(id="metrics-table")
                yield Static(id="hardware-stats")
            with Vertical(id="right-panel"):
                yield Static(id="trace-log")
                yield Static(id="chat-area")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(2.0, self.refresh_metrics)

    def refresh_metrics(self) -> None:
        # Update widgets with new data
        pass
```

---

## 3. Available Metrics Infrastructure

### 3a. HardwareMonitor (`kazma_core.telemetry`)

**Class**: `HardwareMonitor`

**Key methods**:
- `async get_stats() -> TelemetrySnapshot` -- single reading
- `async stream(interval=1.0) -> AsyncGenerator[TelemetrySnapshot]` -- continuous stream

**TelemetrySnapshot fields**:
| Field | Type | Description |
|-------|------|-------------|
| `cpu` | `float` | CPU utilization % (0-100) |
| `ram_used_gb` | `float` | RAM used in GB |
| `ram_total_gb` | `float` | Total RAM in GB |
| `gpu` | `float` | GPU utilization % (0-100, 0 if no NVIDIA) |
| `vram_used_gb` | `float` | VRAM used in GB |
| `vram_total_gb` | `float` | Total VRAM in GB |
| `timestamp` | `float` | Unix timestamp |
| `error` | `str` | Non-empty if any subsystem failed |

**Usage**:
```python
monitor = HardwareMonitor()
stats = await monitor.get_stats()
# Use stats.cpu, stats.ram_used_gb, stats.gpu, etc.
```

### 3b. MetricsCollector (`kazma_core.swarm.metrics`)

**Class**: `MetricsCollector`

**Key methods**:
- `record(worker, tokens, cost, duration, success)` -- record a worker dispatch
- `get_worker_metrics(worker, date) -> WorkerMetricSnapshot | None` -- single worker/day
- `get_worker_aggregate(worker) -> dict` -- aggregated across all dates
- `get_all_metrics() -> list[dict]` -- all workers aggregated
- `get_task_totals(worker_results) -> dict` -- aggregate from WorkerResult list

**WorkerMetricSnapshot fields**:
| Field | Type | Description |
|-------|------|-------------|
| `worker` | `str` | Worker name |
| `date` | `str` | YYYY-MM-DD |
| `tasks_completed` | `int` | Completed count |
| `tasks_failed` | `int` | Failed count |
| `avg_latency` | `float` | Average latency |
| `total_tokens` | `int` | Total tokens used |
| `total_cost` | `float` | Total cost in USD |

**Note**: Requires a `TaskStore` instance to be passed at construction. Without it, uses in-memory accumulators only.

### 3c. TraceStore (`kazma_core.tracing`)

**Class**: `TraceStore` (global singleton via `get_trace_store()`)

**Key methods**:
- `add(entry: TraceEntry)` -- add a trace entry
- `recent(limit=50) -> list[TraceEntry]` -- get recent traces
- `stats() -> dict` -- aggregate statistics
- `register_ws(websocket)` -- WebSocket broadcasting (for web dashboard)

**TraceEntry fields**:
| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | `float` | Unix timestamp |
| `trace_type` | `str` | "llm", "tool", "state", "compaction" |
| `label` | `str` | Display label |
| `status` | `str` | "success", "error", "warning" |
| `duration_ms` | `float` | Duration in ms |
| `tokens` | `int` | Token count |
| `cost` | `float` | Dollar cost |
| `details` | `str` | Additional info |

**stats() returns**:
```python
{
    "total_cost": float,
    "total_tokens": int,
    "total_llm_calls": int,
    "total_tool_calls": int,
    "total_traces": int,
    "uptime_seconds": float,
}
```

---

## 4. ModelRegistry API

**Module**: `kazma_core.model_registry`

**Singleton lifecycle**:
```python
from kazma_core.model_registry import initialize_model_registry, get_model_registry

registry = initialize_model_registry(config_store)  # create + deserialize
registry = get_model_registry()                       # retrieve existing
```

**Key methods for active provider/model info**:

| Method | Returns | Description |
|--------|---------|-------------|
| `get_active_profile()` | `dict[str, str]` | Keys: `provider`, `base_url`, `model`, `api_key` (masked) |
| `set_active_provider(provider, base_url, model, api_key)` | `dict` | Switch provider, returns normalized profile |
| `set_active_model(model)` | `None` | Change model within active provider |
| `get_client(model=None)` | `LLMProvider` | Pre-configured LLM client (cached) |
| `list_providers()` | `list[dict]` | All configured providers |
| `list_unified_options()` | `dict` | Models, providers, profiles, defaults |
| `discover_models(provider_name)` | `list[str]` | Hit /models endpoint for model IDs |

**Backward-compatible alias**: `UnifiedModelRegistry = ModelRegistry`

**Note**: The existing TUI's `_resolve_runtime_model_name()` already uses `UnifiedModelRegistry` and `ConfigStore` to resolve the active model. The new TUI should use `get_model_registry()` instead.

---

## 5. Entry Points

### Current Entry Points
1. **CLI script**: `kazma-tui` command -> `kazma_tui.tui:main`
2. **Module**: `python -m kazma_tui` -> `__main__.py` -> `main()`

### Root pyproject.toml Configuration
```toml
[project.scripts]
kazma-tui = "kazma_tui.tui:main"

[project.optional-dependencies]
tui = ["textual>=8.0.0", "python-bidi>=0.4.0"]

[tool.hatch.build.targets.wheel]
packages = [..., "kazma-tui/kazma_tui", ...]
```

### What Needs to Change
- The `main()` function in `tui.py` should be updated to instantiate the new dashboard app instead of the simple chat TUI
- The entry point `kazma_tui.tui:main` remains valid -- just swap the App class
- The `kazma-tui/kazma_tui/__main__.py` continues to work unchanged
- The optional `tui` dependencies in `pyproject.toml` are already correct (`textual>=8.0.0`, `python-bidi>=0.4.0`)

---

## Summary of Key Findings

1. **The old TUI is already Textual-based**, not curses. It is a simple 244-line chat interface with Arabic RTL support.
2. **Textual provides everything needed** for a multi-panel dashboard: grid layouts, DataTable, reactive attributes, periodic timers (`set_interval`), and async workers (`@work`).
3. **Three metrics sources exist**: `HardwareMonitor` (CPU/RAM/GPU), `MetricsCollector` (per-worker swarm metrics), and `TraceStore` (trace ring buffer with stats).
4. **ModelRegistry singleton** provides `get_active_profile()` for current provider/model info.
5. **Entry points need minimal changes** -- swap the App class in `main()`, keep the same module path.
