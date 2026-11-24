<!-- AGENTS.md, piece 44-database-outage. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 44. A database outage is ridden out, not a restart (2026-09-30)

On 2026-09-28 at 22:15 UTC a Docker Desktop update restarted the Docker
engine; Postgres was gone for two minutes. Kazma turned that into a restart:
the heartbeat's `atomic_update` and the restic handler's settings read sat on
the event loop waiting for a pool connection, `/health/ready` ran its checks
one after another (one of them on the loop) past the guard's 10 s probe, the
pools' reconnects hung on psycopg's 130 s connect timeout, and the guard --
which restarted on any three failed probes -- killed a server whose only
problem was its database. Each layer now holds on its own:

- **Nothing waits on the database on the loop** (§35: the settings-store
  gate, and the loop-stall helpers of the third pass).
- **`/health/ready` answers in seconds** (`kazma_ui/health.py`): every check
  off the loop, capped, all at once (`asyncio.gather`, a check that raises
  fails itself). A database that is away is a quick 503 naming it.
  `tests/test_readiness_answers_in_time.py` (negative control: the same
  checks one after another).
- **The pools come back with the database** (`db/postgres_pool.py`
  `pool_connection_kwargs`, used by the shared pool and the checkpointer's):
  a 5 s connect timeout unless the DSN names one, and `check=` so a
  connection the server closed is replaced at checkout, not handed to a
  caller. `tests/test_pg_pool_recovery.py` (a gate over every pool Kazma
  builds; on a real Postgres a terminated backend is replaced; negative
  control: a plain pool hands it out).
- **The guard rides out an answered "not ready"** (§39) and pages; it
  restarts only a server that does not answer, one that says only a restart
  clears its failure (`restart_required`: the volatile settings store), or
  one not ready for `KAZMA_GUARD_DEPENDENCY_OUTAGE_S`.
- **No settings statement holds the store's lock while it waits on the
  database, and the probes always answer** (2026-11, FA-259: a 2-minute
  outage still got Kazma killed, a 12-minute one kept it down 15 minutes).
  See §54.
- **One answer to "is the database away"** (`kazma_core/db/availability.py`,
  resilience plan A3, 2026-11). Live 2026-11-03/04 Docker took the database
  away for eleven hours: every background loop retried on its own clock and
  logged a WARNING each pass (the pool alone 1,900 lines an hour), the memory
  queue dead-lettered work that had done nothing wrong, and 33 of 34 guard
  restarts were a boot that waited its 13 minutes and exited. Now a failure
  of Kazma's own database (`is_kazma_database`; `is_outage`: SQLSTATE class
  08/28, 57P01-03, 53300, 3D000, a pool timeout, `PostgresUnreachable`,
  through the cause chain -- never a query error, never SQLite) is reported
  with `note_failure`; ONE prober thread then signs in every `PROBE_EVERY_S`
  (one per outage, respawned when stale) and ends it. One WARNING when it
  starts, one page `database.away` at `PAGE_AFTER_S` -- unless the guard
  already paged the outage (its database service or the boot waiting for
  it, §33 "One page per problem": asked again each probe) -- one
  `database.back` only when the away page was delivered (`on_delivery`;
  else its page receipt is settled with the outage); the pool's own
  chatter is filtered only while away (`_install_log_filter`). Every
  background loop that touches the database waits while it is away
  (`wait_while_away`, or `is_away` to skip a pass), or is declared with why
  (`AWARE` / `DECLARED` in the gate, found from the source: every `async def`
  holding a `while` that sleeps). The memory queue puts an outage-failed task
  back without spending an attempt (`task_queue._ack(outage=True)`); the
  maintenance runner records a sweep that met the outage as
  `waiting_for_database`, not a failure; cron holds a task's fire (a
  reminder, which needs no database, still rings). A guarded boot waits for
  its settings database without a limit and says so in
  `KAZMA_GUARD_BOOT_FILE` (`config_availability.write_boot_status`), which
  the guard reads (`read_boot_status`) instead of counting a hang; readiness
  carries the state (`database_availability`), and the guard does not page
  an outage the server is already watching (`app_watches_database`). Gate:
  `tests/test_database_availability.py` (planted loops and the turn-liveness
  loop without its wait as negative controls; a normal failure spending its
  attempt; a task fire running while answering).

- **A start without the database is read-only, not silent** (resilience plan
  A4, `kazma_core/settings_snapshot.py`, `kazma_ui/read_only_mode.py`). The
  maintenance sweep "settings snapshot" writes the settings' local copy every
  15 minutes (`<data dir>/settings_snapshot.yaml`, secrets as `vault://`
  pointers). A minute into a boot with the database away and a copy present
  (`wait_for_settings_store(on_wait=read_only_after)`), `create_app` serves
  the read-only server: the chat apps, built from the copy, tell each chat
  once that the database is away and keep every message (`chat_inbox.db`);
  `/health/ready` answers not ready with the server's own watch
  (`database_availability`, so the guard holds a restart); pages say since
  when; writes answer 503 `database_away_read_only`; the copy refuses every
  write (`SnapshotSettings`). When a sign-in to the database works, Kazma asks
  the guard for a reload, and the normal start hands the kept messages to the
  gateway in order (`drain_inbox`; a handed row keeps no text).
  `KAZMA_READ_ONLY_START=0` turns it off. Gate: `tests/test_read_only_start.py`
  (no copy: the boot keeps waiting, as the control).

