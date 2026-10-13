# Scheduled industrial hardening — continuous execution

Status: OPEN -- 2026-10-13

## Objective

Carry out the whole Scheduled audit remediation and qualification plan, and
record evidence for each requirement. The plan covers cron reminders,
scheduled agent tasks and scheduled X posts.

Industrial readiness does not follow from fixing only the reproduced bugs,
or from a green unit suite. The plan ends with the qualification soak and
load test in the Stage 5 exit gate below.

## Baseline

**Code:** the uncommitted working tree of 2026-10-13 on top of commit
`d7e27c194d3f170092a9c405709b9197a2c00161`. None of the in-scope Scheduled
files had an uncommitted change when the audit was merged.

**Audit:** `docs/audits/AUDIT_SCHEDULED_2026-10-13.md`.
It merges `scheduled-engine.md` (`SCH-E*`, `SCH-DOC`) and
`scheduled-surfaces.md` (`SCH-S*`), both in the same folder. The result is
23 entries: 8 P1, 9 P2 and 6 P3.

**Inert witnesses** (they confirm defects; they do not test the fix):

- `scheduled_engine_probes.py`: 17 passed in 2.18 s
  (`scheduled-engine-probes.log`).
- `test_scheduled_surfaces_probes.py`: 14 passed in 0.96 s
  (`scheduled-surfaces-probes.log`).

That is 31 witnesses in total. Each asserts that an observed defect is
present. **The witnesses must never become permanent tests that require the
defects.** When a row is fixed, its witness is inverted into a regression
test that asserts the fixed behaviour, and the old behaviour becomes the
negative control.

**Existing selected suites, both green while every witness is green:**

- Engine selection: 140 passed in 12.47 s
  (`scheduled-engine-existing-tests.log`).
- Surfaces selection: 118 passed in 7.79 s
  (`scheduled-surfaces-existing-tests.log`).

The two selections share five files, and their union is 155 distinct tests.
They are not an aggregate count. The defects therefore sit in a test gap.
In particular, `tests/test_scheduled_api.py` uses a fake scheduler.

## Requirements and completion evidence

Rows are ordered by stage, and within a stage by dependency. IDs are the
original audit IDs. A split ID names the half the row carries.

