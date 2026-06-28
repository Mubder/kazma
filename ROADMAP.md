# 🇰🇼 Kazma Feature Roadmap

> **Last updated:** June 28, 2026  
> **Status:** All originally planned features shipped ✅ + two remediation rounds complete ✅  
> **Tests:** 2,382+ passing

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
```

### Overall: 21/21 original features shipped ✅ + remediation rounds complete ✅

---

## 🔜 Future Work — Hardening & Security (Post-Remediation Audit)

The following items were identified by the post-remediation weak-points audit and are
prioritized for upcoming sprints.

### P0 — Critical Security

| Priority | Item | Description |
|:---:|:---|:---|
| P0 | API Authentication | Add authentication to all API endpoints (settings, swarm, MCP, skills) |
| P0 | SSRF Protection | Guard URL-fetching tools (read_url, vision_analyze) against SSRF |
| P0 | CORS Middleware | Add configurable CORS middleware to the FastAPI app |

### P1 — High Priority

| Priority | Item | Description |
|:---:|:---|:---|
| P1 | Non-blocking web_search | Wrap blocking `web_search` call in `asyncio.to_thread` |
| P1 | Bounded LRU Eviction | Add bounded LRU eviction to `_thread_locks`, `_sessions`, `SessionManager`, `_checkpoint_locks` |
| P1 | Error Handler Leakage | Error handlers must not leak `str(exc)` to clients |
| P1 | file_read Workspace Restriction | Restrict `file_read.py` to the configured workspace root |

### P2 — Medium Priority

| Priority | Item | Description |
|:---:|:---|:---|
| P2 | mypy Type Errors | Resolve the remaining 208 mypy type errors |
| P2 | Test Coverage | Add tests for agent_runner.py, graph_builder.py, and tool modules |
| P2 | LLM Retry with Backoff | Add retry with backoff to LLM calls on the main graph path |
| P2 | SQLite Lock Handling | Improve "database is locked" error handling for concurrent access |

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
