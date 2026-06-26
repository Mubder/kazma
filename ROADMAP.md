# 🇰🇼 Kazma Feature Roadmap

> **Last updated:** June 26, 2026  
> **Status:** All planned features shipped ✅  
> **Tests:** 2,129 passing | 4 failed | 10 skipped

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

---

## 📈 Progress Tracking

### By Sprint
```
Sprint 1 (Week 1):  ██████████ 5/5 tasks ✅
Sprint 2 (Week 2):  ██████████ 3/3 tasks ✅
Sprint 3 (Week 3):  ██████████ 3/3 tasks ✅
Sprint 4 (Week 4):  ██████████ 6/6 tasks ✅
Sprint 5 (Month 2): ██████████ 2/2 tasks ✅
Sprint 6 (Month 3): ██████████ 2/2 tasks ✅
```

### Overall: 21/21 features shipped ✅ + 9 additional features ✅

---

## 🏷️ Label Legend

| Label | Meaning |
|-------|---------|
| `sprint-1` through `sprint-6` | Which sprint the feature belongs to |
| `quick-win` | High impact, low effort (1-2 days) |
| `high-impact` | Major user value |
| `polish` | UX improvement |
| `competitive` | Catch up to market leaders |
| `tool` | New agent tool |
| `gateway` | Gateway/adapter feature |
| `memory` | Memory/context feature |
| `core` | Core framework feature |
