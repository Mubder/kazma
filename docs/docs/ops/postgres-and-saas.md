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

### Memory search (pgvector)

Setting `KAZMA_DATABASE_URL` also promotes **V2 dense recall** from sqlite-vec
to **pgvector** in the same database (table `kazma_memory` — the
`memory.backends.vector.collection` setting — cosine, HNSW index), **if that
Postgres can hold vectors**. The cognitive store (`memory_state.db`) stays
SQLite until you set `KAZMA_MEMORY_STATE_ROLE=primary`.

The server must ship the extension. `postgres:16-alpine` — the image in
`docker-compose.postgres.yml` and `docker-compose.ha.yml` — does **not**; use
`pgvector/pgvector:pg16` (or a managed Postgres that offers pgvector).

1. Kazma creates the extension and table on first use when its role may.
   Otherwise, as a superuser: `CREATE EXTENSION IF NOT EXISTS vector;` in the
   Kazma database, and grant the role `CREATE` on its schema.
2. Kazma checks at boot and on every memory search (cached one minute):
   - pgvector picked **automatically** (no explicit choice) on a Postgres
     without the extension: one INFO line, memory stays on sqlite-vec, and
     Settings → Memory shows *Vector: full (local)* with the reason.
   - pgvector **chosen** in Settings but unusable (no extension, role may not
     create it, table of another vector size, server down): one WARNING
     naming the fix, and the Settings banner says the same.
   - **Test vector** in Settings → Memory runs the same check on demand. When
     pgvector was picked automatically and the extension is missing, it tests
     the store actually in use (local sqlite-vec) and reports *Vector OK*
     with a note saying why pgvector is not used; a pgvector you chose that
     cannot work still reports *Vector failed* with the fix.
3. Rebuild embeddings once if you already have history:
   Settings → Memory → Rebuild embeddings (upserts into pgvector).
4. Changing embedder size: the table is sized by the embedder and never
   resized. Point `memory.backends.vector.collection` at a new name, then
   rebuild embeddings.
5. Kill-switch: `KAZMA_PGVECTOR=0` (sqlite-vec on purpose, no check). Explicit
   Qdrant in Settings is never overridden.

**Moving an existing `postgres:16-alpine` database to `pgvector/pgvector:pg16`:**
dump and restore, do not just swap the image on the same volume. Alpine uses
musl and the pgvector image glibc; text indexes built under one collation are
out of order under the other. With Kazma stopped, dump the whole database
from the old container (`pg_dump -Fc`), restore it with `pg_restore` into a
pgvector container on a **new** volume, point `KAZMA_DATABASE_URL` at it and
start Kazma. Keep the old volume until the new one has run for a while.
(`scripts/pg_backup.py` dumps only Kazma's own tables — right for a shared
database, not for moving a whole one.)

Postgres-primary recall (`state.role=primary`) is **ILIKE + pgvector RRF**,
not ILIKE-only.

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

