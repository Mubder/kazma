<!-- AGENTS.md, piece 21-postgres-and-backups. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 21. Postgres Shared-State Backup & Schema Assurance (`kazma-core/kazma_core/db/pg_backup.py`)

Scheduled `pg_dump` backups + boot-time schema verification for the tables
Kazma owns in Postgres. Built after the 2026-08-14 incident where a second
app pointed at the shared `kazma` database dropped Kazma's tables
(checkpoints / settings / chat sessions / document jobs) and there was no
scheduled PG backup to restore from.

**A. `KAZMA_PG_TABLES` is the single SoT of which tables Kazma owns.**
The list lives in `kazma_core/db/pg_backup.py` (not here — this file will
drift). It includes LangGraph `checkpoints*`, shared-state
(`kazma_settings`, `kazma_chat_sessions`, `kazma_swarm_tasks`,
`kazma_swarm_worker_metrics`, `kazma_platform_users`, `kazma_web_sessions`),
document jobs (`document_jobs`, `document_job_events`), document intake
ownership and admission (`document_intake_reservations`,
`document_intake_mutex`, §19 B2), **and** the document catalog from H-13
(`documents`, `document_blobs`, `document_versions`, `document_artifacts`,
`document_acl`, `document_tombstones`, `document_chunks`,
`document_audit_events`), and the memory mirror (`kazma_episodes`,
`kazma_beliefs`, §15K: recall's store when Postgres is the primary, and the
only copy of what other replicas wrote; boot requires them only when memory's
state backend is Postgres, `pg_backup._required_pg_tables`). A new shared-state PG table MUST be added to that
Python list or it silently stops being backed up
(`tests/test_pg_backup.py::test_kazma_pg_tables_covers_document_ddl` reads the
document stores' DDL). This dump does not carry the document store's content
files; a document store on Postgres is backed up as one point in time with its
files by `documents/backup_pg.py` (§19 F2).
The dump is deliberately table-filtered — never a whole-DB dump — so a
shared database neither leaks foreign-app data into Kazma's backups nor
restores over another app's tables. `hitl_gates.db` is SQLite (single-process
like the turn journal), not this list.

**B. Dump pipeline (6h loop, `worker_bootstrap.py`).**
The backup/export loop enqueues `native_pg_backup` when
`pg_backup_enabled()` (live-checked: Postgres backend + `backups.pg.enabled`
config + `KAZMA_PG_BACKUP_ENABLED` env kill-switch). The handler
(`_handle_native_pg_backup`) runs `perform_pg_backup()` in a worker thread:
dump via `migration/pg_bridge.dump_database(tables=KAZMA_PG_TABLES)` to a
`.tmp` file, validate the `PGDMP` magic, atomically rename into
`{kazma-data}/backups/pg/pg_shared_<epoch>.dump`, then prune to
`backups.pg.retention` (default **3** local staging dumps; restic keeps
history. Env `KAZMA_PG_BACKUP_RETENTION`).
Failures return False so the durable queue retries (max 3) then
dead-letters; a failed dump never leaves a valid-looking file behind.

**C. Boot-time schema verification (fail-open, never blocks boot).**
`app.py:_on_startup` calls `verify_required_pg_tables(pool)` when PG is
active. Missing tables → CRITICAL log naming the missing tables + the
restore command (`python scripts/pg_backup.py restore --latest`); the
server still boots (SQLite-side features work) but chat/settings/document
history is broken until restored. A pool failure returns None = "unknown",
not "all present".
**Dropped, or added by this build?** (2026-10-14.) The check runs before
the stores start, on purpose (a store recreates a dropped table empty and
would hide the drop), so a table a newer build adds is missing too until its
store creates it: the first boot of the Documents hardening told the owner to
restore a backup over two tables that had never existed. Every check records
the Kazma tables it finds in `<data dir>/pg_tables_seen.json` (beside the
install, so a lost Postgres cannot lose the record; `machine`).
`report_missing_tables` calls a missing table on the record dropped
(CRITICAL, restore command) and one never seen new (INFO; WARNING with no
record yet), and `recheck_new_tables` looks for those again a minute after
start (still missing: WARNING). Gate: `tests/test_pg_tables_seen.py` (the
live boot as the witness, the old rule as the negative control).

