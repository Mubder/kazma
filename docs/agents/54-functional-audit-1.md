<!-- AGENTS.md, piece 54-functional-audit-1. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 54. The functional audit of 2026-11, Stage 1 (`docs/audits/AUDIT_FUNCTIONAL_LIVE_2026-11.md`)

The live install used like an owner, end to end (128 findings; plan
`docs/plans/FUNCTIONAL_AUDIT_2026-11_REMEDIATION.md`). Stage 1 is the High
findings. Each fix has a gate that fails on the old code.

- **A database outage is ridden out** (FA-259, §44 extended). On Postgres a
  settings statement runs OUTSIDE `ConfigStore._lock` (`_statement_lock`:
  reads take no lock, writes `_pg_write_lock`; the cache is touched under
  `_lock`, a write bumps `_write_epoch` so a read that raced it does not cache
  the old value). A refused connection marks the store down for
  `_PG_OUTAGE_HOLD_S` (5 s): calls fail at once (`ConfigStoreUnavailableError`)
  and a read answers from the cache, one caller probes (`_pg_access`). The
  probes (`/health`, `/health/live`, `/health/ready`, `/health/deep`) skip the
  auth and tenant middleware (`auth.HEALTH_PROBE_PATHS`), readiness runs one
  thread per check (`health._offloaded_check`: a check still running is
  awaited, never started twice), and boot waits for the settings store up to
  `config_availability.BOOT_WAIT_S` (13 min, `wait_for_settings_store`).
  Documents and the swarm count the outage as an outage
  (`documents/errors.infrastructure_errors`, `engine._store_error_passes`).
  Gate: `tests/test_pg_outage_ridden_out.py` (the old lock scope as the
  control; a real Postgres stop with `KAZMA_TEST_PG_OUTAGE_CONTAINER`).
- **A crawl a reload cut off resumes** (FA-263): a crawl that ends
  `interrupted` while Kazma shuts down says `interrupted_by: process_ended`
  (`crawl_jobs._CrawlJob.finish`, `_process_is_ending`), the mark
  `resume_interrupted_crawls` acts on. Gate: `tests/test_crawl_auto_resume.py`.
- **The documented restore restores** (FA-267/268/269/270). A restic restore
  is assembled into a data folder Kazma reads (`backup/restore.py`
  `_flatten_into_data_dir`: `assets/` first, `dbs/` over it, sidecars
  removed), checked by a child that resolves every store through
  `kazma_core.paths` (`_check_layout`), its databases opened `immutable=1`;
  the Postgres dump lands in `pg/`. The universal backup no longer copies a
  live database a second time into `assets/` (`_copy_assets`' `held` set).
  The CLI names the passphrase file. Gate: `tests/test_restore_layout.py`
  (the old assembly and copier as controls).
- **A migration export is of an install, and an import revives no revoked
  state** (FA-252, FA-255). `export_bundle` refuses a folder that holds no
  store of an install (`ExportRefused`, before the settings store is opened;
  `paths.data_dir(create=False)`) and `verify` refuses a bundle with none;
  settings travel as whole rows (`settings_rows.json`) and the import writes
  only what `settings_restore` classes as carried (never RUNTIME: grants,
  long tasks, cursors; never a web session). Gates:
  `tests/test_migrate_export_needs_an_install.py`,
  `tests/test_migration_carries_no_revoked_state.py`.
- **`kazma update` reads its stash restore** (FA-101): a pop that conflicts
  resets the tree to the updated code, names the stash and exits 3
  (`EDITS_NOT_RESTORED`); the postflight refuses unmerged paths. Gate:
  `tests/test_update_reads_its_stash_restore.py` (real git).
- **A fork keeps what its chat keeps out of memory** (FA-246):
  `forget.inherit_ledger` copies the source's ledger rows (whole-chat
  keep-out, each kept-out turn) under the fork's keys, journaled, before the
  fork is written; a ledger that cannot be copied refuses the fork (503
  `memory_ledger_unavailable`). Gate: `tests/test_fork_keeps_the_ledger.py`.
- **A lesson comes only from a turn memory would keep** (FA-212, §11): the
  one hand-off is `close_turn` (`learn_from_finished_turn`); no lesson from a
  failed turn, a turn or chat kept out of memory, a refiner that could not
  answer (no template line), or a lesson that does not fit whole. Gate:
  `tests/test_lessons_only_from_kept_turns.py`.
- **A streaming 429 waits and retries** (FA-214): the stream path honours
  `Retry-After` with the blocking path's bounded schedule
  (`_RATE_LIMIT_RETRIES`, `_rate_limit_wait`); `LLMError` carries `attempts`
  and `waited_s`, and `friendly_llm_error` words a rate limit from them, not
  as a lost connection. Gate: `tests/test_stream_rate_limit.py`.
