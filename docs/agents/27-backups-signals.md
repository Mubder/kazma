<!-- AGENTS.md, piece 27-backups-signals. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 27. Backups that report success (2026-08-29)

A backup that logs "complete" while the offsite copy cannot write is the
same as no backup. Reproductions: `tests/test_backup_silent_failures.py`.

**A. Prove the write.** `restic_repo.remote_writable()` PUTs a probe before
any restic call. `rclone:` and `s3:` are both probed (`s3:` = stdlib SigV4
PUT+DELETE under `locks/` — the prefix an append-only key must still be
allowed to delete). A read-only probe is worthless. `_run` refuses restic
when the probe fails (`backup.restic_remote_read_only`). Tests:
`tests/test_restic_s3_write_probe.py`.

**B. A missing passphrase is not a config note.** `alert_missing_password()`
is critical, and silent only when no repository exists yet.

**C. Mechanisms that only speak when they break cannot be told from
mechanisms that never run.** Successful restic snapshot + restore each log
one line. `observability/firing_ledger.py` (`run_weekly_sweep`, started
from `start_memory_worker`) counts them. `_log_paths()` must read
**guard.log and kazma.log, and their rotated siblings** (the app log rotates
at midnight; reading only the live file made a weekly report of one day and
called six days of backups "silent", 2026-09-23). Signatures are checked
against the code: `test_every_ledger_signature_matches_a_line_the_code_emits`
derives the emitted lines from logger format strings, ops-alert keys and the
result `summary()` methods -- a signature no code emits fails the build. A
mechanism logs on SUCCESS too (restic maintenance did not, and its signature
matched only the failure line). A health-gated restart is a
`guard.restarting` whose reason is `unhealthy (...)`, not a failed probe. The
sweep runs in a thread (it reads ~4M lines) and must stay scheduled (it
shipped unscheduled once).

**The report says everything, problems first (2026-09-30).** The week to
2026-09-29 read "15 of 29 mechanisms fired" and named eight: six health-gated
restarts and thirty loop stalls were among the seven it cut, "29" counted a
placeholder row, and 141 of its "149 alerts" were one alert held back 140
times by its cooldown. Now `firing_ledger.render` names every mechanism; each
signature has a kind (symptom / recovery / routine / alerting) and routine
ones a schedule (`per_week`: a job below 75 % of it "needs a look" -- the
daily digest ran 2 of 7); alerts count as SENT (a `(throttled)` line is a
repeat held back, counted apart); restart reasons and alert keys are tallied
(`group`); the stall dumps of the week are read for what blocked the loop
(the first Kazma frame above the storage layer, and its caller -- old dumps
by the thread in `_run_once`); manifest mechanisms with no signature are
"not watched", never a mechanism. Tests: `tests/test_firing_ledger_report.py`.

**The report is laid out in lines and sent whole (2026-11-11).** Telegram
showed the week to 2026-11-11 as one paragraph under a 111-character title,
cut at "Stops over budget 0": `firing_ledger.render` joined its sections with
spaces, `ops_alerts.alert` cut every detail at 600 characters, and the daily
digest printed the title as the alert's name. Now: a short title
(`firing_ledger._title`: what needs a look, the targets missed), sections
with a blank line between them and one item a line (a tally's reasons under
their mechanism; the targets marked ✅ ❌ ❔, `weekly_targets.render_targets`),
sent with `max_detail=firing_ledger.BODY_BUDGET`; past it the last sections
are left out whole and named. A missed target makes the report a warning; a
failure measuring the targets is the report's scan error, never the loss of
the report. Every detail `ops_alerts` cuts ends at a line or word with
`ops_alerts.CUT_MARK`, and the digest cuts an alert's label at
`daily_digest._LABEL_MAX`. **The finished-turn line has one reader**,
`turn_line.read_turn_line`: the targets matched `failed=True|False` while
`close_turn` writes `yes`/`no`, so "Turns that succeed" was unknown every
week (its fixture wrote the form no code writes). Gate:
`tests/test_weekly_report_reads_turns.py` (the real `close_turn` line; the old
pattern as the negative control; no second parser).

**D. One restic run per repository** (`restic_repo._run`, 2026-09-30). The
universal backup's snapshot and the maintenance task's `forget --prune` /
`check` are separate queue tasks and overlapped; `forget` and `check` lock a
repository exclusively, so the second failed "already locked" (a skipped
offsite snapshot 09-28 04:07, a skipped remote forget 09-29 22:21). A lock
per repository orders Kazma's own runs; `--retry-lock 10m` waits out a
restic started elsewhere (the restore listing too). A missed snapshot alerts
(`backup.restic_snapshot_failed`, warn, 12 h) -- it was a WARNING line only.
Tests: `tests/test_restic_one_run_per_repo.py` (negative control: a lock per
call overlaps).

