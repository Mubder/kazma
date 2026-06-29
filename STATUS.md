# Kazma Project Status

Generated: 2026-06-30

## Metrics

| Metric | Value |
|---|---|
| Total Tests Passing | 3510 |
| Total Lines of Code (excluding .venv, tests/) | 52333 |
| Python Version | 3.11+ |
| Project Version | 0.1.0 |
| Status | production_ready |

## Namespace Audit

- `hermes` (case-insensitive): 0 remaining references
- `Hermes`: 0 remaining references
- `.hermes/`: 0 remaining references
- `HERMES_`: 0 remaining references

Expected: 0 for all.

## Entry Points

| Entry Point | Command | Status |
|---|---|---|
| Web UI | `kazma-web` / `kazma serve` | Active |
| Terminal UI | `kazma-tui` | Active (English-only) |
| CLI | `kazma` | Active |
| Gateway | `kazma gateway start` | Active |
| Swarm | `kazma swarm ...` | Active |

## Recent Milestones

- hermes → kazma namespace migration completed
- Unified ModelRegistry singleton
- Textual TUI with metrics dashboard and chat
- Swarm task persistence and Results Dashboard fixes
- Telegram adapter reliability fixes
- CheckpointManager async writes
- Retry/auth error mapping improvements

## Notes

- The TUI is intentionally English-only and does not support Arabic/RTL.
- TelegramWorker shells out to `kazma -p <profile>` (legacy `hermes` CLI purged).