- **A Settings control changes something** (FA-250). Settings > Tools'
  switch is real (`skills/switches`: `switched_off_reason` for a skill or a
  tool, `offered` applied by the supervisor to every model call -- the graph
  holds the list it was built with -- and the registry refuses the call).
  Class gate `tests/test_settings_keys_have_readers.py`: every key the
  Settings writers write has a reader outside the Settings page (the rules
  and `DECLARED` readers are in its docstring); a section written field by
  field is checked field by field (`EXPANSIONS`). It found: Change password
  wrote a hash sign-in never reads (the route now changes the platform user
  sign-in checks, `platform_rbac.set_local_password`, and ends the user's
  other sessions; a `KAZMA_SECRET` session is told the secret is in `.env`);
  the session list read a key nothing wrote (now `web_sessions.list_sessions`);
  the context strategy and threshold, the sidebar position and custom CSS
  were read by nothing (removed); the accent colour applied on the Settings
  page only (now every page). The logging, safety and proxy writers take
  only their fields. Gates: `tests/test_tool_switch_is_real.py`,
  `tests/test_settings_account_is_real.py`.
- **Kazma reads its own Arabic-page PDF** (FA-004, §19H): a block takes the
  glyphs whose baseline is inside it (`pdf_rtl._BASELINE_SLACK`, 0.05 of a
  size; half a size took the line above), and a left-to-right line keeps its
  word gaps. Gate: `tests/test_document_pdf_ltr_blocks_on_arabic_pages.py`.
- **The free-slot search is the operator's day** (FA-112, §34): one finder,
  `calendar/backends/base.free_slots`, in the scheduler's zone, around every
  event that overlaps the working day (multi-day, all-day, overnight); the
  backends only list events. Gate: `tests/test_calendar_free_slots.py`.
- **Every advisory has a floor at its fix** (FA-258, §50):
  `tests/test_dependency_floors.py` holds pyproject to a recorded OSV answer
  (`tests/fixtures/osv_advisories.json`, refreshed with
  `scripts/record_osv_advisories.py --python <live python> --python
  <this checkout's .venv python> --write`: what live runs AND what a fresh
  install gets from the lock; rows only accumulate, so a floor an advisory
  once needed stays held). Recorded from live alone, the drill VM's fresh
  install (2026-11-06) got certifi, oauthlib and pypdf below their fixes
  past a green gate. A `uv lock` upgrade that crosses a major version
  (pypdf 7) is kept to the series the fix is in until it is tested, in
  pyproject too (`pypdf>=6.21.4,<7`): `kazma update` installs the newest
  version within the floors and does not read uv.lock, so the drill VM's
  update installed pypdf 7.0.0 while the lock said 6.21.4. A
  default provider's imports are base dependencies with floors (FA-248:
  `edge-tts>=7.3.1`), and a failed voice reply on Telegram is a WARNING.
  Gate: `tests/test_default_voice_providers_declared.py`.
- **Kazma notices its services reachable beyond the machine** (FA-218):
  `security/service_exposure.py` reads `docker ps` (Kazma's containers by
  name: the image of a recreated container is an id) and `pg_roles`; the
  security report's `check_service_exposure` and a boot WARNING plus the ops
  alert `security.service_exposed`. The compose files publish on loopback.
  The way off a superuser login is `deploy/postgres/kazma_app_role.sql`
  (role `kazma_app`: no superuser, no CREATEROLE, CREATEDB for the restore
  rehearsal; it owns the database, its schema and every object, each named:
  Postgres refuses `REASSIGN OWNED BY` the bootstrap superuser), the steps in
  `docs/docs/ops/postgres-and-saas.md`; the memory mirror keeps its own copy
  of the address (`memory.backends.state.url`, no fallback to
  `KAZMA_DATABASE_URL`). Gates: `tests/test_service_exposure_check.py`,
  `tests/test_compose_ports_loopback.py`, `tests/test_pg_app_role.py` (every
  store's setup, a dump and the rehearsal as the role, on a database seeded
  as the superuser; the bare role as the negative control). **A refused
  sign-in says why** (2026-11-03: the live switch to `kazma_app` waited 18
  minutes on a role with no password while the log said "unreachable"):
  once the pool gives up, `postgres_pool.refusal_reason` signs in once and
  words Postgres's answer (a refused password, a missing role or database,
  nothing listening), logged at ERROR and carried in `PostgresUnreachable`
  and `ConfigStoreUnavailableError.reason` (`config_store._refusal_in`).
  `scripts/check_database_login.py` signs in as Kazma will before a reload
  (psql inside the container proves nothing: it trusts loopback). Gate:
  `tests/test_postgres_refusal_reason.py` (the probe answering nothing as
  the negative control; a role with no password on real Postgres).

