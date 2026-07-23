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
| Dashboard | `/dashboard` | Observability: cost, tokens, tool calls, cost breaker, uptime, traces |
| Settings | `/settings` | Models, providers, safety, account, **Email** (`?tab=email`) |
| Swarm / Command Center | `/swarm` | Workers, live tasks, dispatch UI |
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

Research / scrape runs through **chat tools** (see [Web research](../guide/web-research)), not a separate dashboard action.

## Email (Settings → Email)

Connect real mailboxes for the agent without env-only setup:

| Card | Modes |
|------|--------|
| Sandbox | Always on (demo SQLite mailbox) |
| Gmail / Workspace | **OAuth** (Gmail API) · **IMAP** · **POP** |
| Microsoft 365 | **OAuth** (Graph browser or device code) · **IMAP** · **POP** |

API: `/api/email/status`, OAuth start/callback, `POST /api/email/protocol/connect`. Full guide: [Email integration](../guide/email-integration).

## Auth

- Single-operator: `KAZMA_SECRET` (opaque sessions preferred over legacy raw cookie).  
- Multi-user: platform users + RBAC + optional OIDC — see [Multi-user SaaS](multi-user-saas).  
- API routes default-deny when secret is configured.

## UI conventions

- Dialogs: `window.kazmaConfirm` / `kazmaAlert` / `kazmaPrompt` (not `window.confirm`).  
- Toasts: `window.showToast` / Alpine `$store.toast`.  
- Soft-nav SPA may be feature-flagged off — full page loads are the reliable path.

## Related

- [IDE](ide) · [Command Center](command-center-swarm) · [API routes](../reference/api-routes)  
- [Deployment](../guide/deployment) · [Troubleshooting](../guide/troubleshooting-and-workarounds)  
