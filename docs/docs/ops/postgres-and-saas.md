---
id: postgres-and-saas
title: Postgres & SaaS Cutover
sidebar_label: Postgres & SaaS Cutover
description: Postgres & SaaS Cutover — production ops
---

# SaaS multi-user + full Postgres cutover

## What is on Postgres when `KAZMA_DATABASE_URL` is set

| Store | Table(s) | Module |
|-------|----------|--------|
| Config / settings / platform users | `kazma_settings` | `config_store.py` |
| Chat sessions | `kazma_chat_sessions` | `session_manager.py` |
| Swarm tasks + metrics | `kazma_swarm_tasks`, `kazma_swarm_worker_metrics` | `task_store.py` |
| LangGraph checkpoints | LangGraph internal schema | `AsyncPostgresSaver` via `create_checkpointer` / `agent_runner` |
| Web sessions | still ConfigStore keys (also Postgres via settings) | `web_sessions.py` |

SQLite remains the default when no database URL is set (tests, local single-node).

## Cutover procedure

1. Install extras: `pip install -e ".[postgres]"`
2. Start Postgres:
   ```bash
   docker compose -f docker-compose.postgres.yml up -d db
   ```
3. Migrate **all** stores:
   ```bash
   export KAZMA_DATABASE_URL=postgresql://kazma:PASSWORD@localhost:5432/kazma
   python scripts/migrate_sqlite_to_postgres.py --data-dir kazma-data
   ```
4. Run Kazma with the same URL (compose sets it automatically).
5. Smoke:
   - Login (user / secret / OIDC)
   - Chat history across restart
   - Swarm task list
   - Settings persist

## Multi-user UI

- `/login` — User · Secret · SSO  
- Settings → Account — users + tenants (admin)  
- Header — role badge + logout  

Create admin:

```python
from kazma_core.security.platform_rbac import create_local_user
create_local_user("admin", "long-password-here", role="admin")
```

## Env checklist

```bash
KAZMA_DATABASE_URL=postgresql://…
KAZMA_DB_BACKEND=postgres          # optional force
KAZMA_PRODUCTION=1
KAZMA_VAULT_KEY=…
KAZMA_SECRET=…
KAZMA_PUBLIC_URL=https://…
# OIDC optional
KAZMA_OIDC_ISSUER=…
KAZMA_OIDC_CLIENT_ID=…
KAZMA_OIDC_CLIENT_SECRET=…
```

## Backup & disaster recovery

Kazma backs up its own Postgres tables automatically (added after the
2026-08-14 incident in which another app dropped Kazma's tables from a
shared database):

- **Nightly, automatic:** the 24h backup/export loop dumps exactly the
  tables in `kazma_core.db.pg_backup.KAZMA_PG_TABLES` to
  `{kazma-data}/backups/pg/pg_shared_<epoch>.dump` (custom `-Fc` format,
  atomic write, magic-validated, retention default 7 →
  `backups.pg.retention` / `KAZMA_PG_BACKUP_RETENTION`). First dump ~2 min
  after boot.
- **Manual dump now:** `python scripts/pg_backup.py backup`
- **Restore:** `python scripts/pg_backup.py restore --latest`
  (`--file <name>`, `--dry-run`, `list`). Restores only Kazma's own tables —
  a foreign app sharing the database is never touched.
- **Boot guard:** if a required table is missing at boot, the server logs a
  CRITICAL with the restore command instead of limping along with
  `UndefinedTable` errors.
- Kill-switch: `KAZMA_PG_BACKUP_ENABLED=0` (or `backups.pg.enabled=false`).
- The dump is table-filtered on purpose — never a whole-DB dump — so no
  foreign app's data leaks into Kazma's backups.

## DR

Use `docs/ops/DISASTER_RECOVERY.md` plus `pg_dump` for Postgres. After restore, secrets must match (`KAZMA_VAULT_KEY`, `KAZMA_SECRET`).

