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

All project references use the current `kazma` namespace (`kazma`, `Kazma`, `.kazma/`,
`KAZMA_`). No legacy namespace artifacts remain.

## Entry Points

| Entry Point | Command | Status |
|---|---|---|
| Web UI | `kazma-web` / `kazma serve` | Active |
| Terminal UI | `kazma-tui` | Active (English-only) |
| CLI | `kazma` | Active |
| Gateway | `kazma gateway start` | Active |
| Swarm | `kazma swarm ...` | Active |

## Recent Milestones

- Namespace migration to kazma completed
- Unified ModelRegistry singleton
- Textual TUI with metrics dashboard and chat
- Swarm task persistence and Results Dashboard fixes
- Telegram adapter reliability fixes
- CheckpointManager async writes
- Retry/auth error mapping improvements

## Notes

- The TUI is intentionally English-only and does not support Arabic/RTL.
- TelegramWorker shells out to `kazma -p <profile>` (legacy CLI namespace purged).
