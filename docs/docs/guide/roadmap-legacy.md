---
id: roadmap-legacy
title: Roadmap (Legacy)
sidebar_label: Roadmap (Legacy)
description: Kazma Roadmap (Legacy) — code-audited reference (unified docs, v0.6.1+)
---
> **Last updated:** July 10, 2026  
> **Version:** 0.4.0  
> **Status:** All originally planned features shipped ✅ + deep audit remediation ✅ + GitHub OAuth integration ✅ + full Arabic i18n ✅  
> **Tests:** ~3,544 root + ~118 package + ~216 TUI  
> **i18n:** ~926 keys (EN + AR)

---

## 📊 Kanban Overview

### 🟢 Done — All Features Shipped

| # | Feature | Effort | Impact | Sprint |
|---|---------|--------|--------|--------|
| [#1](https://github.com/Mubder/kazma/issues/1) | Web search tool (DuckDuckGo) | 2 days | 🔥🔥🔥 | Sprint 1 |
| [#2](https://github.com/Mubder/kazma/issues/2) | URL content extractor | 1 day | 🔥🔥🔥 | Sprint 1 |
| [#3](https://github.com/Mubder/kazma/issues/3) | Typing indicators | 2 days | 🔥🔥 | Sprint 1 |
| [#4](https://github.com/Mubder/kazma/issues/4) | Slash commands | 1 day | 🔥🔥 | Sprint 1 |
| [#5](https://github.com/Mubder/kazma/issues/5) | Markdown rendering | 1 day | 🔥🔥 | Sprint 1 |
| [#6](https://github.com/Mubder/kazma/issues/6) | Tool output truncation | 2 days | 🔥🔥🔥 | Sprint 2 |
| [#7](https://github.com/Mubder/kazma/issues/7) | File read/write tools | 3 days | 🔥🔥🔥 | Sprint 2 |
| [#8](https://github.com/Mubder/kazma/issues/8) | Automatic retry with backoff | 2 days | 🔥🔥🔥 | Sprint 2 |
| [#9](https://github.com/Mubder/kazma/issues/9) | Conversation summarization | 3 days | 🔥🔥🔥 | Sprint 3 |
| [#10](https://github.com/Mubder/kazma/issues/10) | Inline Python REPL | 4 days | 🔥🔥🔥 | Sprint 3 |
| [#11](https://github.com/Mubder/kazma/issues/11) | Image generation tool | 2 days | 🔥🔥 | Sprint 3 |
| [#12](https://github.com/Mubder/kazma/issues/12) | Agent personality templates | 1 day | 🔥🔥 | Sprint 4 |
| [#13](https://github.com/Mubder/kazma/issues/13) | Rate limit user feedback | 1 day | 🔥🔥 | Sprint 4 |
| [#14](https://github.com/Mubder/kazma/issues/14) | Emoji reactions | 1 day | 🔥 | Sprint 4 |
| [#15](https://github.com/Mubder/kazma/issues/15) | Quick reply buttons | 2 days | 🔥🔥 | Sprint 4 |
| [#16](https://github.com/Mubder/kazma/issues/16) | Graceful error messages | 1 day | 🔥🔥 | Sprint 4 |
| [#17](https://github.com/Mubder/kazma/issues/17) | Session export | 1 day | 🔥 | Sprint 4 |
| [#18](https://github.com/Mubder/kazma/issues/18) | Knowledge graph context | 2-3 weeks | 🔥🔥🔥 | Sprint 5 |
| [#19](https://github.com/Mubder/kazma/issues/19) | Time travel replay | 1-2 weeks | 🔥🔥 | Sprint 5 |
| [#20](https://github.com/Mubder/kazma/issues/20) | IDE integration (MCP server) | 4-6 weeks | 🔥🔥🔥 | Sprint 6 |
| [#21](https://github.com/Mubder/kazma/issues/21) | Voice/multimodal support | 3-4 weeks | 🔥🔥 | Sprint 6 |

### 🆕 v0.4.0 — GitHub Integration + Arabic i18n + Security Audit (July 2026)

| Feature | Description | Status |
|---------|-------------|--------|
| **GitHub OAuth Integration** | Read-only OAuth flow (no PAT in .env); GitHubClient (REST + GraphQL + pagination); 7 read endpoints (pulls, issues, commits, workflows, branches, releases); repo picker with clone; activity timeline with filter chips; unified workspace | ✅ Done |
| **Full Arabic i18n** | ~926 translation keys (EN + AR); JS-side `t()` for Alpine expressions; all templates wired (workspace, agents, swarm, settings, skills, mcp, dashboard); Calibri font; RTL font sizing | ✅ Done |
| **Deep Security Audit** | ~40 bugs found and fixed: async HITL auto-approve, adapter access control (Discord/Slack), semantic-cache cross-user leak, delegation auth bypass, autoscaler race, SQLite WAL pragmas (14 stores), model registry lock, checkpoint timeout | ✅ Done |
| **Unified Workspace** | All 3 cards (Project Files, Git Status, GitHub Telemetry) follow one active repo atomically; filesystem autocomplete | ✅ Done |

### 🆕 Additional Features (Not in Original Plan)

| Feature | Description | Status |
|---------|-------------|--------|
| FTS5 Memory | SQLite full-text search with BM25 ranking | ✅ Done |
| Swarm Manager | Unified in-process + distributed worker orchestration | ✅ Done |
| Web UI Rebuild | 12-tab settings, Alpine.js, provider management | ✅ Done |
| Provider Integration | 9 built-in providers with model discovery | ✅ Done |
| Settings Persistence | SQLite config_store for all settings | ✅ Done |
| Voice Transcription | Telegram voice message STT | ✅ Done |
| Vision Analysis | Image analysis via LLM vision | ✅ Done |
| Role Presets | Swarm worker role presets (orchestrator, backend, frontend, etc.) | ✅ Done |
| Bug Fixes | 13 audit bugs fixed (RBAC, cron, schema, KG, etc.) | ✅ Done |
| **Chaos Testing Framework** | Failure injection engine with 10 predefined experiments | ✅ Done (Sprint 19) |
| **Config Migration UI** | Runtime DB schema migration management | ✅ Done (Sprint 19) |
| **Load Testing Infrastructure** | Locust + k6 test suites with CI integration | ✅ Done (Sprint 19) |
| **Adapter Extraction** | Clean platform output abstraction (swarm_output.py) | ✅ Done (Sprint 19) |
| **WebSocket → SSE HITL** | Full HITL support via Server-Sent Events | ✅ Done (Sprint 19) |

### 🔧 Architecture Remediation (Sprint 8 — June 2026)

| Feature | Description | Status |
|---------|-------------|--------|
| P0 Bug Fixes | 5 P0 correctness bugs fixed (agent_handler race, Windows code_exec crash, global session messages, config write race, session store deletion) | ✅ Done |
| Dead Code Removal | 6 dead modules deleted (consumer, dispatcher, recovery, checkpoint, stubs, compact_node) | ✅ Done |
| UnifiedToolExecutor | 3 tool registries consolidated onto UnifiedToolExecutor | ✅ Done |
| Service Facade | Zero private attribute access from UI; service layer facade | ✅ Done |
| Unified Session Stores | Merged session stores into a single coherent store layer | ✅ Done |
| HITL Approval UI | Inline approve/deny panel for tiered tool-safety gates | ✅ Done |
| Session History Loading | Browse and load prior conversations | ✅ Done |
| Agents Page | Dedicated agent inspection/control page | ✅ Done |
| UI Bug Fixes | Telemetry dedup, toast null-ref, cost breaker type, swarm logs, init error surfacing | ✅ Done |
| Cross-Platform Hardening | setup.ps1, portable paths, PowerShell completion, env var overrides | ✅ Done |
| RTL / Arabic Completion | Cairo font, i18n system (150+ translations, 71 CSS selectors) | ✅ Done |

### 🎨 UI Bug Fixes (Sprint 9 — June 2026)

| Feature | Description | Status |
|---------|-------------|--------|
| Dark Mode Dropdown Fix | WCAG-compliant dropdown contrast in dark theme | ✅ Done |
| Model Selection Pipeline | Chat-model selector, provider switch on save, SSE passthrough, API key validation | ✅ Done |
| Bilingual Language System | EN/AR toggle, cookie middleware, shared Jinja2Templates, complete i18n | ✅ Done |

---

## 📈 Progress Tracking

### By Sprint
```
Sprint 1 (Week 1):    ██████████ 5/5 tasks ✅
Sprint 2 (Week 2):    ██████████ 3/3 tasks ✅
Sprint 3 (Week 3):    ██████████ 3/3 tasks ✅
Sprint 4 (Week 4):    ██████████ 6/6 tasks ✅
Sprint 5 (Month 2):   ██████████ 2/2 tasks ✅
Sprint 6 (Month 3):   ██████████ 2/2 tasks ✅
Sprint 7 (June 2026): ██████████ Web UI rebuild + memory ✅
Sprint 8 (June 2026): ██████████ Architecture remediation ✅
Sprint 9 (June 2026): ██████████ UI bug fixes + bilingual ✅
Sprint 10 (June 2026):██████████ Model picker + tool-call fallback ✅
Sprint 11 (June 2026):██████████ Swarm pro-grade overhaul (Phase 1-4) ✅
Sprint 12 (June 2026):██████████ Swarm output routing + bugs ✅
Sprint 13 (June 2026):██████████ Active Tasks + test fixes ✅
Sprint 14 (July 2026):██████████ HITL gates (all platforms) + test isolation ✅
Sprint 15 (July 2026):██████████ ConfigStore atomicity + MCP auth/HITL ✅
Sprint 16 (July 2026):██████████ Skill checksums + task cancel/retry ✅
Sprint 17 (July 2026):██████████ Config reconciliation + engine refactor ✅
```

### Overall: 21/21 original features shipped ✅ + 8 remediation sprints complete ✅

---

## 🔜 Remediation Audit — Completed Items (Sprints 14–17)

The following items were identified by the post-remediation weak-points audit and
completed in Sprints 14-17.

| Priority | Item | Status |
|:---:|:---|:---:|
| P0 | **HITL Approval Gates** — Wire HITL into WebUI + all gateway adapter paths (Web, Telegram, Discord, Slack). Fail-closed danger-tool gating with graph interrupt() + swarm bus + MCP classification. | ✅ Done |
| P0 | **Test Isolation Fix** — Root-caused 36 failing tests. Fixed KAZMA_SECRET env leak (23 failures), handoff cycle detection, workspace singleton pollution. 36→3 failures. | ✅ Done |
| P0 | **Hub API Auth** — Rewrote `hub/api.py` to read `X-Kazma-Secret` with `hmac.compare_digest`. | ✅ Done |
| P0 | **Route Gating** — Added auth middleware to `/api/sessions`, `/api/mcp/servers`, `/api/approve`, `/api/system/*`. | ✅ Done |
| P0 | **Docker Bind Fix** — Dockerfile CMD changed to `--host 0.0.0.0`. | ✅ Done |
| P1 | **SSRF Validation** — Added URL validation to discover/MCP endpoints. | ✅ Done |
| P1 | **Active Tasks Tab** — In-flight task tracking, non-blocking dispatch, live polling. | ✅ Done |
| P1 | **MCP Auth + HITL** — Per-server auth (bearer tokens), trust levels, MCP tool classification + HITL gate. | ✅ Done |
| P1 | **ConfigStore Atomicity** — WAL journaling, busy_timeout, batch transactions, process-wide singleton, YAML→SQLite reconciliation. | ✅ Done |
| P1 | **Skill Checksums** — Fail-closed verification, HMAC-SHA256 signatures, `kazma hub sign` CLI. | ✅ Done |
| P2 | **Circuit Breaker Badges** — Live per-worker ⚡ breaker state in swarm panel. | ✅ Done |
| P2 | **Per-Worker Start/Stop** — Individual worker lifecycle control (API + UI). | ✅ Done |
| P2 | **Task Cancel/Retry** — Cancel running tasks, retry failed tasks from UI. | ✅ Done |
| P2 | **Docs Accuracy** — Test count, Slack description, README feature descriptions updated. | ✅ Done |
| P2 | **Config Reconciliation** — YAML auto-seeds SQLite on startup, non-clobbering. | ✅ Done |
| P2 | **Engine Refactor** — 1,878→1,573 lines. 3 modules extracted (ReliabilityRegistry, WorkerPhonebook, CheckpointManager). | ✅ Done |

### Remaining Open Items

| Priority | Item | Effort | Status |
|:---:|:---|:---:|:---:|
| P2 | Unify routing algorithms (merge 3 implementations) | M | ✅ Done |
| P2 | Semantic routing (embeddings-based capability matching) | L | ✅ Done |
| P2 | Visual pipeline editor (drag-and-drop DAG) | XL | |

---

## 🏷️ Label Legend

| Label | Meaning |
|-------|---------|
| `sprint-1` through `sprint-9` | Which sprint the feature belongs to |
| `quick-win` | High impact, low effort (1-2 days) |
| `high-impact` | Major user value |
| `polish` | UX improvement |
| `competitive` | Catch up to market leaders |
| `tool` | New agent tool |
| `gateway` | Gateway/adapter feature |
| `memory` | Memory/context feature |
| `core` | Core framework feature |
| `P0` / `P1` / `P2` | Remediation priority (critical / high / medium) |
