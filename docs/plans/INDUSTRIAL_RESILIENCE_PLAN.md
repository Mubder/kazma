# Industrial resilience plan

Written 2026-11-05, after a day of using the live install the way its owner
does (long chats, documents, research, crawls, the Scheduled page, X,
approvals) and reading its logs back to 6 October. This plan turns the ten
weak spots found that day into work: every action, what it costs, what the
owner must decide, what it depends on, the test that proves it, and the
stage it belongs to.

It also covers two additions the owner asked for:

- **kazma.ai as an outside observer.** A small service on the same
  Cloudflare account as kazma.ai watches the install from outside and pages
  the owner when it, its machine or its tunnel stops (Part B).
- **Postgres as a normal OS service on every install.** Windows, macOS,
  Linux and WSL, installed the way Kazma's other dependencies are
  (Parts A1 and K).

## The standard

Kazma is industrial when all five hold, and each is proven by a drill that
runs on a schedule, not by a test that ran once:

1. **It never stops silently.** Every stop is seen from outside within three
   minutes and reaches the owner's phone.
2. **It never loses accepted work.** A message, a turn, a reply, a scheduled
   job or a document that Kazma accepted is finished, or the owner is told it
   was not and what to do.
3. **It recovers by itself, or rides out what a restart cannot fix.** A
   power cut, a database restart, a Docker update or a reload ends with Kazma
   serving again, with nobody signed in to the machine.
4. **Every failure ends with a plain statement and one next step.** No
   "something went wrong", no stack trace, no fake success.
5. **It is proven.** Soaks and chaos drills run on a schedule and their
   results are in the weekly report.

## The evidence

From the live logs (6 October to 5 November 2026) and the day of use:

| What | Count |
|---|---|
| Chat turns finished / failed | 133 / 7 (5 were deliberate fault tests; the 2 real ones, 31 October, are fixed) |
| Guard restarts since 6 October | 48, of which 42 "never became healthy" |
| The 4 November incident | topgrade stopped Docker Desktop; the database container went with it; the guard restarted Kazma about every 20 minutes for 11 hours |
| Database pool timeouts | 6,477 in all, 3,043 on 4 November |
| CRITICAL log lines | 594 |
| Postgres password failures | 660 (the switch to the `kazma_app` role on 3 November) |
| Reloads | 163; 8 ran past the graceful stop; 9 "graceful shutdown exceeded" on 2 November |
| Replies cut off at the output limit | 29; 8 file writes refused because their arguments were cut |
| One DOCX turn | about 1.43 million tokens |
| SearXNG engines suspended | 14 times |
| Chat-app send failures | 57 Slack timeouts, about 65 Discord drops, 15 Telegram failures |
| Plain-format log lines | 20,409, of which 12,156 are "Batch set" |
| Alerts still open | requirements unmet (7), document worker image outdated (5), MCP (2), restore drill unverified (1), malware signatures (1) |

What the machine is today: Windows 11, one PC, no UPS. Kazma at
127.0.0.1:9090 behind Cloudflare Tunnel and Access. Postgres 16 in the Docker
container `kazma-db-win` (127.0.0.1:5433). SearXNG and Neo4j in Docker too.
The guard runs as the boot task `KazmaAgent` and does not need anyone signed
in. Docker Desktop does: it is a per-user program, and the guard's own
"start Docker" step refuses with nobody signed in (`no_session`). **After a
power cut with nobody signed in, Kazma starts and its database never does.**
That one fact is why Part A1 comes first.

## How each item is written

Each item has:

- **Why.** The evidence.
- **Actions.** Everything needed, in order.
- **Cost.** Money (one-off and monthly) and work (days).
- **Owner decides.** Only what is the owner's to decide; everything else has
  a recommendation and proceeds.
- **Depends on.** What must land first.
- **Proof.** The test or drill, with its negative control (the old code or
  setup must fail it).
- **Stage.** When it lands.

## The stages at a glance

| Stage | What | Work | Money |
|---|---|---|---|
| 1 | Outside watch (B1), disk alert (B2a), one "database away" state (A3), refusals stop ending turns (D1), turn budget (E1), chat-reply outbox (F3), clear the open alerts (H) | about 2 weeks | $0 (optional $5/month) |
| 2 | Native Postgres on every OS (A1, K1), WAL archiving and point-in-time restore (A2), resume after a reload (C1), check after every deploy (C2), clean shutdown (C3), maintenance wrapper for updates (B2b) | about 2 weeks | $0 software; R2 within the free tier |
| 3 | Second search (F1), backup model (F2), long files in parts (D2), a summary that matches the records (D3), duplicate and re-read guards (D4), read-only boot (A4), every system dependency installed by Kazma (K2) | about 2 weeks | search $0 at up to 1,000 queries a month; backup model pay per use |
| 4 | Quiet logs (G1), one open-problems list (G2), weekly report against targets (G3), the UI truths (I) | about 1 week | $0 |
| 5 | Soaks and the weekly chaos drill (J), power watch with a UPS (B2c) | ongoing | UPS about $80-250 one-off; optional test machine |

## Progress

The owner approved the plan on 2026-11-05 with every recommended option
(native Postgres 16; the observer on kazma.ai's Cloudflare account with its
own "Kazma Watch" bot and a private status page; Tavily; 500,000 tokens a
turn and no daily dollar limit; a failed deploy reverts, then pulls; a safe
interrupted turn continues by itself).

| Item | State | Where |
|---|---|---|
| D1 refusals stay correctable | done | `bd98c710b` |
| E1 turn and daily budgets | done | `4b1a50864` |
| A3 one "database away" state | done | `f1c228347` |
| B2a disk watch, B1 the guard's heartbeat | done | `551722e01` |
| B1 the observer Worker, pairing, Settings card, digest line | built; the Cloudflare side is the owner's (`deploy/observer/README.md`) | this batch |
| F3 chat-reply outbox | done | this batch |
| H open alerts: malware signatures refreshed by Kazma itself | done | this batch |
| A1 `kazma setup postgres`, the guard's service watch, `kazma doctor` | built; the live move is the owner's (elevated terminal) | this batch |
| A2 WAL archiving, base backups, offsite, restore | built; active once A1 runs | this batch |
| C1 a cut turn continues | done | this batch |
| C2 `scripts/deploy_live.py` | done | this batch |
| C3 every step of the stop timed | done | this batch |
| B2b `kazma maintenance begin/end` | done; topgrade and the two machine settings are the owner's | this batch |

