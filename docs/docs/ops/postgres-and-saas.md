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

## Postgres as an OS service

A database in a Docker container lives and dies with Docker Desktop, a
program that starts only when someone signs in. On 4 November 2026 an update
tool stopped Docker Desktop and Kazma's database was gone for eleven hours;
after a power cut with nobody signed in it would never have started at all.
A database installed as an OS service starts at boot and is restarted by the
OS when it dies.

`kazma setup postgres` does it on every OS Kazma supports, with the OS's own
package manager:

| OS | Installed with | Started by | With nobody signed in |
|---|---|---|---|
| Windows 10/11 | `winget` (`PostgreSQL.PostgreSQL.18`, the EDB installer, unattended) | the Windows service `postgresql-x64-18`, start type Automatic, restarted on failure | yes |
| macOS | Homebrew `postgresql@18` | `sudo brew services start postgresql@18` (a LaunchDaemon) | yes, unless FileVault is on (see below) |
| Debian / Ubuntu | the PostgreSQL project's apt repository, `postgresql-18` | systemd, `Restart=on-failure` | yes |
| Fedora / RHEL | the PostgreSQL project's dnf repository, `postgresql18-server` | systemd, `Restart=on-failure` | yes |
| WSL 2 | as Debian/Ubuntu, inside the distro Kazma runs in, with `systemd=true` in `/etc/wsl.conf` | systemd in the distro (the distro itself is started at boot by the Windows task that runs Kazma) | while that task runs |

What it does, in order (it prints the plan and asks first; `-y` skips the
question, `--dry-run` prints the commands, `--check` only reports):

1. Installs Kazma's own Postgres driver (the `kazma[postgres]` extra: psycopg)
   into the Python environment Kazma runs in, when it is not there. Without it
   Kazma has an address and no way to reach it, and the server waits at
   startup saying the driver is not installed. A second run installs it too.
   If it does not install, nothing after it is done.
2. Installs Postgres 18 as a service that starts at boot and restarts when it
   fails, listening on this machine only (`listen_addresses = 'localhost'`),
   with `scram-sha-256` sign-in. (An install made before 13 November 2026 runs
   Postgres 16; see "Moving to a newer Postgres" below.)
3. Creates the `kazma` database and the `kazma_app` role (not a superuser,
   `deploy/postgres/kazma_app_role.sql`).
4. Turns on WAL archiving (below).
5. Writes `KAZMA_DATABASE_URL` into the install's `.env` (never printed; the
   file restricted to your account), and signs in once as Kazma will.
6. Records what it did in `kazma-data/setup_record.json`: `kazma doctor`
   checks the service from it, and the guard watches the service (it starts a
   service that stopped once per outage, and pages once if it stays down).

The superuser's password is kept in `<install>/.kazma/postgres_superuser.pass`
(your account only); no password is put on a command line, except the Windows
installer's throwaway one, which the next step replaces. A second run checks
and repairs, and never reinstalls: it repairs the Postgres the record names
(16 on an older install), never installs another one beside it. Run it from
an elevated terminal on Windows.

**Moving to a newer Postgres.** `kazma doctor` says when the database runs an
older major than Kazma installs now (16 before 18). One command moves it,
with Kazma running:

```bash
kazma setup postgres --upgrade
```

A Postgres major cannot open another major's data folder, so the new one is
a second server, filled with a copy:

1. It installs the new major beside the old one, on a spare port (55432 or
   the next free one), with Kazma's role, the password Kazma already uses and
   its own WAL archive folder.
2. Once no chat turn is running, it stops Kazma through its guard, so nothing
   is written while the data is copied.
3. It copies Kazma's database with the new major's `pg_dump` and
   `pg_restore` (as `kazma_app`, in one transaction), then compares the row
   count of every table and the value of every sequence.
4. It switches the servers: the new one onto the old port, the old one
   stopped and taken off the boot list. `.env` and every other address stay
   as they are.
5. It records the new server (the old one under `previous`), starts Kazma,
   sees it signed in, and takes the new server's first base backup.

The old server is kept, stopped, with its data, archive and base backups:
the way back, and the server a restore to a time before the move reads.
Remove it with its own uninstaller once you are sure. Every stage is written
to `kazma-data/setup_record.json` first. A run that stops anywhere is safe to run again:
before the switch Kazma still uses the old server and the new one holds only
a copy (replaced only while Kazma has never started on it); a switch that did
not finish is undone first (the old server back on its port and on the boot
list). A copy that is not whole, or a new server that does not answer on the
old port, puts Kazma back on the old server, unchanged. The guard's database
watch leaves the services alone while a switch is under way, for at most 15
minutes. Drilled on Windows; the Debian, Fedora and macOS steps are tested
against their package managers' commands, not yet on a machine.

