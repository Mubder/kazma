# Kazma Architecture

> **Canonical documentation lives under `docs/docs/` (Docusaurus).**

This root file is a pointer so agent onboarding and `AGENTS.md` links resolve
without searching.

| Doc | Path |
|-----|------|
| Docs home | [`docs/docs/intro.md`](docs/docs/intro.md) |
| Full architecture (Guide) | [`docs/docs/guide/architecture.md`](docs/docs/guide/architecture.md) |
| Monorepo system map | [`docs/ARCHITECTURE_AND_SYSTEM_MAP.md`](docs/ARCHITECTURE_AND_SYSTEM_MAP.md) |
| Security & HITL | [`docs/docs/guide/security-and-safety.md`](docs/docs/guide/security-and-safety.md) |
| Configuration | [`docs/docs/guide/configuration.md`](docs/docs/guide/configuration.md) |
| Tools catalog | [`docs/docs/reference/tools-catalog.md`](docs/docs/reference/tools-catalog.md) |
| Swarm | [`docs/docs/guide/swarm-orchestration.md`](docs/docs/guide/swarm-orchestration.md) |
| IDE | [`docs/docs/products/ide.md`](docs/docs/products/ide.md) |
| Production checklist | [`docs/docs/ops/production-checklist.md`](docs/docs/ops/production-checklist.md) |
| Binding industrial audit | [`docs/audits/AUDIT_DEEP_2026-09-01_EXEC.md`](docs/audits/AUDIT_DEEP_2026-09-01_EXEC.md) (waves 0–8 shipped) |
| Historical production audit | [`docs/audits/AUDIT_PRODUCTION_READINESS_2026-07-21.md`](docs/audits/AUDIT_PRODUCTION_READINESS_2026-07-21.md) |
| Changelog | [`CHANGELOG.md`](CHANGELOG.md) |

## Package map (quick)

There is **no** `kazma-memory` package (retired; V2 lives in `kazma_core.memory`).

| Package | Role |
|---------|------|
| `kazma-core` | Agent brain, swarm, tools, model registry, vault, IDE service, V2 memory, documents |
| `kazma-gateway` | Telegram / Discord / Slack adapters + agent handler |
| `kazma-ui` | FastAPI web UI, SSE/WS chat, settings, IDE page, turn journal |
| `kazma-tui` | Textual dashboard / editor |
| `kazma-cli` | CLI entrypoints (`kazma serve`, `migrate`, `ask`, ACP) |
| `kazma-skills` | Skill manifests + native skills |

## Critical rules (see AGENTS.md)

1. Model + provider always switch together (`model_registry.py`).
2. Platform IDs never enter LangGraph state (`SessionStore` only).
3. Three HITL **execution** paths (graph interrupt, swarm bus, pipeline checkpoints) + one **registry** (`hitl_gates.db`). Swarm FanOut is tri-state, not first-wins.
4. IDE mutations go through `LocalToolRegistry` (shared HITL).
5. ConfigStore singleton via `get_config_store()`; sensitive keys prefer the vault when `KAZMA_VAULT_KEY` is set.
6. Turn Delivery: `close_turn` is the only closer; the client projects; no second painter (`chat.js`).
7. Soul deltas live in ConfigStore (not `SOUL.md` / `agent_evolution.json`) and must be prompt-fenced.
8. Never start/restart the server; operator reload is `kazma_guard.py --reload`.