**D2. Kazma's own stop is not a repository failure** (`restic_repo._run`,
2026-11-12). The guard stops the server with CTRL_BREAK / SIGTERM to its
whole process group, and restic, in it, quit mid-check: a deploy's reload
logged "restic check FAILED for remote", paged that the remote repository
failed verification, and `deploy_live.py` read the ERROR and reverted a good
commit. restic starts in its own process group (`_own_process_group`: a run
Kazma started finishes even when the server stops), and a run that still
fails while Kazma is stopping is `interrupted` -- said at INFO by every
caller, never alerted, run again at the next pass. `deploy_live.py`'s log
check reads from the new server's spawn (the guard's `child.spawned`,
`new_boot`), never from the reload request. And its revert is built on
`origin/main` in a worktree of its own (`revert_on_origin`), never on a
checkout's HEAD: run from the live install's copy, its revert was made on
the live HEAD and pushed the live install's own commits (its local
`kazma.yaml` since 2026-10-03) to the public repository, and `main` shipped
that file until it was restored. Run it from the dev checkout
(`.venv/Scripts/python.exe scripts/deploy_live.py --live
C:/Users/balfa/kazma`). Tests: `tests/test_restic_stop_is_not_a_failure.py`,
`tests/test_deploy_live.py` (each with the old behaviour as the control).

**D2b. A backup tool Kazma's stop ended is interrupted, never failed**
(`kazma_core/child_stop.py`, 2026-11-13). pg_dump and pg_basebackup stay in
the server's group -- killed at a stop, a dump starts again cleanly, where an
orphan would leave a half-written file -- and a reload during the 6-hour dump
paged "Postgres dump failed ... pg_dump: terminated by user" (2026-11-12,
twice). `child_stop.ended_by_kazmas_stop(returncode)` is the one answer: a
stop's exit code (`STOP_EXIT_CODES`: Windows' STATUS_CONTROL_C_EXIT, a POSIX
SIGINT or SIGTERM) and Kazma stopping within `STOP_WAIT_S` (the child sees the
signal 1-3 ms before the server marks its stop). The dump raises
`PgBridgeError(interrupted=True)`, `pg_backup.last_pg_backup_interrupted()`
says so, and the handler pages nobody; the base backup raises
`child_stop.StoppedByKazma`, which a new backup tool the server starts and
pages on raises too. Tests: `tests/test_child_stop.py` (real children).

**D2c. A missing local repository is made, and said once**
(`restic_repo._local_repo_made`, from `restic_repo.backup`, 2026-11-20).
Nothing in Kazma made the local repository (`<data dir>/backups/restic`): an
install restored from a migration bundle (the passphrase travels, the
backups never) had a passphrase and no repository, so every copy into it
failed "repository does not exist" and the Postgres dump's copy gave up as a
failed task. A copy into the install's own local repository makes it first
and raises `backup.restic_local_made` (earlier local snapshots are not in
it); a missing remote is never made (it may be a wrong address). Every restic
backup goes through `restic_repo.backup`. Gate:
`tests/test_restic_local_repo_made.py` (real restic; the copy without the
step fails as live did; the gate on the one backup function, a planted one
as the control).

**D3. A restore can be removed by the account that made it**
(`restic_repo.restore`, 2026-11-13). restic restores a snapshot under the
source's absolute path and gives each folder it rebuilt above the backup
the REAL folder's recorded rights. `C:\Users` lets an ordinary account read
but not delete, so pytest could not remove the restic tests' restores and
kept each run's whole temp folder (29 of them, 7.5 GB). After the restore,
`_release_rebuilt_parents` reads the snapshot's paths and gives each rebuilt
parent, top down, the rights it inherits from the target (`icacls /reset`,
never `/T`) and clears its read-only flag; the backed-up folder and its
contents keep what the snapshot recorded. Every `restic restore` goes
through `restic_repo.restore`. Tests: `tests/test_restic_restore_removable.py`
(parents that deny the account's own SID, so the case holds as administrator
too; restic's command alone as the negative control; a planted second
restore command fails the gate). Not in `tests/test_restic_repo.py`: the CI
recovery-drills job runs that file and fails on any skip, and these cases
need Windows.

**E. The daily digest reads every source of its window**
(`observability/daily_digest.py`, 2026-10-03). The live digest said "Turns
completed: 0" over a day with four turns (three web, one Telegram that paused
for an approval and finished), and "Needs attention: maintenance
pauses: 2" over two `kazma update` pauses of 41 s and 11 s that had ended. It
read only the live `kazma.log`, which rotates at local midnight (eight hours
of its 24 at the 05:17 UTC send), and counted turns by "SSE turn complete",
which only the web stream writes. Now:
- the live log and its rotated siblings written in the window
  (`_window_files`, as `firing_ledger._log_paths`);
