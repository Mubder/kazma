---
id: web-ui
title: Web UI
sidebar_label: Web UI
description: FastAPI + Alpine web dashboard — chat, settings, swarm, IDE, login
---

# Web UI

The primary operator surface is **`kazma-ui`**: FastAPI + Jinja templates + Alpine.js + SSE streaming.

## Start

```bash
kazma serve          # default 127.0.0.1:9090
# or
python serve.py
```

Open `http://127.0.0.1:9090` (or your `KAZMA_HOST`/`KAZMA_PORT`).

## Main pages

| Page | Path | Purpose |
|------|------|---------|
| Chat | `/` or chat route | SSE streaming agent chat (`sse_chat.py`, `streaming.js`) |
| Dashboard | `/dashboard` | Observability + **Memory & Governance** (health board, L2 property graph explorer, backups) |
| Settings | `/settings` | Models, providers, safety, account, **Email** (`?tab=email`), **Proxy Provider** (System tab) |
| Swarm / Command Center | `/swarm` | Workers, live tasks, dispatch UI |
| Time Travel | `/replay` | Snapshot timeline browser, restore (rewind), fork (branch), compare diff, live SSE snapshot events |
| Research | `/research` | **Start deep research** (live SSE sessions), results list, archive, compare, export |
| IDE | `/ide` | Workspace files, run, git, AI-assisted edit |
| Login | `/login` | Secret / local user / OIDC |

## Dashboard metrics

Cards are fed by the in-memory **`TraceStore`** (LLM/tool traces) plus the **cost circuit breaker** budget state:

| Card | Source | Notes |
|------|--------|--------|
| Total Cost | TraceStore total + cost breaker | API returns **numbers**; UI formats `$…`. Resets on process restart. |
| Total Tokens | TraceStore | Same process lifetime as traces. |
| Tool Calls | TraceStore (`trace_type=tool`) | Supervisor tool worker traces. |
| Circuit Breaker | **Cost** breaker (OK / WARNING / HALTED) | Not swarm worker breakers. |
| Uptime | TraceStore start time | Process lifetime of the store singleton. |

`GET /api/dashboard/status` and WebSocket `/ws/dashboard` push numeric metrics (legacy string forms like `"$0.00"` / `"1,234"` are parsed safely in `dashboard.js`). Chat footer token/cost on a turn still comes from the SSE `done` event for that session.

### Memory & Governance (Dashboard)

- **Component health** from `build_memory_health()` (embedder, VectorMemory, L1–L4, consolidator, packages).
- **Property graph (L2)** canvas: search, refresh, clear; APIs under `/api/memory/graph*`.
- Backups/maintenance for FTS + vector stores.

Full guide: [Memory & RAG](../guide/memory-and-rag). Remaining work: [`MEMORY_REMAINING.md`](https://github.com/Mubder/kazma/blob/main/docs/plans/MEMORY_REMAINING.md).

Research / scrape runs through **chat tools** and the **Research panel** start form + live SSE sessions (see [Web research](../guide/web-research)). Tour of the latest stack: [Recent features](../guide/recent-features).

### Memory context (chat workbench)

With **Settings → Memory → Explain recall** on, each turn’s workbench can show a
**Memory context** panel (beliefs / episodes / KB hits with channel chips:
`fts5`, `dense`, `belief_ppr`, `kb_rrf`, …). Dashboard probe uses the same
explain tags; **Run golden eval** exercises the golden recall set.

## Email (Settings → Email)

Connect real mailboxes for the agent without env-only setup:

| Card | Modes |
|------|--------|
| Sandbox | Always on (demo SQLite mailbox) |
| Gmail / Workspace | **OAuth** (Gmail API) · **IMAP** · **POP** |
| Microsoft 365 | **OAuth** (Graph browser or device code) · **IMAP** · **POP** |

API: `/api/email/status`, OAuth start/callback, `POST /api/email/protocol/connect`. Full guide: [Email integration](../guide/email-integration).

## Proxy Provider (Settings → System)

Opt-in scraping-resilience addon. When enabled, scrape/crawl paths route through
a residential rotating proxy (httpx ladder, Playwright, KB discover, Bing/Wiki
SERP). Local SearXNG, Jina/Firecrawl APIs, and LLM traffic stay direct.

- **Provider:** `None` (direct) or **anyIP.io** (residential/mobile rotation).
- Fields: host/port/username/password (password vault-encrypted), network type,
  optional country, sticky-session toggle.
- **Test Connection** fetches `api.ipify.org` through the proxy and shows the exit IP.
- Live config — a Settings change takes effect without a restart.

API: `GET/PUT /api/settings/proxy`, `POST /api/settings/proxy/test`.  
Coverage matrix: [Recent features → Proxy](../guide/recent-features#2-shared-web-acquisition--proxy-provider) · [Web research](../guide/web-research#bulletproof-scraping-proxy-provider-addon-ipua-rotation).

## Auth

- Single-operator: `KAZMA_SECRET` (opaque sessions preferred over legacy raw cookie).  
- Multi-user: platform users + RBAC + optional OIDC — see [Multi-user SaaS](multi-user-saas).  
- API routes default-deny when secret is configured.

## UI conventions

- **Dialogs**: `window.confirm`, `window.alert`, and `window.prompt` are
  **globally overridden** — every call (current and future) routes through
  the styled Kazma modal (Alpine-based). Developers can still write
  `if (!await confirm('Delete?'))` and get the branded dialog. The
  `kazmaConfirm` / `kazmaAlert` / `kazmaPrompt` helpers remain available
  for opts-based calls (title, danger, confirmText, etc.).
- **Chat Stop button**: the send button transforms into a red pulsing Stop
  button during generation. Click it or press **Escape** to abort the SSE
  stream. The input field stays enabled so the user can type their next
  message while the agent works.
- **Toasts**: `window.showToast` / Alpine `$store.toast`.
- **Research archive**: each research card has an archive button. Archived
  items move to the "Archived" tab (with restore + delete). Uses the JSON
  `metadata.archived` flag — no schema migration.
- Soft-nav SPA may be feature-flagged off — full page loads are the reliable path.

## Related

- [IDE](ide) · [Command Center](command-center-swarm) · [API routes](../reference/api-routes)  
- [Deployment](../guide/deployment) · [Troubleshooting](../guide/troubleshooting-and-workarounds)  
