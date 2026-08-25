---
id: intro
slug: /
title: Kazma Documentation
sidebar_label: Docs home
description: Map of all Kazma documentation — start here
---

# Kazma documentation

**Single source of truth** for the Kazma agent framework (v0.10+).  
Everything user-facing lives under this Docusaurus site (`docs/docs/`). Historical audits live in [`docs/audits/archive/`](https://github.com/Mubder/kazma/tree/main/docs/audits/archive).

## Start here

| I want to… | Go to |
|------------|--------|
| Install and send a first message | [Quickstart](guide/quickstart) |
| Run the agent from the terminal (no web server) | `kazma ask "…"` · `kazma acp` (ACP stdio) — [Quickstart](guide/quickstart) |
| Understand the engine | [Architecture](guide/architecture) |
| Configure providers / YAML / env | [Configuration](guide/configuration) · [LLM providers](reference/llm-providers) · [Environment variables](reference/environment-variables) |
| Run in production | [Deployment](guide/deployment) · [Production checklist](ops/production-checklist) · [Kazma Update](ops/kazma-update) · [Smoke matrix](ops/smoke-matrix) |
| Move Kazma to a new machine | [Migration](ops/migration) · [Portability](ops/portability) · [Disaster recovery](ops/disaster-recovery) |
| Get server status alerts in chat | [Lifecycle notifications](guide/deployment#10-lifecycle-status-notifications) |
| Debug multi-path bugs ("X related to Y") | [Diagnosis map](ops/diagnosis-map) · [System map](reference/system-map) |
| Use tools safely | [Tools catalog](reference/tools-catalog) · [Security & HITL](guide/security-and-safety) · [Commitment Layer](guide/commitment-layer) |
| Use built-in skills (browser, calendar, docs, …) | [Native skills](guide/native-skills) |
| Send voice / images / documents | [Voice & media](guide/voice-and-media) |
| Connect MCP servers (stdio/sse/streamable_http) | [Skills, MCP & tools](guide/skills-mcp-and-tools) |
| Ingest documentation into a knowledge corpus | [Knowledge Library](guide/knowledge-library) |
| Process documents (parse, OCR, index, generate, redact) | [Document Intelligence](guide/document-intelligence) · [Phase map 0–10](guide/document-phases) |
| Run V2 memory + KB inject the recommended way | [Memory best path](guide/memory-best-path) · [Memory & RAG](guide/memory-and-rag) |
| Web search / scrape / research | [Web research](guide/web-research) |
| **New features tour** (research sessions, KB re-index, explain panel, proxy) | [Recent features](guide/recent-features) |
| Email (Gmail / Microsoft / sandbox) | [Email integration](guide/email-integration) |

## Documentation map

### Guide (concepts & how-to)

- [Quickstart](guide/quickstart) · [Architecture](guide/architecture) · [Configuration](guide/configuration)
- [Gateways & platforms](guide/gateways-and-platforms) · [CLI](guide/cli-reference) · [Skills, MCP & tools](guide/skills-mcp-and-tools)
- [Native skills](guide/native-skills) · [Voice & media](guide/voice-and-media)
- [Swarm](guide/swarm-orchestration) · [Memory & RAG](guide/memory-and-rag) · [Memory best path](guide/memory-best-path) · [Security](guide/security-and-safety) · [Commitment Layer](guide/commitment-layer)
- [Web research](guide/web-research) · [Knowledge Library](guide/knowledge-library) · [**Document Intelligence**](guide/document-intelligence) · [Document phases](guide/document-phases) · [**Recent features**](guide/recent-features) · [Email](guide/email-integration)
- [Arabic & cultural](guide/arabic-cultural-features) · [Deployment](guide/deployment) · [Development](guide/development)
- [Troubleshooting](guide/troubleshooting-and-workarounds) · [FAQ](guide/faq) · [Glossary](guide/glossary) · [Roadmap](guide/roadmap-and-future)

### Products (UI surfaces)

- [Web UI](products/web-ui) · [IDE](products/ide) · [TUI](products/tui)
- [Command Center / Swarm panel](products/command-center-swarm) · [Multi-user SaaS](products/multi-user-saas)

### Reference (exhaustive catalogs)

- [Tools catalog](reference/tools-catalog) · [LLM providers](reference/llm-providers) · [Slash commands](reference/slash-commands)
- [Environment variables](reference/environment-variables) · [API routes](reference/api-routes)
- [Skill manifest](reference/skill-manifest) · [System map](reference/system-map)

### Ops (production)

- [Production checklist](ops/production-checklist) · [Kazma Update](ops/kazma-update) · [Smoke matrix](ops/smoke-matrix) · [**Diagnosis map**](ops/diagnosis-map) (multi-path X↔Y)
- [Postgres & SaaS](ops/postgres-and-saas) · [Disaster recovery](ops/disaster-recovery)
- [Document processing ops](ops/document-processing) · [Multi-region / HA](ops/multi-region) · [OIDC](ops/oidc-setup)
- [Portability](ops/portability) · [Migration (`kazma migrate`)](ops/migration) · [WSL fixed access](ops/wsl-fixed-access)

### Skills · Security · Contributing

- Skill development & Hub sidebars in the navbar  
- [Security policy](security/security-policy) · [Vulnerability reporting](security/vulnerability-reporting)

## Engineering (not in this site)

| Path | Purpose |
|------|---------|
| `docs/audits/` | Security & architecture audits ([industry stack 2026-08-25](https://github.com/Mubder/kazma/blob/main/docs/audits/AUDIT_INDUSTRY_STACK_2026-08-25.md) — keep/upgrade/replace vs world-class; parts 1–8 done; [memory system 2026-08-24](https://github.com/Mubder/kazma/blob/main/docs/audits/AUDIT_MEMORY_SYSTEM_2026-08-24.md) — M-01..M-17 closed) |
| `docs/plans/` | Implementation plans (email, [KB + research](https://github.com/Mubder/kazma/blob/main/docs/plans/KB_AND_RESEARCH_DEPTH_PLAN.md), [**Memory remaining**](https://github.com/Mubder/kazma/blob/main/docs/plans/MEMORY_REMAINING.md), [**Post-industry leftovers GOAL**](https://github.com/Mubder/kazma/blob/main/docs/plans/POST_INDUSTRY_NON_SAAS_GOAL.md) (done 2026-08-25; SaaS still parked), [Document docs goal](https://github.com/Mubder/kazma/blob/main/docs/plans/DOCUMENT_DOCS_REMEDIATION_GOAL.md), [Document residuals](https://github.com/Mubder/kazma/blob/main/docs/plans/DOCUMENT_RESIDUALS_GOAL.md)) |
| `docs/audits/AUDIT_DOCUMENT_CERTIFICATION.md` | Document Intelligence certification report |
| `docs/ARCHITECTURE_AND_SYSTEM_MAP.md` | Full monorepo map (linked from [System map](reference/system-map)) |
| `docs/plans/done/DOCS_CONSOLIDATION_PLAN.md` | This docs consolidation plan (completed) |
| `AGENTS.md` | Rules for coding agents working in the repo |
| `CHANGELOG.md` | Sprint history |
| `docs/audits/archive/` | Archived audits (former docs-v2 / legacy trees) |

## Honesty policy

Docs distinguish **what the code does today** from **planned / library-only** features. Retired/unwired code is tracked in the audits [`UNWIRED_INVENTORY.md`](https://github.com/Mubder/kazma/blob/main/docs/audits/UNWIRED_INVENTORY.md) — see [Roadmap](guide/roadmap-and-future).
