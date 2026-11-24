<!-- AGENTS.md, piece 33-alerts. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 33. Ops alerting — three paths, no fourth notifier

| Path | Who | When | Channel |
|------|-----|------|---------|
| Guard `Notifier` | Supervisor process, stdlib urllib | Child dead (with its last stderr lines) / unhealthy / up but not ready (a dependency down, §44) / crash-loop / pause | Telegram-direct — must work when the app cannot |
| `observability/ops_alerts.alert()` | Inside Kazma | Backup/offsite/restic/MCP/persist/turn-fail | Fan-out bus + Telegram-direct fallback |
| `lifecycle_notifier` | App boot/shutdown | one start card (started / restarted, with each adapter's connection) + startup_failed; starting / shutting_down only when switched on | Same bus, filtered by `notifications.ops.channels` |

Model fallbacks (§38) ride the second row plus the web banner
(`AlertDispatcher.post_banner`, banner only) — not a fourth notifier.
The outside observer (`deploy/observer`, §39) is not Kazma's notifier: it
runs off the machine on the owner's Cloudflare account and pages through a
bot of its own, so a fault in Kazma or its bot cannot silence it.
Run incidents (§52) go to the chat the work started from through the chat
apps' own send path (`send_message`, with buttons), else the second row, and
to the bell — not a fourth notifier either.
Swarm results take the swarm output target (`/swarm config`, Settings). The
separate `swarm_notify` module (`SWARM_BOT_TOKEN`), a fourth notifier that
reported a send Telegram refused as sent, was removed on 2026-10-14 (Swarm
audit SW-S15); a set `SWARM_BOT_TOKEN` is a startup WARNING.

**One catalog of alerts, one card** (2026-11-09, Stage 1 of
`docs/plans/UNIFIED_ALERTS_PLAN.md`).
- `kazma_core/observability/alert_catalog.py` names every alert the four
  senders raise: Kazma's ops alerts by key (a part known only at run time is
  a placeholder: `llm.provider_unreachable.{provider}`), the lifecycle events
  (`lifecycle.started`), the guard's pages by the `kind=` every `_page` call
  now passes, and Kazma Watch's conditions (`watch.tunnel_down`, and its
  recovery). Each entry has its severity, a title in English and Arabic, the
  page it is about, the entry that closes it (the same sender's recovery),
  and for a problem two senders can see, its `condition` and whether this
  sender is its `owner` (the plan's one page per problem, Stage 2 below).
  Two inside owners of one condition are declared in `SHARED_OWNERS` with
  why. An alert added anywhere goes into the table the same day.
- The guard (stdlib only) and Kazma Watch (Cloudflare) read generated
  copies: `scripts/service/alert_catalog.json` and
  `deploy/observer/src/catalog.js`, written by
  `python -m kazma_core.observability.alert_catalog --write` (`--check`
  says which is stale). The guard says a kind its copy lacks once
  (`guard.page_kind_unknown` in guard.log) and pages anyway; Kazma Watch
  takes each condition's title and severity from its copy.
- One card: `alert_card.format_operator_card` knows the sources Watch and
  Cloudflare, a reminder (`⏰ Still:` after the icon), a recovery
  (`🟢 [Source] <what> is back: it lasted 12 min.`) and a last line "Since
  <time> (<duration>) · Open: <link>". A call without the new options is the
  old card byte for byte. The guard's stdlib copy and
  `deploy/observer/src/card.js` must say the same:
  `tests/fixtures/alert_card/cards.json` holds each input and its exact text.
  Kazma Watch's pages are the card with the source Watch, its times in the
  zone Kazma told it (Stage 3, below), else UTC; its incident rules are
  unchanged.
- Gates: `tests/test_alert_catalog.py` (the senders read by
  `tests/_alert_senders.py`: ops keys through f-strings, wrappers and local
  assignments, the guard's `_page` kinds, `diagnose.js`'s conditions, the
  notifier's events; both directions, literal severities, owners, pages of
  this site, the copies, the card in Python and the guard; each check run on
  a planted violation first), and the observer's own Node tests for
  `card.js` and the pages (`deploy/observer/test/observer.test.mjs`).

**One page per problem** (2026-11-09, Stage 2 of the same plan). A
database outage, a full disk and a restart longer than three minutes were
each paged two or three times: by Kazma, its guard and Kazma Watch.
- **Inside owners agree.** `alert_catalog.inside_hold` decides who pages a
  condition: a sender marked `owner=False` records it while an owning sender
  can speak; of two inside owners (`SHARED_OWNERS`: `database_away` --
  Kazma's `database.away`, the guard's `dbsvc.down` and
  `child.waiting_for_database`) the first whose page reached someone pages,
  the other records it. `ops_alerts.alert` asks it
  (`page_receipts.held_by`) and the guard's `_page` asks its stdlib copy
  (`_inside_hold`) for EVERY entry with a condition, so a condition added to
  the catalog follows the rule the day it is added. A held ops alert writes
  `[ops_alert_held] <key> | <title> (paged by <who>)`, never the
  `[ops_alert]` line the digest counts; a held guard page logs
  `guard.page_held` once a kind and returns False, so its caller sends no
  recovery (`dbsvc.down`, the boot wait). `guard.gone` is Kazma Watch's
  while the observer was answering the guard's heartbeats up to its last
  beat. The database prober asks again every probe while the guard has the
  page (`availability._held_by_another`), so the page is Kazma's once the
  guard's closes. Both copies are held to
  `tests/fixtures/alert_receipts/holds.json`.
- **Page receipts.** A page Kazma or the guard sends that opens an incident
  (a recovery closes it, or another sender sees it: `opens_incident`) is a
  receipt -- the catalog ENTRY key (never a name known at run time), when,
  whether a channel took it -- until its recovery (`closed_by_key`) goes
  out. Kazma's are written by one writer thread to the file the guard hands
  over in `KAZMA_GUARD_PAGES_FILE` (`kazma_pages.json` beside the state
  file; `page_receipts.py`), stamped with the writing process's start so
  the guard reads only its current server's (`PageReceipts.kazma`); the
  guard keeps its own in its state file (`pages`; `Notifier.send` returns
  whether Telegram took it, and a held page says so when it goes out,
  `on_delivered`). A page that reached nobody is settled with its outage
  (`page_receipts.settle`). The heartbeat carries both (below, §39).
- **Kazma Watch** (`deploy/observer/src/receipts.js`, `incidents.js`) stays
  quiet about a condition a fresh heartbeat's receipt says was paged and
  delivered (`ANSWERED_BY`: a database page answers for Kazma not ready or
  down while it boots), and sends no recovery for it (`held_by` in the D1
  table `incident_notes`). It pages when the condition is one only it can
  see (its catalog entry owns it), when the inside page did not reach anyone
  within `INSIDE_GRACE_S` (5 min) or none came 5 min after it would have
  paged on its own, or when the heartbeats stop (its own condition; the page
  names the inside pages that were open). A heartbeat with no receipts at
  all (an older guard) is read the old way. An incident it paged first gets
  no hourly reminder while a delivered inside page answers for it
  (`incidents.js`, 2026-11-12: five reminders over the guard's Docker page;
  the guard's `deps.engine_down` answers `dependency_down` since then).
- Gates: `tests/test_one_page_per_problem.py` (both hold copies on the
  fixture, a planted non-owner on each side, the receipts' life, the
  relations; three live-shaped scenarios -- a database outage, a critical
  disk, a restart longer than three minutes -- run Kazma's real `alert` and
  the real guard, then replay its signed heartbeats through the Worker,
  `deploy/observer/test/replay_scenario.mjs`: one page and one recovery from
  the same sender each; the senders as they were as the negative control,
  3, 2 and 2 pages), `deploy/observer/test/one_page.test.mjs` (each rule
  with the old reading as its control).

**Cloudflare's notifications through Kazma Watch** (2026-11-09, Stage 4).
`POST /v1/cloudflare` (`deploy/observer/src/cloudflare.js`) takes a
Cloudflare Notifications webhook: refused without the destination's secret
in `cf-webhook-auth` (`CF_WEBHOOK_SECRET`, compared in constant time; 503
until set), 16 KiB at most, a body already taken (its SHA-256 in
`cloudflare_seen`) or older than three days answered and never paged. Each
notification is a catalog entry of the sender `cloudflare` (tunnel health,
tunnel deleted or changed, Access credentials expiring, Worker errors; the
rest `cloudflare.notification` with its own text, cut to 300 characters,
control and direction characters out). One of Kazma Watch's own causes
(`SAME_CAUSE`: the tunnel's 530 reads as the tunnel or the whole machine)
joins the incident Kazma Watch has open (`incident_notes.witnesses`; its
pages say "Cloudflare says so too") or waits `JOIN_WAIT_S` for the one its
probe is about to open; else it is paged as the card with the source
Cloudflare, holds Kazma Watch's own incident of that cause, and its end --
or Kazma Watch reading Kazma up -- sends its one recovery. At most
`PAGES_PER_HOUR` (20) an hour. Nothing is ever sent to Kazma: it is behind
the tunnel the notice is about. The owner's steps are in
`deploy/observer/README.md`. Gates: `deploy/observer/test/one_page.test.mjs`,
and in `tests/test_one_page_per_problem.py` every `cloudflare` entry mapped
by `cloudflare.js` and every key it maps catalogued (both directions).
**Settings -> Alerts: one place for every sender** (the unified alerts plan,
Stage 3, 2026-11-09; `kazma_ui/settings_alerts.py`, `static/js/settings_alerts.js`,
deep link `?tab=alerts`). It replaced the alert half of Adapters & Routes and
System's read-only outside-watch card (`/api/settings/outside-watch` is gone:
`GET /api/settings/alerts` carries it). Every route is under `/api/settings`
(admin), every write same-origin and through `settings_validation`; one bad
field writes nothing. Sections and the keys they write, each with its reader:
- Where Kazma's alerts go (`notifications.ops.channels`, now validated
  against `ALERT_ROUTES`) and Send a test alert (unchanged route); the
  alerts group (`ops_alerts.ALERTS_GROUP_KEY`, 2026-11-09): when set, the
  "Telegram (group)" route sends there with Kazma's own bot instead of the
  swarm's output group, so one Telegram group can hold Kazma's, the guard's
  and Kazma Watch's alerts while swarm results stay put; the
  server status messages (`notifications.lifecycle.events`). Adapters &
  Routes keeps the swarm's routes; no page script saves the alert keys any
  more, so two tabs cannot write over each other.
- **The bot that sends them** (2026-11-11; the owner's alerts landed among
  his chats with Kazma, every sender using the chat bot):
  `ops_alerts.ALERTS_BOT_KEY` chooses `chat_bot` (Kazma's own, the default)
  or `alerts_bot`, a separate bot -- `ALERTS_BOT_TOKEN_KEY` (in the vault,
  install-scoped: `vault.INSTALL_SCOPED_SECRETS`) and `ALERTS_BOT_CHAT_KEY`.
  `ops_alerts.alerts_bot` is the one answer; with it the Telegram route goes
  to its chat (`ops_alerts.telegram_route`; Kazma's chat bot leaves the
  route, `bus_send_targets(skip_telegram=)`, a required keyword so every
  sender decides), the group route to the alerts group
  (`ops_alerts.group_route`), the start card the same way, and the guard
  pages with it (`ops_alerts.pager_credentials`, run by the guard's settings
  lookup, read again at a page `_NOTIFY_REFRESH_S` later). An alerts bot
  that does not take an alert hands it to Kazma's chat bot (outcome
  `alerts-bot` failed). A save choosing it asks Telegram
  (`ops_alerts.check_alerts_bot`: a token it does not know is 400
  `bot_refused` and nothing is written; a chat the bot cannot see is saved
  and said). The token is never answered back. Gates:
  `tests/test_alerts_bot.py` (the delivery as it was as the negative
  control), `tests/e2e/test_settings_alerts_browser.py` (EN/AR, by the
  sidebar, a stand-in Telegram).
- The guard's pager: `guard.telegram.chat_id` (a number or a public @name;
  read by the guard's lookup and `ops_alerts`' direct fallback; a SETTING in
  `settings_restore`). The guard records every page's outcome in its state
  file (`pager`: when, taken or not, the card's first line, the chat, where
  the credentials came from -- never the token, even inside an error) and
  names the card's first line in `notify.sent`/`notify.failed`. **Send a
  test page** writes `guard.test_page` beside the reload request
  (`guard_pager.request_test_page`: only to a guard whose heartbeat is
  fresh and whose `guard_features` hold `pager_test`); the running guard's
  `PagerTest` sends one card through a notifier of its own (the credentials
  a page would resolve now) on a thread and answers in its state file
  (`pager_test`: sent, failed with why, or expired after 5 min -- never sent
  late); the page polls `GET .../guard-test`. Credentials the test read from
  settings become the guard's pager's (a chat just changed). The test card
  is catalog `guard.test_page` (`_test_page_card`, declared outside `_page`).
- Kazma Watch: pair from the page (the token used once, never stored, logged
  or answered; then `push_watch_settings`), its test page, what it is told
  (`notifications.watch.email_to` and the zone from `cron.timezone`), and
  unpair -- each a request signed by the install's own key
  (`outside_watch._signed_post`, a `v2` signature over its own route: one
  signed for another route is refused, §39 "Signatures bind the route"):
  `POST /v1/settings`, `/v1/test-page`,
  `/v1/leave` (`deploy/observer/src/watch_settings.js`, D1
  `install_settings`; an older signed request is refused, a test page at
  most every 20 s). An install the observer no longer knows
  (`unknown_install`) unpairs here too; any other refusal keeps the pairing
  file (removed alone, the observer would page "machine unreachable"). The
  observer writes its cards' times in the zone it was told and emails the
  recipient it was told when its email binding exists.
- Notices (`notifications.ops.notices_to_chats`, default on): off, an info or
  success card stays off the chats and is posted to the bell
  (`AlertDispatcher.post_banner`, subsystem `notice:<key>`; acquisition and
  run notices are already there); a problem, and a recovery note (an entry
  some page names as `closed_by`), always goes. The decision is made off
  the loop (`_deliver_from_the_loop`), and the caller's `on_delivery` hears
  True: the place chosen took it.
- Recent alerts (`observability/recent_alerts.py`, 7 days): Kazma's from the
  log the digest reads, each with its receipt -- every delivery now ends in
  `[ops_alerts] receipt <key>: delivered|not delivered|uncertain|kept off the
  chats` (`ops_alerts._Receipt`; never the `[ops_alert] ` marker the digest
  and the weekly report count) --, the guard's from guard.log, Kazma Watch's
  from `/v1/summary` (`paged_at`, `recovery_paged_at`); severity, Arabic
  title and page from the catalog (`alert_catalog.entry_for`). A source that
  cannot be read is named, never shown as no alerts.
Gates: `tests/test_settings_alerts.py` (routes against a stand-in observer
that checks signatures the Worker's way; notices with the default as the
control; recent alerts without receipts as the control),
`tests/test_guard_pager_test.py` (the guard's outcome and test page, the
token kept out, expiry, the server's refusals, and the real guard process
answering a request), `deploy/observer/test/watch_settings.test.mjs`
(signatures required, settings kept and replay refused, the zone on the
cards with an untold install's UTC as the control, test-page channels and
rate, leave), `tests/e2e/test_settings_alerts_browser.py` (by the sidebar,
English and Arabic: the guard's test page, pair, test, unpair; cards the
card surface in light and dark). The tab's cards flow down at most three
columns of 320px or more (`.alerts-grid`: CSS columns, each card whole),
never a grid: a grid row is as tall as its tallest card, and the page was
empty under the short cards (2026-11-11). Gate:
`test_the_alerts_cards_leave_no_holes_at_any_width` (700 to 2,200px, EN/AR;
the old grid as the negative control).

**Where Stages 2, 3 and 4 meet** (merged 2026-11-09). Each delivery has ONE
receipt, told the outcome once: Kazma's `ops_alerts._Receipt` writes the
receipt line Recent alerts reads (7 days, every alert) and tells the open
page receipt (`page_receipts.record_page`'s callback, only for a page that
opens an incident, until its recovery; the heartbeat's view), then the
caller. The guard's `Notifier._heard` is its one place: `send` and
`send_now` say whether Telegram took the page, and the outcome goes to the
state file's `pager` (Settings) and, for a held page that went out after
all, to its receipt (`on_delivered`). The catalog has one `entry_for` (the
entry itself; Recent alerts reads `as_dict`). A page held because another
sender paged the problem is not in Recent alerts: an `[ops_alert_held]` or
`guard.page_held` line is no page, and `/v1/summary` names each Kazma Watch
incident's `held_by` so a held one is not listed as a page that failed.
Every card Kazma Watch writes is in the zone Kazma told it -- its own
(`step` takes `timeZone`), the test page, and a Cloudflare card (the
settings of the install that told it last, `watch_settings.js`), which is
also emailed to the recipient told. Unpairing removes `install_settings`;
forgetting removes the install's incidents, their notes and its Cloudflare
notices.

**An alert reaches someone, or says it did not (2026-10-02).**
- An alert raised in a worker thread is delivered ON the server's loop
  (`ops_alerts.bind_server_loop`, bound at the top of `_on_startup` and
  unbound at the end of `_on_shutdown`). The chat-app senders' HTTP clients
  belong to that loop. The old delivery thread ran them on a new loop of its
  own: on live the boot check's alert raised `RuntimeError` in the Telegram
  sender and reached nobody. A client first made on such a loop would also
  break the server's own sends afterwards.
- A bus adapter's `send` returns whether the platform took the message (the
  Telegram, Discord and Slack senders, FanOut: any; Null: False). `_deliver`
  counts only a True. A bus that took the alert nowhere falls back to
  Telegram-direct when the routing allows. When the chosen channels
  themselves refused it, the alert is reported as NOT delivered, never as a
  "routing choice". Every sender used to answer `None`, so a finished
  `gather` counted as a delivery and a failed send never reached the
  fallback. The lifecycle card had the same blind spot.
- **The operator can prove it** (Settings -> Alerts -> Send a
  test alert, `POST /api/settings/notifications/test-alert`,
  `ops_alerts.send_test_alert`): one message along the saved routes, from a
  worker thread onto the server's loop like a background job's alert, and
  the routes that took it and those that did not (`_deliver(outcome=)`).
  Never throttled, and logged as `[ops_alerts] test alert ...`, which the
  weekly report does not count. It blocks, so it is a loop-stall helper.
- Gate: `tests/test_ops_alert_delivery.py` (the loop a worker-thread alert
  is delivered on, with the unbound path as the negative control; a refusing
  bus; each platform sender's answer; the test alert's routes and its route).
- **A Telegram alert shows what it says** (`telegram_bus._escape_md`,
  2026-10-16): MarkdownV2's escape character is the backslash, so the escaper
  escapes it FIRST (`_MDV2_SPECIALS`), and the plain-text fallback unescapes
  it too. It did neither, and every Windows path in a Telegram alert lost its
  separators (".venvScriptspython.exe scriptsdocument_worker_image.py build").
  Gate: `tests/test_telegram_bus_backslash.py` (read back the way Telegram
  shows it; the old escaper as the negative control).

**Research/crawl progress and results (2026-10-12).**
`web_acquire.monitor` reconciles local session/crawl journals every 15 s, with
ConfigStore CAS observations committed before delivery through `ops_alerts.alert`.
The notification API merges those durable records into the existing bell; no
fourth notifier or second poller. Completion and partial/error/cancelled/
interrupted outcomes link to saved results. Stalls use meaningful stage/count/
effect changes, not heartbeat timestamps, and never fail/retry the job. Fresh
balances and actual provider HTTP failures are separate from request allowances;
no monitor provider probes. Settings controls are admin-scoped. Initial old
terminal history is quiet. A crawl or pipeline an agent runs in its turn
(`research_crawl`, an unlinked `research_pipeline`: `_AGENT_RUN_KINDS`) is not
alerted: its result goes to the agent (2026-10-20, "Partial results" under a
complete swarm answer). An alert stored for such a run before that rule is
settled at the next scan (`_collect` names the runs it leaves to the agent):
yesterday's stayed in the bell a day after the fix (2026-10-21). An alert for a run no
longer stored -- a session the owner deleted, a crawl evicted from the journal --
is settled the same way (`_collect` names every stored run, 2026-11-02: "Research
failed" for a deleted test session linked to "Session not found";
`tests/test_acquisition_monitor.py`, the monitor without the list as control). A
finished crawl of a library that no longer exists is not stored either
(`_gone_libraries`; a store that cannot answer keeps every crawl, said once), and
deleting a library removes its finished crawls from the journal
(`crawl_jobs.forget_library`, 2026-11-05: "Crawl interrupted" for a deleted
library stayed in the bell), and so does the monitor's scan
(`monitor._forget_gone_crawls`: a library deleted before that rule, or whose purge
failed; the Knowledge page offered Resume on them). The monitor pages its own failure only after
`_UNAVAILABLE_AFTER_S` (5 min) without a check, naming the cause, and follows a page with
`acquisition.monitor_recovered`: it paged on the first failed 15 s check, so a 35 s Postgres restart
sent a red page and no recovery (2026-10-22). Delivery callbacks persist platform acceptance,
refusal or uncertainty; a requested send is never proof of delivery. Unknown
delivery after a crash is not replayed. The loop follows the pytest scheduler
gate and is cancelled/joined before stores close. Soft navigation initializes
page scripts before URL history: research/KB deep links read incoming template
data attributes, not the previous page's location. Gates:
`tests/test_acquisition_monitor.py`, `tests/test_ops_alert_delivery.py`,
`tests/js/test_research_alert_visibility.js`, `tests/e2e/test_acquisition_alerts.py`.

**The guard's credentials come from a child process.** `Notifier` reads
env first, then Kazma's settings — through `_NOTIFY_LOOKUP`, run with the
server's interpreter in the install folder, which loads the install's `.env`
the way the server does. It used to import kazma_core in-process; once
importing stopped loading `.env` (2026-09-22) that lookup had no vault key
and no DSN, and the pager skipped every page for a day (from 2026-09-24
22:30). Never import the app into the guard, never load `.env` into the
guard's own environment (every server inherits it), and never let a test
run the real lookup (`_settings_lookup_allowed` is False under pytest). The
daily digest counts `notify.skipped`/`notify.failed`, so a silent pager shows
up through the app's channel. **A page it cannot send is held, not dropped**
(`Notifier._held`, `_NOTIFY_HELD_MAX` 20): the credentials live in Kazma's
settings database, so a guard started while Postgres starts (a reboot,
2026-10-17: three "never became healthy" pages lost) sends them, marked
"⏳ Delayed", before its next page or on a probe tick once they resolve.

Cooldown default 900s per key (`KAZMA_OPS_ALERT_COOLDOWN_S`). Never raises.
Kill-switch `KAZMA_OPS_ALERTS=0` (does not mute lifecycle). Mute theorem:
60 identical messages = the channel is ignored.

Guard `probe()` reads JSON 503 `checks` and names the failing
dependency (P0). Same-detail restart pages collapse on a cooldown; a
recovery card fires once after an unhealthy kill (P1). A failed
`native_pg_backup` pages (`backup.pg_dump`, with its reason) and so does a
failed offsite copy of it (`backup.pg_offsite`); a success is counted by the
weekly report, never paged (`docs/plans/GUARD_OPS_ALERTING_CAUSE_QUALITY.md`).
The copy goes only into a repository restic is set up for
(`restic_repo.configured_repos`: the offsite one when set, the local one once
its repository or a passphrase exists): the default local path is no
destination, and on a fresh install with no restic every dump's copy failed
three times and stayed a failed task (the drill VM, 2026-11-06). Set up and
failing, it still fails and alerts. Gate: `tests/test_pg_offsite_fresh_install.py`
(the old rule as the negative control).
Do not invent a fourth notifier, mix ops pages into HITL cards, or page every
backup success.