| ID | Required result | Evidence required | State |
|---|---|---|---|
| SCH-S01 (= Swarm SW-S01) | **Stage 1, P1.** No danger-tier call made from detached work skips approval because an ancestor graph step was gated. Detached work means a swarm dispatch, a sub-agent, or any `create_task`, `spawn_background` or `to_thread` started from a tool. The graph flag covers only the calls the graph itself gated, and detached work starts with both HITL ContextVars cleared. One fix closes this row and Swarm SW-S01. | Inverted `test_background_task_inherits_graph_gate_and_skips_bus_approval` and Swarm `test_sw_s01_*`: the tool is refused, or the bus is asked once. A behavioural turn through the real tool worker: `dispatch_swarm`, then a fake engine, then `registry.execute("file_write")`, which reaches the bus or is refused. A class gate over every detached-work starter reachable from tool bodies (AST plus runtime probe), with a planted violation. `tests/test_bus_approval_can_be_answered.py::test_both_gates_honour_an_actual_approval` re-pointed to the per-call rule. Negative control: the old node-wide flag. | Open |
| SCH-S03 | **Stage 1, P1 (multi-user/production profile).** Every cron mutation route and tool is tenant-scoped. Another tenant's id answers exactly like an unknown id. A missing tenant is refused in multi-user mode. `edit_scheduled` has a resolver that checks the caller's tenant, and its card shows the old and the new prompt. In multi-user mode, delivery goes to the booking principal. | Inverted `test_cron_mutations_ignore_tenant_while_list_is_scoped`. A route-by-tool tenant matrix in the style of `tests/test_memory_routes_tenant_scope.py`: every `/api/scheduled/*` route and every scheduling tool, called as tenant B with A's ids, changes nothing. Negative control: the current store methods. | Open |
| SCH-S05 (authority half) | **Stage 1, P2.** Fired jobs run with a declared capability set, shown at booking. The default is read tools plus the one delivery. Write-tier delegation and booking are opt-in per job. The injected prompt is labelled as a stored instruction. Jobs do not fire while HITL is disabled, unless the operator opts in. | A cron-graph tool-surface test: the tool definitions offered to a fired job equal the declared set (negative control: the full registry). A test that a job does not fire while HITL is disabled. A test that the approval card shows the capability set. | Open |
| SCH-E04 | **Stage 2, P1.** No silent loss across a restart or crash. A leased fire record (owner, lease, attempt, phase receipts). Every phase transition is one `BEGIN IMMEDIATE` compare-and-swap, and recurrence advances in the same step as finalize. Recovery decides per phase: retry, deliver the stored result, or finalize; never RUNNING-to-FAILED. Shutdown drains fires within a bound (`asyncio.wait`) before the agent closes. `/health/activity` counts fires. Exec-task exceptions are retrieved and logged. | Inverted `test_e04_*`. A real child-process crash matrix after every statement of `_execute`, `_deliver` and `_finalize`, for one-shot and daily jobs, in the pattern of `tests/test_document_crash_recovery.py`: each case ends with exactly one delivery, or one honest terminal state with an alert, and a daily job keeps a correct `next_run`. A `--when-idle` reload waits for an in-flight fire. Negative controls: the three-statement `_finalize` and the RUNNING-to-FAILED recovery. | Open |
| SCH-E01 | **Stage 2, P1.** A turn with `error` or `turn_failed` is a failed fire: a typed outcome, the failure budget, and `cron.job_failure`. An error string is never delivered as the reminder. Reminders deliver their stored message without the LLM. A failed turn of an agent task is a failure. | Inverted `test_e01_*`. A stub turn that raises, and one that returns `turn_failed`, for one-shot and daily jobs. Negative control: the current `_execute`. | Open |
| SCH-E02 + SCH-S05 (delivery half) | **Stage 2, P1.** Cron delivery goes through a durable, leased outbox with bounded retries, acknowledged only on `sent:`. A refused or undeliverable delivery ends `undeliverable` with one ops alert (`cron.delivery_failed`), never DONE. The delivery outcome and its reason appear on the job, on the page and in the daily digest. | Inverted `test_e02_refused_delivery_is_done` and `test_refused_delivery_is_recorded_as_done`. A refusing backend gives `delivery_pending`, then `undeliverable`, with a retry history and exactly one alert. A backend that recovers gives exactly one delivery. A digest line. Negative control: today's `_deliver`-then-`_finalize` order. | Open |
| SCH-E06 | **Stage 2, P2.** An explicit `missed` state with a per-job catch-up window. A "missed while offline" notice through the outbox. A late delivery carries its original due time. The policy is documented for reminders, agent tasks and X posts. | Inverted `test_e06_missed_one_shot_skipped_silently`. Fake-clock tests for each lateness band: "missed" never reads as done, and each missed job yields exactly one notice. Negative control: the current stale skip. | Open |
| SCH-E07 | **Stage 2, P2.** Retention purges by `finished_at` and keeps the last outcome of every recurring job. Recovery reads every running row (`WHERE status='running'`, no limit). | Inverted `test_e07_*`. Purge and recovery behave the same with 0, 499 and 5,000 rows. Negative controls: the `created_at` purge and the limit-500 `list_all`. | Open |
| SCH-E12 + SCH-S07 | **Stage 2, P2.** Only legal actions are offered and accepted: edit and cancel carry a version and an expected status. An edit or cancel during a fire is recorded and applied after it. A refused action answers `busy` (409) with a reason, not `not_found`/404. Bringing back a done, failed or cancelled job is an explicit action. The page shows the recurrence, failure count, last delivery result and destination. | Inverted `test_e12_reschedule_during_fire_is_lost` and `test_running_delete_and_cancelled_resurrection`. A state-by-action matrix on the real store. A blocked-fire race test. A JS test that every offered control maps to an allowed transition. Negative controls: the current `update_job` and `canDelete`. | Open |
| SCH-S02 | **Stage 2, P1.** Editing a scheduled task never changes the stored prompt unless the operator edited it. The list returns a display summary, the dialog fetches the full prompt, a timing-only edit sends no prompt, and a stale form is refused by version. | Inverted `test_page_edit_round_trip_truncates_and_rewrites_prompt`. A real-store route round trip (list, edit timing, save) leaves the prompt byte-identical, with `tests/test_scheduled_api.py` using the real scheduler for it. Negative control: the current `openEdit`/`saveCron`. | Open |
| SCH-E03 | **Stage 3, P1.** A naive time means the operator's zone on every path. Chat (`authorize_effect`), Web create, `edit_scheduled` and X booking resolve one string to one instant. The gate rewrites only to an aware ISO string in the operator's zone, or not at all. `edit_scheduled` goes through the same resolver. AGENTS §20's rewrite-on-allow is restated in the same change. | Inverted `test_e03_commitment_rewrite_shifts_naive_iso`. A shared cross-path fixture in UTC, Asia/Kuwait and America/New_York. Negative control: the UTC-assuming `parse_absolute_timing`. | Open |
| SCH-E05 + SCH-S09 (recurring items) + SCH-E09 (docstring item) | **Stage 3, P1.** English and Arabic recurring requests keep their recurrence and clock anchor through the commitment gate, and are never turned into a one-shot. The X tool docstring, the X dialog hint and the docs offer only forms the X path accepts. | Inverted `test_e05_*` (English and Arabic) and `test_recurring_x_time_is_advertised_then_refused`. An English and Arabic recurrence corpus through `authorize_effect`, whose first occurrence matches the scheduler. A test that every docstring, manifest and hint example is accepted by its validator. Negative controls: the current resolver and docstring. | Open |
| SCH-E09 (parser items) + SCH-S06 (timing items) | **Stage 3, P2.** One typed validator for cron and X. It accepts RFC 3339, future times only and a finite range; applies one DST policy; supports a typed recurrence (an RRULE subset: daily or weekly, BYHOUR/BYMINUTE, BYDAY, UNTIL/COUNT, TZID); states the zone in errors and on the form; and answers 400 for validation errors, never 500 or 404. | Inverted `test_e09_parser_gaps`, and the timing assertions of `test_cron_routes_accept_past_zero_huge_and_misreport`. A timing input table run through both the cron and X paths. A fake-clock run across two DST transitions: each daily occurrence exactly once, at the intended local time. Negative control: the current parser. | Open |
| SCH-S04 | **Stage 4, P2.** The Scheduled page reads X operation truth, and shows upcoming, needs attention, published, cancelled and failed, with catalog keys in both languages. Edit and Cancel go through the revision-bound queue actions. `PUT/DELETE /api/scheduled/x/{id}` are retired, or held to the same binding as X Studio. | Inverted `test_new_x_bookings_are_managed_and_page_treats_them_as_done`. A JS test over every projected X and cron status (negative control: `else c.done++`). A route parity test for both reschedule paths. The Arabic page tour seeded with `managed` and `held` posts. | Open |
| SCH-E08 | **Stage 4, P2.** Rebooking a terminal proposal, or a proposal booked at another time, never reports `scheduled: True`. It answers a conflict or creates a new key revision. The response echoes the stored `due_at` and state, and `ok` is false unless the state is `scheduled`. | Inverted `test_e08_rebook_after_cancel_reports_scheduled`. A rebook after cancel, after `failed_permanent`, and while pending at a new time. Negative control: the current `reserve` replay. No real X call. | Open |
| SCH-S08 | **Stage 4, P2.** Clarify and confirm cards carry structured options (kind, ISO instant, job id). They are rendered from the catalog in both languages and show the date, time and zone. The question is phrased for the user. | Inverted `test_remind_clarify_options_are_english_and_date_only`. An Arabic e2e clarify step: no English, and a visible time. The semantic card added to `english_on` coverage. Negative control: the current label builder. | Open |
| SCH-E14 + SCH-S06 (bounds items) | **Stage 5, P2.** Caps per tenant and per thread, with typed refusals. Body limits on prompt and timing. A rate bucket for `/api/scheduled/*`. A per-kind execution budget in configuration. An index on `(status, next_run)`. | The size assertion of `test_cron_routes_accept_past_zero_huge_and_misreport`, inverted. Body-size, cap and rate tests. A load test with 1,000 pending jobs and 50 due at once, within a stated latency bound. | Open |
| SCH-E10 + SCH-S11 | **Stage 5, P3.** No settings, RBAC or SQLite read, and no graph build, on the event loop from cron, the Scheduled routes or the scheduling tools. These values are resolved once, in a thread, at the boundary. | Inverted `test_e10_cron_reads_settings_on_the_loop` and `test_cron_routes_read_settings_and_rbac_on_the_loop`. `_LOOP_STALL_HELPERS` extended, or the gate made to follow public and imported wrappers, with a planted async caller of `multi_user_or_production` as control. The scheduled routes added to the GET-route loop walk, with the RBAC store traced. Coordinate with KB-S06, MEM-S10, SW-E07 and TT-07. | Open |
| SCH-E11 | **Stage 5, P3.** A cron poll heartbeat in readiness, with a staleness threshold. Metrics and digest lines for fires, lateness p50/p95, missed, failed and undelivered. An alert when the failure budget runs out. | Inverted `test_e11_health_ok_with_dead_poll_loop`. Cancelling the poll task makes readiness degraded within one interval. An emitted-line gate (`tests/_emitted_lines.py`). | Open |
| SCH-E13 | **Stage 5, P3.** The X fire cycle isolates per-row errors (a held state plus a notice). A bounded automatic retry for provably unsent (`not_sent`) posts, within the missed window. Retention by age for terminal operations, events and delivered outbox rows. The booking reply gives its time in the stated zone. | A raising-row test: a later due row is still claimed in the same cycle. A `not_sent` retry test. A retention test. No real X call. | Open |
| SCH-S10 | **Stage 5, P3.** No scheduling tool result contains a platform address. The model gets the platform and a non-identifying label. | Inverted `test_schedule_task_result_hands_the_chat_id_to_the_model`. A result-shape test over every scheduling tool: no match for `^\w+:\d+`. Negative control: the current result. | Open |
| SCH-S12 | **Stage 5, P3.** Dates go through `KazmaFormat`, with a zone label. One same-origin helper over `configured_browser_origins()` replaces the copies in `scheduled_api.py`, `x_api.py` and `email_api`. Every cron mutation records its actor and source. | Inverted `test_page_dates_bypass_kazmaformat_and_x_menu_label` (dates half), `test_local_csrf_copy_ignores_declared_public_origin` and `test_cron_rows_record_no_actor`. A static gate that refuses `Intl.DateTimeFormat` and `toLocale*` outside `locale_format.js`, with a planted call. A CSRF test with `KAZMA_PUBLIC_URL` and a rewritten `Host` (negative control: the current copy). An audit-row test. | Open |
| SCH-DOC + SCH-S09 (documentation items) | **Stage 5, P3.** Every item in section 6 of the merged audit is corrected in the same change as its behaviour. That covers AGENTS.md (§16B fallback text, §16 invariant count, §51 cron note, §7A double-gating wording, §20 rewrite-on-allow and the G2 note, §29C rebook scope); the comments in `scheduler.py`, `app.py`, `health.py` and `x_api/schedule.py`; `x-publisher.md`, `native-skills.md`, `api-routes.md` and `configuration.md`; the manifest statuses; and the `/x` menu. A new section says what a scheduled task can do when it fires. | A doc and comment diff tied to the behavioural tests above. A docs check that the route paths named in `x-publisher.md` exist in the built app. The menu half of `test_page_dates_bypass_kazmaformat_and_x_menu_label`, inverted. | Open |

