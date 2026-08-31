---
id: multi-replica-and-saas
title: Multi-replica & SaaS residual hardening
sidebar_label: Multi-replica & SaaS
description: What is shared-state ready vs process-local after residual hardening
---

# Multi-replica & SaaS residual hardening

Kazma's default profile remains **single-operator / single-node**. The
work below closes residual gaps for multi-user and multi-replica *where
the architecture allows* — it does not claim full multi-tenant SaaS.

## Post-HITL host power

After a human approves a danger tool, the host is still powerful by
design. Hardening (2026-07 residual batch):

| Control | Behavior |
|---------|----------|
| Allowlist binaries | No interpreters; prod drops `ps`/`kazma` |
| Archive tools | `tar`/`zip` off in production strict mode unless `KAZMA_SHELL_ALLOW_ARCHIVE=1` |
| Restricted PATH | `KAZMA_SHELL_STRICT` defaults **on** in production — system/build dirs only |
| Binary resolve | `shutil.which` under restricted PATH; no `/tmp/evil/git` |
| Scrubbed env | No parent API keys |
| Git denylist | push/force/credential/reset/rebase/clean -fd blocked |
| Workspace paths | Absolute args must stay inside workspace root |
| Docker code_exec | Force in production / `KAZMA_CODE_EXEC_DOCKER=force` |

**Not removed:** approved `shell_exec` / `file_write` / `python_exec` still
mutate the host for the trusted operator. Treat YOLO and long HITL grants
as elevated.

## Document Intelligence (jobs + metadata)

| Layer | Multi-replica status |
|-------|----------------------|
| Job claim/lease | **Yes** on Postgres (`documents/jobs_pg.py`) when `KAZMA_DATABASE_URL` is set |
| Metadata CRUD | **Yes** when `KAZMA_DOCUMENTS_METADATA_BACKEND=postgres` or `auto` with PG (`repository_pg.py`) |
| Content blobs | Shared filesystem/volume on `documents.storage_root` required |
| GC | Backend-agnostic — `retention._mark` dispatches to `repository.gc_mark` on both SQLite and Postgres |

Always check `GET /api/documents/ops/readiness`. See [Document processing ops](./document-processing) and [Document Intelligence](../guide/document-intelligence#multi-replica).

## Multi-tenant isolation

| Layer | Status |
|-------|--------|
| Tenant ContextVar | `tenant_context` + middleware |
| Client `X-Tenant-ID` | **Ignored** when production **or** multi-user; JWT / principal only |
| Opaque sessions | Forced when multi-user |
| Document ACL | Per-tenant document + principal ACL on repository |
| Vault secrets | Tenant column + ContextVar filter |
| Vector memory | Metadata `tenant_id` filter on query |
| Swarm tasks | `metadata.tenant_id` stamped on dispatch |
| Knowledge / cron / MCP | Still largely **global** (not per-tenant productized) |

Env: `KAZMA_MULTI_USER=1`, `KAZMA_PRODUCTION=1`, `KAZMA_DATABASE_URL`,
`KAZMA_VAULT_KEY`, `KAZMA_JWT_SECRET` (if JWT tenancy).

## Multi-replica process state

| State | Was | Now |
|-------|-----|-----|
| HITL bus approvals | In-process Event only | **ConfigStore dual-write** via `swarm/shared_approvals.py` + local Event |
| Swarm task results | TaskStore (SQLite/PG) | Unchanged — use Postgres for multi-node |
| Task list tenancy | Global | Auto `metadata.tenant_id` filter when multi-user/prod |
| Circuit breakers | Process memory | **Shared** via ConfigStore when multi-user/prod (`KAZMA_SHARED_BREAKERS`) |
| MCP servers (user-added) | Global ConfigStore | **Tenant-scoped** key `tenant.<id>.mcp.servers` |
| Cron job list | Global | **tenant_id** column; UI list filtered |
| `_active_tasks` | Process memory | Still process-local; persist via TaskStore for history |
| KB crawl jobs | Process + ConfigStore | Durable ConfigStore (`stores/kb_jobs.py`) |
| Knowledge libraries | Global | **tenant_id** column + list/get filter when multi-user/prod |
| Swarm bus adapters | Process singleton | Fan-out multi-platform; still one process |
| Browser affinity | None | Cookie ``kazma-replica`` (`KAZMA_REPLICA_ID`, LB sticky) |

**Minimum multi-replica stack:** shared Postgres (`KAZMA_DATABASE_URL`),
shared/networked `kazma-data` or object store for workspaces, sticky LB on
cookie `kazma-replica` (or source-IP hash), same `KAZMA_SECRET` / vault key
on all nodes. Set unique `KAZMA_REPLICA_ID` per process.

### Host code_exec sandbox

| Mode | Behavior |
|------|----------|
| Production or multi-user | **No host-local** `python_exec` — Docker required |
| `KAZMA_CODE_EXEC_DOCKER=force` | Same |
| Lab escape | `KAZMA_CODE_EXEC_ALLOW_LOCAL=1` (not recommended) |

## Discord / Slack vs Telegram

| Feature | Telegram | Discord | Slack |
|---------|----------|---------|-------|
| Text chat | Full | Full | Full |
| Typing indicator | Full | Yes | Yes |
| Voice STT/TTS | Deep path | **Same depth** via `voice_helpers` + `discord_stt` / `slack_stt` |
| Graph HITL buttons | `telegram_keyboards` | `discord_keyboards` | `slack_blocks` |
| Swarm HITL buttons | Inline keyboard | Components v2 | Block Kit |
| Shared callback IDs | `platform_callbacks` | same schemes | same schemes |
| Modular parse/send | `telegram_parse/send` | `discord_parse/send` | `slack_parse/send` |
| Shared multi-replica approvals | Yes | Yes | Yes |
| Message reactions | setMessageReaction | `set_reaction` API | `set_reaction` API |

Layout (2026-07 UX modules):

```
adapters/
  platform_callbacks.py   # hitl: / swarm_ / model_ schemes
  platform_keyboards.py   # Discord components + Slack blocks
  telegram_{keyboards,callbacks,parse,send,stt}.py
  discord_{keyboards,callbacks,parse,send}.py
  slack_{blocks,callbacks,parse,send}.py
  voice_helpers.py
```

## Related

- [Postgres & SaaS](postgres-and-saas)
- [Production checklist](production-checklist)
- [Security hardening](../security/hardening-guide)
