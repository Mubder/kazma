# Kazma Architecture

> **Canonical documentation lives in docs-v2.**

This root file is a pointer so agent onboarding and `AGENTS.md` links resolve
without searching.

| Doc | Path |
|-----|------|
| Full architecture | [`docs/docs/guide/architecture.md`](docs/docs/guide/architecture.md) (Docusaurus **Guide**) |
| Security & HITL | [`docs/docs/guide/security-and-safety.md`](docs/docs/guide/security-and-safety.md) |
| Configuration | [`docs/docs/guide/configuration.md`](docs/docs/guide/configuration.md) |
| Swarm | [`docs/docs/guide/swarm-orchestration.md`](docs/docs/guide/swarm-orchestration.md) |
| Offline map (history) | [`docs-v2/README.md`](docs-v2/README.md) |
| Latest audit | [`docs/audits/AUDIT_FULL_2026-07-18.md`](docs/audits/AUDIT_FULL_2026-07-18.md) |
| Changelog | [`CHANGELOG.md`](CHANGELOG.md) |

## Package map (quick)

| Package | Role |
|---------|------|
| `kazma-core` | Agent brain, swarm, tools, model registry, vault, IDE service |
| `kazma-gateway` | Telegram / Discord / Slack adapters + agent handler |
| `kazma-ui` | FastAPI web UI, SSE chat, settings, IDE page |
| `kazma-tui` | Textual dashboard / editor |
| `kazma-cli` | CLI entrypoints |
| `kazma-skills` | Skill manifests + native skills |
| `kazma-memory` | Arabic tokenizer / search helpers |

## Critical rules (see AGENTS.md)

1. Model + provider always switch together (`model_registry.py`).
2. Platform IDs never enter LangGraph state (`SessionStore` only).
3. Three HITL gates: graph interrupt, swarm bus, pipeline checkpoints.
4. IDE mutations go through `LocalToolRegistry` (shared HITL).
5. ConfigStore singleton via `get_config_store()`; sensitive keys prefer the vault when `KAZMA_VAULT_KEY` is set.
