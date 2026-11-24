<!-- AGENTS.md, piece 19f-documents-backup. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
**F. Multi-replica backends.**
Jobs: `jobs_pg.py` when Postgres. Metadata: `repository_pg.py` when the
metadata backend resolves to Postgres (D) and the pool is up. GC is
backend-agnostic: `retention._mark` dispatches to `repository.gc_mark`, which
both backends implement, so mark/sweep runs on Postgres metadata -- in fact
only since 2026-11 (FA-205): the Postgres mark read its rows by position and
failed every pass (`repository_pg._cell` reads by name), and an install whose
catalog is SQLite sweeps the orphan rows a Postgres catalog left
(`retention._sweep_idle_catalog`). The old
`gc_postgres_metadata_sql_port_pending` skip is gone and
`tests/test_leftovers_except_g.py` asserts it stays gone. Audit works on both
backends.

**F2. A Postgres store is backed up as ONE point in time** (2026-10-13, F06).
`documents/backup_pg.py`: a REPEATABLE READ transaction exports its snapshot,
reads the blob/manifest references inside it, PINS them (`.backup-pins`; GC
re-reads pins before each delete), and `pg_dump --snapshot` dumps the document
tables while that transaction stays open; the files are copied with
checksums. The scheduled document backup takes this path whenever the
installed store is on Postgres. A restore goes only into an EMPTY database and
storage root, re-checks every table's row count against the snapshot, expires
the dead source's leases and marks its unfinished intakes `restored` (rolled
back at start). The knowledge index is a separate store (library chunks in
the settings store, vectors in Chroma): restore reports `reindex_required`,
and every worker start runs `DocumentIngestionService.reconcile_index` once,
which re-publishes each version the store records as indexed but no library
holds, from its own manifest, as the owner, through `index_document_ir`. Each
is re-checked under the document's publication lock
(`DocumentKnowledgeAdapter.publication_lock`, striped per tenant and
document; index and unindex hold it), so an unindex is never undone. A
missing library, a superseded version or a missing manifest is reported,
never invented; chunks the store does not record are reported and removed
only with `index --apply --prune`. Operator: `scripts/document_pg_backup.py`
(`backup`, `verify`, `restore`, `index`). Gates:
`tests/test_document_pg_backup_restore.py` (real Postgres, private databases;
a dump outside the snapshot is caught by the row-count check),
`tests/test_document_backup_pins.py`, `tests/test_document_reindex_after_restore.py`
(both backends; without the re-check an unindex is undone), and the opt-in
`tests/test_document_pg_restore_drill_scale.py` (`KAZMA_TEST_RESTORE_DRILL_DOCUMENTS`).

**F3. Backup, export and import follow the CATALOG** (2026-10-13, DOC-A6/A7/A8).
`backup.installed_document_backends()` is the one answer: the running
service's repositories, else the configured backends (a CLI has no service;
asking only the service exported a Postgres store as its empty local
`documents.db`). Postgres catalog -> coordinated backup (F2); its files travel
in a migration bundle, the catalog in `data/postgres.dump`. SQLite catalog
beside a Postgres queue -> the SQLite snapshot, with quarantine->originals
promotions read from the queue after the snapshot and kept in the manifest
(`promoted_versions`, read again by `verify_document_backup`); the jobs
backend alone used to pick the coordinated backup, which read a catalog that
was not there. Postgres catalog beside a SQLite queue (a fallback) ->
`UNSUPPORTED_SHAPE`, by name. Backups are written under a `.…partial` name
and renamed when complete. A failed document restore fails `kazma migrate
import`. Restoring BOTH Postgres backups into one database: the whole-database
dump first, then `restore --replace-document-tables` (drops the dump's older
document tables only after the backup verifies and the storage root is empty).
Gates: `tests/test_document_backup_shapes.py`,
`tests/test_document_pg_restore_order.py` (both wrong orders as controls),
`tests/test_document_backup_restore_hardening.py`.

**F4. The Postgres audit is append-only too** (2026-10-13, DOC-A1). Trigger
`document_audit_guard` (function `kazma_document_audit_guard`) refuses every
UPDATE and every DELETE outside a transaction the retention prune marks
(`SET LOCAL kazma.document_audit_prune`); the prune runs in bounded batches on
Postgres (it called the SQLite connection, `None` there, and the table grew
forever). `pg_dump -t` carries a trigger but not its function: every restore
creates `db/pg_backup.KAZMA_PG_FUNCTIONS` first, and any trigger function in
product code must be declared there. Gate: `tests/test_document_audit_pg.py`
(old prune, no trigger and no function step as controls).

