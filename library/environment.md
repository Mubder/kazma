# Environment

Environment variables, external dependencies, and setup notes.

**What belongs here:** Required env vars, external API keys/services, dependency quirks, platform-specific notes.
**What does NOT belong here:** Service ports/commands (use `services.yaml`).

---

## Dependencies

- `textual` — TUI framework (install via pip)
- `kazma-core` — HardwareMonitor, MetricsCollector, TraceStore, ModelRegistry
- `psutil` — CPU/RAM metrics (via HardwareMonitor)

## Platform Notes

- Windows PowerShell 5.1 (legacy) — does NOT support && or ||; use ; instead
- Use `python -m pytest` instead of `pytest` directly