## Stage exit gates

The full text is in section 7 of the merged audit. A stage is complete only
when its gate passes on current evidence.

1. **Stage 1:**
   - the SCH-S01/SW-S01 end-to-end test and the detached-work class gate
     pass;
   - the route-by-tool tenant matrix is green;
   - a fired job's offered tools equal its declared set.
2. **Stage 2:**
   - the real-process crash matrix gives exactly one delivery, or one honest
     terminal state with an alert, for one-shot and daily jobs;
   - a provider outage gives a failed fire plus an alert;
   - a refusing platform gives `undeliverable` plus an alert;
   - a `--when-idle` reload waits for a fire;
   - the edit round trip leaves the prompt byte-identical;
   - the state-by-action matrix is green;
   - "missed" never reads as done.
3. **Stage 3:**
   - one fixture gives the same instant or series on every path, for English
     and Arabic, in UTC, Asia/Kuwait, America/New_York and Europe/London;
   - a fake-clock run across two DST transitions is exact;
   - the docstring examples are accepted by their validators.
4. **Stage 4:**
   - JS status tests and reschedule-path parity pass;
   - the rebook tests pass;
   - the Arabic tour finds no English and no raw status ids;
   - the Arabic clarify step shows a time and a zone.
5. **Stage 5 (qualification).** This gate is not a finding row, and the goal
   is not complete without it. A 7-day fake-clock soak with thousands of
   jobs, injected provider, delivery and database failures, and restarts
   every few hours, must show:
   - zero silent losses;
   - every non-delivered job visible with its reason, and alerted once;
   - p95 fire lateness at or below one poll interval plus the turn time;
   - readiness degraded within one interval of a dead loop.

   The 1,000-job / 50-due load test must stay within its bound. Every
   witness must be inverted, and the docs checks must pass.

