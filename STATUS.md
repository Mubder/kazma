# Kazma Project Status

Generated: 2026-07-10

## Metrics

| Metric | Value |
|---|---|
| Root suite collected (`tests/`) | ~3,544 |
| Package tests (core+gateway+ui) | ~118 |
| TUI tests (`kazma_tui_tests`) | ~216 |
| i18n translation keys | ~926 (en + ar) |
| Python Version | 3.11+ |
| Project Version | 0.4.0 |
| Status | active_development |

## Entry Points

| Entry Point | Command | Status |
|---|---|---|
| Web UI | `kazma-web` / `kazma serve` | Active (bilingual EN/AR) |
| Terminal UI | `kazma-tui` | Active (bilingual EN/AR) |
| CLI | `kazma` | Active |
| Gateway | `kazma gateway start` | Active |
| Swarm | `kazma swarm ...` | Active |

## Recent milestones

- **v0.4.0 (2026-07-10)** — Full Arabic i18n coverage (~926 keys, all templates + JS); GitHub OAuth integration (read-only, repo picker, activity timeline, unified workspace); deep security audit remediation (~40 fixes: HITL, concurrency, semantic-cache leak, delegation auth, autoscaler, SQLite pragmas); Calibri font; modal width fixes.
- **Sprint S0/S1** — Auth prefixes for chaos/migrate/workspaces; chaos kill-switch; HITL ownership harden; task history lock; root CI job.
- **Sprint 19 / Phase 3** — Chaos framework, config migration UI, loadtests, swarm output adapters.
- **Sprint 17** — Engine refactor, skill checksums, config reconcile.
- **Sprint 14–15** — HITL all platforms; ConfigStore atomicity; MCP auth/HITL.

## Security notes

- Set `KAZMA_SECRET` in production.
- Chaos APIs require `KAZMA_CHAOS_ENABLED=true` (default off) **and** auth prefix.
- Prefer SSE `/api/chat/stream` over WebSocket chat (410 Gone).
- GitHub OAuth token stored in ConfigStore only (never `.env`).
- Async + sync HITL paths both fail-closed on `NullBusAdapter`.
