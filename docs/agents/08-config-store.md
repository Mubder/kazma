<!-- AGENTS.md, piece 08-config-store. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 8. ConfigStore Singleton + Atomicity (`kazma-core/kazma_core/config_store.py`)
- Uses WAL + `busy_timeout=5000` (like all other SQLite stores)
- Process-wide singleton: `get_config_store()` — all components MUST use this, not `ConfigStore()` directly
- Multi-key writes MUST use `batch_set()` for atomicity. `transaction()` is
  SQLite-only (on Postgres its `_get_conn()` refuses) and no product code
  calls it: do not add a caller (audit 2026-10-26 AUD-079)
- **`atomic_update` on Postgres locks the key itself first**
  (`pg_advisory_xact_lock` on `_ATOMIC_UPDATE_LOCK_PREFIX` + key, for the
  transaction; 2026-11-18, the Spaces S1-10 review). `SELECT ... FOR UPDATE`
  locks nothing while the row does not exist: two processes' first updates
  of a key each read nothing and the second replaced the first. A
  read-modify-write belongs INSIDE the updater: a read before
  `atomic_update` loses what another process wrote in between. Gate:
  `tests/test_atomic_update_first_write.py` (real Postgres, two stores; the
  raw `FOR UPDATE` order as the control).
- Never construct `ConfigStore()` in gateway/core code — use `get_config_store()`
- **`self._lock` covers the live statement, and `close()` must take it too.**
  Every read/write holds the lock across `_get_conn()` *and* the statement
  that follows. `close()` did not, so it freed sqlite's native handle under a
  thread already inside `sqlite3_step` — a use-after-free, which on Windows
  is a bare `access violation` with no traceback that no `try/except` can
  catch. It killed a full pytest run at 19% on 2026-09-20: the memory
  worker's session-purge cadence read the `auth` category on a pool thread
  while the per-test fixture closed the same store. **Any new method that
  touches `_conn` holds `_lock` for as long as the statement is live.**
  A read after a close is fine — `_get_conn()` reopens lazily.
  Repro: `tests/test_config_store_close_race.py`.
  `_stored_raw` holds that same RLock through its native SELECT too, including
  dict/secret preparation before `batch_set` enters its transaction (2026-10-11).
  A cache-only lock exposed SQLite API misuse/corrupted reads under concurrent
  acquisition first writers. Gate: `tests/test_config_store_raw_read_lock.py`,
  including the original lock boundary as a traced-statement negative control.
- Background cadences do not run under pytest:
  `worker_bootstrap.background_schedulers_enabled()` is False there (override
  with `KAZMA_TEST_BACKGROUND_SCHEDULERS=1`). One switch for all eight
  schedulers — the purge was simply the one that got caught.
- **A snapshot answers a key stored nowhere as `get` does** (2026-11-13):
  `ConfigStore.snapshot(prefix)` walked kazma.yaml with `{}` as each missing
  level's default, so `snapshot(k).get(k)` answered `{}` for a key held in
  neither the database nor the YAML, and the crawl journal read a removed
  job as a broken row ("journal unavailable": CI, a library deleted as its
  crawl ended). A prefix the YAML does not hold is now absent from the
  snapshot. The journal also deletes an evicted job's revision key before
  its row (`crawl_jobs._forget_job_keys`): between the two deletes, the row's
  key read back as its child merged in. Gates:
  `tests/test_config_store_snapshot.py`, `tests/test_knowledge_crawl_journal.py`
  (the old walk and the old delete order as controls).
- **A write drops only what it can change in the read cache**
  (`ConfigStore._invalidate_key`, 2026-10-01): the key, its ancestors (`get`
  merges a missing key's children, so `a`'s cached view holds `a.b`) and its
  descendants. Every `set`/`batch_set`/`delete` cleared the WHOLE cache,
  misses included, so any write anywhere -- the heartbeat, a session stamp --
  sent the next read of every setting back to the database (on Postgres a
  round trip, often from the event loop). A raw `transaction()`,
  `reset_all()` and a YAML reload still clear everything (their keys are not
  known). `tests/test_config_store_cache_scope.py` (the full clear as the
  negative control).
- **GET nested vault walk is resolve-only.** `_resolve_vault_value` decrypts
  `vault://` pointers inside dicts/lists. Lazy-migrate of plaintext secrets
  is **only** for the exact string key `get()` was called with. Nested
  `api_key` fields inside `providers.list` must not share
  `cfg:providers.list.api_key` — that ping-ponged `vault.store` on every
  registry read and stalled SSE (2026-09-08 `_No response received._`).
- **A changed shipped default declares whether installs follow**
  (`kazma_core/config_defaults.py`, 2026-09-29). The first boot copies every
  merged shipped/local YAML value into the database (`reconcile_from_yaml`, through
  `shipped_settings`) and a stored value wins, so a default changed later
  never reached an install that had booted before it -- the live install
  still held four lifecycle events after the fix, and ten older shipped
  values (a read-only audit that day; most no longer read). A
  `RetiredDefault` (key, every old value, date, why) makes installs follow:
  at boot a stored value equal to an old one becomes today's, ONCE per entry
  (`system.config.retired_defaults`), so an owner who picks it again keeps
  it. `NEW_INSTALLS_ONLY` keeps the old value, with the reason. Never
  "follow" by equality with no declaration: the live `agent.language: ar`
  equals an old default and is read. Gate:
  `tests/test_shipped_config_defaults.py` (kazma.yaml against
  `tests/fixtures/shipped_config_defaults.json`; `python
  scripts/shipped_defaults.py --write` refreshes it and refuses an
  undeclared changed value).
- **First boot seeds merged YAML, retirement reads shipped defaults**
  (`reconcile_from_yaml`, 2026-10-06): local provider/model choices must
  survive a restart (the fallback followed the active workspace then; it no
  longer does, §62). Seed missing keys through
  `load_merged_yaml` and `shipped_settings`, including local-only files;
  existing rows retain precedence. Check declared retirements against raw
  shipped defaults BEFORE seeding, so a fresh local choice equal to a retired
  value is not mistaken for an old stored default. No valid shipped mapping
  means no retirement. `tests/test_config_reconcile_local.py` covers both
  SQLite and real PostgreSQL, plus the workspace reload that exposed it.