## Execution rules

- **Keep the boundaries the design already has:**
  - booking stays `danger`/`ALWAYS_HITL`;
  - checkpointer-less child graphs keep `auto_deny`;
  - the job's tenant is installed at fire time;
  - the delivery address is captured at booking (AGENTS §16B);
  - the cron store keeps its autocommit rule (AGENTS §16C);
  - the X publisher's claim, lease, binding and `outcome_unknown` rules
    stay;
  - platform ids stay out of graph state (AGENTS §2).
- **Fix equivalent sibling paths together.** Examples: both X reschedule
  paths; every cron mutation route and tool; every detached-work starter.
- **Each row gets three things:**
  - a behavioural regression test;
  - a class gate wherever the defect has siblings;
  - a negative control that shows the gate failing on the old behaviour
    (AGENTS §28, §35).
- **Re-point existing tests that pin the old behaviour; do not delete
  them.** Example: `test_both_gates_honour_an_actual_approval`.
- **No row is complete without current evidence of its full scope.** The
  soak may stay outstanding while implementation proceeds. Do not silently
  remove it, shorten it, or mark the whole goal complete.

## Execution log

- 2026-10-13: Tracker opened from the merged audit. No implementation yet.

## Start here

**Read first:**

1. `docs/audits/AUDIT_SCHEDULED_2026-10-13.md`.
2. The two source halves in the same parent folder:
   `scheduled-engine.md` and `scheduled-surfaces.md`.
