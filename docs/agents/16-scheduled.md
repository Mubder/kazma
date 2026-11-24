<!-- AGENTS.md, piece 16-scheduled. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 16. Scheduled reminders and tasks (`kazma-core/kazma_core/cron/`)

The agent's `schedule_task` / `list_scheduled` / `edit_scheduled` /
`cancel_scheduled` (native `task_scheduler_cron`) and the Scheduled page
(`/scheduled`, `kazma_ui/scheduled_api.py`) → `CronScheduler`
(`cron/scheduler.py`) → `SQLiteCronStore` (`cron/store.py`,
`kazma-data/cron.db`). X posts on the same page are the X publication
store's operations (§29C). Hardened 2026-10-14 against the Scheduled audit
(`docs/audits/AUDIT_SCHEDULED_2026-10-13.md`, tracker
`docs/plans/SCHEDULED_INDUSTRIAL_HARDENING_GOAL.md`; the 7-day fake-clock
soak's harness is built, I; the run itself is still open). Every rule below
must hold:

**A. The scheduler MUST be constructed with a `graph_builder=`.**
`app.py` builds `CronScheduler(store=…, graph_builder=_cron_graph_builder,
poll_interval=…)`. The closure returns `cron_fire_graph(agent,
capabilities=…)` (`cron/capabilities.py`): the agent's one-shot
`build_child_graph(declared_tools=…)` (checkpointer=None). Without it a
task's fire fails -- counted and alerted, never delivered. A reminder needs
no graph.

**A2. A fired job does only what it declared** (decision D12, SCH-S05).
`kind="reminder"` (the tool's default) delivers its stored text with NO
model call. `kind="task"` runs an agent turn offered exactly its declared
tools: `read` (read-tier tools, the default) or `read_write` (the write
tier too); MCP and `ALWAYS_HITL` tools never. `DeclaredToolExecutor`
refuses a call to anything else, and the job's one delivery is the
scheduler's, not a tool. The prompt reaches the model labelled as a stored
instruction. The booking's approval card says what the job will do, and an
edit's card shows the prompt now and after (`cron/card.py`). A task does
not fire while HITL is off unless the operator set
`safety.cron_fire_without_hitl` (the agent cannot: `safety.` is protected,
§45). Gate: `tests/test_cron_capabilities.py` (the old builder's whole
registry as the negative control).

**A3. Ready-made automations** (2026-11-14, REMAINING 4.3,
`cron/recipes.py`): a recipe is an ordinary `task` with `capabilities="read"`
(days fixed, time chosen, the page's language), booked by the Scheduled page's
own path (`scheduled_api` `_book`, which the page's form uses too: delivery
address, zone, outbox, approval card all apply). The `read` set includes the
`safe` tier (email reading: `side_effects` and the MCP server already count
it as read), never `email_send`/`email_delete`/`email_categorize`. Gates:
`tests/test_automation_recipes.py` (the real scheduler; every recipe a card
and words), `tests/test_cron_capabilities.py`
(`test_a_read_task_reads_email_and_never_sends_it`, the old read set as the
control), `tests/e2e/test_automation_gallery_browser.py`.

**B. `delivery_target` is captured at schedule time, not resolved at fire time.**
`schedule_task` reads `get_current_delivery_target()` (the
`_current_delivery_target` ContextVar, bound at the gateway handler entry
AND re-set in the tool-worker node from the `_gateway` routing block —
same two-layer pattern as `_current_thread_id`) and stores it on the job.
At fire time the job's own target is used. A job whose target is empty or
malformed adopts a sibling job's target on the same thread (and keeps it),
then tries the thread's session -- best effort only: sessions TTL-evict
after 5 min, so no reminder may rely on it. With none, the delivery is
`undeliverable` at once, with an alert naming the job; nothing goes to a
guessed address (the old `"{platform}:unknown"` fallback is gone). No tool
result or page answer carries the address: `ScheduledJob.to_dict()` gives
`destination`, a label (SCH-S10). The platform-isolation invariant (§2) is
preserved — `chat_id` never enters graph state; `delivery_target` joins
`thread_id`/`platform` in the internal `_gateway` routing sub-dict.

**B2. A web turn's delivery target is for reminders, not for answers.**
`web_gateway_block` stamps the operator's Telegram as a web turn's
`delivery_target` so a reminder booked on the web rings somewhere. A tool that
answers INTO the conversation reads the conversation's platform
(`send_message.get_current_platform()`, bound beside the delivery target in
the tool worker), never the delivery target alone: `send_file` did, and a web
chat's files went to Telegram while the answer said "sent to this chat"
(2026-09-28). On a web turn `send_file` and `generate_image` share a copy into
the chat (`kazma_core/chat_files.py`, `<data dir>/attachments/shared/`),
served by `GET /api/chat/files/{id}` to that chat's owner only (raster images
inline, everything else a sandboxed download) and shown through the Markdown
the tool hands the model. `tests/test_web_chat_files.py`.

**C. The store's connection is autocommit (2026-09-27).** `SQLiteCronStore`
opens cron.db with `isolation_level=None`, so every statement is its own
transaction. In Python's default mode a DELETE opens a transaction even when
it matches nothing, and the purge (at start and hourly) committed only when
it had deleted something: from each boot until the next reminder fired the
server held cron.db's write lock, and the live-data cleanup stopped half way
with "database is locked". A group of writes (a claim, a fire's finish, a
delivery's outcome) is one `BEGIN IMMEDIATE` through
`db.sqlite_session.write_transaction`. Gates:
`tests/test_cron_store_write_lock.py` (every public method, then another
connection must take the write lock at once) and §35's kept-connection gate.

**D. A fire is a durable, leased record, and ends once** (SCH-E04, E01,
E07). A fire claims its job by compare-and-swap (`claim_job`: pending →
running, with `fire_owner`, `lease_until`, `fire_due`, `fire_attempt` and
the version it read). Its result, a recurring job's next occurrence, the
job's status and the delivery row then commit in ONE transaction
(`finish_fire`). A crash anywhere leaves either nothing (the fire runs
again) or a finished fire whose delivery the outbox owns. Recovery decides
per phase. At boot every running row belongs to a dead runtime and goes
back to pending at the same occurrence; it used to be marked FAILED,
delivered or not. While running, a row past its lease, or ours with no
task, goes back the same way. A failed agent turn (an exception, `error`,
`turn_failed`) is a failed fire: counted and alerted (`cron.job_failure`;
`cron.job_failed` when its budget of 3 runs out), never delivered as the
reminder. A task's turn runs in its fire's own task, under
`asyncio.timeout` (§9: on Python 3.11 `wait_for` gave it a task of its own,
which a second cancel left running). Shutdown stops cron first and drains
fires within a bound; `/health/activity` counts them (`active_fires`), and
`kazma_guard.py --reload --when-idle` waits for them. Retention purges by
`finished_at`, and recovery reads every running row. Gates:
`tests/test_cron_fire_record.py`, `tests/test_cron_crash_matrix.py` (a real
child killed at each phase, one-off and daily: exactly one delivery, or one
honest state with an alert; the old RUNNING→FAILED recovery is the negative
control).

**E. Delivery goes through a leased outbox, acknowledged only on `sent:`**
(SCH-E02, §51). `cron_deliveries` holds a row per message. It is claimed
with a lease, retried with backoff (30 s doubling to 15 min, at most 6
attempts within 2 hours), and acknowledged only when `send_message`
answers `sent:`. A refused delivery ends `undeliverable` -- the job too,
never DONE -- with exactly one alert (`cron.delivery_failed`). A row whose
sender died mid-send ends `unknown` (`delivery_unknown` on the job, alert
`cron.delivery_unknown`) and is never sent twice. The job, the page and the
daily digest show the outcome. Gate: `tests/test_cron_delivery_outbox.py`
(the old deliver-then-finalize order is the negative control).

**E2. Alerts go through an outbox too** (2026-10-15, found by the Stage 5
soak). A change that needs an ops alert writes its `cron_alerts` row in its
OWN transaction (`SQLiteCronStore._enqueue_alert`, called only inside
`self._tx()`): a failed fire or a budget run out in `finish_fire`
(`cron.job_failure` / `cron.job_failed`), a message ended undeliverable in
`finish_delivery` (`cron.delivery_failed`), a send recovered `unknown` in
`recover_deliveries` (`cron.delivery_unknown`). The scheduler drains it after
the commit -- after a fire, after a delivery pass, at each poll and at start
(`CronScheduler._drain_alerts`) -- into `ops_alerts.alert` (§33: no fourth
notifier), and marks a row sent only after `alert` returned. A crash after the
commit loses nothing; a crash between the alert and its mark raises it once
more, the only duplicate. One pass at a time, a row whose mark failed is
marked next pass without being raised again, and `stop()` waits for a pass in
progress before it cancels. A row `ops_alerts` cannot raise stays queued with
a WARNING and a backoff (`kazma_cron_alerts_pending` on /metrics,
`alerts_pending` in readiness). Raised rows follow the job retention; one not
yet raised is kept. A missed occurrence alerts only through its notice's
delivery. Until then a delivery's alert was marked before it was raised and a
fire's was raised after its commit: a crash in either window lost it. Gate:
`tests/test_cron_alert_outbox.py` (a real child killed in each window of each
gap, the old order as the negative control; the cron engine names
`ops_alerts.alert` only in `CronScheduler._raise_alert`; no alert row outside
its change's transaction). The soak kills in both windows and counts the
recorded duplicates (`scripts/scheduled_soak.py`, `alert_redelivered`).

**F. Missed is missed** (SCH-E06). A job due longer ago than its catch-up
window does not run. The window is `cron.catchup_minutes` (default 60), or
`KAZMA_CRON_STALE_HOURS` when that is unset; a window given at booking wins
over both. The job is `missed` (a recurring job moves on to its next
occurrence), and its chat gets ONE notice through the outbox. A late fire
inside the window says how late ("(due 09:00 Asia/Kuwait, 12 min late)").
X posts keep their own 5-minute hold (§29C). Gate:
`tests/test_cron_fire_record.py::test_each_lateness_band`.

**G. One timing model, in the operator's zone** (SCH-E03, E05, E09;
`cron/timing.py`). A naive time means `cron.timezone` (else `KAZMA_TZ`,
else UTC) on every path: chat, the Web form, `edit_scheduled` and X
booking resolve one string to one instant. It accepts `5m`/`2h`/`3d`,
RFC 3339 (`Z` and offsets honoured), and a typed recurrence: daily,
weekdays or weekly at a clock time, or an RRULE subset (FREQ daily/weekly,
BYDAY, BYHOUR/BYMINUTE, UNTIL/COUNT, TZID). It refuses, naming the zone:
the past; more than two years ahead (`MAX_HORIZON`); more than 100
characters; and a one-off inside a clock change's gap or fold. A recurring
job takes the earlier time of a fold and shifts across a gap. X booking
takes one-offs only. The commitment gate (§20) never rewrites a recurrence,
and rewrites a one-off only to an aware instant in that zone; a recurring
request booked as a one-off is refused. Gate:
`tests/test_cron_timing_model.py`. It runs one fixture in UTC, Asia/Kuwait,
America/New_York and Europe/London, a daily series across two clock
changes, and checks that every docstring, manifest and hint example is
accepted by its validator.

**H. Only legal actions, and the stored truth** (SCH-E12, S02, S07, S04).
The statuses are pending, running, delivering, done, failed, cancelled,
undeliverable, missed and delivery_unknown.
- Edit and cancel carry the version the form read; a stale one is 409
  `conflict`. During a fire they are recorded and applied when it ends
  (202 `edit_pending` / `cancel_pending`).
- A finished job comes back only through Restore
  (`POST /api/scheduled/cron/{id}/restore`).
- The page offers only what the server lists in a job's `actions`. Its
  dialog fetches the full prompt and sends only the changed fields, so a
  timing edit never touches the prompt.
- The page's X rows are the publication store's operations, changed through
  `/api/x/queue/{op}/reschedule|cancel`. The page's own X PUT/DELETE routes
  are gone.
- Every change records its actor and source (`cron_events`).
- The API and the page share one status-class map
  (`tests/test_scheduled_presentation.py`).

Gates: `tests/test_scheduled_api.py` (real scheduler and store),
`tests/test_cron_fire_record.py::test_state_by_action_matrix`,
`tests/js/test_scheduled_page.js`.

**I. One operator, bounded, off the loop** (SCH-S03, E14, E10, E11, S12).
- **Single operator:** on an install with platform users every Scheduled
  surface refuses (decision D1, `cron/single_operator.py`). The routes answer
  409 `scheduled_single_operator`, the page shows a banner, the tools give the
  reason. In production mode reads and changes keep to the caller's tenant;
  another tenant's id reads as unknown.
- **Bounds:** 500 jobs a tenant and 50 a thread (Settings keys
  `cron.max_jobs_per_*`); a prompt of 8,000 characters and a timing of 100; a
  `scheduled` rate bucket (30 a minute); a task turn's budget
  (`cron.task_budget_seconds`).
- **Throughput:** a pass reads up to `max(50, max_concurrent * 10)` due
  jobs and starts them all; the semaphore holds all but `max_concurrent`.
  50 jobs due among 1,000 fire in one pass
  (`tests/test_cron_load.py`; the old four-a-pass rule is the negative
  control).
- **Off the loop:** the zone, the tenant rule, the address and the graph build
  are resolved in a thread (static gate area `scheduled`,
  `tests/test_scheduled_off_loop.py`).
- **Readiness and metrics:** readiness reads the poll heartbeat
  (`CronScheduler.liveness()`), degraded once the heartbeat is older than
  two poll intervals plus 30 s. /metrics carries fires, missed jobs, lateness p50/p95, the
  outbox and the heartbeat. The digest counts outcomes from the
  `[cron] outcome` line (`tests/test_cron_liveness.py`).
- **Dates and origin:** dates on the page and in X Studio go through
  `KazmaFormat`. `kazma_ui/same_origin.verify_same_origin` is the one
  same-origin check for the Scheduled, X and email routes.
- **Qualification soak:** `scripts/scheduled_soak.py` (Stage 5) runs the real
  scheduler and store on a private cron.db through seven simulated days of a
  fake clock -- the engine reads every time and its poll wait from one
  `CronClock` (`cron/clock.py`; gate `tests/test_cron_clock.py`) -- with
  thousands of jobs, injected provider, delivery and database failures and
  restarts, and checks each due occurrence against its own ground truth
  (`tests/test_scheduled_soak_harness.py`: a short run, and planted losses as
  negative controls).

**Multi-tenant memory:** tenants are enforced with
`KAZMA_MEMORY_ENFORCE_TENANT=1`, in production (`KAZMA_PRODUCTION=1`) and with
multi-user on (`memory.config.memory_tenant_enforced`, the one answer for
turns and surfaces). **Turns and surfaces resolve the same tenant** (Memory
audit M07, 2026-10-13): under enforcement both are the principal's tenant in
every mode (`resolve_tenant_id` wrote shared-mode turns to "default" while the
page looked in the principal's), and only `shared` can be saved
(`settings_validation`, key `memory.config.TENANT_MODE_KEY`); not enforced,
the page keeps the install's own view, and its "Don't remember" ledger rows
and About me (`profile.about_for_turn`) reach every tenant's turns. The chat
delete says when there was nothing to forget. Gate:
`tests/test_memory_tenant_matrix.py` (mode x enforcement x principal; the old
resolver as negative control). Then every `/api/memory` route
reads, counts and changes only the request tenant's rows -- another tenant's
id reads as not found (`memory_api._row_tenant_ok`,
`hygiene.invalidate_belief(tenant_id=)`) -- and the engine's own maintenance
(task queue, reconsolidation, golden eval) is admin-only
(`platform_rbac._ADMIN_PREFIXES`). The install's own tenant (`default`: no
binding) is the install's view (`_tenant_clause`) only while Spaces is not
enforced; once it is, `default` is the owner's space and reads its own rows
(§70, `memory/tenant_view.py`). Gate:
`tests/test_memory_routes_tenant_scope.py` -- every route, enumerated from the
app, called as one tenant with another's ids; nothing of the other shown or
changed, and the caller's own rows shown. **Entities are keyed by
`(tenant_id, id)`** (Memory audit M21, 2026-10-15, `memory/entity_key.py`):
each tenant holds its own row of an id -- its own `kazma`, its own `user` hub
(`self_hub.ensure_user_hub` writes the caller's) -- and `entity_merges` names
its entities by the pair (composite foreign keys). Every statement on
`entities`, `entity_merges` or `entity_merges_archive` names its tenant, and
one that picks a row by id names it in that WHERE; a statement that reads
across tenants on purpose is declared with why. Which row a request means is
one answer, `entity_key.entity_owner`: a tenant's own; the install's view
(`default`) its own first, else the one tenant holding the id (several, and
none of the install's: not found). A merge keeps to the source's tenant (a
target of another tenant is not found), and the belief rewire and the ledger
row are that tenant's. A store made before then keeps the `id` key until the
operator runs `scripts/memory_entity_key.py` (`size` reads only; `apply --db
--yes` copies to `<db>.pre_entity_key` first, then rebuilds both tables in one
transaction -- every row, id, tenant and rowid kept, the search index rebuilt
-- idempotent); every statement works on both keys (on the old one the
2026-10-13 rules hold the boundary: creation never writes another tenant's
row, rename promotes only a node of the caller's own facts, an id another
tenant holds and an unused one both answer not_found), and memory health says
which key the store has (`entity_key`). The Postgres mirror holds facts and
memories, never entities. Gates: `tests/test_memory_entity_key.py` (the source
gate, planted old statements as its control; two tenants holding one id
through every memory route, enumerated from the app, and every agent memory
tool, from the registry, with an owner lookup answering the other tenant and
the old key as negative controls; the migration on a copy of a store the
product's writers seeded under the old key, then recall, the graph and a
merge; the script), `tests/test_memory_entity_tenancy.py` (the four witnesses
inverted).

