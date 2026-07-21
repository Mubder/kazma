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
| Settings | `/settings` | Models, providers, safety, account |
| Swarm / Command Center | `/swarm` | Workers, live tasks, dispatch UI |
| IDE | `/ide` | Workspace files, run, git, AI-assisted edit |
| Login | `/login` | Secret / local user / OIDC |

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