3. Swarm `SW-S01` in `swarm-surfaces.md:76-109`.
4. The witness files `scheduled_engine_probes.py` and
   `test_scheduled_surfaces_probes.py`, to see the exact defect each row
   must invert.
5. AGENTS.md: §2, §7 A/B, §15B0, §16 (A, B, B2, C), §20 (remind act,
   rewrite-on-allow), §28, §29C, §30, §35 and §51.
6. Code:
   - `kazma-core/kazma_core/cron/scheduler.py`
   - `kazma-core/kazma_core/agent/turn.py`
   - `kazma-core/kazma_core/agent/graph_tool_worker.py`
   - `kazma-core/kazma_core/agent/tool_registry.py`
   - `kazma-core/kazma_core/agent/tool_builtins/system.py`
   - `kazma-core/kazma_core/agent_runner.py` (`build_child_graph`)
   - `kazma-core/kazma_core/safety/commitment/authorize.py`
   - `kazma-core/kazma_core/safety/commitment/relative_time.py`
   - `kazma-core/kazma_core/safety/commitment/side_effects.py`
   - `kazma-core/kazma_core/tools/send_message.py`
   - `kazma-core/kazma_core/x_api/publication_service.py`
   - `kazma-core/kazma_core/x_api/publication_store.py`
   - `kazma-core/kazma_core/x_api/notifications.py` (the outbox pattern to
     reuse)
   - `kazma-core/kazma_core/x_api/booking.py`
   - `kazma-core/kazma_core/x_api/schedule.py`
   - `kazma-ui/kazma_ui/scheduled_api.py`
   - `kazma-ui/kazma_ui/static/js/scheduled.js`
   - `kazma-ui/kazma_ui/templates/scheduled.html`
   - `kazma-ui/kazma_ui/app.py` (cron wiring and shutdown)
   - `kazma-ui/kazma_ui/health.py`
   - `kazma-skills/kazma_skills/native/task_scheduler_cron/tools.py`
   - `kazma-skills/kazma_skills/native/x_publisher/tools.py`
   - `tests/test_document_crash_recovery.py` (the crash-matrix pattern)

