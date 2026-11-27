<!-- AGENTS.md, piece 39-guard. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 39. The guard carries out reloads, keeps a heartbeat, never dies silently (`scripts/service/kazma_guard.py`)

Live 2026-09-26, three failures in one reload. `--reload` from an operator
shell could not stop the server -- the `KazmaAgent` task runs the guard
elevated, in its own logon session, and the shell got "Access is denied" --
so the old build kept serving. The request file it left behind was the kind
that, from 2026-09-20 to 09-22, woke the guard on every sleep: 3.8 million
health probes in 47 hours, 53 a second. And the guard itself had died twenty
minutes earlier, exit code 1 and nothing in guard.log; the task sat "Ready"
(Task Scheduler does not restart a process that ran and exited, whatever its
code) and Kazma ran with nobody supervising it.

- **The running guard does the stop.** `--reload` writes a dated request;
  the guard handles it within a second, in its sleep loop and during a
  boot. A request older than the running child is already satisfied and is
  cleared; a newer one stops the child and respawns at once. The CLI waits
  for the acknowledgement (`reload_ack`, or a spawn after the request, in
  the state file), starts the task when no guard is alive, and stops the
  server itself only for a guard from before this change -- never a server
  that booted after the request -- and withdraws a request it could not
  apply.
- **Deliberate stops are graceful.** Reload, pause and the guard's own
  shutdown send CTRL_BREAK (Windows) / SIGTERM (POSIX) to the child's
  process group and wait `KAZMA_GUARD_GRACEFUL_STOP_S` (60) before the
  kill; uvicorn drains and the app announces its own stop, so the guard pages
  a reload only when the app could not. An unhealthy child gets 15 s. Never
  signal process group 0: that is every process on the console.
- **One wake-up per request; probes keep their interval.** A handled
  request is remembered by its on-disk signature (a file the guard cannot
  delete is handled once), and `_supervise` never probes more often than
  `KAZMA_GUARD_INTERVAL`, whatever wakes it.
- **The heartbeat answers "is a guard running?"** The guard writes
  `heartbeat` into its state file at least every 10 s in every state it waits
  in; `--status` and `--reload` read it, because an operator shell cannot
  open the elevated processes to ask. The guard hands the server that file's
  path in `KAZMA_GUARD_STATE_FILE`.
- **Never silent.** The whole body of the supervision loop sits in a try
  whose handler logs `guard.internal_error` with the traceback, pages once
  per kind, and keeps supervising the SAME child (a second server next to it
  is the one outcome worse than the error). `_run_supervisor` logs
  `guard.crashed` for anything that still escapes; native crashes go to
  `guard.fault.log`. The sleep loop reads the clock once per pass (a negative
  `time.sleep` raises), and the logger never raises.
- **A recorded PID is verified before it is reaped:** creation time recorded
  at spawn, a python image otherwise. A PID left by a dead guard can belong
  to anything by now. On Linux the stamp (`child_stamp`, the broker's
  `stamp`: boot id and start ticks, `_process_start_stamp`) is compared
  first, exactly: the creation time there moves with the wall clock, and a
  WSL2 VM drifting a second every 30 s turned an orphan recorded minutes
  earlier into "pid reused", not reaped (2026-11-27). The stamp counts only
  for the pid it was recorded for (`child_stamp_pid`, the broker's
  `stamp_pid`): a guard from before the stamps merges its child's pid and
  time and leaves the earlier stamp behind. The stamp is
  `kazma_core.process_start`'s (§35), checked by `tests/test_process_start.py`;
  the guard reads `/proc` only in `_process_started_at` and
  `_process_start_stamp`.
- **The server says when its guard is gone.** The maintenance cadence runs
  `observability/supervisor_watch.check_supervisor`: with
  `KAZMA_GUARD_STATE_FILE` set (the guard started this server) and no
  heartbeat for 5 minutes, it pages `guard.gone` (critical, every 6 hours
  while it lasts) and logs once when the guard is back. A server started
  without the guard is not watched.
