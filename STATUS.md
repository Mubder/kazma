# Kazma Project Status

Generated: 2026-07-09

## Metrics

| Metric | Value |
|---|---|
| Total Tests Passing | 138 (core packages) |
| Total Tests Collected | 138 |
| Skipped (missing optional deps) | 0 |
| Environmental Failures | 0 |
| engine.py Lines | 1,573 (down from 1,878; 3 modules extracted) |
| Python Version | 3.11+ |
| Project Version | 0.2.0 |
| Status | active_development |

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

- **Sprint 17** — Engine refactored (1,878→1,573 lines, 3 modules extracted). Config reconciliation on startup.
- **Sprint 16** — Skill checksums (fail-closed + HMAC). Task cancel/retry from UI. Circuit breaker badges + per-worker start/stop.
- **Sprint 15** — ConfigStore atomicity (WAL, batch transactions, singleton). MCP server auth + HITL gate + tool classification.
- **Sprint 14** — HITL approval gates on ALL platforms (Web, Telegram, Discord, Slack). Fail-closed danger-tool gating. Test isolation (28→3 failures).
- **Sprint 13** — Active Tasks tab, provider/model resolution fixes, swarm output routing, security quick wins.

## Sprint B Completion (2026-07-09)

- **B1**: Fixed conftest ImportPathMismatch by renaming test dirs to package-specific names
- **B2**: Fixed FallbackChain test API mismatches (3 tests)
- **B3**: Fixed TestNoPrivateAccessInUI - route through public API
- **B4**: Enabled CI integration job (flipped if: false)
- **B5**: Refreshed README/STATUS badges

## Notes

- The TUI is intentionally English-only and does not support Arabic/RTL.
- TelegramWorker was removed (commit 94205bb); swarm dispatch uses InProcessWorker.
