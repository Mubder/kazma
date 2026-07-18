# Kazma (كاظمه) — Autonomous Agent Framework

> **Version anchor:** This documentation reflects the codebase as of **July 2026** (`pyproject.toml` → **0.5.0**). Every factual claim is traceable to source; anything not yet implemented is explicitly marked **Planned / Roadmap**. Latest full audit: `docs/audits/AUDIT_FULL_2026-07-18.md`.

Kazma is a multi-platform, autonomous AI agent framework built on a **LangGraph supervisor brain**, **swarm orchestration**, **cross-platform dispatch** (Telegram / Discord / Slack / Web / TUI), and an **OpenAI-compatible LLM provider layer**. It is designed with first-class **Arabic / RTL** support and a Gulf-cultural conversational protocol (*Majlis*).

This `docs-v2/` tree is a **ground-up rewrite** produced by auditing the live codebase — not a repackaging of older notes. It replaces speculative or stale descriptions with verified, source-referenced reference material.

> **Merged into Docusaurus (July 2026):** published copies live at `docs/docs/guide/`. Use the site navbar **Guide**, or edit those files for the live docs. This `docs-v2/` folder is retained as the authoring history / offline map.

---

## Why Kazma

| Concern | What Kazma provides |
|---|---|
| **One brain, many channels** | A single LangGraph supervisor graph is reused across Telegram, Discord, Slack, a FastAPI + SSE Web UI, and a Textual TUI. |
| **Provider freedom** | Any OpenAI-compatible endpoint (OpenAI, Anthropic, DeepSeek, Google Gemini, xAI, OpenRouter, Ollama, LM Studio, NVIDIA NIM) via plain `httpx` — no SDK lock-in. |
| **Safe autonomy** | Three independent Human-in-the-Loop (HITL) gates ensure dangerous tools never execute without explicit approval. Fail-closed by default. |
| **Swarm orchestration** | Six dispatch patterns (dispatch, broadcast, pipeline, fan-out, consult, conditional) with circuit breakers, retries, timeouts, output validation, and bounded concurrency. |
| **Arabic-native** | Custom Arabic tokenizer, Kuwaiti-dialect stop words, RTL-aware UI, 16px readability floor, and the Majlis cultural protocol. |
| **Durable execution** | LangGraph checkpointing with SQLite WAL, time-travel / replay, and crash-recoverable HITL pauses. |

---

## Documentation Map

| Document | What it covers |
|---|---|
| [Quickstart](docs/quickstart.md) | Install, configure one provider, send your first message in <10 minutes. |
| [Architecture](docs/architecture.md) | Engine internals, the supervisor ReAct loop, data-flow diagrams (Mermaid). |
| [Configuration](docs/configuration.md) | **The exhaustive reference** — every key in `kazma.yaml`, every env var, override precedence, ConfigStore. |
| [CLI Reference](docs/cli-reference.md) | Complete `kazma`, `kazma-tui`, `kazma-web` command tree with flags and examples. |
| [Gateways & Platforms](docs/gateways-and-platforms.md) | Telegram/Discord/Slack/Web/TUI adapters, platform isolation, slash commands, parity matrix. |
| [Memory & RAG](docs/memory-and-rag.md) | ChromaDB, FTS5, NetworkX, sqlite-vec, the Arabic tokenizer, compaction — and honest status notes. |
| [Skills, MCP & Tools](docs/skills-mcp-and-tools.md) | Skill manifests, HMAC signing, MCP transports, tool classification, the Hub. |
| [Swarm Orchestration](docs/swarm-orchestration.md) | Patterns, aggregation, reliability layers, worker registry, checkpoints. |
| [Security & Safety](docs/security-and-safety.md) | The three HITL tiers, danger-tool lists, fail-closed behavior, delegation crypto. |
| [Arabic & Cultural Features](docs/arabic-cultural-features.md) | i18n system, RTL handling, font policy, Majlis protocol, dialect support. |
| [Deployment](docs/deployment.md) | Docker Compose, Kubernetes, Windows `setup.ps1`, portable paths, server management. |
| [API & Extension Points](docs/api-and-extension-points.md) | REST/SSE endpoints, SSE event contract, how to add tools/providers/adapters. |
| [Troubleshooting & Workarounds](docs/troubleshooting-and-workarounds.md) | Provider/LLM, memory, HITL, SQLite, Windows/Docker, Arabic tokenization, registry, gateway/Telegram, providers-hub UI, TUI, CLI, swarm panel — plus a diagnostics checklist. |
| [Development](docs/development.md) | Repo layout, test/lint/typecheck commands, contributing conventions. |
| [Roadmap & Future](docs/roadmap-and-future.md) | What exists vs. what is planned, with honest trade-offs. |
| [FAQ](docs/faq.md) | Common questions and gotchas. |
| [Glossary](docs/glossary.md) | Terms used throughout Kazma. |

---

## Quick Links

- **Install:** `pip install -e .` (core) or `pip install -e ".[rag]"` (with ChromaDB + sentence-transformers).
- **Run the Web UI:** `kazma serve` → <http://127.0.0.1:8000>
- **Run the TUI:** `kazma-tui`
- **Minimal config:** set `OPENAI_API_KEY` (or any provider key) in `.env`, edit `kazma.yaml`.

---

## Auditor's Note on Documentation Honesty

This rewrite intentionally distinguishes **what the code does today** from **what older README/marketing copy claims**. During the audit we found several places where described features are only partially wired, mis-attributed, or not yet implemented. Those are flagged in the relevant documents (especially [Memory & RAG](docs/memory-and-rag.md) and [Troubleshooting](docs/troubleshooting-and-workarounds.md)) under **"Honest status notes."** This is a feature, not a defect, of reference documentation.

---

## Provenance & consolidation

This `docs-v2/` is the **single consolidated master** for Kazma. It was assembled by merging the three pre-existing doc sets into one code-verified whole:

- **Base:** the prior `docs-v2/` rewrite (auditor edition) — the strongest, most source-referenced set.
- **From the duplicate `docs-v2/` copy:** 6 glossary terms (`AutoScaler`, `BlackboardStore`, `Code-switch token`, `CommandConsole`, `PermissionLevel`, `WorkerCapabilities`); the hardening-runner / dependency-scanner / disclosure-workflow tables in [Security & Safety §8](docs/security-and-safety.md); 5 `swarm.*` config keys; the `--port` precedence table + exit codes in [CLI Reference](docs/cli-reference.md).
- **From the original Docusaurus docs:** the deep operational troubleshooting content (gateway/Telegram, providers-hub UI, TUI, CLI, swarm panel — ~25 issues) folded into [Troubleshooting §1.6–§1.9, §9–§14](docs/troubleshooting-and-workarounds.md); and the consumer `search`/`browse`/`install` workflow in [Skills §4.3](docs/skills-mcp-and-tools.md#43-finding--installing-skills-consumer-workflow).

**Corrections applied during merge:** `ConfigStore()` → `get_config_store()` everywhere (avoids SQLite lock contention); the two distinct HITL endpoints are now disambiguated; stale `TelegramWorker`/`hermes` narrative dropped (the class no longer exists). **Excluded as fabricated/unverified:** the remaining `kazma-hub/*` and `skill-development/*` pages (non-existent `hub publish`, fake `--level`/`--security-audit` flags, invented `MySkill.execute(context)` contract, placeholder dates) and `architecture/PROVIDERS.md` (superseded by the current architecture/configuration pages). Note: `kazma wizard` *is* a real command (`main.py:117`) and is preserved in the consumer workflow.

See `AUDIT_SUMMARY.md` for the full change record.
