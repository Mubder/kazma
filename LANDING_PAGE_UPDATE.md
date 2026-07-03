# Kazma Landing Page Update Prompt

**Site:** https://kazma.ai  
**Goal:** Integrate Sprint 14–17 changes — updated metrics, 5 new screenshots, revised roadmap, and refined feature copy.

---

## 1. Metrics Update

Replace the three hero numbers:

| Current | → | New |
|---------|---|-----|
| "3,376 Verified System Assertions" | → | **"3,409 Passing Tests"** (3 environmental only — Windows admin, LM Studio offline) |
| "120+ Pre-Integrated Cloud/Local Models" | → | Keep as-is |
| "4 Omnichannel Integrations" | → | Keep as-is |

---

## 2. New Screenshots — "Inside Kazma" Section

Keep all existing tabs. Add these 5 new tabs alongside the existing ones. The existing **Swarm.png** should remain. The 5 new files are:

| Tab label | File |
|-----------|------|
| **Swarm Active Tasks** | `SwarmActiveTasks.png` |
| **Swarm Task Builder** | `SwarmTaskBuilder.png` |
| **Swarm Results Dashboard** | `SwarmResultsDashboard.png` |
| **Swarm Task History** | `SwarmTaskHistory.png` |
| **Settings Connectors** | `SettingsPlatformConnectors.png` |

The full screenshot tabs should now be (order flexible):
- Dashboard
- Chat
- Swarm Active Tasks
- Swarm Task Builder
- Swarm Results Dashboard
- Swarm Task History
- Swarm
- Agents
- Skills
- MCP servers
- Settings Connectors
- Workspace
- Settings

---

## 3. Roadmap Section Update

Replace the "Roadmap" section with:

**✓ DONE: Foundation & setup** — Monorepo, uv, CI/CD, core security — 192 Python files, 47K source LOC.

**✓ DONE: Agent brain & memory** — LangGraph workflow, ChromaDB RAG, 8 personalities, long-context, Arabic cultural layer.

**✓ DONE: Tools & dispatcher** — 15+ built-in tools, sub-agent spawning, multi-platform gateway, cost breakers, MCP bridge.

**✓ DONE: Security hardening** — HITL approval gates on ALL platforms (Web, Telegram, Discord, Slack). Fail-closed danger-tool gating. MCP tool classification + auth. Skill checksum enforcement with HMAC signatures.

**✓ DONE: Reliability engineering** — Test suite: 3,409 passing / 3 failing. ConfigStore atomicity (WAL, batch transactions, singleton). Config reconciliation (YAML→SQLite on startup). Engine refactored (1,878→1,573 lines).

**↻ IN PROGRESS: Dashboard & ecosystem** — Advanced web dashboard, full EN/AR docs, MCP integration, production deployment. Task cancel/retry. Circuit breaker UI badges. Per-worker start/stop.

**⋯ UP NEXT: Launch & community** — Public launch, developer community, comprehensive docs, long-term support.

---

## 4. Features Section — "Why Kazma" Updates

Update these existing feature cards with the new copy:

### Human-in-the-Loop (was: generic approval gate)
**Headline:** Human-in-the-Loop  
**Body:** Three-tier approval gates (graph interrupt + swarm bus + MCP classification). Danger tools require human approval on Web, Telegram, Discord, and Slack. Fail-closed by default — no unattended `shell_exec` or `file_write`.

### Production-Grade Resilience (was: generic)
**Headline:** Production-Grade Resilience  
**Body:** 3,409 tests, 3 environmental failures. Atomic ConfigStore with WAL journaling prevents config corruption. SQLite checkpointing survives SIGKILL. Engine god-class refactored into focused modules — zero test regressions.

If there's room, add a new card:

### Omnichannel Safety
**Headline:** Omnichannel Safety  
**Body:** The same HITL approval gates work identically whether you're chatting via Telegram inline keyboards, Discord components, Slack Block Kit, or the Web UI. One security model, every surface.

---

## 5. Module Maturity / Capabilities Section

If there's a module maturity grid or capability checklist, add these new items as ✅ DONE:

- ✅ **HITL Approval Gates** — Web, Telegram, Discord, Slack — fail-closed
- ✅ **MCP Tool Security** — Auth tokens + trust levels + HITL classification
- ✅ **ConfigStore Atomicity** — WAL, batch transactions, crash-safe writes
- ✅ **Skill Checksums** — HMAC-signed, fail-closed verification
- ✅ **Task Cancel / Retry** — Cancel running tasks, retry failed ones
- ✅ **Circuit Breaker Badges** — Live per-worker breaker state in swarm panel
- ✅ **Per-Worker Start/Stop** — Individual worker lifecycle control
- ✅ **Engine Refactor** — 1,878→1,573 lines, 3 modules extracted

---

## 6. Copy Tweaks — Hero / Tagline

If the hero mentions safety, update it to be more specific:

**Current:** "Not another wrapper — a durable, culturally-aware, safety-first platform…"  
**Suggested:** "Not another wrapper — durable execution, 3,409 tests, fail-closed safety gates on every surface, all from a bilingual dashboard."

---

## Summary of what NOT to change
- Navigation structure
- "How it works" steps
- Quickstart section
- Architecture diagram
- FAQ
- Footer
- Bilingual/Arabic support
- Production stack bar (LangGraph, ChromaDB, FastAPI, etc.)
- "Star on GitHub" buttons
- Docker deployment mentions