- **A reload's stop is fast because nothing waits out a timeout.** The
  gateway stops its adapters together, and the Discord and Slack readers
  close their websocket the moment shutdown is set
  (`kazma_gateway/adapters/ws_shutdown.closes_on_shutdown`); they used to sit
  in `recv()` until the platform next spoke, costing 5 s each, in sequence, on
  every reload (`tests/test_gateway_prompt_shutdown.py`, real websockets).
  A new socket-reading adapter uses the same helper. **And the shutdown hook
  builds nothing it only came to close** (2026-09-27): it called
  `get_session_manager()`, which on a server nobody had opened the web UI on
  BUILT the manager -- every chat session loaded from Postgres, on the loop --
  so the guard's 60 s ran out, the server was killed and the owner paged.
  `_on_shutdown` uses the `peek_*` accessors (session manager, model
  registry, message bus: what exists, or None) and closes off the loop.
  Gate: `tests/test_shutdown_builds_nothing.py` (every accessor the hook
  calls, resolved through its imports; one that assigns a module global
  fails).
- **The OS brings a dead guard back:** `install_service.py` registers a
  5-minute repeating trigger with `MultipleInstances IgnoreNew`. An existing
  task gets it only when re-registered from an elevated shell (owner action).
- **`--install` never replaces the task for the worse; `--status` says what
  differs** (2026-09-30). `install_service.py --status` reads the registered
  task (`registered_task`: none is `registered: false`, unreadable is `null`
  -- never the same) and checks each setting `--install` registers (state,
  account, triggers, IgnoreNew, `TASK_PRIORITY`, the guard it starts), `FIX`
  per difference, exit 1. A rerun of `--install` refuses, changing nothing:
  without admin rights over a task that starts at boot, or one it could not
  read (its user-level fallback used `-Force`); and over a task that starts
  another folder's guard unless `--move` (a second checkout beside the live
  install). Both name the command to run instead. The elevated `--install`
  prints the status after registering. Gate:
  `tests/test_windows_task_registration.py` (every check fails alone; each
  refusal with its negative control; the real query on Windows).
- **Kazma runs at an interactive program's priority**
  (`kazma_core/process_priority.py`, 2026-09-30). The live task had no
  `<Priority>`, so Task Scheduler's default (7, background) started the
  guard BELOW_NORMAL, and a child inherits a below-normal class AND its
  parent's memory and I/O priority (measured; even a child created at
  NORMAL keeps them): the server ran at base priority 6 with 60 MB of its
  4.7 GB resident and froze 15-27 s whenever something heavy ran beside it.
  The server raises itself -- never lowers -- in
  `KazmaAppBuilder._adopt_process_environment` (CPU class, memory priority,
  I/O priority; one INFO line naming what the launcher gave it), and the
  guard does the same at `guard.start` (`guard.priority` event) with the
  same file LOADED BY PATH, registered in `sys.modules` first (`@dataclass`
  needs it) -- the module must stay standard-library only.
  `install_service.py` registers `-Priority 4`; an existing task changes
  only when re-registered (owner action). `KAZMA_PROCESS_PRIORITY=keep`
  opts out; `fast_test.py` sets it for the processes it lowers. Tests:
  `tests/test_process_priority.py`, the guard's in
  `tests/test_guard_integration.py` (a guard started below normal; `keep`
  as the negative control).

- **A probe that got an answer is not a probe that got none** (2026-09-30).
  `probe()` returns `ProbeResult` (unpacks as `(healthy, detail)`, plus
  `answered`). Three unanswered probes in a row (a timeout, a refused
  connection) restart Kazma as before. An answered "not ready" (a 503 naming
  the database) is ridden out: `health.dependency_down`, one page, and a
  restart only after `KAZMA_GUARD_DEPENDENCY_OUTAGE_S` (600) -- a restart
  cannot bring a database back (§44). An answer carrying `restart_required`
  (readiness sets it for the volatile settings store: a boot while the
  database was away keeps the in-memory fallback for the life of the
  process) is restarted like no answer. A result without the flags gets the
  old rule. `tests/test_guard_probe_answers.py` (real HTTP).