**Order of work:**

- Begin with SCH-S01, together with whoever holds Swarm SW-S01: one fix,
  one evidence set, both rows closed.
- In Stage 2, build the fire record (SCH-E04) first. SCH-E01, SCH-E02,
  SCH-E06 and SCH-E12 depend on its states and versions.

**Run rules:**

- **Run every pytest process at below-normal priority.**
  `scripts/fast_test.py` lowers its own processes
  (`scripts/fast_test.py:158`). Start targeted runs at BelowNormal yourself.
- **Test order.** Run targeted tests while working. Run the full suite once,
  at the end, with `python scripts/fast_test.py` and no `--chunks`, using the
  repository's `.venv` interpreter.
- **Run e2e separately.** `fast_test.py` excludes `tests/e2e`
  (`scripts/fast_test.py:76-79`). Run the e2e files you touch on their own,
  including `tests/e2e/test_pages_read_in_arabic.py` after any template or
  i18n change.
- **Never touch the live install** at `C:\Users\balfa\kazma`: no edits,
  copies, pulls or reloads.
- **Never start, stop or reload the Kazma server.**
- **Never post to X, and never send real Telegram, Slack or Discord
  messages, from tests.** Use temporary stores under `tmp_path`, fake send
  backends and bus, a stub LLM, and an X client that fails the test if it is
  reached, as the witnesses do.
- **Fix the class, not the instance.** Each change adds a gate with a
  negative control (AGENTS §28, §35).
- **Leave the witness files where they are.** They stay in
  `session-artifacts/audits-2026-10-13/` and are not copied into `tests/` as
  they are.
- **Compile-check before finishing:** `py_compile` for changed Python, and
  `node --check` for changed JavaScript.
- **No commits or pushes** unless the user asks.

**Closing a row:** set its State to
`Implemented YYYY-MM-DD — <what changed; evidence: test names and log paths>`.
Name in the State anything that is still open. Write logs under
`session-artifacts/`.
