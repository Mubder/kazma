<!-- AGENTS.md, piece 06-task-store. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 6. TaskStore WAL Mode (`kazma-core/kazma_core/swarm/task_store.py`)
- SQLite uses WAL + `busy_timeout=5000` for concurrent read/write
- Schema auto-migrates on init (ALTER TABLE for new columns on existing DBs)
  through `kazma_core.db.sqlite_columns.add_missing_columns` -- every SQLite
  store does (2026-09-30): read `PRAGMA table_info`, add what is missing, let a
  real failure raise; only another process adding the column first is
  tolerated. The old `try: ALTER ... except Exception: pass` hid a locked or
  read-only database until every later query failed with "no such column".
  Gate: `tests/test_sqlite_column_migrations.py` (no SQLite `ADD COLUMN`
  outside the helper; Postgres `ADD COLUMN IF NOT EXISTS` is idempotent and
  exempt -- and never swallowed: it aborts the transaction it runs in).
- Worker filter uses `json_each()` not `LIKE` for exact matching
- **A deleted task leaves no log** (2026-11-25): `TaskStore.delete_task`
  removes what the pipeline log keeps of the task first
  (`pipeline_logger.forget_pipeline_logs`), then the row. The log names a task
  by its id alone, so a row deleted by itself orphaned its workers' output
  from every later erase. A log that fails fails the delete (it is tried
  again by the row); none is made to delete from. Gate:
  `tests/test_chat_move_leftovers.py` (both callers; the row alone as the
  control).