- **One address for the guard and every command** (`health_url`, 2026-11-07):
  `KAZMA_GUARD_HEALTH_URL` when set; for a command (`--status`, `--reload`,
  `--pause --stop`, `--resume`, the update's hold) the URL the running guard
  recorded (`health_url` in its state file); else the port the server binds
  (`install_port`: `KAZMA_PORT` from the environment or the install's .env,
  one key read, the file never loaded); else 9090. On the drill VM Kazma
  bound 9191 and `--status` probed the built-in 9090 whatever was set,
  calling a healthy Kazma "not answering"; a port in .env now needs no
  `setx`. Gate: `tests/test_guard_health_url.py` (no other read of the
  variable or the built-in URL).
- **Waiting for its owner is up, never a restart** (the drill VM, 2026-11-06:
  a fresh install with no provider key was restarted every 15 minutes, its
  log saying only `not_ready`, while its owner tried to set it up). A
  readiness check only the owner can fix carries `needs_setup` (the
  provider check: no active model, no API key for a remote provider), and
  `/health/ready` lists `needs_setup` (and what to do, `setup`) when EVERY
  required check that fails is one; it still answers 503. The guard reads it
  (`ProbeResult.needs_setup`): at start the child is up
  (`child.up_needs_setup`, not `child.never_ready`), while running it counts
  toward no restart, said once and paged once (`health.needs_setup`,
  `_say_needs_setup`), and `health.setup_done` when it is ready.
  `--reload` ends there ("up on build X, but needs setup"), `--status` says
  "up, needs setup" (it said "not answering" for any answered failure), and
  every required check that is not ok is named in the guard's detail, not
  only `failed` ones. Gates: `tests/test_readiness_needs_setup.py` (the real
  readiness body read by the guard's own probe over HTTP; a real failure
  beside it as the negative control), `tests/test_guard_integration.py`
  (a real guard: no restart past the start budget and the outage runway, the
  same body without the flag restarted as the control).
- **Waiting for a dependency outside Kazma is up too** (2026-11-12: a Windows
  update restarted the PC, nobody signed in, Docker Desktop never started; the
  live install requires code execution, readiness answered 503, and the guard
  restarted a Kazma that served chat 18 times in six hours, paging each
  time). A failing check that waits for something no restart of Kazma brings
  back carries `waits_for` (the code-execution check: the Docker engine not
  installed, not running or not answering); `/health/ready` lists
  `waits_for` (`{check: dependency}`) when every failing required check
  waits for the owner or for such a dependency and no `restart_required`
  is said; it still answers 503. The guard reads it
  (`ProbeResult.waits_for`, `_answered_waits`): at start the child is up
  (`child.up_waiting_for`), while running it counts toward no restart,
  said once (`health.waiting_for`), paged once after `WAIT_PAGE_S` (300 s)
  unless the guard's Docker watch sees the engine down and pages it
  (`_deps_speaks_for`: one page per problem), and `health.wait_over` (with
  `health.ready_again` when the guard paged) when it is ready. The check says
  a Docker outage once and its end once (`_say_docker_down`). The Docker
  watch pages at `DEPS_PAGE_AFTER_S` (300 s; 180 s in `DEPS_STUCK_STATES`,
  nobody signed in or the guard as SYSTEM), so its page comes before Kazma
  Watch's own (seven minutes in), and `deps.engine_down`'s condition
  `docker_away` answers Kazma Watch's `dependency_down` and
  `kazma_restarting` (`ANSWERED_BY`). Windows' automatic sign-in after an
  update was on that morning and signed nobody in (the update restarted
  twice): only a sign-in starts Docker Desktop, and the page says so.
  Gates: `tests/test_readiness_waits_for_docker.py` (the real readiness body
  read by the guard's probe over HTTP; a database failure beside it as the
  control), `tests/test_guard_integration.py` (a real guard; the body
  without the flag restarted as the control),
  `tests/test_guard_dependencies.py` (one page with the Docker watch, the
  wait's own page and recovery, the earlier Docker page).
- **A restart reason pages once an hour while it repeats**
  (`notify_restart`, `RESTART_REPAGE_S`), saying how many restarts there
  were since; a healthy start ends the series (`notify_recovered`). Every
  restart is in `guard.log`. The 900 s page cooldown let a restart every 15-20
  minutes page each time. A page quotes the server's last words only when it
  ended by itself (`_ended_by_itself`: an exit, an exit while starting), never
  the boot lines of a server the guard stopped.
- **The server's last words are kept.** The guard sends the server's stderr to
  `.kazma/server.stderr.log` (beside `guard.log`, rotated at 2 MB) and quotes
  the lines the server wrote before it went in `guard.restarting`
  (`stderr_tail`) and the page. `serve.py` enables `faulthandler` and prints
  a failure's whole traceback there: on 2026-09-25 "process exited (code 1)"
  left no trace anywhere (the message went to stdout, nothing kept it).
- **The guard runs the document worker broker** (`DocumentBroker`,
  2026-10-15; the Kazma side is §19 E1). Same loop, same thread: `tick()` runs
  in every wait (`_sleep`, `_wait_ready`) and never raises into the guard
  (`broker.internal_error`). It runs the broker when Kazma's request asks
  (`KAZMA_GUARD_DOCUMENT_BROKER=auto`; `on` always, `off` never) and starts
  it BEFORE spawning Kazma when the persisted request wants one; restarts it
  on exit (5 s .. 5 min), after three missed status probes, and at a spawn
  when its script, image or token changed (`broker.code_changed`); retries a
  missing image (exit 4) or a silent Docker engine (exit 5) every 30 s,
  logging it once; pages once after three failed starts (`broker.failed`) or
  ten minutes without Docker, and once on recovery; stops it gracefully
  (CTRL_BREAK / SIGTERM, 45 s, then the tree) at a pause, when no longer
  wanted, at guard shutdown, `--once` and a guard crash; reaps a broker an
  earlier guard left (`broker.orphan_reaped`). Its environment is scrubbed
  (`_BROKER_ENV_KEEP`: the guard's Telegram token never reaches the Docker
  holder). The loopback port is kept for the install
  (`KAZMA_GUARD_BROKER_PORT`, else the recorded one while free, else the first
  free one from a port derived from the install folder, through all of
  20000-29999). **A port the broker cannot listen on is replaced, and Kazma
  follows** (2026-10-31): live, Docker Desktop restarted, Windows reserved
  20181-20780, the recorded 20527 with it, and documents were refused for
  hours -- the 64-port search could only fall back to the reserved port. On
  exit 7 the guard moves the broker at once (`_move_port`, three times in a
  row at most; a pinned port is never moved), remembers the addresses it
  handed out (a request naming one is still Kazma's), and hands Kazma
  `KAZMA_DOCUMENT_BROKER_URL_SOURCE=guard`: Kazma then takes the broker's
  current loopback address from the guard's state file
  (`broker_supervision.broker_url`), no restart. Gates:
  `tests/test_document_broker_port.py`, the port tests in
  `tests/test_guard_document_broker.py` (the old search as the negative
  control, and the real guard with a fake broker refusing its first port).
  **A restarted broker takes back its own port** (Linux CI, 2026-11-10): the
  guard's status probes leave the broker's port in TIME_WAIT, and on Linux a
  plain bind is refused there for a minute, so a killed broker could not
  listen again until the guard gave up. On POSIX the broker's listener, its
  test bind and the guard's `_port_is_free` set SO_REUSEADDR (a port another
  socket LISTENS on is still refused); never on Windows, where it would let
  a bind take a port in use. The broker sees a client leave with `poll`
  (`worker_broker._readable_now`): `select.select` refuses a descriptor
  numbered 1024 or more on Linux. Gates: `tests/test_document_broker_port.py`,
  `tests/test_broker_sees_a_client_leave.py`.
  The token
  `.kazma/document_broker.token` is made once, restricted to the guard's
  account (`icacls /inheritance:r` + its SID) before the secret is written,
  never logged; it is restricted again at every start, and a file that
  cannot be restricted gets no secret and stops the broker (2026-10-15: a
  failed restriction was a warning and the token was served anyway). The server gets URL, token file and request file through
  `spawn()`'s env, like the state and reload files. The state file's
  `document_broker` is what readiness reads; `guard_features` lists
  `document_broker`, and `guard_code` lets `--status` say the running guard
  is older than its file -- a reload restarts Kazma, never the guard, so a
  guard change needs the task restarted once (ops doc, "Restart the guard").
- **Every step of the stop is timed** (`shutdown.StopClock`, resilience plan
  C3). `_on_shutdown` marks each step of `_shutdown_services` as it ends
  (`_mark_stop`) and logs one line at the end: every step's time, or a WARNING
  naming the three slowest when the stop ran over its budget
  (`KAZMA_SHUTDOWN_BUDGET_S`; target zero). Gate: `tests/test_stop_clock.py`.
- **An update window** (`kazma maintenance begin|end`, plan B2b): begin
  pauses the guard (when idle, under a maintenance reason, expiring by itself)
  so planned work restarts and pages nothing, and the heartbeat says
  maintenance; end resumes, waits five minutes for `/health/deep`, and pages
  (`kazma_guard.py --page`, the guard's own notifier) if Kazma is not healthy.
  Gate: `tests/test_maintenance_window.py`.
- **The guard starts what Kazma runs on** (`Dependencies`, 2026-11-04). Live
  2026-11-03 a topgrade update left Docker Desktop stopped; the database
  container was gone and the guard restarted Kazma 34 times over eleven hours.
  Same loop as the broker (`deps.tick()` after `broker.tick()`, never raises:
  `deps.internal_error`); `KAZMA_GUARD_START_DEPENDENCIES` (`auto`, `off`).
  - *Docker Desktop*, after two minutes without an engine answer
    (`docker version`): on Windows `_launch_docker_desktop` registers the
    on-demand task `KazmaDockerDesktop` (InteractiveToken, LeastPrivilege,
    IgnoreNew; UTF-16 XML, deleted after) and runs it, so Docker starts in
    the signed-in user's session with normal rights, never the guard's
    elevated token; elsewhere `docker desktop start -d`. Never while Docker's
    installer runs (`docker_updating`); a running Docker Desktop that is not
    ready is asked once (`desktop_nudged`), never launched twice; no launch
    with nobody signed in (`no_session`) or as SYSTEM (`cannot_launch`). A
    launch that does not bring the engine up in 240 s, or fails, backs off
    300/900/1800 s. One page at ten minutes without an engine, one on the
    recovery; the broker's own Docker page is left to it while it watches the
    engine (`owns_engine_alerts`). Docker answering again ends the restart
    backoff at once (`_sleep(wake_on_deps_recovered=True)`), and the restart
    page says what Docker is doing (`restart_note`).
  - *Containers*: only the ones the server's record names
    (`kazma_core.guard_dependencies._publish_dependencies`, at boot and on the
    maintenance sweep "guard dependencies", written to
    `KAZMA_GUARD_DEPENDENCIES_FILE`, only when it changed, never on a silent
    Docker): the containers publishing a loopback port Kazma's database,
    memory mirror, graph or search address names (`_match_containers`; a
    guessed address takes only a Kazma-named container, a tie goes to the
    running one, then the last record, else nothing; `KAZMA_DB_CONTAINER`
    counts only when it publishes the database's port, and with none running
    a port that answers belongs to a server outside Docker, so no stopped
    container is named for it). The guard starts one
    only when an engine outage stopped it, twice seen stopped, 60 s after the
    engine came back; a container stopped while the engine stayed up, or
    after it ran again, is left alone (`deps.container_left_stopped`). Three
    failed starts page once; the recovery pages once. The state file's
    `dependencies` and `--status`'s `docker` line say what it does;
    `guard_features` lists `dependencies`.
  Gate: `tests/test_guard_dependencies.py` (fake clock, fake docker, the task
  XML; the guard without the check as the negative control; the broker page
  with and without it).
- **The guard watches the free disk space** (`DiskWatch`, resilience plan
  B2a). Once a minute, off the same loop (`_broker_tick`), it reads the data
  folder's drive and, when Docker keeps its disk image on another drive,
  that one (`watched`). Either measure crosses a level (`disk_level`: low 15 %
  or 20 GB, critical 5 % or 5 GB); leaving takes both clear by 1 % and 1 GB,
  so a drive at a line is one page. One page per level, naming what stops
  first (documents below their 512 MB floor, then backups, then the
  database; on Docker's drive, the database), and one "Disk space is back"
  only after a page. The state file's `disk` and `--status`'s `disk` line say
  the level; `KAZMA_GUARD_DISK_WATCH=off` turns it off (the root conftest
  does: a CI runner has less than the low line). Gate:
  `tests/test_guard_disk_watch.py` (the rule without the margin and a guard
  without the watch as negative controls).
- **A restarted guard pages nothing the guard before it paged** (2026-11-13).
  A guard update restarts the guard while Kazma is paused; each one sent a
  second "Kazma supervision paused" (the new guard read the same pause file)
  and "Disk space low" again (its disk watch started every drive at ok). The
  state file says what was announced: `pause_announced` (the pause's
  signature, cleared at the resume) and the disk record, whose levels a new
  `DiskWatch` takes as paged when younger than `DISK_INHERIT_S` (6 h; older,
  a level still there is paged again as a reminder). Gate:
  `tests/test_guard_restart_pages_nothing_new.py` (two guards on one state
  file; the old behaviour as each control).
- **The guard watches the power** (`PowerWatch`, resilience plan B2c). A USB
  UPS is a battery to the OS (`_power_state`: `GetSystemPowerStatus`,
  `/sys/class/power_supply`). Every 30 s: on battery, one page; at
  `POWER_STOP_PERCENT` (30 %) on battery, a pause of its own (reason
  `power:`, no expiry: it waits for the power, not a clock) so the guard
  stops Kazma gracefully, one page -- never over another pause; power back
  (also after a reboot), it lifts only its own pause and Kazma starts, one
  page. No battery: nothing. `KAZMA_GUARD_POWER_WATCH=off` turns it off (the
  root conftest does). Gate: `tests/test_guard_power_watch.py` (a guard
  without the watch as the negative control).
- **The guard sends a heartbeat to an outside observer once paired**
  (`OutsideWatch`, resilience plan B1). Nothing on this machine can say the
  machine is off, asleep or cut off. With `<install>/.kazma/observer.key` (32+
  characters, written by `kazma observer pair`, restricted to the guard's
  account like the broker token: a key it cannot restrict is never used,
  `key_unprotected`), it POSTs every minute to
  `KAZMA_GUARD_OUTSIDE_WATCH_URL` (https, or http to this machine only),
  signed `v2=<HMAC-SHA256>` of the request's route and body (`sign_request`
  over `signed_message`; the shared vector
  `tests/fixtures/observer/heartbeat_vector.json`, read by the observer's
  verifier too; "Signatures bind the route" below). The body is an allowlist (`heartbeat_body`):
  states, numbers, the readiness checks' NAMES (`ProbeResult.checks`, never
  the detail's text), the commit, the database, Docker, cloudflared, disk,
  power and the page receipts (`receipts_allowlist`: an inside catalog key
  that opens an incident, checked against the guard's copy of the catalog,
  a time, delivered; the observer's `src/receipts.js` cuts the vector's
  `pages` the same way; §33 "One page per problem") -- no chat, path, name
  or error ever. One send in flight, on a daemon
  thread with a 5 s bound; a hung observer is `skipped`. The answer (`reached`,
  `rejected`, `unreachable`) is logged once per change and kept in the state
  file's `outside_watch` and `--status`'s `outside` line.
  `KAZMA_GUARD_OUTSIDE_WATCH=off` wins over a key. **Every request the guard
  makes carries its own name** (`_request`, `GUARD_USER_AGENT`): Cloudflare's
  bot check refuses urllib's ("Python-urllib", error 1010), and on the first
  live pairing (2026-11-06) every heartbeat was a 403 before the Worker.
  `kazma observer status` reads the guard's state file where the guard keeps
  it when no path was handed over (`outside_watch._guard_state_path`). Gate:
  `tests/test_observer_heartbeat.py` (the snapshot sent as it is, a guard
  without the watch, and a bare URL or Request (11 in the old guard), as
  negative controls; the name on a real request).
  **The observer** is `deploy/observer` (a Worker: `src/diagnose.js` the
  table pull-probe x heartbeat -> condition, a heartbeat missing after 90 s,
  a 5-minute `/health/ready` 503 superseded by a heartbeat saying ready 45 s
  or more later, `READY_SUPERSEDED_S` -- a probe that met a reboot paged
  "dependency down" for 6 minutes on the first live day;
  `src/incidents.js` one page on the third failed minute, hourly reminders,
  one recovery page, maintenance quiet, an unsent page retried; D1
  `schema.sql`; the status page behind Access and the Worker's own check of
  Access's token, closed when unset). **Kazma's side** is
  `kazma_core/observability/outside_watch.py`: `kazma observer pair` writes
  the pairing file as JSON (install id, key, the observer's address) only
  after the observer accepted, restricted while still empty; the guard reads
  the same file (`OutsideWatch._load_key`). Settings -> Alerts -> Kazma
  Watch (`GET /api/settings/alerts`, never the key; pairing and unpairing
  from the page too, §33) and the daily digest's line read the observer's
  summary with a request signed by the key. The pairing file is a Kazma secret file
  (`store_registry.SECRET_FILE_NAMES`).
  Gates: `tests/test_outside_watch.py` (the guard reads what pairing writes;
  a refused pairing or an unrestrictable file leaves no key; it runs the
  Worker's own `node --test` suite, a D1 stand-in over `node:sqlite`).
  **`kazma observer setup` puts a watch on the person's own Cloudflare**
  (2026-11-14, REMAINING 3.2; `kazma_core/observability/watch_setup.py`):
  one guided command -- `wrangler` (pinned `WRANGLER_VERSION`, through npx)
  signs in, makes the D1 database and its tables, deploys the Worker from
  this build (`worker_source`: the wheel's `watch_worker`, force-included
  from `deploy/observer`, else the checkout) to workers.dev, sends the
  secrets on stdin (`secret bulk`), keeps the pairing token in the vault
  (`PAIRING_TOKEN_NAME`, install-scoped), pairs, tells the zone, sends a
  test page. A second run keeps database, token and bot; `--update` asks
  nothing; an install paired elsewhere is refused without `--move`. With no
  address the Worker judges by heartbeats alone (`PULL_URL` empty:
  `diagnose` -> `heartbeat_silent` when they stop; nothing watched with no
  install). The pairing (`POST /v1/pair`, Bearer code) is the contract a
  watch Kazma hosts later will use: the install does not change. Gates:
  `tests/test_watch_setup.py` (a stand-in wrangler and Telegram; no secret on
  a command line or on disk), `deploy/observer/test/heartbeat_only.test.mjs`
  (the old reading, which blamed the tunnel, as the control). Drilled once
  on Cloudflare with a throwaway name, then deleted.
  **Signatures bind the route** (2026-11-11). Every signed request -- the
  guard's heartbeat, Kazma's summary, settings, test page and leave -- sends
  `X-Kazma-Signature: v2=<hex>`, HMAC-SHA256 under the pairing key of
  `"v2\n<timestamp>\n<METHOD>\n<path>\n" + body` (a newline is in neither a
  method nor a path; `<path>` is the request line's, the body empty for a
  GET). Three copies, held byte for byte to the vector's `v2` routes: the
  guard's `signed_message` (stdlib), Kazma's `outside_watch._sign`, the
  Worker's (`deploy/observer/src/verify.js`: its v2 message and request
  check). The v1 form (`"<timestamp>." + body`) bound no route: one signed
  heartbeat body replayed to `/v1/leave` unpaired the install. It is
  **retired** (2026-11-12, `ACCEPT_V1 = false`, once the owner's guard and
  Kazma both signed v2; `wrangler tail` showed no v1 request): every signed
  route refuses it with `v1_retired` before any key is read. Never set it
  true again; an install that cannot sign v2 updates. Gates: the node suite
  (`deploy/observer/test/observer.test.mjs`,
  `deploy/observer/test/watch_settings.test.mjs`: a v2 signature for one
  route refused on another; v1 refused on the heartbeat, the summary,
  settings, test page and leave, the install still paired; the verifier
  with v1 accepted as the negative control), `tests/test_observer_heartbeat.py`,
  `tests/test_outside_watch.py`.
  **A report's page is plainly a report** (2026-11-15): `POST /v1/report`
  (`deploy/observer/src/reports.js`, open to anyone) pages only a `version`
  matching `VERSION_RE` and a note cleaned in `reports.js` (control, direction and
  invisible characters out, one line, links and `@` made inert, 160
  characters), and the page says someone sent it and nothing in it is
  checked: a crafted version made fake Watch alerts.
- **A secret is compared with `secret_compare.same_secret`** (UTF-8 bytes;
  2026-11-15): `hmac.compare_digest` raised on non-ASCII text, and a webhook
  answered 500 with a traceback. Nothing else calls `compare_digest` but the
  standard-library document broker (`tests/test_secret_compare.py`).
- **Settings' "Restart server" asks the guard.** A server the guard started
  writes the guard's reload request (`supervisor_watch.request_guard_reload`;
  the guard hands the path over in `KAZMA_GUARD_RELOAD_FILE`, an older guard
  keeps it beside its state file) when the heartbeat is under a minute old,
  and the guard does its graceful reload. It used to spawn a detached copy and
  `os._exit`: the guard restarted its own child and killed the copy as a
  foreign server, and no shutdown hook ran. The page reloads when a
  different process answers (`/health/live` `build.started_at`).

Gates: `tests/test_guard_owns_reload.py` (each with a negative control),
`tests/test_guard_integration.py` (the real guard against a fake server:
graceful reload, an ignored stop, a leftover request, the heartbeat, a
dependency outage ridden out and one past its runway, a crash's stderr kept,
the server's own reload request), `tests/test_idle_reload.py`,
`tests/test_settings_restart_via_guard.py`,
`tests/js/test_settings_restart_reload.js`, `tests/test_guard_document_broker.py`
(the supervisor on a fake clock with the old no-broker guard as control; the
real guard with a stand-in broker: before Kazma, the handed address, a killed
broker restarted, a failing one paged once, the request deciding, `--once`
stopping it, an orphan reaped).

