# Kazma Project Status

Generated: 2026-07-09

## Metrics

| Metric | Value |
|---|---|
| Root suite collected (`tests/`) | ~3,544 |
| Package tests (core+gateway+ui) | ~108 |
| TUI tests (`kazma_tui_tests`) | ~216 |
| Python Version | 3.11+ |
| Project Version | 0.3.0 |
| Status | active_development |

## Entry Points

| Entry Point | Command | Status |
|---|---|---|
| Web UI | `kazma-web` / `kazma serve` | Active |
| Terminal UI | `kazma-tui` | Active (English-only) |
| CLI | `kazma` | Active |
| Gateway | `kazma gateway start` | Active |
| Swarm | `kazma swarm ...` | Active |

## Recent milestones

- **Sprint S0/S1 (2026-07-09)** — Auth prefixes for chaos/migrate/workspaces; chaos kill-switch; HITL ownership harden; disclosure key; Gemini close; task history lock; WS dead code removed; root CI job.
- **Sprint 19 / Phase 3** — Chaos framework, config migration UI, loadtests, swarm output adapters, OTel.
- **Sprint 17** — Engine refactor, skill checksums, config reconcile.
- **Sprint 14–15** — HITL all platforms; ConfigStore atomicity; MCP auth/HITL.

## Security notes

- Set `KAZMA_SECRET` in production.
- Chaos APIs require `KAZMA_CHAOS_ENABLED=true` (default off) **and** auth prefix.
- Prefer SSE `/api/chat/stream` over WebSocket chat (410 Gone).
