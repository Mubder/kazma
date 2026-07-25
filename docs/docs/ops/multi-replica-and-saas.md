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

## Multi-tenant isolation

| Layer | Status |
|-------|--------|
| Tenant ContextVar | `tenant_context` + middleware |
| Client `X-Tenant-ID` | **Ignored** when production **or** multi-user; JWT / principal only |
| Opaque sessions | Forced when multi-user |
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
| Circuit breakers | Process memory | Still process-local (acceptable; probes reset on restart) |
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
| Voice STT/TTS | Native deep path | `voice_helpers` | `voice_helpers` |
| Swarm HITL buttons | Inline keyboard | Components v2 | Block Kit |
| Shared multi-replica approvals | Yes | Yes | Yes |
| Message reactions | setMessageReaction | `set_reaction` API | Partial (reactions API not first-class) |
| Modular keyboards/parse | Dedicated modules | In-adapter | In-adapter |

Telegram remains the richest UX surface; Discord/Slack are production-capable
for text + HITL + voice helpers. Further parity is incremental adapter work,
not a core gap.

## Related

- [Postgres & SaaS](postgres-and-saas)
- [Production checklist](production-checklist)
- [Security hardening](../security/hardening-guide)
