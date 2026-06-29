# Architecture: TUI Replacement Mission

## Overview

Replace the old Arabic-focused Textual-based TUI with a new professional English-only Textual-based TUI dashboard. The new TUI provides a metrics dashboard, chat interface, and ModelRegistry integration.

## System Components

### 1. New TUI Application (`kazma-tui/kazma_tui/`)

**Entry Point**: `app.py` — Main Textual application class

**Components**:
- `dashboard.py` — Metrics dashboard widgets (CPU, RAM, RPM, latency, error rate, active agents)
- `chat.py` — Chat interface with input and message display
- `header.py` — Header bar with provider/model info from ModelRegistry
- `footer.py` — Footer bar with keyboard shortcuts
- `__init__.py` — Package initialization
- `__main__.py` — CLI entry point

### 2. Metrics Infrastructure (Existing)

**HardwareMonitor** (`kazma-core/kazma_core/telemetry.py`):
- `get_stats()` → `TelemetrySnapshot` with CPU, RAM, GPU metrics
- Async, non-blocking, psutil-based

**MetricsCollector** (`kazma-core/kazma_core/swarm/metrics.py`):
- `get_all_metrics()` → list of per-worker aggregates
- `get_worker_aggregate(worker)` → dict with tasks_completed, tasks_failed, avg_latency, total_tokens, total_cost
- Thread-safe, backed by TaskStore

**TraceStore** (`kazma-core/kazma_core/tracing.py`):
- `stats()` → dict with total_cost, total_tokens, total_llm_calls, total_tool_calls, total_traces, uptime_seconds
- `recent(limit)` → list of TraceEntry objects
- In-memory ring buffer, WebSocket broadcasting

### 3. ModelRegistry (Existing)

**Singleton** (`kazma-core/kazma_core/model_registry.py`):
- `get_model_registry()` → ModelRegistry instance
- `get_active_profile()` → dict with provider, model, base_url, api_key
- `get_client(model)` → LLM client instance

### 4. SwarmEngine (Existing)

**Engine** (`kazma-core/kazma_core/swarm/engine.py`):
- `_workers` dict with active worker instances
- `_task_store` for persistent metrics

## Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    TUI Application                          │
│  ┌─────────┐  ┌─────────────┐  ┌─────────┐  ┌──────────┐  │
│  │ Header  │  │  Dashboard  │  │  Chat   │  │  Footer  │  │
│  │(provider│  │(CPU,RAM,RPM,│  │(input,  │  │(shortcuts│  │
│  │ ,model) │  │ latency,    │  │ messages│  │ )        │  │
│  │         │  │ errors,     │  │ )       │  │          │  │
│  │         │  │ agents)     │  │         │  │          │  │
│  └────┬────┘  └──────┬──────┘  └────┬────┘  └──────────┘  │
│       │              │              │                       │
└───────┼──────────────┼──────────────┼───────────────────────┘
        │              │              │
        ▼              ▼              ▼
┌───────────────┐ ┌─────────────┐ ┌─────────────┐
│ ModelRegistry │ │ Metrics     │ │ Chat        │
│ (singleton)   │ │ Infrastructure│ │ Handler    │
│               │ │             │ │             │
│ get_active_   │ │ Hardware    │ │ Command     │
│ profile()     │ │ Monitor     │ │ Parser      │
│               │ │ Metrics     │ │             │
│               │ │ Collector   │ │             │
│               │ │ TraceStore  │ │             │
└───────────────┘ └─────────────┘ └─────────────┘
```

## Key Design Decisions

1. **Textual Framework**: Professional TUI framework with CSS styling, reactive attributes, timers, and widget system
2. **Read-Only Consumer**: TUI only reads from ModelRegistry, no model-switching logic
3. **Periodic Refresh**: Dashboard updates every 2 seconds using Textual's `set_interval`
4. **Graceful Degradation**: Handle missing/unavailable metrics with "N/A" fallback
5. **English-Only**: All UI text in English, no Arabic/RTL support

## File Structure

```
kazma-tui/
├── kazma_tui/
│   ├── __init__.py
│   ├── __main__.py
│   ├── app.py          # Main Textual app
│   ├── dashboard.py    # Metrics widgets
│   ├── chat.py         # Chat interface
│   ├── header.py       # Header with provider/model
│   └── footer.py       # Footer with shortcuts
├── pyproject.toml
└── tests/
    └── test_tui.py
```

## Dependencies

- `textual` — TUI framework
- `kazma-core` — HardwareMonitor, MetricsCollector, TraceStore, ModelRegistry
- `psutil` — CPU/RAM metrics (via HardwareMonitor)