- turns by the line `close_turn` writes once per finished turn, from every
  transport (`[turn] Turn finished: platform= key= failed=`;
  `turn_runtime._note_finished_turn`): the key hashes thread, turn number and
  question, and the digest counts distinct keys, so a close after a restart
  is not a second turn. A turn that failed (`turn_failed`) needs attention;
- approvals from the gate registry (`hitl_gates.gate_outcomes_since`, one row
  per question, a `superseded` twin not counted), never from log lines: the
  WebSocket logged one more each time a page connected while a question
  waited. A registry that is off or unreadable is said. A question the
  approval-timeout watchdog settled (`deny` by `hitl_gates.TIMEOUT_ACTOR`,
  `expired_unanswered`) is "expired unanswered", never a denial: the
  2026-11-01 digest said "denied 3" over two cards nobody saw in time;
- lines of either shape the log file is written in (`_app_line`: JSON, or
  the plain "standard" format in local time), and a turn or alert line it
  cannot read is "Needs attention: log lines this digest could not read".
  Kazma set up logging at a Windows boot before Postgres was up, the
  `logging.format` read failed, and the server wrote plain lines for 31
  hours (2026-10-30/31): the JSON-only digest said "Turns completed: 0 ...
  No failures, no restarts, no alerts" over that day. `logging_config`
  now falls back to the install's kazma.yaml when the store cannot answer
  (`tests/test_log_format_without_settings_store.py`);
- reloads (`guard.operator_reload`) and maintenance pauses (paired
  `maintenance.active` -> `maintenance.resumed`, with the longest) are
  "Planned work", never a problem;
- alerts from the line `alert()` writes for every occurrence
  (`[ops_alert] <key> | <title>`, ` (throttled)` on a repeat held back),
  never from `alert_state()`, which starts empty at every reload. Each
  reads by its title (its key when the title varies), every one is named
  (past 12 a section, the rest on one line: never "each is in the log"),
  and an alert of severity `success` or `info` -- logged at INFO by
  `alert()` -- is listed under "Notices", not "Alerts raised", and leaves
  the day quiet. The 2026-11-01 digest listed keys only, "Crawl completed"
  among the problems, and hid one behind "and 1 more";
- what ran without asking, chat by chat ("Ran without asking", times in
  the operator's zone, `cron.scheduler.resolve_cron_zone`): each YOLO
  window opened (`[SECURITY] YOLO ENABLED`, its length, who; the
  `YOLO DISABLED` that ended it) and each call a standing approval let skip
  its card, by YOLO, "Allow tool" or "Approve for task". Every gate that
  lets one through says it in ONE line, `safety/unasked.UNASKED_LINE`
  (`note_ran_without_asking`): the chat graph's split (`hitl.approval_need`,
  set only for a call its card would have asked about), its YOLO
  defense-in-depth branch, the swarm bus (`_allowed_without_asking`) and the
  MCP gate (`hitl.standing_approval`, the one reader of the three); before
  2026-11-01 a grant on the chat graph and anything on the MCP gate said
  nothing. The card that grants the approval is answered first and resumed
  after, so its own calls run under the grant: they were asked
  (`card_answered`, from the web route's `_apply_scope_grants`, the chat
  apps' "Approve for task" and `kazma ask`'s "always"; the 2026-10-31 log
  called that card's `file_write` a YOLO auto-approval). The older bus lines
  are read too. Gate: `tests/test_digest_ran_without_asking.py` (the
  writers' own lines through the reader; every function that reads a
  standing approval says it or is declared, every card grant names its
  calls, each with the old code as the negative control);
- the day is stamped only when a channel took the digest
  (`_deliver_digest` awaits `ops_alerts._deliver` on the server loop):
  `failed` is tried again in an hour, `no_channel` (nothing takes ops
  messages) waits for the next day. It was handed to the fire-and-forget
  `_dispatch` and stamped whatever happened. The weekly report counts
  "[digest] daily digest delivered" ("dispatched", the old line, kept for
  the week of the change).
Every marker and guard event it reads is checked against the lines the code
writes (`tests/_emitted_lines.py`, shared with the weekly report's gate;
"Detached turn completed" never counted anything: its line was removed the
day the digest shipped, 2026-08-28).
Tests: `tests/test_daily_digest.py` (each with the old code failing it).

**Chaos injection is only real where it lands.** `InjectionTarget.LLM_PROVIDER`
is injected INSIDE `resilient_chat`'s attempt loop. `ChaosInjectionError`
carries `.transient` (408/429/5xx) so retry/failover actually run.


