<!-- AGENTS.md, piece 18-migration. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 18. Migration System (`kazma-core/kazma_core/migration/`)

A portable-bundle system for moving a full Kazma installation across
machines/OSes (WSL→Windows, Linux→Mac, server→laptop) without the silent
breakage of a naive copy-paste. User surface is the `kazma migrate` CLI
(`export` / `verify` / `import`); the engine lives in
`kazma_core/migration/` so REST/UI can wrap it later.

**Three load-bearing invariants (the whole point of the tool):**

**A. `vault.db` + `KAZMA_VAULT_KEY` travel as an atomic pair.** The vault's
per-installation PBKDF2 salt lives *inside* `vault.db`, so the DB is
undecryptable without its matching key. The bundle carries both: the key in
`meta.env`, a non-reversible fingerprint in `manifest.json`. On import,
`check_vault_key()` (`migration/vault_pairing.py`) compares them: MATCH
proceeds, EMPTY writes the bundle's key, MISMATCH **aborts** unless
`--reset-vault-key` is passed (which backs up the target's existing vault.db
first, then overwrites the key). This is the #1 silent-breakage mode of a
copy-paste migration.

**B. Embedded absolute paths are rewritten to the target root.** A Linux
source (`/home/user/kazma`) has its workspace root and data dir baked into
stored text. Before the swap the importer rewrites them, in the staged
copies, to the target's (`build_path_map`: the source workspace root and the
source data dir, each to the target's; `migration/path_rewrite.py`), in
exactly these columns (`importer._PATH_REWRITE_TARGETS`):
`workspaces.root_path` (exported as `workspaces.db`, merged into
`settings.db` after the swap); `sessions.messages` (`chat_sessions.db`) and
`spool.payload` (`chat_sessions_spool.db`); in `memory_state.db`
`entities.metadata_json`, `episodes.user_text`, `episodes.assistant_text`,
`episodes.summary_text`, `episodes.metadata_json`, `beliefs.object`,
`beliefs.metadata_json`, `procedural_dags.preconditions_json`,
`procedural_dags.dag_steps_json`, `procedural_dags.postconditions_json`; in
`memory_ops.db` `memory_audit_log.reason`,
`memory_audit_log.state_before_json`, `memory_audit_log.state_after_json`;
`cron_jobs.prompt` (`cron.db`); `agent_artifacts.value`
(`agent_artifacts.db`); `task_ledgers.goal`, `task_ledgers.next_action`,
`task_ledgers.steps`, `task_ledgers.findings` (`task_ledgers.db`). The
import report counts the rows changed per database (`rows_rewritten`).
Nothing else is rewritten: `config.yaml` is imported as exported, the
document store holds no paths, and time-travel snapshots (`snapshots.db`)
are not carried at all, so none of their columns is rewritten (2026-10-14,
Time Travel audit TT-05: a copy elsewhere would bring back chats the owner
deleted or forgot; §12, `store_registry`: `machine`). Three correctness
properties: (1) `PathMap` is ordered **longest-source-first** so
`/home/u/kazma` doesn't partially rewrite `/home/u/kazma-repos/ShipX`; (2)
substitution is byte-level substring on the column text (NOT a JSON parse --
paths appear anywhere), of the forward-slash form, the backslash form AND
the JSON-escaped Windows form (`C:\\Users\\...`, every backslash doubled
inside a JSON column; TT-09: a Windows-source migration left those paths on
the old machine), whose rest is written in the target's separators (`/` for
a POSIX target, still escaped for a Windows one); a match is anchored at
path boundaries, so no false positives (`/home/u/kazma-backup` is not
`/home/u/kazma`, `barcode` ≠ `code`); (3) a row that parsed as JSON and
whose rewritten text would not keeps its original text and is counted in a
WARNING (`_parses`). Gates: `tests/test_path_rewrite.py`,
`tests/test_time_travel_bounds.py` (both directions with the old rewrite as
the control; this list against the importer's). Workspace root rewrite also
fires `notify_root_changed()` so MCP rebinds.

**C. Import is atomic — never corrupt the target mid-flight.** The importer
(`migration/importer.py:import_bundle`) stages to `kazma-data/.migrate-staging-<ts>/`,
verifies, path-rewrites the *staged* copies, backs up the live DBs to
`.migrate-backup-<ts>/`, then swaps staging → live one file at a time via
`rename`. Any exception before the swap leaves live data untouched; the
staging dir is preserved on failure. `verify` runs as a dry-run inside every
import and is available standalone. **A kept staging dir expires**
(2026-11-11): the next import removes one older than `STALE_STAGING_S` (30
days), never one a running import marks as its own (`IN_USE_MARKER`, written
by `mark_working_folder`, taken off by `import_bundle` however the run ends;
`_working_folder_in_use` keeps a folder whose mark it cannot read or rule
out). It was swept with `max_age_s=inf`, kept forever. Gate:
`tests/test_failed_import_staging_expires.py` (the old age as the control).

**What restoring the old Kazma after the PC reset taught it** (2026-11-19):
- **`kazma migrate import` holds Kazma itself.** It runs the whole check
  first as a dry run (a bundle that would be refused never stops Kazma),
  stops Kazma through its guard once no chat turn runs (`setup._MoveHold`,
  the database and engine moves' hold, under `_IMPORT_PAUSE_REASON`),
  imports with `server_stopped=True` (the heartbeat the server stamped in
  its last minute is no live server: the gate refused the owner for two
  minutes after their own pause), and starts Kazma again whatever the
  outcome (invariant C left the data as it was). `--force` imports
  without the hold, as before. A bundle it cannot read is one sentence
  (`_refuse_unreadable`), not zipfile's traceback.
- **The vault status said at the end is the one after the import**
  (`_say_key_written`: `replaced`, or `set` for an install with no key).
  It printed the pairing read before the swap: "mismatch ... Re-run with
  --reset-vault-key", after that flag had written the right key.
- **The document list follows the bundle** (`_document_list_plan`, step
  2b, before anything changes). A bundle carries `data/documents.db` only
  when its source kept the list in SQLite; on an install whose list follows
  Postgres the import said "documents.db not installed" and 29 documents
  came back with no list. Now the setting unset or `auto` gets
  `KAZMA_DOCUMENTS_METADATA_BACKEND=sqlite` written into `.env` (and the
  process) before the store is restored, said in the output; named
  `postgres`, the import stops, nothing changed.
- **Every sync Postgres pool is closed before Python exits**
  (`postgres_pool.close_at_exit`, one `atexit` handler): on Python 3.14
  psycopg_pool's `__del__` cannot join its threads at shutdown, and every
  `kazma` command that touched Postgres ended with "Exception ignored ...
  PythonFinalizationError". Checked on the owner's 3.14 install (a plain
  pool as the control).
Gates: `tests/test_migrate_import_after_reset.py`,
`tests/test_migrate_cli_holds_kazma.py`, `tests/test_pools_closed_at_exit.py`
(each with the old behaviour as its control).

**Scope — SQLite + Postgres (v2).** The bundle always carries the SQLite
files (vault, memory, cron and the other stores `store_registry` carries —
these are SQLite even under a Postgres backend). When the source is Postgres, it ALSO carries a
``data/postgres.dump`` produced by ``pg_dump -Fc`` containing the
shared-state tables (settings, chat sessions, checkpoints, swarm tasks).
The exporter reads via the backend-agnostic data-access layer for the
SQLite portion and shells out to ``pg_dump`` for the Postgres portion.

**Postgres dump/restore discovery — ``pg_bridge.py``.**
``resolve_pg_dump()`` / ``resolve_pg_restore()`` try, in order: (1) the
binary on PATH, (2) ``docker exec ${KAZMA_DB_CONTAINER:-kazma-db} <bin>``
(the common Docker-deployment default — the DB container has the client
tools even when the host doesn't), (3) raise ``PgToolNotFound`` with a
clear install hint. Override the container name with ``KAZMA_DB_CONTAINER``.

**Target-backend matching on import.** The importer checks the target
backend: if Postgres, it ``pg_restore`` the dump (``--clean --if-exists``
idempotent; schema self-recreates, target DB can be empty) into
``KAZMA_DATABASE_URL``, then proceeds with the SQLite-file restore +
path-rewrite of the staged SQLite stores (B). If the target is SQLite but the
bundle has a Postgres dump, it **aborts with a clear error** rather than
silently importing partial data (the SQLite files alone lack chat history,
settings, checkpoints that live in Postgres). No SQLite↔Postgres content
translation is attempted — the bundle is source-backend-shaped.

**Bundle layout:** `manifest.json` (version, source OS/host, per-file sha256,
vault-key fingerprint, table counts, source workspace root), `meta.env`
(vault key + public url), `config.yaml` (ConfigStore.export_yaml — secrets
are `vault://` refs, not plaintext), `vault.db` (encrypted, under `data/`),
every other SQLite store `store_registry` says a bundle carries, under
`data/` (`bundle_store_names`, plus the `checkpoints_<tenant>.db` family
members present), `data/postgres.dump` (only when the
source was Postgres — custom format, restored via pg_restore), `pathmap.json`,
and verbatim `assets/`: every other entry of the data dir that
`store_registry.carried_in_bundle` says travels (2026-10-01) -- the swarm's
worker registry and templates even with `--no-assets`; attachments,
documents, exports, images, the default workspace and the operator's own
files otherwise. It was five hand-listed folders, so the workers, the
workspace and everything the agent wrote into the data dir stayed behind.
The import asks the same question of every name the bundle holds (a bundle
cannot plant `install_id`, backups or a database), keeps a target file it
replaces in `.migrate-backup-<ts>/files/`, and fails on a file it could not
restore; an export that could not read a file records it and `verify`
refuses the bundle.

**Key files:**
- `migration/bundle.py` — `Manifest`, `KazmaBundle`, `verify()`, `sha256_file`
- `migration/path_rewrite.py` — `PathMap`, `build_path_map`, `rewrite_paths_in_sqlite`
- `migration/vault_pairing.py` — `check_vault_key`, `sync_vault_key`
- `migration/exporter.py` — `export_bundle()`
- `migration/importer.py` — `import_bundle()`, `ImportReport`
- `kazma-cli/kazma_cli/migrate.py` — CLI dispatch (`kazma migrate export|verify|import`)
- `memory/backup.py:backup_one()` — the WAL-safe SQLite copier reused by the
  importer's pre-swap safety backup (promoted from `_backup_one`).
- Document store export/import is also wired — see §19 F3 and
  `migration/exporter.py`. What travels follows where the document CATALOG is
  (`documents.backup.installed_document_backends`: the running service, else
  the configured backends, so the CLI sees a Postgres store too): a SQLite
  catalog as `documents.db` + the content it references; a Postgres catalog as
  `data/postgres.dump` + every content file it references (copied after the
  dump: a superset); a Postgres catalog beside a SQLite queue is refused by
  name. The export records a refusal or a missing referenced file under
  `_document_store`, and `verify` refuses the bundle. A document restore that
  fails on import fails the import (2026-10-13, DOC-A7/A8).

