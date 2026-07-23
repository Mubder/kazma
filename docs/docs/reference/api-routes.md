---
id: api-routes
title: API & Route Matrix
sidebar_label: API Routes
description: Primary HTTP/SSE/WebSocket routes exposed by kazma-ui and gateway control
---

> Primary surfaces of the FastAPI app (`kazma_ui`). Auth is **default-deny** for `/api/*` unless listed open. HITL danger applies to tool execution, not every HTTP route.

## Always open (health)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | Open | Liveness |
| GET | `/health/live` | Open | Process live |
| GET | `/health/ready` | Open | Readiness (DB ping when configured) |

## Auth & account

| Method | Path | Auth scope | Description |
|--------|------|------------|-------------|
| GET/POST | `/login` | Public page | Multi-mode login (user / secret / OIDC) |
| POST | `/api/auth/*` | Varies | Login, logout, session (see `auth.py`, `saas_api.py`) |
| GET | `/api/saas/*` | Admin / operator | Tenants, platform users (RBAC) |

## Chat & sessions

| Method | Path | Auth | HITL | Description |
|--------|------|------|------|-------------|
| POST | `/api/chat/stream` | Session/secret | Graph interrupt on danger tools | SSE chat stream |
| POST | `/api/approve/{thread_id}` | Session | Resumes graph HITL | Approve danger tool |
| * | Session CRUD under `/api/sessions*` | Session | — | Thread list / history (`session_manager`) |

## IDE

| Method | Path | Auth | HITL | Description |
|--------|------|------|------|-------------|
| * | `/api/ide/*` | Session | Via tool registry | Files, run, git, swarm send (`ide_api.py`) |
| GET | `/ide` | Session | — | IDE page |

## Swarm / Command Center

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| * | `/api/swarm/*` | Session / RBAC | Workers, dispatch, tasks, metrics |
| GET | SSE swarm events | Session | Live task stream (`swarm_sse`) |
| GET | `/swarm` | Session | Swarm panel page |
| * | `/api/replay/*` | Session | Time travel: threads, snapshots, restore, fork, compare, clear (`replay_routes.py`) |
| GET | `/replay` | Session | Time Travel panel page |

## Settings & config

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| * | `/api/settings*`, config export | Admin/operator | ConfigStore-backed settings UI |
| * | Workspace routes `/api/workspaces*` | Session | WorkspaceStore CRUD |

## Email (`email_api.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/email/status` | Session | Active provider, auth modes, presets summary |
| GET | `/api/email/accounts` | Session | Multi-account aliases (env) |
| GET | `/api/email/presets` | Session | Gmail/Microsoft IMAP/POP host presets |
| POST | `/api/email/protocol/connect` | Session | Save IMAP/POP for gmail\|microsoft\|generic |
| POST | `/api/email/protocol/disconnect` | Session | Clear protocol + OAuth tokens for provider |
| POST | `/api/email/gmail/connect` | Session | Gmail app-password → IMAP |
| POST | `/api/email/gmail/disconnect` | Session | Clear Gmail creds |
| POST | `/api/email/oauth/gmail/client` | Session | Save Google OAuth client id/secret |
| GET | `/api/email/oauth/gmail/start` · `start.json` | Session | Browser OAuth redirect / JSON |
| GET | `/api/email/oauth/gmail/callback` | Open (OAuth) | Token exchange; redirects to Settings |
| POST | `/api/email/oauth/microsoft/client` | Session | Save Azure app id/secret |
| GET | `/api/email/oauth/microsoft/start` · `start.json` | Session | Browser OAuth |
| GET | `/api/email/oauth/microsoft/callback` | Open (OAuth) | Token exchange |
| POST | `/api/email/oauth/microsoft/device/start` · `…/poll` | Session | Device-code fallback |
| POST | `/api/email/oauth/microsoft/disconnect` | Session | Clear Microsoft tokens |

Agent mail ops use tools (`email_list`, …), not these HTTP routes. Guide: [Email integration](../guide/email-integration).

## Gateways & platforms

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| Webhooks | Telegram/Discord/Slack callbacks | Adapter secrets | Inbound messages + HITL buttons |
| * | `/api/gateway/*` | Session | Gateway status/control from CLI |

## Other

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| WS | Voice routes | Session | `routes_voice_ws.py` |
| * | Chaos routes | `KAZMA_CHAOS_ENABLED` | **Dev only** |
| Static | `/`, `/chat`, `/settings`, … | Cookie/session | HTML pages |

Exact route lists evolve with routers mounted in `app.py`. For extension points see [API & Extension Points](../guide/api-and-extension-points).

## Auth model summary

| Mode | When |
|------|------|
| Shared secret cookie / header | Single-operator |
| Opaque web session | Multi-user default |
| Platform RBAC (viewer/operator/admin) | SaaS APIs |
| OIDC PKCE | SSO login |
| API token / JWT | Programmatic (where enabled) |

See [Multi-user SaaS](../products/multi-user-saas) and [Environment variables](environment-variables).