**D. Operator CLI (`scripts/pg_backup.py`).**
`backup` (one-shot dump now), `restore --latest|--file <name> [--dry-run]`
(via `pg_bridge.restore_database`, `--clean --if-exists` — only the dumped
tables are touched), `list`. Loads the `.env` python-dotenv finds walking up
from the script's folder (not the CWD); never prints the DSN userinfo.

**E. Config is live-read, never raises** (mirrors `get_hitl_config`):
ConfigStore keys `backups.pg.enabled` / `backups.pg.retention`; env
kill-switch `KAZMA_PG_BACKUP_ENABLED=0`. Tests:
`python -m pytest tests/test_pg_backup.py`.

**E2. The dump must find its tools without PATH's help.** `pg_dump` usually
runs inside the DB container through `docker exec`, and a Docker Desktop
update dropped the CLI from the system PATH on 2026-09-25 — every dump after
it failed while Docker and the container were fine. The docker CLI is found
by `kazma_core.docker_cli.find_docker_cli()` (`KAZMA_DOCKER_BIN`, PATH,
Docker's install folders), never `shutil.which("docker")` alone
(`tests/test_docker_cli_discovery.py` gates it). The tools of the Postgres
`kazma setup postgres` installed (its record's `bin_dir`,
`pg_bridge._installed_tool`) come after PATH and before any container: after
the move to the service (2026-11-06) `KAZMA_DB_CONTAINER` still named the
old container, and a dump through it read that container's stale database.
Boot checks the tool
(`pg_dump_tool_problem`, ops alert `backup.pg_tools`), and a failed dump's
alert carries its reason (`last_pg_backup_failure`), redacted. Putting the
folder back on PATH then needs only `kazma_guard.py --reload`: the server
adopts PATH entries the OS settings gained since the guard started (§38).

**E3. Schema setup takes no table lock it does not need**
(`kazma_core/db/pg_schema.py`, 2026-10-13). Postgres takes `ALTER TABLE ...
ADD COLUMN IF NOT EXISTS`'s ACCESS EXCLUSIVE lock -- and `CREATE INDEX IF NOT
EXISTS`'s SHARE lock -- BEFORE it looks for the column or index (measured: the
ALTER waits behind `pg_dump`'s read locks, both wait behind any open writer).
Every store sent them at every start, so starts deadlocked each other (a
4-writer restore drill, in the intake store's constructor), and a start during
a dump waited for the whole dump with every query on the table queued behind
it -- the core schema's 5 s timeout then failed the pool. `ensure_schema`
takes one transaction-scoped advisory lock every Kazma schema setup shares,
and sends a `CREATE TABLE`, `CREATE INDEX` or `ADD COLUMN` only when the
catalog says it is missing; any other `ALTER TABLE` is refused. Every
Postgres DDL site uses it: the core schema, the swarm task store, the memory
mirror and pgvector table, and the document jobs, catalog and intake stores.
Gate: `tests/test_pg_schema_setup.py` (no `ADD COLUMN IF NOT EXISTS` sent
outside it, the old shapes as control; on real Postgres each store's setup
completes under a held dump lock and an open writer, and fresh starts at the
same moment both succeed -- the direct statements wait and collide). A table
`ensure_schema` makes gets its second-lock rule in the same transaction
(`db/row_security.apply_made_tables`, Spaces S4-01 H3), only once the rules
are in place in this process and in a database that holds them (§70 (1)).

**F. Universal backup — "never left anything behind"**
(`kazma_core/backup/universal.py`). One unified backup that backs up
literally everything: every `*.db` in `kazma-data/` (WAL-safe via
`sqlite3.backup()` API), every non-DB dir (attachments, document-store,
workspace, exports, vectors — via `_robust_copytree` that skips ephemeral
files like LibreOffice cache). It does **not** run a second `pg_dump`.
It **records whether `native_pg_backup` is fresh** (`_pg_dump_state`); a
stale/missing dump is a failed item in the manifest, not `"ok": true`.
Produces `manifest.json` + retention-capped (default 7). Wired into the
6h `native_backup` handler (auto) +
`POST /api/backup/now` (manual, background task) + Settings → Backup tab
(animated progress bar polling `GET /api/backup/status` every 2s).
Delete/archive/download: `DELETE /api/backup/{name}`,
`POST …/archive` (zip), `GET …/download`. The per-file copy MUST be robust
(`_robust_copytree`) — `shutil.copytree` aborts on one vanishing file and
loses the entire `document-store`. Do NOT revert to `copytree`.
**Old offsite copies are deleted after each successful upload** (2026-11-03,
the owner had deleted eleven 344 MB zips by hand). `_offsite_sync` runs
`_prune_offsite` on the path that made the copy -- the native provider's
`list_backups` / `delete_backup` (every provider in `cloud_sync.py`, each
page of a listing, a bad name refused before any request) or `rclone lsf` /
`rclone purge --drive-use-trash=false` (a folder per copy) -- keeping the
newest `backups.offsite.keep` (`offsite_keep`, default 7; `0` keeps every
copy; Settings -> Backup -> Keep cloud copies). Names are the backup's
epoch (`_NATIVE_COPY` / `_RCLONE_COPY`); a listing that fails, is not whole
or lacks the copy just uploaded deletes nothing (`_old_copies` raises); a
copy already gone is done; deletes stop at `_PRUNE_BUDGET_S` and the rest
wait for the next run. The answer joins the offsite result (`pruned`,
`prune_error`, `prune_left`), never fails the backup, and a `prune_error`
raises `backup.offsite_prune_failed` (warn) before the fallback check in
`_alert_on_backup_gaps`. Deletes are for good, never to a trash. Gates:
`tests/test_offsite_retention.py` (a trusting rule deleting from a stale
listing as the negative control), the provider cases in
`tests/test_cloud_sync.py`.
**A copy in a folder the owner chooses** (2026-11-20, the owner's item;
`kazma_core/backup/folder_copy.py`, Settings -> Backup -> "A copy on another
drive"). Another drive, a USB disk or a network share holds a restic
repository (`<folder>/kazma-restic`, `restic_repo.repo_paths()["folder"]`,
also in `configured_repos`, so the Postgres dump's copy goes there too):
every universal backup is copied into it, encrypted with Kazma's restic
passphrase, its repository made on the first copy
(`restic_repo._folder_repo_made`). Settings `backups.folder.enabled`,
`.path` (absolute, never inside the data folder: `folder_copy.path_problem`)
and `.keep` (the newest N of each kind, `--keep-last`; 0 keeps every copy;
the nightly restic maintenance applies it, `folder_copy.keep_policy`), each
with a `settings_validation` rule. A folder that is not there is never a
failed backup: `note_presence` says it once (`backup.folder_missing`, the
runtime mark `system.backups.folder_missing_since`) and its return once
(`backup.folder_back`). Routes: GET/PUT `/api/settings/backup/folder`, POST
`.../test` (writes and removes one probe file; free space, same disk as the
data, restic, passphrase) and POST `.../browse` (the page's folder picker;
never a network path: listing one connects to its host). Gates:
`tests/test_backup_folder_copy.py` (real restic; the copy without making the
repository, the notice without its mark and the maintenance with the default
policy as negative controls), `tests/e2e/test_backup_folder_browser.py` (by the
sidebar, EN/AR, the picker, a refused save, Test).
**The folder's rules hold for every name a path can have** (2026-11-21, the
SonarCloud review of the Test). `folder_copy.path_problem` answers one of
`folder_copy.PATH_PROBLEMS`, and the save refuses every one of them (it
named two and kept a path with any other): a device path (`\\.\`, `\\?\`,
`\??\`: a named pipe, a raw disk) is refused on its text before anything
opens it (`remote_paths.device_path_reason`; a plain `\\server\share` is a
folder, choosing one is the feature); "inside the data folder" is judged by
the real path AND by each folder's identity (`folder_copy._identity`: volume
and file id), since `\\?\C:\...` and a loopback share
(`\\localhost\C$\...`) resolve to themselves; the walk up stops at the first
folder that does not answer (`folder_copy._not_made_yet`: an offline share
waits out a network timeout per question); "the same disk" is the volume
(`st_dev`), never the drive letter. Gate:
`tests/test_backup_folder_path_rules.py` (the Test, the save and a stored
path; every code worded by the page in both languages; the old rule, the old
save and the drive-letter check as negative controls; the loopback share
where this account can reach it).

**Every copy of a live SQLite database is one step, through one helper**
(`kazma_core.db.sqlite_snapshot.snapshot_copy`, 2026-10-13, Memory audit M19):
the universal backup, the native memory backup (`memory/backup.backup_one`,
also the migration importer's pre-swap copy), the embedding rebuild's
pre-copy, the memory restore route and the X shadow script. A batched copy
(`pages=N`) restarts whenever the source is written: the native memory
backup -- the declared restore source -- finished only in a quiet window (778
restarts in two seconds). Gate: `tests/test_sqlite_snapshot.py` (the copy
finishes under a writer every 2 ms; `pages=100` as the negative control; no
`.backup(` call outside the helper).

**G. Postgres as an OS service** (resilience plan A1,
`kazma_core/postgres_service.py`, `kazma_cli/setup.py`). A database in a
container dies with Docker Desktop, which starts only when someone signs in
(live 2026-11-04: eleven hours). `kazma setup postgres` installs Postgres 16
with the OS's package manager (winget, Homebrew, apt, dnf; WSL as its distro)
as a service that starts at boot and restarts on failure, `listen_addresses =
'localhost'`, creates `kazma` and the `kazma_app` role (`kazma_app_role.sql`,
which also makes `kazma_space`, the second lock's role: §70 (1)),
turns on WAL archiving, writes `KAZMA_DATABASE_URL` into `.env` (never
printed, restricted) and records it in `<data dir>/setup_record.json`.
**A client library Windows refuses gives way to Postgres's own** (live
2026-11-26 11:30 UTC: an Application Control policy began refusing
psycopg_binary's `pq` module; the next reload loaded no libpq and Kazma
started read-only with the database up): `kazma_core`'s import runs
`libpq_reach.reach_libpq()` before anything imports psycopg (no Kazma module
imports it at its top). When Windows refuses the bundled library by policy
(WinError 5, 1260, 4551), the newest installed PostgreSQL's `bin` (its signed
`libpq.dll`) goes at the end of the process's PATH and psycopg's pure-Python
wrapper uses it; said once at WARNING. An allowed bundle, or a libpq already
on PATH, changes nothing. Gate: `tests/test_libpq_reach.py` (an allowed
library as the control, and no top-level psycopg import).

**Kazma's own driver first** (the drill VM, 2026-11-06: the address went into
an environment without psycopg, and the server waited ten minutes on a
database that was fine): when `postgres_pool.driver_missing()` says so, the
plan's first step installs the `postgres` extra (`setup._extra_requirements`,
read from the package's metadata) with `uv pip install --python
<sys.executable>`, which adds and never removes; a second run on a set-up
service does it too, and a failed install writes no `.env`. `--check` reports
the driver. A missing driver is `PostgresDriverMissing`: the pool raises it
at once, says it once at ERROR, its `reason` reaches the boot wait, the guard
and the settings error (`DRIVER_MISSING`, what to run), and it is never an
outage (`driver_missing` on the error, read first by
`availability._outage_one`); a driver installed while the boot waits is found
at the next try (import caches cleared). Gates:
`tests/test_postgres_driver_missing.py`, the driver cases in
`tests/test_setup_postgres.py` (the plan without the step as the negative
control). No
password on a command line (the Windows installer's throwaway one aside,
replaced at once through psql's stdin); the superuser's is kept in
`<install>/.kazma/postgres_superuser.pass` (a Kazma secret file). `--check`
and `--dry-run` change nothing; a second run repairs and never reinstalls;
`--move-from-container NAME` switches Kazma in ONE run (`setup._MoveHold`):
Kazma stopped through its guard once idle, the container's data copied in
(`backup/pg_move.py`: dump inside the container, `pg_restore` as
`kazma_app`, every Kazma table's row count compared), `.env` written, the
memory backends' own addresses that name the container repointed through
the vault (`setup._repoint_memory_backends`: the mirror has no fallback to
`KAZMA_DATABASE_URL`), Kazma started on the new server and seen signed in
there, the container stopped and kept. Live 2026-11-06 the first move copied while Kazma kept writing to
the container and said "reload when idle": the next restart would have
started Kazma on a copy missing everything since. A second run replaces an
earlier copy only when Kazma never started on it (`pg_move._replace_refusal`:
its `system.lifecycle.last_boot_epoch` not newer than the container's,
nothing connected; `postgres_service.empty_database_steps`, after the dump),
and after a failed copy into an emptied database `.env` names, Kazma stays
stopped. Gates: `tests/test_postgres_move_switch.py` (the old setup fails
all 16), and on real Postgres `tests/test_resilience_postgres.py`.
`kazma doctor` checks the service from the record; the guard watches it
(`DatabaseServiceWatch`: starts a stopped service once per outage, pages once
at five minutes, once on recovery). A state `sc` refuses ("Access is
denied": a guard without admin rights, the user-level task) is unknown and
the port decides -- read as stopped it paged a running database down; a
refused start is said once with the fix (2026-11-11). An S4U boot task of an
admin account runs with its full token whatever its run level (measured on
the drill VM), so `--install`'s boot task keeps `Highest`. Gates: `tests/test_setup_postgres.py`
(every package manager faked; a password on psql's command line as the
negative control), `tests/test_guard_database_service.py`.

**G2. Postgres 18, and moving an install to it** (2026-11-13,
`kazma_core/postgres_upgrade.py`, `kazma_cli/postgres_upgrade.py`).
`postgres_service.PG_MAJOR` is 18: a new install gets 18, and every name
that held 16 takes the major (`service_name`, `bin_dir_for`, `setup_plan(...,
major=)`, `empty_database_steps`). **A repair keeps the major the record
names**: `_setup_postgres` repairs the record's installed service (16 on an
install made before), never installs 18 beside it, keeps the record's history
fields, and signs in with the stored superuser password (a repair used the
installer's throwaway, which no installer had set). `kazma setup postgres
--upgrade` moves an install: 18 installed beside 16 on a spare port
(`postgres_upgrade._free_port`, from 55432) with the role and the password
Kazma already uses; Kazma held through its guard (`_MoveHold`, its own
reason); `copy_database` (the new major's `pg_dump`/`pg_restore` as
`kazma_app`, one transaction, every public table's count and every sequence
compared; an earlier copy replaced only by `pg_move._replace_refusal`'s
rule); the switch (`switch_steps`: the new server's port written ON DISK,
`postgres_service.port_step` -- never through a connection -- then the new
stopped, the old stopped and off the boot list, the new started on the old
port, checked to answer as 18 and to sign Kazma in); the record (new server,
the old under `previous`), Kazma started and seen signed in, the first base
backup taken. `.env` and the memory backends' addresses do not change.
Every stage is journaled first (`setup_record.json` `postgres_upgrade`);
the record names the server about to be on the port before any server stops,
and the guard's `DatabaseServiceWatch` leaves the service alone while the
journal says `switching` (at most `DBSVC_UPGRADE_HOLD_S` =
`postgres_upgrade.SWITCH_HOLD_S`), so the watch never starts the old one. A
failed copy starts Kazma again on the old server; a failed switch is undone
(`undo_steps`: the new stopped, off the boot list and back on its spare
port; the old back on the boot list and started); a run killed in the switch
is undone by the next run first. Gates: `tests/test_postgres_upgrade.py` (a
fake Windows machine whose services keep their own ports and states: the
order, each failure, a killed switch, the repair rule, each platform's
switch; the run without the undo as the negative control),
`tests/test_postgres_upgrade_real.py` (real 16 and 18 servers, opt-in
`KAZMA_TEST_PG_UPGRADE_BIN`; a partial restore as the control). Drilled end to
end on the Windows drill VM (`scripts/postgres_upgrade_drill.py`: 16 set up by
the code before, seeded, upgraded, 12 checks; passed 2026-11-13,
`docs/audits/evidence/postgres_upgrade_drill_2026_11_13.json`). Its first runs
found three faults, each fixed with a test: the Postgres setup's runner lacked
the winget package-list repair (`setup._do_setup_step`), uv was looked for on
PATH only (`kazma_core/uv_cli.py`, `find_uv`: also `~/.local/bin`, where setup
puts it; a uv environment has no pip), and the finish waited for a Kazma that
was not running.
**Compose deployments** (`kazma_core/postgres_compose_upgrade.py`,
`scripts/pg_compose_upgrade.py`): `docker-compose.postgres.yml` and
`docker-compose.ha.yml` run 18 on a volume of its own (`kazma_pgdata18`,
mounted at `/var/lib/postgresql`, the 18 image's layout), with 16's
`kazma_pgdata` mounted read-only at `/kazma/pg16` only so the db entrypoint
refuses an empty 18 while a 16 database is there (exit 78, naming the
script; `KAZMA_PG_UPGRADING=1` lets the script's own start through): a
`docker compose up` after a pull never starts Kazma on an empty database.
The script dumps from the 16 container, or, when compose already replaced it,
from a temporary 16 server on the 16 volume (`OLD_IMAGES`); keeps the 16
container's definition in a journal beside the compose file (owner only: it
holds the password), because compose finds a service's container by labels
and removes the old one; restores into 18 and compares; any failure removes
the 18 container and its volume and makes 16 again from the kept definition
(`engine_move._create_args`). Gate: `tests/test_postgres_compose_upgrade_real.py`
(a real compose project, opt-in `KAZMA_TEST_PG_COMPOSE_UPGRADE=1`: the refused
`compose up` as the negative control, both sources, a planted restore failure
put back and then moved).

**H. WAL archiving and point-in-time restore** (A2,
`kazma_core/backup/wal_archive.py`). `archive_mode = on`, `archive_timeout =
300`, each segment copied by the OS's own copy then renamed (the server's
service account cannot run Kazma's Python). The maintenance sweep "WAL
archive" reads `pg_stat_archiver` (a failing archiver, or none archived for
15 minutes, pages `backup.wal_archive_stale`), takes a weekly base backup
(`pg_basebackup -Ft -z -X fetch`, two kept, a failed one leaves no folder),
prunes the WAL the older one no longer needs, and sends new segments and base
backups offsite (`pgwal-*`, `pgbase-*`, in order; a failed upload stops the
pass). **Each major has its own archive, base backups and offsite names**
(2026-11-13): `wal_archive.default_archive_dir(platform, major)`,
`default_base_dir(major)`, `offsite_tag(major)`, named by the record
(`base_backup_dir`, `offsite_tag`; empty on an install made before 18, which
keeps its folders). A new major numbers its segments from the start again on
the same timeline: beside 16's base backup the prune took every 18 segment
for older than that backup's start (the shared folder is the negative control
in `tests/test_postgres_upgrade.py`). `kazma restore postgres --to TIME --into DIR` lays the newest base
backup taken before TIME (`wal_archive._base_before`; a time without a zone is
this machine's) into a new folder set to replay to TIME; the live database is
never touched. It took the newest base whatever TIME was, and Postgres cannot
replay a base back to a time before it was taken. A TIME before a move to a
newer major is restored from the server it replaced
(`wal_archive.generation_for`: the record's `previous`, its archive, base
backups and programs). **Every value the restore writes into
`postgresql.auto.conf` goes through `wal_archive._conf_string`** (backslashes
and quotes doubled): the file's reader takes a backslash as an escape, and
written plain the Windows copy command read `copy
"C:ProgramDataKazmapg_wal_archive-18%f"` -- no point-in-time restore on Windows
had ever replayed past its base backup (found 2026-11-14 restoring the owner's
first 18 backup into a scratch folder). Gate:
`tests/test_setup_postgres.py` (retention, the archiver's numbers, restore and
its base, offsite order; the written file read back with Postgres's own
quoting rules, the plain writer as the negative control),
`tests/test_wal_restore_real.py` (opt-in `KAZMA_TEST_PG_UPGRADE_BIN`: a real
cluster archiving with the product's command, its base backup, rows before and
after a moment, the restore holding exactly the rows before it with archiving
off; the plain writer cannot replay on Windows),
`tests/test_resilience_postgres.py` (the archiver's numbers, outage or query
error and the move from a container, on a real Postgres; a row lost in the
move as the negative control).

**G3. The data folder: the database and the engine off Windows' own drive**
(2026-11-21, `kazma_core/data_folder.py`, `kazma_core/data_folder_move.py`).
The 19 Nov PC reset lost the database: setup put it in `C:\Program
Files\PostgreSQL\18\data`, its WAL archive in `C:\ProgramData\Kazma` and the
engine's disk in `%LOCALAPPDATA%\Kazma\engine`, while the install folder
(`.env`, `.kazma`, the dumps) was on another drive. Windows only.
- **One folder, chosen once, recorded** (`data_folder` in
  `setup_record.json`; `kazma setup data-folder D:\Kazma` or `kazma setup
  install --data-folder D:\Kazma`; a drive alone, `D:`, means `D:\Kazma`):
  `postgres-<major>\data`, `postgres-<major>\wal_archive`,
  `postgres-<major>\base`, `engine` (`data_folder.Layout`). Every later run,
  a repair and an upgrade (`postgres_upgrade.Upgrade.new_data_dir`) read it.
  Each folder is restricted before anything is in it, inherited rights
  removed (`data_folder.prepare_steps`: a second drive's root lets every
  signed-in user change files): SYSTEM and the administrators, the
  service's account on the data, Kazma's account (by SID, `whoami /user`) on
  the base backups and the archive.
- **Refused before anything changes** (`data_folder.folder_problem`): not
  Windows, not a full local path, a share or device path, characters the
  archive command, the installer or a service's command line cannot take
  (letters, digits, spaces, `. _ - ( )`), Windows' own drive, a drive that is
  not fixed, a `subst` drive, inside Kazma's install or data folder, too
  little free space (a new install: `FRESH_FREE_BYTES`; a move: what moves
  plus `MOVE_MARGIN_BYTES`).
- **A new cluster** is made there by the installer (`setup_plan(data_dir=)`:
  `--datadir`), its archive and base backups beside it; the record keeps
  `data_dir` (`PostgresRecord.data_dir`), and a step that writes the port
  names the folder (`port_step(..., data_dir)`, `Step.data_dir`), never
  Program Files.
- **After a reset** (the service gone, the data kept: `data_folder.
  kept_cluster_major`) setup attaches it (`setup._reattach_steps`): the
  installer's own empty cluster under Program Files (left, unused), the
  service pointed at the kept one (action `point_data_dir`: `-D` through
  `Win32_Service.Change`, the folder given to the service's account, its
  `archive_command` set on disk), then the settings and the role signing in
  with the kept database's superuser password (refused when
  `<install>/.kazma/postgres_superuser.pass` is lost). The engine's kept `ext4.vhdx` is
  registered in place (`wsl --import-in-place`, the new account granted on
  it: WSL attaches as this account, a new one after a reset). So after a
  reset `setup.cmd` is all it takes.
- **The move of an installed part** (`kazma setup data-folder FOLDER`,
  `data_folder_move.move`; `kazma setup install --data-folder` adds it as a
  step): Kazma held through its guard (`_MoveHold`), every stage journaled
  first (`data_folder_move` in the record), a part that fails put back
  (`_put_back`), a run that died put back by the next (`recover`), Kazma
  started again and seen ready. The database: the service disabled (no
  guard of any version can start it on half a copy) and stopped; the data,
  archive and base backups copied and compared file by file (path, size,
  SHA-256); the copy's `archive_command` and the service's `-D` pointed at
  it; started, and it must answer, run from the copy (`postmaster.pid`
  there) and let Kazma sign in; the record names the new folders last; the
  old ones kept. The engine: its `KazmaEngine` task disabled (its 5-minute
  trigger would start the distro), Docker stopped cleanly in it, the distro
  ended, its disk hashed, moved by WSL (`wsl --manage kazma-engine --move`,
  WSL 2.5+), hashed again, the task run, Docker must answer. Refused while
  Kazma runs under a guard without `data_folder_move` in `guard_features`
  (`_MoveHold.guard_lacks`).
- **The engine's disk is free before it moves** (live, 2026-11-21: the
  owner's move found the distro stopped, moved at once, and WSL refused
  `ERROR_SHARING_VIOLATION`; it was put back). WSL keeps a terminated
  distro's disk attached a while. `data_folder_move._release_disk` waits
  (`DISK_FREE_WAIT_S`) until the `ext4.vhdx` opens with no sharing
  (`Machine.disk_held`, `CreateFileW` share mode 0), then shuts WSL down
  when no other distro runs (a line of `wsl -l -q --running` counts only
  when it names a registered distro) -- never under one: the move is put
  back and names it. Checked before the hash, again right before `--move`,
  and before a put-back's move.
- **Every move holds a Kazma its guard runs, not only one that answers**
  (live, 2026-11-21: run seconds after `--resume`, the guard's child still
  booting, the port alone said "not running" and nothing was paused).
  `setup._kazma_to_hold`: the port answers, or a live guard (fresh
  heartbeat) supervises this install and is not paused for something else
  -- its pause holds a booting child and a backoff alike, as `kazma
  update`'s `_ServerHold` always did. One rule for the database's move from
  a container, the Postgres upgrade, the engine's move, the data-folder
  move and `kazma migrate import`. Every product call of
  `update._is_server_running` is declared with why it never decides "nothing
  to hold" under a guard. Gate: `tests/test_move_hold_counts_a_starting_kazma.py`
  (each move's hold with a booting child; the port-only rule as the
  negative control; a planted port-only caller).
- **The guard leaves a moving part alone** for `DATA_MOVE_HOLD_S` from the
  stage's start (`data_folder_move.GUARD_HOLD_S`, held equal): its database
  watch neither starts nor pages a moving database (state `moving`), its
  engine watch does not start a moving engine (`engine_moving`). The disk
  watch reads the engine's disk where WSL has it registered
  (`docker_cli.wsl_distro_location`, `HKCU\...\Lxss`; the root conftest
  answers None for it in tests).
- Gate: `tests/test_data_folder.py` (a fake Windows: its service, icacls,
  the service change, WSL's registration and the task; real folders. Each
  refusal; the choice not read, a run that knows no folder, no comparison,
  no journal, no hash check, a fresh import over a kept disk, no move
  journaled at the guard, a move straight after the running check, a disk
  checked once and a put-back that does not wait as negative controls),
  `tests/test_data_folder_drill.py` (the drill's judging).
- **Drilled** (`scripts/data_folder_drill.py`, 2026-11-21, `passed`;
  `docs/audits/evidence/data_folder_drill/`): on the drill VM with a second
  disk, Kazma installed with PostgreSQL on C:, `kazma setup data-folder
  E:\Kazma` moved it in 30 s with Kazma held (the service ran from the copy,
  a mark written before read back, a forced WAL switch archived into the
  new archive), then PostgreSQL's own uninstaller removed it and `kazma setup
  install` attached the kept database to the new service (the mark, a new
  segment archived there). Its `fresh` mode (also `passed`): a first `kazma
  setup install --data-folder E:\Kazma` on a clean VM, the installer's
  `--datadir` under the restricted `postgres-18` folder, Kazma's data kept
  and archived there. Not drilled: the engine's move (`wsl --manage --move`
  across drives; it needs WSL on the VM).

