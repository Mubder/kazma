---
id: postgres-and-saas
title: Postgres & SaaS Cutover
sidebar_label: Postgres & SaaS Cutover
description: Postgres & SaaS Cutover — production ops
---

# SaaS multi-user + full Postgres cutover

> This page is published at [kazma.ai/docs/ops/postgres-and-saas/](https://kazma.ai/docs/ops/postgres-and-saas/) ([Arabic](https://kazma.ai/ar/docs/ops/postgres-and-saas/)).

## What is on Postgres when `KAZMA_DATABASE_URL` is set

| Store | Table(s) | Module |
|-------|----------|--------|
| Config / settings / platform users | `kazma_settings` | `config_store.py` |
| Chat sessions | `kazma_chat_sessions` | `session_manager.py` |
| Swarm tasks + metrics | `kazma_swarm_tasks`, `kazma_swarm_worker_metrics` | `task_store.py` |
| LangGraph checkpoints | LangGraph internal schema | `AsyncPostgresSaver`, opened by `kazma_core/checkpoints_pg.py` (agent and gateway). Pruned since 2026-09-27: each chat keeps its newest 200 checkpoints, one idle for `checkpoints.retention_days` (Settings → System → Chat step history, default 30; 0 keeps all) its newest 10 |
| Web sessions | still ConfigStore keys (also Postgres via settings) | `web_sessions.py` |

SQLite remains the default when no database URL is set (tests, local single-node).

### Memory search (pgvector)

Setting `KAZMA_DATABASE_URL` also promotes **V2 dense recall** from sqlite-vec
to **pgvector** in the same database (table `kazma_memory` — the
`memory.backends.vector.collection` setting — cosine, HNSW index), **if that
Postgres can hold vectors**. The cognitive store (`memory_state.db`) stays
SQLite until you set `KAZMA_MEMORY_STATE_ROLE=primary`.

Neither pgvector nor Qdrant is needed: without them memory's meaning search
is exact over every memory, locally (sqlite-vec or NumPy, about 1 ms per
1,000 memories).

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

## A database role that is not a superuser

Kazma needs to own its own tables and nothing more. A superuser login can
read and change every database on the server and run programs on the
database host, so every boot warns when Kazma signs in as one
(`[security] Kazma signs in to Postgres as kazma, a superuser`). The Docker
setup's `POSTGRES_USER` is the server's first superuser, which is how most
installs end up that way.

[`deploy/postgres/kazma_app_role.sql`](https://github.com/Mubder/kazma/blob/main/deploy/postgres/kazma_app_role.sql)
makes the role `kazma_app` (no superuser, may not create roles, may create
databases for the weekly restore rehearsal's scratch copy) and gives it the
database, its schema and every table, sequence and function. It runs in one
transaction and can be run again. Postgres refuses the shorter
`REASSIGN OWNED BY <the first superuser>`, so the script names each object.
`tests/test_pg_app_role.py` runs Kazma's own setup of every Postgres store,
a dump and the restore rehearsal as the new role.

1. Run the script as the superuser, in Kazma's database:

   ```bash
   docker exec -i kazma-db-win psql -U kazma -d kazma -v ON_ERROR_STOP=1 < deploy/postgres/kazma_app_role.sql
   ```

   It ends with `COMMIT`. If it stops on `lock timeout`, a long query held a
   table: run it again when Kazma is idle. Kazma keeps working throughout:
   it still signs in as the superuser until the reload.
2. Make a password of letters, digits, `-` and `_` (anything else must be
   percent-encoded in an address):

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(24))"
   ```

   and put the new address in `.env`, keeping your host, port and database
   name: `KAZMA_DATABASE_URL=postgresql://kazma_app:<password>@127.0.0.1:5433/kazma`.
3. Give the role that same password, read from `.env` so it is never typed,
   shown or kept in your shell history (PowerShell):

   ```powershell
   $line = (Get-Content <install>\.env | Where-Object { $_ -like 'KAZMA_DATABASE_URL=*' }) -replace '^KAZMA_DATABASE_URL=',''; $pw = ([uri]$line.Trim().Trim('"')).UserInfo.Split(':',2)[1]; "ALTER ROLE kazma_app PASSWORD '$pw';" | docker exec -i kazma-db-win psql -U kazma -d kazma -v ON_ERROR_STOP=1
   ```

   It prints `ALTER ROLE`. Do not use psql's `\password` prompt: a password
   pasted into it can arrive empty, and Postgres then clears the role's
   password with only a notice (live, 2026-11-03).
4. Check the sign-in **before** reloading, with the install's own Python
   (`<install>` is the folder Kazma is installed in):

   ```bash
   <install>\.venv\Scripts\python.exe <install>\scripts\check_database_login.py
   ```

   It signs in as Kazma will and prints `OK: Kazma signs in to kazma as
   kazma_app (not a superuser).`, or `NOT OK:` with Postgres's reason. A
   check run with psql inside the database container proves nothing: the
   container lets loopback sign-ins in without a password.
5. If the memory mirror is on (Settings -> Memory -> Memory backends ->
   Shared state set to Postgres), set its **Postgres DSN** to the same
   address and press **Save backends**. It keeps its own copy of the
   address.
6. Reload: `python scripts/service/kazma_guard.py --reload --when-idle`.
7. Check: the boot log no longer has the superuser warning, and
   `/health/ready` answers 200. If the server waits on its database
   instead, its log names Postgres's reason (`[PostgresPool] Postgres
   refused the password for kazma_app ...`).

To undo, put the old address back in `.env` (and the memory mirror) and
reload; the superuser can still use every table. Leave the `kazma`
superuser in place: it is how you administer the server.

## Backup & disaster recovery

Kazma backs up its own Postgres tables automatically (added after the
2026-08-14 incident in which another app dropped Kazma's tables from a
shared database):

- **Every 6 hours, automatic:** the backup/export loop dumps exactly the
  tables in `kazma_core.db.pg_backup.KAZMA_PG_TABLES` to
  `{kazma-data}/backups/pg/pg_shared_<epoch>.dump` (custom `-Fc` format,
  atomic write, magic-validated; 3 local dumps kept, restic keeps the
  history → `backups.pg.retention` / `KAZMA_PG_BACKUP_RETENTION`). First dump
  ~2 min after boot. The weekly deep drill restores the newest one into a
  scratch database, checks it and drops it (on by default since 2026-09-27;
  [Disaster recovery](disaster-recovery)).
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

