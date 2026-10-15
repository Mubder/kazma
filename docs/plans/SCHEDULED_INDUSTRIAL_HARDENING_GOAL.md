# Scheduled industrial hardening — continuous execution

Status: OPEN -- 2026-10-15: every finding row implemented; the Stage 5 soak's harness is built (`scripts/scheduled_soak.py`); the qualification run itself (D3, owner) and an independent review remain

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
| SCH-S01 (= Swarm SW-S01) | **Stage 1, P1.** No danger-tier call made from detached work skips approval because an ancestor graph step was gated. Detached work means a swarm dispatch, a sub-agent, or any `create_task`, `spawn_background` or `to_thread` started from a tool. The graph flag covers only the calls the graph itself gated, and detached work starts with both HITL ContextVars cleared. One fix closes this row and Swarm SW-S01. | Inverted `test_background_task_inherits_graph_gate_and_skips_bus_approval` and Swarm `test_sw_s01_*`: the tool is refused, or the bus is asked once. A behavioural turn through the real tool worker: `dispatch_swarm`, then a fake engine, then `registry.execute("file_write")`, which reaches the bus or is refused. A class gate over every detached-work starter reachable from tool bodies (AST plus runtime probe), with a planted violation. `tests/test_bus_approval_can_be_answered.py::test_both_gates_honour_an_actual_approval` re-pointed to the per-call rule. Negative control: the old node-wide flag. | Implemented 2026-10-14 with Swarm SW-S01, not committed. Witness `test_background_task_inherits_graph_gate_and_skips_bus_approval` red (the detached task's write no longer happens). Regression and detached-starter gate in `tests/test_swarm_detached_work_asks_the_bus.py`; the graph's authority covers one call (`safety/call_authority.py`); `test_both_gates_honour_an_actual_approval` re-pointed |
| SCH-S03 | **Stage 1, P1 (multi-user/production profile).** Every cron mutation route and tool is tenant-scoped. Another tenant's id answers exactly like an unknown id. A missing tenant is refused in multi-user mode. `edit_scheduled` has a resolver that checks the caller's tenant, and its card shows the old and the new prompt. In multi-user mode, delivery goes to the booking principal. | Inverted `test_cron_mutations_ignore_tenant_while_list_is_scoped`. A route-by-tool tenant matrix in the style of `tests/test_memory_routes_tenant_scope.py`: every `/api/scheduled/*` route and every scheduling tool, called as tenant B with A's ids, changes nothing. Negative control: the current store methods. | Implemented 2026-10-14 -- decision D1: on an install with platform users every Scheduled surface refuses (`cron/single_operator.py`; routes 409 `scheduled_single_operator`, page banner `scheduled.single_operator` EN/AR, tools return the reason; a store that cannot answer refuses). Production mode scopes every read and change to the caller's tenant (`scheduled_tenant_filter`); another tenant's id answers `not_found`. `edit_scheduled` has its own act (`edit_job`, `_resolve_edit_job_act`: the tenant's live jobs only) and its card shows the prompt now and after (`cron/card.py`). Evidence: `tests/test_scheduled_api.py` (`test_every_route_keeps_to_the_callers_tenant`, `test_the_tools_keep_to_the_callers_tenant`, `test_with_platform_users_every_route_refuses`, `test_with_platform_users_every_tool_refuses`, `test_the_page_says_why_in_its_language`; negative control `test_negative_control_the_old_store_ignored_the_tenant`), `tests/test_cron_capabilities.py` (edit resolver and card), `tests/test_single_operator_surfaces.py` (cron module added); witness red 2026-10-14 (scratch copy, deleted). Not built (D1): per-user scheduling, and delivery to the booking principal in multi-user mode, where scheduling refuses. |
| SCH-S05 (authority half) | **Stage 1, P2.** Fired jobs run with a declared capability set, shown at booking. The default is read tools plus the one delivery. Write-tier delegation and booking are opt-in per job. The injected prompt is labelled as a stored instruction. Jobs do not fire while HITL is disabled, unless the operator opts in. | A cron-graph tool-surface test: the tool definitions offered to a fired job equal the declared set (negative control: the full registry). A test that a job does not fire while HITL is disabled. A test that the approval card shows the capability set. | Implemented 2026-10-14 -- decision D12: `kind` reminder (stored text, no model) or task; a task is offered exactly `read` (default) or `read_write` tools, never MCP or ALWAYS_HITL (`cron/capabilities.py`, `build_child_graph(declared_tools=)`, `DeclaredToolExecutor` refuses the rest); the prompt is labelled a stored instruction; tasks do not fire while HITL is off unless the operator set `safety.cron_fire_without_hitl`; the booking card says what the job will do. Evidence: `tests/test_cron_capabilities.py` (15; negative control `test_negative_control_the_old_builder_offered_the_whole_registry`); witness red 2026-10-14 (scratch copy, deleted). |
| SCH-E04 | **Stage 2, P1.** No silent loss across a restart or crash. A leased fire record (owner, lease, attempt, phase receipts). Every phase transition is one `BEGIN IMMEDIATE` compare-and-swap, and recurrence advances in the same step as finalize. Recovery decides per phase: retry, deliver the stored result, or finalize; never RUNNING-to-FAILED. Shutdown drains fires within a bound (`asyncio.wait`) before the agent closes. `/health/activity` counts fires. Exec-task exceptions are retrieved and logged. | Inverted `test_e04_*`. A real child-process crash matrix after every statement of `_execute`, `_deliver` and `_finalize`, for one-shot and daily jobs, in the pattern of `tests/test_document_crash_recovery.py`: each case ends with exactly one delivery, or one honest terminal state with an alert, and a daily job keeps a correct `next_run`. A `--when-idle` reload waits for an in-flight fire. Negative controls: the three-statement `_finalize` and the RUNNING-to-FAILED recovery. | Implemented 2026-10-14 -- leased fire record on `cron_jobs` (`fire_owner`, `lease_until`, `fire_due`, `fire_attempt`, `version`); claim is a CAS; the outcome, next occurrence, status and delivery row commit in ONE `BEGIN IMMEDIATE` (`finish_fire`); recovery per phase (boot: every running row back to pending at its occurrence; running: past lease or ours without a task; mid-send: `unknown`, never resent); `stop()` drains within a bound and runs first at shutdown; `/health/activity` counts `active_fires` and the guard's `--when-idle` waits for them; exec-task exceptions retrieved. Evidence: `tests/test_cron_fire_record.py`, `tests/test_cron_crash_matrix.py` (a real child killed after each committed step -- claim, in the turn, finish, outbox claim, platform acceptance, acknowledgement -- one-off reminder and daily task: 11 cases + 1 not applicable; negative control `test_negative_control_the_old_recovery_marked_failed`), `test_health_activity_counts_fires_and_the_guard_waits_for_them`; witness red 2026-10-14 (scratch copy, deleted). |
| SCH-E01 | **Stage 2, P1.** A turn with `error` or `turn_failed` is a failed fire: a typed outcome, the failure budget, and `cron.job_failure`. An error string is never delivered as the reminder. Reminders deliver their stored message without the LLM. A failed turn of an agent task is a failure. | Inverted `test_e01_*`. A stub turn that raises, and one that returns `turn_failed`, for one-shot and daily jobs. Negative control: the current `_execute`. | Implemented 2026-10-14 -- a raised turn, `error` or `turn_failed` is a failed fire (`FireResult`), counted and alerted (`cron.job_failure`, `cron.job_failed` when the budget of 3 runs out), never delivered; reminders deliver the stored text with no model. Evidence: `tests/test_cron_fire_record.py` (`test_provider_failure_...`, `test_turn_failed_one_off_...`, `test_a_reminder_is_delivered_with_no_model_call`, `test_the_budget_runs_out_...`; negative control `test_negative_control_the_old_produce_delivers_the_failure`), `tests/test_audit_2026_09_04_wave3.py` re-pointed; witness red 2026-10-14 (scratch copy, deleted). |
| SCH-E02 + SCH-S05 (delivery half) | **Stage 2, P1.** Cron delivery goes through a durable, leased outbox with bounded retries, acknowledged only on `sent:`. A refused or undeliverable delivery ends `undeliverable` with one ops alert (`cron.delivery_failed`), never DONE. The delivery outcome and its reason appear on the job, on the page and in the daily digest. | Inverted `test_e02_refused_delivery_is_done` and `test_refused_delivery_is_recorded_as_done`. A refusing backend gives `delivery_pending`, then `undeliverable`, with a retry history and exactly one alert. A backend that recovers gives exactly one delivery. A digest line. Negative control: today's `_deliver`-then-`_finalize` order. | Implemented 2026-10-14 -- `cron_deliveries` outbox: leased, backoff 30 s doubling to 15 min, 6 attempts within 2 h, acknowledged only on `sent:`; `undeliverable` (job too, never DONE) with one `cron.delivery_failed`; a send interrupted mid-way ends `delivery_unknown` with one alert, never resent; shown on the job, the page and the digest. Evidence: `tests/test_cron_delivery_outbox.py` (8; negative control `test_negative_control_the_old_order_ended_done_over_a_refusal`), `tests/test_send_results_read.py` re-pointed; witness red 2026-10-14 (scratch copy, deleted). |
| SCH-E06 | **Stage 2, P2.** An explicit `missed` state with a per-job catch-up window. A "missed while offline" notice through the outbox. A late delivery carries its original due time. The policy is documented for reminders, agent tasks and X posts. | Inverted `test_e06_missed_one_shot_skipped_silently`. Fake-clock tests for each lateness band: "missed" never reads as done, and each missed job yields exactly one notice. Negative control: the current stale skip. | Implemented 2026-10-14 -- `missed` status; catch-up window `cron.catchup_minutes` (60; `KAZMA_CRON_STALE_HOURS` when unset; per-job `catchup_seconds`); one notice through the outbox; a late fire says "(due HH:MM zone, N min late)". Policy documented for reminders, tasks and X (5-minute hold) in `docs/docs/guide/native-skills.md` and the X guide. Evidence: `test_a_job_past_its_catchup_window_is_missed_with_one_notice`, `test_a_recurring_job_missed_moves_on_with_one_notice`, `test_each_lateness_band` (4 bands), negative control `test_negative_control_the_old_stale_skip_reads_as_done`; witness red 2026-10-14 (scratch copy, deleted). |
| SCH-E07 | **Stage 2, P2.** Retention purges by `finished_at` and keeps the last outcome of every recurring job. Recovery reads every running row (`WHERE status='running'`, no limit). | Inverted `test_e07_*`. Purge and recovery behave the same with 0, 499 and 5,000 rows. Negative controls: the `created_at` purge and the limit-500 `list_all`. | Implemented 2026-10-14 -- retention by `finished_at` (legacy rows stamped at init), keeping each recurring job's last outcome; recovery reads every running row (`list_running`, no limit). Evidence: `test_purge_and_recovery_at_any_size[0|499|5000]`, negative control `test_negative_control_the_old_rules`; `tests/test_audit_2026_09_04_wave7.py` re-pointed; witness red 2026-10-14 (scratch copy, deleted). |
| SCH-E12 + SCH-S07 | **Stage 2, P2.** Only legal actions are offered and accepted: edit and cancel carry a version and an expected status. An edit or cancel during a fire is recorded and applied after it. A refused action answers `busy` (409) with a reason, not `not_found`/404. Bringing back a done, failed or cancelled job is an explicit action. The page shows the recurrence, failure count, last delivery result and destination. | Inverted `test_e12_reschedule_during_fire_is_lost` and `test_running_delete_and_cancelled_resurrection`. A state-by-action matrix on the real store. A blocked-fire race test. A JS test that every offered control maps to an allowed transition. Negative controls: the current `update_job` and `canDelete`. | Implemented 2026-10-14 -- edit and cancel carry `expected_version` (409 `conflict`); during a fire they are recorded and applied after it (202 `edit_pending`/`cancel_pending`); finished jobs answer 409 with a reason and come back only through Restore (`POST /api/scheduled/cron/{id}/restore`, "Run again"); the page offers the server's `actions` and shows recurrence, failures, last delivery and destination. Evidence: `test_state_by_action_matrix` (every status x action on the real store), `test_a_stale_version_is_a_conflict`, `test_edit_and_cancel_during_a_blocked_fire_are_applied_after_it`, negative control `test_negative_control_the_old_update_forced_pending`, `tests/js/test_scheduled_page.js`; witness red 2026-10-14 (scratch copy, deleted). |
| SCH-S02 | **Stage 2, P1.** Editing a scheduled task never changes the stored prompt unless the operator edited it. The list returns a display summary, the dialog fetches the full prompt, a timing-only edit sends no prompt, and a stale form is refused by version. | Inverted `test_page_edit_round_trip_truncates_and_rewrites_prompt`. A real-store route round trip (list, edit timing, save) leaves the prompt byte-identical, with `tests/test_scheduled_api.py` using the real scheduler for it. Negative control: the current `openEdit`/`saveCron`. | Implemented 2026-10-14 -- the list carries a display summary; the dialog fetches the full prompt (`GET /api/scheduled/cron/{id}`); a timing-only edit sends no prompt; a stale form is refused by version. Evidence: `tests/test_scheduled_api.py` on the real scheduler and store (`test_a_timing_edit_leaves_the_prompt_byte_identical`, `test_a_stale_form_is_refused_by_version`, negative control `test_negative_control_the_old_round_trip_truncates`); witness red 2026-10-14 (scratch copy, deleted). |
| SCH-E03 | **Stage 3, P1.** A naive time means the operator's zone on every path. Chat (`authorize_effect`), Web create, `edit_scheduled` and X booking resolve one string to one instant. The gate rewrites only to an aware ISO string in the operator's zone, or not at all. `edit_scheduled` goes through the same resolver. AGENTS §20's rewrite-on-allow is restated in the same change. | Inverted `test_e03_commitment_rewrite_shifts_naive_iso`. A shared cross-path fixture in UTC, Asia/Kuwait and America/New_York. Negative control: the UTC-assuming `parse_absolute_timing`. | Implemented 2026-10-14 -- one resolver (`cron/timing.py`): a naive time is the operator's zone on chat, Web, `edit_scheduled` and X booking; the gate rewrites only to an aware ISO instant in that zone; AGENTS §20 restated. Evidence: `tests/test_cron_timing_model.py::test_one_string_is_one_instant_on_every_path` (UTC, Asia/Kuwait, America/New_York, Europe/London), negative control `test_negative_control_the_old_gate_read_naive_as_utc`; witness red 2026-10-14 (scratch copy, deleted). |
| SCH-E05 + SCH-S09 (recurring items) + SCH-E09 (docstring item) | **Stage 3, P1.** English and Arabic recurring requests keep their recurrence and clock anchor through the commitment gate, and are never turned into a one-shot. The X tool docstring, the X dialog hint and the docs offer only forms the X path accepts. | Inverted `test_e05_*` (English and Arabic) and `test_recurring_x_time_is_advertised_then_refused`. An English and Arabic recurrence corpus through `authorize_effect`, whose first occurrence matches the scheduler. A test that every docstring, manifest and hint example is accepted by its validator. Negative controls: the current resolver and docstring. | Implemented 2026-10-14 -- EN and AR recurring requests keep their recurrence through the gate (never rewritten), a recurring request booked as a one-off is denied, a clock time anchors a whole-day lead; the X docstring, manifest, dialog hint and docs offer only one-off forms. Evidence: `test_recurring_requests_keep_their_recurrence` (EN+AR corpus; first occurrence equals the scheduler's), `test_a_recurring_request_booked_as_one_fire_is_refused`, `test_every_timing_example_is_accepted_by_its_validator`, negative controls `test_negative_control_the_old_relative_rewrite` and `test_negative_control_the_example_gate_sees_the_old_docstring`; witness red 2026-10-14 (scratch copy, deleted). |
| SCH-E09 (parser items) + SCH-S06 (timing items) | **Stage 3, P2.** One typed validator for cron and X. It accepts RFC 3339, future times only and a finite range; applies one DST policy; supports a typed recurrence (an RRULE subset: daily or weekly, BYHOUR/BYMINUTE, BYDAY, UNTIL/COUNT, TZID); states the zone in errors and on the form; and answers 400 for validation errors, never 500 or 404. | Inverted `test_e09_parser_gaps`, and the timing assertions of `test_cron_routes_accept_past_zero_huge_and_misreport`. A timing input table run through both the cron and X paths. A fake-clock run across two DST transitions: each daily occurrence exactly once, at the intended local time. Negative control: the current parser. | Implemented 2026-10-14 -- one typed validator for cron and X (RFC 3339 with `Z`/offsets, future only, two-year horizon, 100 characters, one DST policy, typed recurrence incl. an RRULE subset with BYDAY/BYHOUR/BYMINUTE/UNTIL/COUNT/TZID); errors name the zone and answer 400. Evidence: `test_one_validator_for_cron_and_x` (input table through both paths), `test_errors_name_the_zone`, `test_one_clock_change_policy`, `test_a_daily_series_across_two_clock_changes` (fake clock, each occurrence once at its local time), `test_count_and_until_end_a_series`, negative control `test_negative_control_the_old_parser`; witness red 2026-10-14 (scratch copy, deleted). |
| SCH-S04 | **Stage 4, P2.** The Scheduled page reads X operation truth, and shows upcoming, needs attention, published, cancelled and failed, with catalog keys in both languages. Edit and Cancel go through the revision-bound queue actions. `PUT/DELETE /api/scheduled/x/{id}` are retired, or held to the same binding as X Studio. | Inverted `test_new_x_bookings_are_managed_and_page_treats_them_as_done`. A JS test over every projected X and cron status (negative control: `else c.done++`). A route parity test for both reschedule paths. The Arabic page tour seeded with `managed` and `held` posts. | Implemented 2026-10-14 -- the page lists the X publication store's operations with one status-class map (upcoming, attention, done, failed, cancelled; Python and JS held equal) and catalog labels EN/AR for every cron and X status; Edit/Cancel use `/api/x/queue/{op}/reschedule|cancel` with the revision; `PUT/DELETE /api/scheduled/x/{id}` retired. Evidence: `tests/test_scheduled_presentation.py` (negative control `test_negative_control_the_old_catalog_lacked_labels`), `tests/js/test_scheduled_page.js`, `test_x_rows_are_the_operation_truth`, `test_the_pages_parallel_x_reschedule_and_delete_are_gone`, `tests/e2e/test_scheduled_reads_in_arabic.py::test_the_scheduled_page_reads_in_arabic_in_every_status` (every cron status, an X post scheduled and one held: no English, no raw id); witness red 2026-10-14 (scratch copy, deleted). |
| SCH-E08 | **Stage 4, P2.** Rebooking a terminal proposal, or a proposal booked at another time, never reports `scheduled: True`. It answers a conflict or creates a new key revision. The response echoes the stored `due_at` and state, and `ok` is false unless the state is `scheduled`. | Inverted `test_e08_rebook_after_cancel_reports_scheduled`. A rebook after cancel, after `failed_permanent`, and while pending at a new time. Negative control: the current `reserve` replay. No real X call. | Implemented 2026-10-14 -- a rebook after cancel, `failed_permanent` or `expired` is a new key revision at the new time; while booked at another time it answers `booking_conflict`; replies echo the stored time and state, `ok` only when `scheduled`. No real X call (the client fails the test if reached). Evidence: `tests/test_x_rebook_and_fire_cycle.py` (rebook tests, negative control `test_negative_control_the_old_replay`), `test_x_booking_cannot_report_scheduled_over_a_cancelled_row`; witness red 2026-10-14 (scratch copy, deleted). |
| SCH-S08 | **Stage 4, P2.** Clarify and confirm cards carry structured options (kind, ISO instant, job id). They are rendered from the catalog in both languages and show the date, time and zone. The question is phrased for the user. | Inverted `test_remind_clarify_options_are_english_and_date_only`. An Arabic e2e clarify step: no English, and a visible time. The semantic card added to `english_on` coverage. Negative control: the current label builder. | Implemented 2026-10-14 -- remind and cancel clarify options are structured (`kind`, ISO `at`, `zone`, `job_id`) with a `question_kind`; the page words them from the catalog EN/AR with date, time and zone (the cancel options now carry each job's next time). Evidence: `tests/test_scheduled_clarify_card.py` (remind card worded in EN and AR through the page's own functions in node; negative control `test_negative_control_the_old_labels_were_date_only`), and the Arabic e2e step `tests/e2e/test_scheduled_reads_in_arabic.py::test_the_cancel_clarify_card_reads_in_arabic_with_a_time` (a real scripted turn paused by the commitment gate; question, header and options Arabic, the job's time shown, no English on the card); witness red 2026-10-14 (scratch copy, deleted). The remind card is checked in node, not through a full turn (that needs a memory event anchor in the harness). |
| SCH-E14 + SCH-S06 (bounds items) | **Stage 5, P2.** Caps per tenant and per thread, with typed refusals. Body limits on prompt and timing. A rate bucket for `/api/scheduled/*`. A per-kind execution budget in configuration. An index on `(status, next_run)`. | The size assertion of `test_cron_routes_accept_past_zero_huge_and_misreport`, inverted. Body-size, cap and rate tests. A load test with 1,000 pending jobs and 50 due at once, within a stated latency bound. | Implemented 2026-10-14 -- caps per tenant (500) and per thread (50) with typed refusals; prompt 8,000 and timing 100 characters; a `scheduled` rate bucket (30/min); `cron.task_budget_seconds`; index `(status, next_run)`; a pass now starts every due job (the semaphore holds all but `max_concurrent`). Evidence: `test_bad_inputs_are_refused_with_a_reason`, `test_the_job_cap_refuses_with_a_code`, `test_mutations_carry_the_scheduled_rate_bucket`, `tests/test_cron_load.py` (1,000 pending, 50 due: all 50 delivered in ONE pass, under 1 s measured, bound 15 s; the due query reads `idx_cron_jobs_due`; negative control: the old four-a-pass rule needs >= 12 passes); witness red 2026-10-14 (scratch copy, deleted). |
| SCH-E10 + SCH-S11 | **Stage 5, P3.** No settings, RBAC or SQLite read, and no graph build, on the event loop from cron, the Scheduled routes or the scheduling tools. These values are resolved once, in a thread, at the boundary. | Inverted `test_e10_cron_reads_settings_on_the_loop` and `test_cron_routes_read_settings_and_rbac_on_the_loop`. `_LOOP_STALL_HELPERS` extended, or the gate made to follow public and imported wrappers, with a planted async caller of `multi_user_or_production` as control. The scheduled routes added to the GET-route loop walk, with the RBAC store traced. Coordinate with KB-S06, MEM-S10, SW-E07 and TT-07. | Implemented 2026-10-14 -- the zone (one settings read, `resolve_cron_zone`), the tenant rule, the delivery address and the fire's graph build are resolved in a thread; alerts are raised off the loop. Evidence: the static gate's area `scheduled` in `_CROSS_MODULE_AREAS` (cross-module inference over the cron, Scheduled route and scheduling-tool modules), `tests/test_scheduled_off_loop.py` (engine and routes traced: nothing on the loop, the zone once per request; planted async caller of `multi_user_or_production` as the gate's control); witness red 2026-10-14 (scratch copy, deleted). Coordination with KB-S06, MEM-S10, SW-E07, TT-07: the shared gate is append-only; only this area was added. |
| SCH-E11 | **Stage 5, P3.** A cron poll heartbeat in readiness, with a staleness threshold. Metrics and digest lines for fires, lateness p50/p95, missed, failed and undelivered. An alert when the failure budget runs out. | Inverted `test_e11_health_ok_with_dead_poll_loop`. Cancelling the poll task makes readiness degraded within one interval. An emitted-line gate (`tests/_emitted_lines.py`). | Implemented 2026-10-14 -- readiness reads the poll heartbeat (`CronScheduler.liveness()`: degraded within two intervals plus a margin of a dead or stuck loop); /metrics carries fires by outcome, deliveries, missed, lateness p50/p95, the outbox and the heartbeat age; the digest counts outcomes from the `[cron] outcome` line; an alert when the failure budget runs out. Evidence: `tests/test_cron_liveness.py` (poll task cancelled -> readiness degraded; negative control: the old flag check reads ok), `tests/test_cron_delivery_outbox.py::test_the_digest_reads_a_line_the_scheduler_writes` (emitted-line gate), `test_the_budget_runs_out_after_three_failures_and_says_so`; witness red 2026-10-14 (scratch copy, deleted). |
| SCH-E13 | **Stage 5, P3.** The X fire cycle isolates per-row errors (a held state plus a notice). A bounded automatic retry for provably unsent (`not_sent`) posts, within the missed window. Retention by age for terminal operations, events and delivered outbox rows. The booking reply gives its time in the stated zone. | A raising-row test: a later due row is still claimed in the same cycle. A `not_sent` retry test. A retention test. No real X call. | Implemented 2026-10-14 -- one raising row is held with a notice and the cycle goes on; a provably unsent post is tried again a minute later, three tries in all; finished operations, events, delivered notices and done projections pruned after 90 days (hourly); the booking reply gives the cron zone. No real X call. Evidence: `tests/test_x_rebook_and_fire_cycle.py` (`test_one_raising_row_does_not_stop_the_rows_after_it`, negative control `test_negative_control_without_isolation_the_cycle_stops`, `test_a_provably_unsent_post_is_retried_bounded`, `test_retention_prunes_finished_operations_by_age`, `test_the_reply_names_the_operators_zone`). |
| SCH-S10 | **Stage 5, P3.** No scheduling tool result contains a platform address. The model gets the platform and a non-identifying label. | Inverted `test_schedule_task_result_hands_the_chat_id_to_the_model`. A result-shape test over every scheduling tool: no match for `^\w+:\d+`. Negative control: the current result. | Implemented 2026-10-14 -- no scheduling tool result or page answer carries the address; jobs give `destination`, a label. Evidence: `test_no_answer_carries_the_chat_address` (every scheduling tool and route, `^\w+:\d+`), negative control `test_negative_control_the_old_result_named_the_address`; witness red 2026-10-14 (scratch copy, deleted). |
| SCH-S12 | **Stage 5, P3.** Dates go through `KazmaFormat`, with a zone label. One same-origin helper over `configured_browser_origins()` replaces the copies in `scheduled_api.py`, `x_api.py` and `email_api`. Every cron mutation records its actor and source. | Inverted `test_page_dates_bypass_kazmaformat_and_x_menu_label` (dates half), `test_local_csrf_copy_ignores_declared_public_origin` and `test_cron_rows_record_no_actor`. A static gate that refuses `Intl.DateTimeFormat` and `toLocale*` outside `locale_format.js`, with a planted call. A CSRF test with `KAZMA_PUBLIC_URL` and a rewritten `Host` (negative control: the current copy). An audit-row test. | Implemented 2026-10-14 -- dates on the Scheduled page and X Studio through `KazmaFormat` with a zone label; one same-origin helper (`kazma_ui/same_origin.py`, declared origins + served origin + Host) for the Scheduled, X and email routes; every cron change records actor and source (`cron_events`). Evidence: `tests/test_scheduled_presentation.py::test_dates_go_through_kazmaformat` (ratchet: the two pages held at zero, others at their count; planted call as control), `test_the_declared_public_origin_passes`, `test_every_change_records_its_actor_and_source`, `tests/test_email_api_security.py`; witness red 2026-10-14 (scratch copy, deleted). |
| SCH-DOC + SCH-S09 (documentation items) | **Stage 5, P3.** Every item in section 6 of the merged audit is corrected in the same change as its behaviour. That covers AGENTS.md (§16B fallback text, §16 invariant count, §51 cron note, §7A double-gating wording, §20 rewrite-on-allow and the G2 note, §29C rebook scope); the comments in `scheduler.py`, `app.py`, `health.py` and `x_api/schedule.py`; `x-publisher.md`, `native-skills.md`, `api-routes.md` and `configuration.md`; the manifest statuses; and the `/x` menu. A new section says what a scheduled task can do when it fires. | A doc and comment diff tied to the behavioural tests above. A docs check that the route paths named in `x-publisher.md` exist in the built app. The menu half of `test_page_dates_bypass_kazmaformat_and_x_menu_label`, inverted. | Implemented 2026-10-14 -- AGENTS.md §16 rewritten (A-I) and the §7A (already restated by SW-S01), §20 (rewrite-on-allow in the operator's zone, recurrence never rewritten, `edit_job`, G2 scope), §29C (rebook) and §51 (outbox) lines; comments in `scheduler.py`, `app.py`, `health.py`, `x_api/schedule.py`; `x-publisher.md`, `native-skills.md` (new section "Scheduled reminders and tasks", incl. what a job may do when it fires), `api-routes.md`, `configuration.md` (`_EXTENDED_DANGER` wording, `cron.*` keys), the env page row, the manifest statuses, the `/x` menu. Evidence: `tests/test_scheduled_docs_routes.py` (every route the X guide and the API reference's Scheduled section name is served; the old planner line as control), `tests/test_scheduled_presentation.py::test_the_x_menu_promises_only_what_x_does` (old menu text as control), `tests/test_cron_timing_model.py::test_every_timing_example_is_accepted_by_its_validator`, docs anchors / file refs / env reference gates. |

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

   **Harness: built 2026-10-15.** `scripts/scheduled_soak.py` runs the
   real `CronScheduler` on a private SQLite cron.db, through `--days` (7)
   simulated days of a fake clock, in minutes. The engine now reads every
   time it records, and its poll wait, from one injectable clock
   (`kazma_core/cron/clock.py`; behaviour and gate in
   `tests/test_cron_clock.py`, negative control included).

   *What it runs:*
   - Thousands of jobs (`--jobs`, 2,000), booked through
     `CronScheduler.schedule`, the path the agent tool and the page use:
     - reminders, and tasks run by a scripted graph;
     - one-offs written four ways: a UTC `Z` time, a Kuwait offset, the
       operator's zone, or relative;
     - daily, weekday and weekly series, friendly or RRULE, with or
       without COUNT/UNTIL;
     - in America/New_York (which falls back during the window), in
       Asia/Kuwait and in UTC;
     - some with their own catch-up window.
   - A fifth of the jobs are booked mid-run. Some are edited or cancelled
     through the versioned actions, and a stale version must be refused.
   - Provider failures: a turn raises, fails, or stops at an approval;
     outage windows in which no graph builds; jobs that always fail, so
     their budget runs out.
   - Delivery failures: refused, raised, Kazma dying mid-send, a chat that
     always refuses, a job with no address.
   - Database failures: cron.db's write lock is held from another
     connection until a poll pass, and a fire's own finish, have failed on
     it.
   - A restart every `--restart-hours` (3): graceful, or abrupt mid-turn or
     mid-send.
   - Kills around an alert (`--alert-kills-per-day`, 1), alternating: right
     after a change that queued an alert committed, before the alert; or
     right after an alert was raised, before its row was marked sent (the
     ledger records it: `alert_redelivered`).
   - One downtime longer than the catch-up window.
   - A dead poll loop.

   *What it checks:* ground truth comes from the harness, not the
   scheduler. It enumerates each job's due occurrences from the job's own
   definition, with `timing.py` as a second opinion at booking. It checks:
   - every due occurrence ends exactly once: delivered; missed with one
     notice; failed, undeliverable or delivery-unknown, with its reason on
     the job and exactly one alert (two only where the ledger recorded a
     kill between raising that alert and marking it sent); or cancelled;
   - nothing is delivered twice, and nothing fires at a time it was not
     due;
   - a missed occurrence was really due while the scheduler was down;
   - a series across the clock change fires once per local day, at its
     wall time (a series is the job's timing between two timing edits;
     every fire counts against the series in force at its time);
   - p95 lateness (claim to due, on fires due while the scheduler was up)
     is at most one poll interval plus the turn time;
   - readiness is degraded within one interval of the dead loop.

   *Evidence it writes:* the report is rewritten whole as the run goes,
   and a ledger is kept so `analyse` can re-check the run.

   *Its test:* `tests/test_scheduled_soak_harness.py`. A three-day run
   with 240 jobs ends healthy, having done each kind of work, both kills
   around an alert included. The report is on disk while the run goes. The
   check reports each planted violation: a message delivered twice, an
   occurrence that never fired, a failure with no alert, and an alert
   raised twice with no kill recorded (or a recorded kill whose alert was
   not raised again).

   **Still open:** the 7-day qualification run itself (D3: a separate
   machine or an agreed overnight slot).

   **Found while building it -- fixed 2026-10-15** (two narrow windows in
   which a crash lost an alert, never the job's visible state):
   - `_alert_failed_deliveries` marked a delivery alerted before it raised
     the alert: a crash between the two dropped it;
   - a failed fire's alert was raised after `finish_fire` committed: a crash
     between the two dropped it.

   *The fix: an alert outbox* (`cron_alerts` in cron.db, AGENTS §16 E2).
   Each change that needs an alert writes its row in its own transaction
   (`SQLiteCronStore._enqueue_alert` inside `finish_fire`,
   `finish_delivery`, `recover_deliveries`; a cron.db an older build left
   gets its unalerted failures queued when the store opens). The scheduler
   drains it after the commit, at each poll and at start
   (`CronScheduler._drain_alerts`, into `ops_alerts.alert`), and marks a
   row sent only once `alert` returned. That is at least once: the only
   duplicate is a crash between the alert and its mark. Within a process a
   row is raised once (one pass at a time; a failed mark is retried without
   raising again; `stop()` waits for a pass in progress). A row that cannot
   be raised stays queued with a WARNING and a backoff;
   `kazma_cron_alerts_pending` on /metrics, `alerts_pending` in readiness.
   Raised rows follow the job retention.

   *Evidence:* `tests/test_cron_alert_outbox.py` -- a real child process
   killed (`os._exit`) in each window: right after the change committed
   (a failed fire, a message ended undeliverable, a send recovered
   unknown at boot) the alert is raised once after the restart; killed
   after the alert and before its mark it is raised once more, then never
   again. Negative control: the old order (alert after the fire's commit,
   a delivery marked before its alert) in the same harness loses it. Also
   in process: a row `ops_alerts` cannot raise stays queued with a WARNING
   and is raised after its backoff; a refused mark is made later and never
   raised twice (control: without that memory it is raised twice);
   concurrent passes raise each row once (control: no pass lock); `stop()`
   lets a pass in progress mark what it raised (control: the old stop
   leaves it unmarked); an older build's unalerted failures are queued
   once; retention. Class gates, each with its negative control: the cron
   engine names `ops_alerts.alert` only in `CronScheduler._raise_alert`;
   no `cron_alerts` row is written outside its change's transaction. The
   soak now kills in both windows (`--alert-kills-per-day`), and its check
   allows a second alert only where the ledger recorded the kill.

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
- 2026-10-14: Stages 1-4 and the Stage 5 rows implemented in one pass (stream S2), on branch `worktree-agent-afe34e1f329b02268`. All 31 witnesses run red from a scratch copy (each for its fixed reason), copy deleted. Area tests, the touched-module selection (217 files) and the listed global gates green; Arabic page tour and the new Arabic Scheduled e2e green. Load test (1,000 pending / 50 due) in `tests/test_cron_load.py`. No Postgres run was needed by a row. Open: the 7-day fake-clock soak with injected failures and restarts (Stage 5 exit gate; D3, owner), the full suite (`scripts/fast_test.py`, the coordinator's), an independent review, and per-user scheduling (D1).
- 2026-10-15: Stage 5 soak harness built.
  - **What was added.** `scripts/scheduled_soak.py`, with its test
    `tests/test_scheduled_soak_harness.py`. A product seam the harness needs:
    the cron engine now reads every time it records, and its poll loop's
    wait, from one injectable clock (`kazma_core/cron/clock.py`; the store
    takes `clock=`, and the scheduler uses the store's). Its behaviour test
    and gate are in `tests/test_cron_clock.py`; the old reads are the
    negative control.
  - **A development run.** The defaults (7 simulated days, 2,000 jobs, seed
    20261015) passed on the dev machine in 169 s of wall time:
    - 5,228 due occurrences, each ended exactly once:
      - 4,932 delivered;
      - 24 missed, with one notice each;
      - 188 failed (51 provider outage, 137 failed turns);
      - 65 undeliverable;
      - 13 delivery unknown;
      - 6 not due, because their failure budget had run out.
    - 57 restarts: 28 graceful, 14 killed mid-turn, 13 killed mid-send, one
      105-minute gap, one after the dead loop.
    - 7 lock holds, which failed 8 writes, 4 of them a fire's own finish.
    - 257 alerts, exactly the 257 the rules expect.
    - p95 lateness 26 s while the scheduler was up; the bound is 50 s.
    - Readiness was degraded at once when the loop died.
    - 176 series crossed the New York fall-back.
    A second run with the same seed gave the same report, except for wall
    time and memory.
  - **This is not the qualification run.** It shows that the harness
    works. The qualification run stays open (D3, owner).
- 2026-10-15: The two alert-loss windows the harness found are closed by an
  alert outbox (Stage 5, "Found while building it": AGENTS §16 E2; evidence
  under the harness above). The soak now also kills in both windows.
  - **A development run** of the defaults with the outbox (7 simulated
    days, 2,000 jobs, seed 20261015) passed in 160 s of wall time:
    - the same 5,228 due occurrences, each ended exactly once (4,932
      delivered, 24 missed with one notice each, 188 failed, 65
      undeliverable, 13 delivery unknown, 6 not due after their budget);
    - 64 restarts: 28 graceful, 14 killed mid-turn, 13 killed mid-send, 4
      killed right after a change that queued an alert committed (three
      failed fires, one undeliverable message), 3 killed after an alert
      was raised and before its mark, one 105-minute gap, one after the
      dead loop;
    - 260 alerts: the 257 the rules expect, plus exactly one more for each
      of the 3 recorded kills between raising an alert and marking it;
      none lost in the 4 kills after a commit;
    - 7 lock holds (8 writes failed, 4 of them a fire's own finish); p95
      lateness 26 s while up (bound 50 s); readiness degraded at once on
      the dead loop; 176 series across the New York fall-back.
  - Still not the qualification run (D3, owner).
- 2026-10-15: A 28-day run (8,000 jobs, seed 20261028, on c4e8f51c) failed
  with two violations, "fired 2 times on local day", both the harness's check
  misjudging an edit. j04319 (daily 07:49) fired 07:49 on 2026-11-02 and was
  edited at 13:31 to "every day at 20:37", whose first fire is 20:37 the same
  day; j04955 (daily 01:45) fired 01:45 on 2026-11-21, was edited at 04:19 to
  12:29pm and fired 12:29 that day. Each fire was due, ended once and was
  delivered once -- what the edits asked for. The check counted a job's fires
  per local day across its edits. It now counts per series (the timing
  between two edits), with every end of the job against the series in force
  at its time. It counted only ends already in the harness's own list, so a
  series that fired twice in a day was caught only as "a time it was not
  due"; now it is caught here too, as is an old series firing after the edit.
  Evidence: the failed run's own ledger re-checks clean (the old check gives
  exactly the two); `tests/test_scheduled_soak_harness.py` (the j04319 shape
  passes; negative controls: both instants of a fall-back fold, and the old
  series firing after an edit, each reported).
  - **The same 28-day run on b52b2fab plus this check** passed in 1,572 s:
    68,397 due occurrences, each ended exactly once (64,681 delivered, 79
    missed with one notice each, 2,278 failed, 787 undeliverable, 55
    delivery unknown, 517 not due after their budget); 251 restarts (111
    graceful, 56 mid-turn, 55 mid-send, 13 after an alert's commit, 14
    between an alert and its mark, a gap, the dead loop); 2,969 alerts =
    2,955 expected + 14 recorded redeliveries; 28 lock holds (33 writes
    failed, 22 a fire's own finish); p95 lateness 28 s (bound 50 s); 847
    series across the fall-back. j04319 and j04955 repeated their edits and
    passed.
  - The 7-day defaults gave the same report as the run above (160 s).

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
