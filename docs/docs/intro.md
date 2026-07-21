---
id: intro
slug: /
title: Kazma Documentation
sidebar_label: Docs home
description: Map of all Kazma documentation — start here
---

# Kazma documentation

**Single source of truth** for the Kazma agent framework (v0.6.1+).  
Everything user-facing lives under this Docusaurus site (`docs/docs/`). Historical trees live in [`archive/`](https://github.com/kazma-ai/kazma/tree/main/archive).

## Start here

| I want to… | Go to |
|------------|--------|
| Install and send a first message | [Quickstart](guide/quickstart) |
| Understand the engine | [Architecture](guide/architecture) |
| Configure providers / YAML / env | [Configuration](guide/configuration) · [Environment variables](reference/environment-variables) |
| Run in production | [Deployment](guide/deployment) · [Production checklist](ops/production-checklist) |
| Use tools safely | [Tools catalog](reference/tools-catalog) · [Security & HITL](guide/security-and-safety) |

## Documentation map

### Guide (concepts & how-to)

- [Quickstart](guide/quickstart) · [Architecture](guide/architecture) · [Configuration](guide/configuration)
- [Gateways & platforms](guide/gateways-and-platforms) · [CLI](guide/cli-reference) · [Skills, MCP & tools](guide/skills-mcp-and-tools)
- [Swarm](guide/swarm-orchestration) · [Memory & RAG](guide/memory-and-rag) · [Security](guide/security-and-safety)
- [Arabic & cultural](guide/arabic-cultural-features) · [Deployment](guide/deployment) · [Development](guide/development)
- [Troubleshooting](guide/troubleshooting-and-workarounds) · [FAQ](guide/faq) · [Glossary](guide/glossary) · [Roadmap](guide/roadmap-and-future)

### Products (UI surfaces)

- [Web UI](products/web-ui) · [IDE](products/ide) · [TUI](products/tui)
- [Command Center / Swarm panel](products/command-center-swarm) · [Multi-user SaaS](products/multi-user-saas)

### Reference (exhaustive catalogs)

- [Tools catalog](reference/tools-catalog) · [Slash commands](reference/slash-commands)
- [Environment variables](reference/environment-variables) · [API routes](reference/api-routes)
- [Skill manifest](reference/skill-manifest) · [System map](reference/system-map)

### Ops (production)

- [Production checklist](ops/production-checklist) · [Postgres & SaaS](ops/postgres-and-saas)
- [Disaster recovery](ops/disaster-recovery) · [Multi-region / HA](ops/multi-region) · [OIDC](ops/oidc-setup)

### Skills · Security · Contributing

- Skill development & Hub sidebars in the navbar  
- [Security policy](security/security-policy) · [Vulnerability reporting](security/vulnerability-reporting)

## Engineering (not in this site)

| Path | Purpose |
|------|---------|
| `docs/audits/` | Security & architecture audits |
| `docs/ARCHITECTURE_AND_SYSTEM_MAP.md` | Full monorepo map (linked from [System map](reference/system-map)) |
| `docs/DOCS_CONSOLIDATION_PLAN.md` | This docs consolidation plan |
| `AGENTS.md` | Rules for coding agents working in the repo |
| `CHANGELOG.md` | Sprint history |
| `archive/` | Retired docs trees (`docs-v2`, legacy pages) |

## Honesty policy

Docs distinguish **what the code does today** from **planned / library-only** features. Unwired packages (e.g. `delegation/*`) are labeled — see [Roadmap](guide/roadmap-and-future) and audits `UNWIRED_INVENTORY.md`.