---

## Part A — The database

### A1. Postgres as a normal OS service, on every install

**Why.** Docker Desktop is a desktop program that starts when a user signs
in. On 4 November an update tool stopped it and the database was gone for
11 hours. After a power cut, nobody is signed in, so it never starts.
A database installed as an OS service starts at boot, before anyone signs in,
on every operating system Kazma supports.

**Does it work the same way on every OS?** Yes, with the same model: the
operating system's own service manager starts Postgres at boot and restarts
it if it dies, and Kazma's guard only watches it.

| OS | How Postgres is installed | What starts it | Starts with nobody signed in? |
|---|---|---|---|
| Windows 10/11 | `winget install --id PostgreSQL.PostgreSQL.16 --exact` (the EDB installer, hash-checked by winget's manifest), unattended | A Windows service (`postgresql-x64-16`), start type Automatic, runs as NetworkService | Yes |
| macOS | Homebrew `postgresql@16`, or Postgres.app | `sudo brew services start postgresql@16` registers a LaunchDaemon (starts at boot). Without `sudo` it is a LaunchAgent and starts only at sign-in | Yes with `sudo` (LaunchDaemon); see the FileVault note |
| Linux (Debian/Ubuntu) | The PostgreSQL project's own apt repository (PGDG), package `postgresql-16` | systemd: `systemctl enable --now postgresql` | Yes |
| Linux (Fedora/RHEL) | PGDG dnf repository, `postgresql16-server`, then `postgresql-16-setup initdb` | systemd: `systemctl enable --now postgresql-16` | Yes |
| WSL 2 | As Linux, inside the same distro as Kazma, with `systemd=true` in `/etc/wsl.conf` | systemd inside the distro; the distro itself is started at boot by the Windows task that runs `wsl.exe` (already done for Kazma by `scripts/fix-cloudflare-tunnel-tasks.ps1`) | Yes, while that Windows task runs it |

Notes per OS:

- **macOS and FileVault.** With FileVault on, a Mac that loses power stops at
  the unlock screen before macOS starts, so nothing starts until someone
  types the password. A Mac server needs FileVault off, or a planned restart
  with `fdesetup authrestart`. Kazma's own macOS service is a LaunchAgent
  today (`scripts/service/install_service.py` installs into
  `~/Library/LaunchAgents` and says to move it to `/Library/LaunchDaemons` for
  an always-on machine); the installer gains a `--daemon` option that does
  that move itself.
- **Linux.** Kazma's own unit is a systemd user unit; the installer already
  runs `loginctl enable-linger` so it starts at boot. A disk encrypted with
  LUKS waits for its passphrase at boot unless it unlocks by TPM.
- **Windows.** BitLocker with TPM-only unlock starts unattended; BitLocker
  with a PIN waits for the PIN.
- **WSL.** Two ways work. Recommended: Postgres in the same distro as Kazma,
  so they start and stop together. Alternative: Postgres as a Windows service,
  reached from WSL at `localhost` with mirrored networking
  (`networkingMode=mirrored` in `.wslconfig`, Windows 11 22H2 or later).
  Never both.
- **Version.** Postgres 16 everywhere, the same as CI (`postgres:16`) and the
  compose files (`postgres:16-alpine`), so a dump restores across them.
  Postgres 16 is supported until November 2028. A move to 17 or 18 is its own
  item later, with a `pg_upgrade` drill.

**Actions.**

1. **`kazma setup postgres`**, a new CLI command (`kazma-cli/kazma_cli/`,
   beside `doctor.py`). One command for every OS:
   1. Detect the OS and package manager (winget, Homebrew, apt, dnf). On an
      OS without one, print the manual steps and stop.
   2. Print the plan: the package, the port, the data folder, the service
      name. Ask before doing anything (`-y` to skip the question).
   3. Install Postgres 16 as an OS service, set to start at boot, listening
      on `localhost` only, with `scram-sha-256` sign-in. On Windows the EDB
      installer is run unattended (`--mode unattended --unattendedmodeui none
      --disable-components stackbuilder,pgAdmin`, with the service name, port
      and data folder). The installer takes the superuser password on its
      command line; the command uses a throwaway one and changes it at once
      through `psql`, reading the new one from a file only the service
      account can read.
   4. Set the service to restart on failure: on Windows `sc failure` (restart
      after 5 s, 30 s, 60 s); on Linux a systemd drop-in
      (`Restart=on-failure`); on macOS the brew LaunchDaemon already has
      `KeepAlive`.
   5. Create the `kazma` database and the `kazma_app` role with
      `deploy/postgres/kazma_app_role.sql` (no superuser, the owner of every
      Kazma object).
   6. Generate the role's password, write `KAZMA_DATABASE_URL` into the
      install's `.env`, and never print it. Restrict `.env` to the owner
      (the security report's file check, §56).
   7. Turn on WAL archiving (A2).
   8. Sign in once as Kazma will (`scripts/check_database_login.py`) and
      report the result in plain words.
   9. Record what it did in `<data dir>/setup_record.json`, which `kazma
      doctor` and the guard read.
   - Idempotent: a second run checks and repairs, never reinstalls.
   - `--check` only reports; `--dry-run` prints the commands.
2. **`kazma doctor`** checks the database service: installed, running, set to
   start at boot, restart-on-failure set, listening only on localhost, the
   major version, WAL archiving fresh. Each failure names the command that
   fixes it.
3. **The guard watches an OS service, not only a container.**
   `kazma_core/guard_dependencies.py` matches the database address to a
   container (`_match_containers`) today. It learns a second kind: a service
   named in `setup_record.json`. The guard checks it (Windows `sc query`,
   Linux `systemctl is-active`, macOS `launchctl print`, plus a TCP connect to
   the port), starts it once if it is stopped after boot (on Windows the guard
   runs elevated and can; on Linux and macOS the OS restart rule does it),
   pages once if it stays down, and pages once on recovery. The heartbeat
   (B1) carries the database's state.
4. **Move the live install off the container.** A short planned window,
   chosen when Kazma is idle:
   1. `kazma setup postgres` on the live machine (port 5432; the container
      stays on 5433).
   2. Pause the guard for maintenance (the existing `kazma update` pause).
   3. `pg_dump -Fc` from `kazma-db-win` (inside the container, as today's
      backups do).
   4. `pg_restore` into the native server as `kazma_app`.
   5. Compare row counts of every table in `KAZMA_PG_TABLES` between the two.
   6. Point `KAZMA_DATABASE_URL` at port 5432 (the owner edits `.env`, or the
      command writes it with the owner's yes).
   7. Resume; the guard reloads Kazma; check `/health/deep`, a test turn and
      the chat history.
   8. Stop the container, keep it (and its volume) for 30 days as the way
      back, then remove it. The memory mirror's own address
      (`memory.backends.state.url`) moves with it.
   - Rollback at any step before 7: start the container and put the old
     address back.
5. **Docker stays for what needs it**: the document worker's container
   profile, the `python_exec` sandbox, SearXNG and Neo4j. With the database
   native, Docker being down means documents wait (retryable) and search falls
   back (F1); chat, memory, settings, approvals and scheduled jobs keep
   working.
6. **Docs.** `docs/docs/ops/postgres-and-saas.md` and
   `docs/docs/guide/quickstart.md` describe `kazma setup postgres` per OS,
   with the FileVault, BitLocker and LUKS notes. SQLite stays the default
   for a new install; Postgres stays opt-in.

**Cost.** Software $0 (Postgres is free). Work: 4-5 days (the command for
four package managers, the doctor checks, the guard's service watch, docs),
plus a 15-minute window for the live move.

**Owner decides.** Move the live install to a native service (recommended)
or keep the container. Postgres 16 (recommended) or 17.

**Depends on.** Nothing. A2 is part of it.

**Proof.**
- `tests/test_setup_postgres.py`: each OS path with the package manager
  faked; idempotence; the password never in output or argv after the swap;
  `--check` changes nothing.
- A real install drill on Linux (a Debian container with PGDG): install, sign
  in as `kazma_app`, every store's setup, a dump.
- **The power-cut drill** on the live machine: shut it down, power it on, do
  not sign in, and check from a phone (B1's status page) that `/health/ready`
  answers ready within 5 minutes. Negative control: the same drill on the
  container setup today never becomes ready.
- macOS and Windows drills on a VM or spare machine, recorded in this plan.

**Stage.** 2.

### A2. WAL archiving and point-in-time restore

**Why.** Postgres is dumped every 6 hours today (`native_pg_backup`, then
restic offsite). A disk failure can lose up to 6 hours of chats, settings and
checkpoints. Write-ahead log (WAL) archiving keeps every change since the last
base backup, so a restore can stop at any minute.

**Actions.**
1. `archive_mode = on`, `archive_timeout = 300` (a segment at least every 5
   minutes), `archive_command` copying each finished segment, compressed, to
   `<data dir>/backups/pg/wal/` (a small standard-library script, the same on
   every OS; it fails if the copy fails, so Postgres keeps the segment and
   tries again).
2. A weekly base backup: `pg_basebackup -Ft -z` into
   `<data dir>/backups/pg/base/`, keeping two, and the WAL since the older one.
3. Offsite: the existing R2 provider in `kazma_core/backup/cloud_sync.py`
   pushes new WAL every 5 minutes and each base backup once, and prunes with
   the local retention.
4. A maintenance sweep reads `pg_stat_archiver`: archiving failing, or the
   last archived segment older than 15 minutes, pages
   `backup.wal_archive_stale`; the daily digest shows the age.
5. A monthly restore drill (`backup/restore_rehearsal.py` gains a second
   mode): restore the newest base backup into a scratch cluster on a spare
   port, replay WAL to a target time, check that a row written just before
   the target is there and one written just after is not, then remove the
   scratch cluster. Never touches the live database.
6. A restore command for the real day: `kazma restore postgres --to
   "2026-11-20 14:05"`, into a new data folder, with the steps printed before
   anything runs.

**Cost.** R2: the free tier holds 10 GB a month and 1 million writes; at
5-minute segments that is about 8,600 uploads a month and a few GB, so $0;
past 10 GB it is $0.015 per GB-month with no download fee. Work: 3 days.

**Owner decides.** Nothing beyond A1 (R2 is already the backup target).

**Depends on.** A1 (archiving also works in the container, but the paths are
simpler native).

**Proof.** `tests/test_wal_archive_restore.py` on a real Postgres: archive,
base backup, point-in-time restore, the before/after rows; negative control:
the same restore without the WAL loses the row. The monthly drill's result is
in the weekly report.

**Stage.** 2.

### A3. One "database away" state

**Why.** In an outage every reader notices on its own and logs on its own:
about 480 lines from the document worker, about 300 from the runs registry,
17 from approvals, and more from the chat store and the checkpointer. The
settings store already holds back for 5 seconds after a refused connection
(§54); the other pools do not.

**Actions.**
1. `kazma_core/db/availability.py`: one process-wide state, fed by every
   Postgres pool's connect failures and successes (the shared pool, the
   checkpointer's, the document job store's, the task store's, the memory
   mirror's).
2. Background loops ask it before they poll and skip quietly while the
   database is away.
3. Going away is one WARNING naming the cause
   (`postgres_pool.refusal_reason`: refused password, missing role, nothing
   listening); coming back is one INFO with how long it lasted.
4. One page after 2 minutes away (`database.away`), one on return
   (`database.back`), with the duration.
5. `/health/ready` and the outside heartbeat (B1) read the same state.
6. A class gate: every background loop that reaches Postgres asks the state,
   or is declared with why.

**Cost.** 2-3 days.

**Proof.** A real Postgres stopped for 2 minutes under a running server
(the existing recovery drill's container, `KAZMA_TEST_PG_OUTAGE_CONTAINER`):
at most one WARNING per reader kind, one page, one recovery. Negative control:
today's code logs hundreds of lines.

**Stage.** 1.

### A4. Start read-only from a local copy of the settings

**Why.** With the database away at boot, Kazma waits up to 13 minutes
(`config_availability.BOOT_WAIT_S`, 780 s) before serving anything. The chat
apps are silent all that time.

**Actions.**
1. A local snapshot of the settings (`<data dir>/settings_snapshot.db`,
   SQLite), written after every successful settings write batch and at most
   every 15 minutes. Secrets stay `vault://` pointers (the vault is local
   already).
2. After 60 seconds without the database at boot, Kazma starts in a
   read-only mode from the snapshot:
   - `/health/ready` answers "not ready: database away" (the guard rides it
     out, §44).
   - The web UI shows a banner with how long the database has been away.
   - The chat apps answer each message once: "Kazma's database is away. Your
     message is kept and will be answered when it is back."
   - Incoming messages are kept in a local inbox (like the chat spool, §36)
     and answered in order when the database returns.
   - Writes are refused with a stable code.
3. The snapshot is only read, never written back.

**Cost.** 6-8 days (the largest single item).

**Owner decides.** Whether the read-only mode is wanted. With A1 the
database is away far less often.

**Depends on.** A3.

**Proof.** Boot with Postgres stopped: the chat apps answer within 2
minutes, the messages are kept, and answered after Postgres starts. Negative
control: today's boot is silent for 13 minutes.

**Stage.** 3.

---

## Part B — Watching from outside: kazma.ai as the observation platform

### B1. The outside observer

**Why.** Everything that watches Kazma today runs on the same machine: the
guard, the supervisor watch (`supervisor_watch.py`), the ops alerts. A power
cut, a dead network, a sleeping machine or a stopped tunnel silences all of
them at once. Nobody is told.

**Can kazma.ai do it?** Yes. kazma.ai runs on Cloudflare (Pages, the
`kazma-ai` project). The same Cloudflare account can run a Worker on a
schedule, keep its readings in a D1 database and send messages. The site
itself does not change; the observer is a separate Worker.

**Design.**

```text
            every minute                          every minute
 Cloudflare Worker  ── pull ──▶  my.kazma.ai/health/live   (through the tunnel)
 "kazma-observer"   ◀── push ──  the guard's heartbeat     (signed, stdlib)
 status.kazma.ai        │
        │               └─▶ D1: readings, incidents, uptime
        ├─▶ Telegram (a separate "Kazma Watch" bot) and email
        └─▶ status page (behind Access)
 Healthchecks.io ◀── the Worker pings it every run (watches the watcher)
```

1. **Pull probe.** A Cron Trigger every minute fetches
   `https://my.kazma.ai/health/live`, and every 5 minutes `/health/ready`.
   The site is behind Cloudflare Access, so the Worker sends an Access
   service token, allowed only on `/health/*` (a separate Access application
   for that path with a Service Auth policy). Kazma's health paths already
   skip its own sign-in (`auth.HEALTH_PROBE_PATHS`). It records the status,
   the time taken and the build.
2. **Push heartbeat.** The guard posts every 60 seconds to
   `https://status.kazma.ai/v1/heartbeat`. Standard library only (the guard
   imports nothing of Kazma's), a 5-second timeout, run from its wait loop so
   it never holds up supervision. The body:
   - an install id (a hash, not a name)
   - the guard's state and the child's (running, restarting, paused)
   - ready or not, and why
   - the database's state (A3), Docker's, cloudflared's (the service is
     running or not)
   - free disk space in percent for the data drive
   - battery state, if any (B2c)
   - the build and uptime

   No chat, no content, no paths, no names. Signed with HMAC-SHA256 under a
   per-install key in `.kazma/observer.key`, restricted to the guard's account
   like the broker token.
3. **Diagnosis.** The two signals together say what broke:

   | Pull probe | Heartbeat | What it means | The page says |
   |---|---|---|---|
   | 200 | fresh | Up | (nothing) |
   | 530 / error 1033 | missing for 3 min | The machine is off, asleep, without power or without network | "Kazma's machine is not reachable (power, network or sleep)" |
   | 530 / error 1033 | fresh, cloudflared down | The tunnel only | "The tunnel is down; Kazma itself is running" |
   | 502 / 504 | fresh, child restarting | Kazma is down, the guard is restarting it | "Kazma is restarting (reason from the heartbeat)" |
   | live 200, ready 503 | fresh | A dependency is down | "Kazma is up; the database / Docker is away" |
   | 200 | missing | The guard is gone | "The guard stopped; Kazma is unsupervised" |

4. **Alerts.**
   - A page after 3 failed minutes, one per incident.
   - A recovery page with how long it lasted.
   - A reminder every hour while it lasts.
   - By Telegram, through a bot of its own ("Kazma Watch", token stored as a
     Worker secret), so a fault in Kazma's own bot cannot silence it.
   - And by email through Cloudflare Email Routing to the owner's verified
     address.
   - Planned work does not page: the guard's maintenance pause and `kazma
     update` set "maintenance" in the heartbeat.
5. **Status page.** `status.kazma.ai` behind Access (the owner only):
   - 90 days of uptime bars
   - the incident list with causes and durations
   - the latest heartbeat
   - response times

   A public up/down page is possible later, an owner decision.
6. **Who watches the watcher.**
   - Every Worker run pings a Healthchecks.io check; if the Worker stops,
     Healthchecks emails the owner.
   - Cloudflare's own Tunnel health notification (email or webhook) is turned
     on too, as a second, independent signal for the tunnel.
7. **Storage.**
   - D1 keeps every reading for 30 days, hourly summaries for a year, and the
     incidents for good.
   - About 2,900 writes a day, inside D1's free limits (enforced since
     1 September 2026).
8. **Inside Kazma.**
   - **Pairing.** `kazma observer pair` writes the key and registers the
     install with the Worker. The owner runs it once, with Access.
   - **Settings → System, "Outside watch".** Shows the last heartbeat the
     Worker acknowledged and the last incident.
   - **Daily digest.** Gains "Outside watch: up 99.9 % this week, 1 incident
     (power, 14 min)".
   - **Off by default.** `observability.outside_watch.enabled` is false until
     the owner pairs.
9. **Code location.** `deploy/observer/` in this repository (a Worker with
   its own `wrangler.jsonc`, D1 schema and tests), deployed with wrangler to
   the kazma.ai Cloudflare account. It is Kazma's operations tooling and
   changes with the guard's heartbeat, so it lives beside it.
10. **Privacy.** kazma.ai's privacy policy says the site collects no
    analytics, and that stays true: the observer reads the owner's own
    install and stores no visitor data.
    - If other installs may later opt in, it is an explicit opt-in setting,
      off by default.
    - The data stays the list above.
    - A delete-my-data request is honoured.
    - The privacy policy gains a section before the first other install is
      accepted.

**Cost.**
- Money:
  - Workers Free (100,000 requests a day, scheduled triggers), D1 free,
    Access free (up to 50 users, service tokens included), Healthchecks.io
    free (20 checks), Telegram free, Email Routing free: **$0 a month**.
  - Workers Paid is $5 a month if more headroom is wanted.
- Work: 5-6 days. That covers the Worker and the diagnosis, D1 and the
  status page, the guard's sender, pairing, the Settings card and the tests.

**Owner decides.**
- Build it on kazma.ai's Cloudflare account (recommended), or use a ready
  service only (Healthchecks.io or UptimeRobot pinging the health path,
  without the heartbeat diagnosis).
- A separate watch bot (recommended) or Kazma's own bot.
- Private status page (recommended) or public.

**Depends on.** Nothing for the pull probe; A3 for the database field.

**Proof.**
- **Worker tests** (Miniflare): every row of the diagnosis table, one page
  per incident, the recovery page, maintenance quiet.
- **`tests/test_observer_heartbeat.py`:**
  - the sender never holds up the guard (a server that never answers);
  - the body carries only the listed fields (a planted chat text is not
    sent);
  - the signature checks;
  - off by default.
- **Live drills, each producing its page:**
  - stop cloudflared ("tunnel only");
  - pause the guard without maintenance ("Kazma down");
  - unplug the network ("machine not reachable").

  Negative control: each drill today pages nobody.

**Stage.** 1.

### B2. What the guard watches on the machine itself

**B2a. Disk space.** No disk alert exists today.

**Actions.**
1. The guard reads the free space of the data drive (and of Docker's disk
   image where it can) every minute.
2. It warns at 15 % or 20 GB free and pages "critical" at 5 % or 5 GB.
3. The value travels in the heartbeat.
4. Backups and document intake already refuse below their floors; the page
   names what will stop next.

**Cost.** 1 day. **Proof.** A test with the free-space reader faked; old guard
as the control. **Stage.** 1.

**B2b. Updates in a maintenance window.** On 4 November topgrade stopped
Docker Desktop.

**Actions.**
1. `kazma maintenance begin` / `end`. Begin waits until no turn runs, pauses
   the guard under the maintenance reason, and tells the observer.
2. End resumes, waits for `/health/deep`, and pages if Kazma is not healthy
   within 5 minutes.
3. topgrade calls them from its own configuration (`pre_commands` and
   `post_commands` in `topgrade.toml`).
4. Docker Desktop's automatic updates are turned off; it updates through
   topgrade inside the window.
5. Windows Update gets active hours set to the owner's day and "notify to
   schedule restart". After the reboot, the boot task and the native
   database bring everything back.

**Cost.** 1-2 days. **Proof.**
- A topgrade-style run: stops Docker inside the window, starts it; zero
  restarts and zero pages.
- Negative control: the same run outside the window today gives restarts and
  pages.

**Stage.** 2.

**B2c. Power.** The PC has no UPS.

**Actions.**
1. Buy a line-interactive UPS with a USB port (600-1500 VA, enough for the
   PC and the router).
2. Set the BIOS option "restore on AC power loss" to "power on".
3. Windows sees a USB UPS as a battery. The guard reads the power status
   (Windows `GetSystemPowerStatus`, Linux `/sys/class/power_supply`, macOS
   `pmset -g batt`):
   - on battery: one page;
   - below 30 %: a graceful stop (turns finish or stop cleanly, the database
     checkpoints);
   - power back: a page and a normal start.
4. The heartbeat carries it.

**Cost.**
- UPS about $80-250 (an estimate for that range; prices vary by region).
- Work: 1-2 days.

**Owner decides.** Buy the UPS; change the BIOS setting.

**Proof.** The power reader faked; the real drill is pulling the UPS's plug.

**Stage.** 5 (the code can land earlier).

---

## Part C — Work survives reloads and deploys

### C1. A turn cut off by a reload continues

**Why.** Since 2 November a turn a reload cuts off ends with an honest
notice (`stopped_for_shutdown`, FA-262). The owner must still send the
request again.

**Actions.**
1. At start, Kazma finds the turns stopped for shutdown in the last 30
   minutes.
2. A turn whose last step was a model call or a read-only tool continues by
   itself, with a note in its block ("Continued after Kazma restarted").
3. A turn whose last step started a tool that changes something, with its
   receipt started or unknown (the effect journal, §30G), never continues by
   itself. It gets a **Continue** button on the web and in the chat apps, and
   the card says which step's effect is unknown.

**Cost.** 3-4 days.

**Owner decides.** Automatic for safe turns (recommended) or always ask.

**Proof.**
- `tests/test_drill_graceful_reload.py` extended: the cut turn completes
  after the reload.
- A turn cut during a file write asks instead.
- Negative control: today's code leaves both stopped.

**Stage.** 2.

### C2. Every deploy is checked, and undone if it fails

**Why.** A deploy today is pull, reload and a manual look.

**Actions.** `scripts/deploy_live.py`, run from the dev checkout. It uses
only the operations the live install allows: pull, the guard's reload, and
the update and image commands.
1. Record the live commit.
2. Pull.
3. If `pyproject.toml` changed, run `kazma update --reinstall -y`.
4. If document code changed, build the worker image.
5. Reload when idle.
6. Wait for `/health/ready` and `/health/deep`.
7. Send one test turn in a private test chat with memory off ("Reply with the
   word OK"), check the answer, then delete the chat.
8. Read the log since the boot for ERROR and CRITICAL lines.
9. On failure: push a revert of the deployed commits to `main`, pull it into
   the live install and reload, so the live install is only ever pulled.
   Then page the owner with what failed.

**Cost.** 2-3 days.

**Owner decides.** Undo by "revert on main, then pull" (recommended).

**Proof.**
- A deploy of a planted bad commit to a private install is undone, and the
  page names the failed check.
- A good deploy passes.

**Stage.** 2.

### C3. A clean stop every time

**Why.** 8 of 163 reloads ran past the graceful stop; on 2 November 9
shutdowns exceeded their budget.

**Actions.**
1. Log each shutdown hook's time, and say which hook ran over.
2. Give each hook its share of the stop budget (`shutdown.step_bound`, §55).
3. Fix the hooks the log names.
4. The weekly report counts stops over budget, with a target of zero.

**Cost.** 2 days, plus fixes the measurements find.

**Proof.** The reload drill under load (a streaming turn, an OCR job, a
crawl): the stop finishes inside the guard's grace in 10 of 10 runs.

**Stage.** 2.

---

## Part D — Turns that continue instead of stopping

### D1. A refusal is not a failure

**Why.** The tool-loop breaker ends a turn after 3 hard failures
(`HARD_FAILURE_THRESHOLD = 3` in
`kazma-core/kazma_core/agent/tool_loop_breaker.py`), and
`classify_mcp_error` counts a refusal made before anything ran ("Path not
found", a policy refusal) as hard. Three wrong guesses at a file name end the
turn.

**Actions.**
1. A refusal before effect becomes correctable.
2. It counts toward its own allowance of 6, and the model sees the refusal
   text.
3. Only the same call refused twice with the same arguments counts as a loop.
4. Real failures (a tool that raised after it started) keep the threshold of
   3.

**Cost.** 1 day.

**Proof.**
- Three different wrong paths, then the right one: the turn finishes.
- The same wrong path twice: it stops.
- Negative control: the old classification ends the first case.

**Stage.** 1.

### D2. Long files are written in parts

**Why.** 29 replies hit the output limit; 8 file writes were refused because
their arguments were cut off. `file_write` takes the whole file in one call
(`path, content, encoding`).

**Actions.**
1. `file_write(..., mode="append")`, so a long file is written in parts.
2. The document tools take their Markdown from a workspace file
   (`generate_docx(source_path=...)`, the same for PDF), so the model never
   has to put a whole report in one call.
3. The output limit for tool-call steps follows the model's own limit (a
   per-model setting, `models.max_output_tokens.<model>`).
4. When a call is cut off, the refusal tells the model to write in parts.

**Cost.** 2-3 days.

**Proof.**
- A 60 KB report through parts.
- A cut-off call answered with the "write in parts" instruction.
- Negative control: the same report in one call today is refused.

**Stage.** 3.

### D3. The closing summary matches what happened

**Why.** A turn's final answer is the model's account of its work. When the
model says a file was written and it was not, nothing says so.

**Actions.**
1. The respond step gets the turn's tool receipts.
2. It checks each file path the answer claims was written or sent against
   them.
3. A claim with no matching receipt gets a correction line in the answer's
   language ("Not done: …").
4. A short "Files written" line is built from the receipts, not from the
   model.

**Cost.** 2-3 days.

**Proof.**
- A planted answer claiming an unwritten file gets the correction.
- A true one gets none.

**Stage.** 3.

### D4. Duplicate writes and re-reads

**Why.** The 1.43-million-token DOCX turn re-read files it had not changed
and wrote the same document again.

**Actions.**
1. Writing the same content to another name in one turn, or generating the
   same document twice, is answered "already written at X".
2. Reading a file whose size and hash have not changed since the turn last
   read it counts toward the stagnation guard.

**Cost.** 1-2 days.

**Proof.** The recorded shape of that turn (a fake model replaying its calls)
stops repeating. The old guard is the negative control.

**Stage.** 3.

---

## Part E — A cost limit per turn

### E1. Turn and daily budgets

**Why.** Nothing bounds what one turn spends. One DOCX turn used about 1.43
million tokens.

**Actions.**
1. `agent.turn_token_budget` (Settings → System), read from the per-call
   ledger (`llm_ledger.turn_usage`) after every model call.
   - At 70 % the model gets a turn note: "70 % of this turn's budget is used;
     finish now".
   - At 100 % the turn stops with what it has. The answer says the budget
     stopped it, and offers **Continue (+50 %)**.
   - An active `/long` lifts it.
2. `agent.daily_cost_limit_usd`, optional: a page at 80 %, and new turns
   refused at 100 % with the reason. The limit resets at midnight in the
   operator's zone.
3. The Dashboard shows today's spend against the limits.

**Cost.** 2-3 days.

**Owner decides.** The defaults. Recommended: 500,000 tokens a turn, and no
daily dollar limit until a month of spend has been seen.

**Proof.**
- A real graph with a fake model reporting usage: the note at 70 %, the stop
  at 100 %, `/long` lifting it.
- Negative control: today's turn runs on.

**Stage.** 1.

---

## Part F — No single outside service

### F1. A second search service

**Why.**
- Search is SearXNG first, then the free fallbacks (DDGS, Bing's page,
  Wikipedia). There is no paid search API.
- SearXNG's engines were suspended 14 times.
- The fallbacks answered off-topic results until 31 October.

**Actions.**
1. A Tavily backend after SearXNG.
   - Tavily: 1,000 free credits a month with no card; then $0.008 per
     credit, pay as you go.
   - Brave is the alternative: a $5 monthly credit, about $5 per 1,000
     queries after it, a card required.
2. It is a remote provider, so it follows the acquisition policy:
   - it needs `KAZMA_ACQUISITION_MODE=local_first_with_remote_fallback`;
   - Tavily is named in the remote-provider list;
   - each query reserves through the request allowance.
3. A page when SearXNG has answered nothing for an hour.
4. The SearXNG container restarts once, by the guard, when every engine is
   suspended.

**Cost.**
- Money: $0 up to 1,000 searches a month; $8 per further 1,000 on Tavily.
- Work: 2 days.

**Owner decides.**
- Whether search queries may leave the machine.
- Tavily (recommended), Brave, or none.

**Proof.**
- SearXNG faked down: results come from Tavily, reserved and recorded.
- The SearXNG page fires once.

**Stage.** 3.

### F2. A backup model from another vendor

**Why.** The live install runs on one provider. The failover chains exist,
but no second vendor is configured, and a switch has never been tested.

**Actions.**
1. Configure a second provider from another vendor in the failover chain.
2. A monthly drill (`scripts/model_failover_drill.py`) forces a failover with
   a test prompt and checks three things:
   - the answer;
   - the fallback notice (§38);
   - the switch back.
3. The result goes into the weekly report.

**Cost.**
- Money: pay per use, only while failing over and for the drill (cents a
  month).
- Work: 1-2 days.

**Owner decides.** The vendor and the key.

**Proof.** The drill, plus a test with the first provider faked down.

**Stage.** 3.

### F3. Chat-app replies go through an outbox

**Why.** 57 Slack timeouts, about 65 Discord drops and 15 Telegram failures.
A reply that fails to send is lost.

**Actions.**
1. Every chat-app reply goes through a durable outbox (`chat_outbox.db`),
   like the scheduled jobs' delivery (§16E):
   - leased;
   - retried with backoff for up to 2 hours;
   - acknowledged only when the platform accepts it.
2. A send that timed out after it left is marked "unknown" and never sent
   twice. The next reply says one earlier message may not have arrived.
3. A reply that runs out of time is undeliverable: one page, and the web
   chat shows it.

**Cost.** 3-4 days.

**Proof.**
- A real child process killed at each stage: exactly one delivery or one
  honest state.
- Negative control: today's send loses the reply.

**Stage.** 1.

---

## Part G — Logs and signals people can use

### G1. Quiet logs

**Why.**
- 12,156 "Batch set" lines out of 20,409 plain-format lines.
- Routine messages bury the ones that matter.

**Actions.**
1. "Batch set" and the other routine INFO lines that repeat every minute
   move to DEBUG.
2. A gate keeps them quiet: a top-10 list of the noisiest messages, each
   below a rate.

**Cost.** 1 day.

**Proof.** A gate over a recorded day of logs.

**Stage.** 4.

### G2. One list of open problems

**Why.** Open alerts live in Telegram history, the bell and the digest. The
owner cannot see at a glance what is still wrong.

**Actions.**
1. One "Open problems" list on the Dashboard, built from:
   - open ops alerts;
   - run incidents;
   - unmet requirements;
   - open advisories;
   - the observer's last incident.
2. Each problem has its "resolved when" condition, an Acknowledge button and
   a Snooze (24 h).
3. The daily digest reads the same list.

**Cost.** 2-3 days.

**Proof.**
- A browser test in English and Arabic, arrived at from the sidebar.
- A resolved problem leaves by itself.

**Stage.** 4.

### G3. A weekly report against targets

**Actions.** The weekly report gains a "Targets" section:

| Target | Goal |
|---|---|
| Uptime (from the observer) | 99.5 % or better |
| Turns that succeed | 99 % or better |
| Restarts not asked for | 0 |
| Time to recover (median) | under 5 minutes |
| Backups and WAL fresh | every day |
| Stops over budget | 0 |
| Alerts per week | 10 or fewer |

Each target is marked met or missed, with the week before for comparison.

**Cost.** 1-2 days.

**Proof.** The report built from recorded logs, with a missed target shown.

**Stage.** 4.

---

## Part H — Clear the alerts that are still open

| Alert | Action |
|---|---|
| Requirements unmet (7) | Run `kazma update --reinstall -y` on the live install, then check that the boot check clears it |
| Document worker image outdated (5) | Build the image (`scripts/document_worker_image.py build`) and check that the broker switches to it |
| MCP (2) | Read the two servers' errors. Fix or switch off each server and record which |
| Restore drill unverified (1) | Run the restore drill and record its result |
| Malware signatures (1) | Schedule `freshclam` on the host (a daily task) and check the age reported in readiness |

Each line is closed only when its alert has gone quiet for 48 hours.

**Cost.** Half a day. **Stage.** 1.

---

## Part I — What the UI says must be true

Each item gets a browser test in English and Arabic, arrived at from the
sidebar, with the old code as its negative control.

| Seen on the live install | Action |
|---|---|
| Rows still running were shown as done | A row shows done only when its record says it ended |
| Separate narration paragraphs were joined into one | Keep the paragraph breaks from the stream to the stored turn |
| An old OCR turn shows "Completed" with no answer | A turn with no answer shows how it ended (stopped, failed, cut off) |
| No "don't remember" option before a chat's first message | Offer the memory choice on a new, empty chat. It applies to the first turn |
| 5 of 13 answer-quality failures have no link to their chat | Every row links to its chat, or says the chat was deleted |
| Scheduled shows "3 finished" next to "History (4)" | One count from one query |
| Switched-off providers show old states | A switched-off provider shows "off" and nothing else |
| `file_search` treated "backup-benefits" as two search words | A query that looks like a file name is matched as a name first |

**Cost.** 4-5 days. **Stage.** 4.

---

## Part J — Proof: soaks and chaos drills

**Why.** The hardening of documents, memory, Scheduled, Knowledge, swarm and
Time Travel each has a soak harness. Several of the long runs never finished.

**Actions.**
1. Run the long soaks to the end, one at a time, at low priority, on private
   stores:
   - documents 48-72 hours (`scripts/document_soak.py`);
   - memory over several days (`scripts/memory_soak.py`);
   - Scheduled through 7 simulated days (`scripts/scheduled_soak.py`);
   - Knowledge 72 hours (`scripts/knowledge_soak.py`);
   - swarm (`scripts/swarm_soak.py`, `scripts/swarm_chaos.py`);
   - Time Travel (`scripts/time_travel_soak.py`);
   - run supervision (`scripts/run_supervision_soak.py`).

   Each report goes into its area's tracker.
2. A weekly chaos drill (`scripts/chaos_week.py`, new) on a private install,
   never the live one:
   - Postgres stopped for 2 minutes;
   - the server killed mid-turn;
   - Docker stopped;
   - the network cut (a firewall rule);
   - the disk filled to its floor (on a small virtual disk);
   - a reload under load.

   Each step has its expected outcome (the five rules at the top). The
   result goes into the weekly report.
3. Where they run: a Hyper-V virtual machine on this PC at low priority
   ($0, but it shares the machine with the live install), or a small
   separate PC.

**Cost.**
- Money: $0 with a VM; a separate mini PC about $200-400 (an estimate).
- Work: 3-4 days for the chaos drill, plus running time.

**Owner decides.** A VM on this PC or a separate machine.

**Depends on.** A1 and B1, so the drills measure the new setup.

**Stage.** 5, then weekly.

---

## Part K — Kazma installs what it needs, on every OS

### K1. Postgres

This is A1's `kazma setup postgres`.

### K2. Every other system dependency

**Why.**
- `setup.ps1` and `setup.sh` install uv and the Python extras only.
- Tesseract (with Arabic and English), LibreOffice, ClamAV, its signature
  updates, the fonts and Docker are installed by hand. Missing ones are found
  later, one failed document at a time.

**Actions.** `kazma setup system`: the same plan, ask and record pattern as
`kazma setup postgres`, one table per OS.

| What | Windows (winget) | macOS (Homebrew) | Debian / Ubuntu / WSL (apt) | Fedora (dnf) |
|---|---|---|---|---|
| Postgres 16 | `PostgreSQL.PostgreSQL.16` | `postgresql@16` | `postgresql-16` (PGDG) | `postgresql16-server` (PGDG) |
| OCR | `UB-Mannheim.TesseractOCR`, plus `ara.traineddata` downloaded and hash-checked | `tesseract`, `tesseract-lang` | `tesseract-ocr`, `tesseract-ocr-ara`, `tesseract-ocr-eng` | `tesseract`, `tesseract-langpack-ara` |
| Office conversion | `TheDocumentFoundation.LibreOffice` | `--cask libreoffice` | `libreoffice-writer` | `libreoffice-writer` |
| Malware scan | `Cisco.ClamAV`, plus a daily `freshclam` task | `clamav`, plus a daily `freshclam` launchd job | `clamav`, `clamav-freshclam` (its own service) | `clamav`, `clamav-update` |
| Fonts | bundled with Kazma | bundled with Kazma | `fonts-noto-core` | `google-noto-sans-arabic-fonts` |
| Docker (for the document container, the sandbox, SearXNG) | `Docker.DockerDesktop` | Docker Desktop (cask) | `docker.io` or Docker's repository | `moby-engine` or Docker's repository |

The winget ids were checked on 2026-11-05 (Tesseract 5.4.0, LibreOffice
26.8.2.2, ClamAV 1.5.4, Docker Desktop 4.98.0, PostgreSQL 16.15-5).

1. `setup.ps1` and `setup.sh` offer `kazma setup system` at the end, with
   the plan shown first.
2. `kazma doctor` checks each dependency, its version and its last update
   (signatures), and names the command that fixes it.
3. Nothing is installed without the owner's yes (`-y` for a scripted
   install).

**Cost.** 4-5 days.

**Proof.**
- Real installs in Debian and Fedora containers: every check passes after
  one run, and a second run changes nothing.
- Windows and macOS drills on a VM, recorded here.
- Unit tests with each package manager faked.

**Stage.** 3.

---

## What it costs

### Money

| Item | One-off | Monthly |
|---|---|---|
| Outside observer: Workers, D1, Access, Email Routing, Telegram, Healthchecks.io | $0 | $0 (optional Workers Paid $5) |
| Postgres as an OS service | $0 | $0 |
| WAL and base backups on R2 | $0 | $0 within 10 GB; then $0.015 per GB |
| Second search (Tavily) | $0 | $0 up to 1,000 searches; then $8 per 1,000 |
| Backup model | $0 | pay per use, cents for the drill |
| UPS | about $80-250 | $0 |
| Chaos drill host | $0 (a VM) or about $200-400 (a mini PC) | $0 |
| **Total** | **about $80-650** | **about $0-5, plus usage past the free tiers** |

### Work

| Stage | Days of work |
|---|---|
| 1 | about 12-15 |
| 2 | about 13-16 |
| 3 | about 18-22 |
| 4 | about 8-10 |
| 5 | about 4-6, then running time |

## What the owner decides

1. Move the live database to a native OS service (recommended), or keep the
   container.
2. Postgres 16 (recommended) or 17.
3. The outside observer on kazma.ai's Cloudflare account (recommended), or a
   ready service only.
4. A separate "Kazma Watch" bot (recommended), or Kazma's own bot.
5. A private status page (recommended), or public.
6. Search queries may leave the machine: Tavily (recommended), Brave, or
   none.
7. The backup model's vendor and key.
8. The turn budget (recommended 500,000 tokens) and whether to set a daily
   dollar limit.
9. Buy a UPS; set the BIOS to power on after a power cut.
10. Undo a failed deploy by revert-then-pull (recommended).
11. Continue turns cut by a reload automatically when safe (recommended),
    or always ask.
12. The read-only start mode (A4): wanted or not.
13. Chaos drills on a VM on this PC, or on a separate machine.
14. Later: let other installs opt in to the observer, with a privacy-policy
    section first.

## Sources

Checked 2026-11-04 and 2026-11-05.

- Cloudflare Workers [pricing](https://developers.cloudflare.com/workers/platform/pricing/) and [limits](https://developers.cloudflare.com/workers/platform/limits/)
- Cloudflare Workers [cron triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/)
- Cloudflare D1 [free-tier limit enforcement, 2026-09-01](https://developers.cloudflare.com/changelog/post/2026-09-01-d1-free-tier-limit-enforcement/) and [FAQ](https://developers.cloudflare.com/d1/reference/faq)
- Cloudflare Tunnel [monitoring](https://developers.cloudflare.com/tunnel/monitoring) and [notifications](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/monitor-tunnels/notifications/)
- Cloudflare Access [service tokens](https://developers.cloudflare.com/cloudflare-one/identity/service-tokens/)
- Cloudflare Email Routing, [sending email from Workers](https://developers.cloudflare.com/email-routing/email-workers/send-email-workers/)
- Cloudflare R2 [pricing](https://developers.cloudflare.com/r2/pricing/)
- Healthchecks.io [pricing](https://healthchecks.io/pricing)
- Telegram Bot API [sendMessage](https://core.telegram.org/bots/api#sendmessage)
- PostgreSQL downloads for [Windows](https://www.postgresql.org/download/windows/), [macOS](https://www.postgresql.org/download/macosx/) and [Ubuntu](https://www.postgresql.org/download/linux/ubuntu/)
- PostgreSQL [versioning and support policy](https://www.postgresql.org/support/versioning/), [16.15 release notes](https://www.postgresql.org/docs/release/16.15/)
- PostgreSQL 16 [continuous archiving and point-in-time recovery](https://www.postgresql.org/docs/16/continuous-archiving.html)
- EDB [Windows installer for 16.15](https://get.enterprisedb.com/postgresql/postgresql-16.15-5-windows-x64.exe); [winget silent install options](https://wingetly.io/apps/postgre-sql/postgre-sql-17/silent-install)
- Homebrew [`brew services`](https://docs.brew.sh/Manpage#services-subcommand) (LaunchDaemon with `sudo`, LaunchAgent without)
- WSL [systemd](https://learn.microsoft.com/en-us/windows/wsl/systemd) and [mirrored networking](https://learn.microsoft.com/en-us/windows/wsl/networking#mirrored-mode-networking)
- Tavily [pricing](https://help.tavily.com/articles/8816424538-pricing) and [API credits](https://docs.tavily.com/documentation/api-credits.md)
- Brave Search API [pricing summary](https://www.costbench.com/software/ai-search-apis/brave-search-api/)