**Moving off the Docker container.** One command switches Kazma, with Kazma
running:

```bash
kazma setup postgres --port 5432 --move-from-container kazma-db-win
```

It sets up the new server, then, once no chat turn is running, stops Kazma
through its guard, so nothing is written to the container while it is
copied. It copies the container's database in (`pg_dump` inside the
container, `pg_restore` as `kazma_app`), compares the row count of every
Kazma table, and only then writes the new address into `.env`. Kazma is then
started on the new database; once it is seen signed in there, the container
is stopped and kept (it does not start again by itself). The container is the
way back (start it, put the old address back); remove it with `docker rm`
once you are sure. A Kazma running with no guard to stop it is refused before
anything is done: stop it yourself, then run the command.

Run again, the command replaces an earlier copy on the new server only if
Kazma never started on it (its last start there is not newer than the
container's) and nothing is connected to it; a database Kazma has started on
is never written over. On a healthy service whose address `.env` already
names, a second run is the move only and needs no elevated terminal. The
memory mirror keeps its own address (`memory.backends.state.url`, and
`memory.backends.vector.url`): the move points each one that names the
container at the new server, while Kazma is stopped. One stored in plain text
instead of the vault is named, for you to change in Settings.

**Disks that ask for a password at boot.** A Mac with FileVault on, a Windows
PC whose BitLocker asks for a PIN, and a Linux disk encrypted with LUKS that
does not unlock by TPM all stop before the OS starts, so nothing starts until
someone types it. A machine that must come back by itself after a power cut
needs unlock by TPM (Windows, Linux), or FileVault off (or a planned restart
with `fdesetup authrestart`) on a Mac.

## WAL archiving and point-in-time restore

The 6-hourly dump below can lose up to six hours. With write-ahead-log
archiving every change is kept, and a restore can stop at any minute.
`kazma setup postgres` turns it on: `archive_mode = on`, a segment at least
every 5 minutes while anything is written (`archive_timeout = 300`), each
finished segment copied into the archive folder (Windows
`C:\ProgramData\Kazma\pg_wal_archive-18`, Debian
`/var/lib/postgresql/18/wal_archive`, Fedora `/var/lib/pgsql/18/wal_archive`,
macOS `kazma-data/backups/pg/wal-18`; an install made before 18 keeps the
folder its record names). Each major has its own archive, base backups and
offsite names: a new major numbers its segments from the start again, so
beside an older major's base backup the prune would take the new major's
segments for old ones.

The maintenance sweep "WAL archive" reads the server's own numbers
(`pg_stat_archiver`): a failing archiver, or no segment archived for 15
minutes, pages `backup.wal_archive_stale`. It takes a base backup every week
(`pg_basebackup` into `kazma-data/backups/pg/base-18/`, two kept; `base/`
on an install made before 18), prunes the
archived WAL the older one no longer needs, and sends new segments and base
backups to your offsite backup provider (the one the universal backup uses).
`kazma doctor` says whether the archive is current.

To restore to a time, into a new folder (the live database is never touched):

```bash
kazma restore postgres --to "2026-11-20 14:05" --into D:\pg-restore
```

It lays out the newest base backup, sets it to replay the archived WAL up to
that time, and prints the steps left: start it on a spare port, check it,
then switch. A time before a move to a newer Postgres is restored from the
server that move replaced (its archive and base backups were kept), and is
started with that major's own programs; the command says which. The restored
copy never archives (`archive_mode = 'off'`), so it cannot write into the
live archive.

Check a restore by connecting to it, not by `pg_ctl`'s answer: `pg_ctl
start -w` can say a server started whose recovery failed a moment later. A
failed one says why in its log (`recovery ended before configured recovery
target was reached` means a segment it needed was not found). Before
2026-11-14 every point-in-time restore on Windows failed that way: the copy
command lost its backslashes. Restore again with an updated Kazma.

To prove your backups work without touching anything, restore to a time a
few minutes ago (the newest archived segment is at most 5 minutes old),
start the copy on a spare port and compare its tables with the live
database; then stop it and delete the folder. Done on the owner's install
on 2026-11-14, the copy matched live table for table (24 tables, 99,935
rows).

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

